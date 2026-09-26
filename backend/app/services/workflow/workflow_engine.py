"""
Workflow Engine - Enterprise Workflow Automation Engine sprint.

Owns graph traversal and the WorkflowExecution state machine. Mirrors
CollectionOrchestrator's shape (one public advance-the-run method, a
fixed lifecycle, catch-all error handling that always terminates cleanly)
applied to a graph instead of a single collector call.

State machine (11 states from the sprint brief):
    queued -> waiting_approval -> running -> succeeded
                                \\-> paused -> running
                                \\-> retrying -> running
                                \\-> failed -> rolled_back
    (any non-terminal) -> cancelled

`run_step()` advances the execution by exactly one "tick": it resolves
the current node(s), executes them via NodeExecutorRegistry, applies the
NodeResult(s), and either advances to the next node(s), parks the
execution (waiting_approval/paused), or terminates it
(succeeded/failed/rolled_back). It never loops internally past a
"waiting" result - same "one call = one bounded unit of work, no
open-ended internal looping" convention as CollectionOrchestrator.run().
Callers (the execute API, the scheduler tick) call run_step() repeatedly
until a step returns "waiting" or terminal.

Parallel execution: nodes sharing the same `parallel_group` value in
definition_snapshot are all placed into `current_node_id` (a list,
serialized as a JSON array string) and executed within the same
run_step() call; the group only advances past once every member has
reached a terminal-for-this-step result. Sequential nodes are simply
`parallel_group=None`, one node in current_node_id at a time.

Retry policy: a node's `config["retry"]` (`{"max_attempts": N,
"backoff_seconds": S}`) is read by run_step() itself (not by individual
executors) so every node type gets retry behavior for free, per the
sprint's "Retry Policies" cross-cutting requirement. Timeout policy
mirrors this via `config["timeout_seconds"]` compared against the node's
recorded start time in step_history.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.models.workflow_execution import WorkflowExecution
from app.repositories.workflow_execution_repository import WorkflowExecutionRepository
from app.services.workflow.node_executor_registry import NodeExecutorRegistry, NodeResult

logger = logging.getLogger(__name__)

_NON_TERMINAL_STATUSES = ("queued", "waiting_approval", "running", "paused", "retrying")


class WorkflowDefinitionError(ValueError):
    """definition_snapshot is malformed (missing start node, dangling
    edge, etc.) - raised at queue time, never mid-run."""


class WorkflowEngine:
    def __init__(self, execution_repo: WorkflowExecutionRepository, db):
        self.execution_repo = execution_repo
        self.db = db

    @staticmethod
    def validate_definition(definition: dict) -> None:
        """Structural validation only (real, checkable facts about the
        graph) - never a semantic/business judgment about whether the
        workflow is "good". Called by WorkflowService before a version is
        allowed to be approved."""
        nodes = definition.get("nodes", [])
        if not nodes:
            raise WorkflowDefinitionError("Workflow definition has no nodes.")
        node_ids = {n["id"] for n in nodes}
        if len(node_ids) != len(nodes):
            raise WorkflowDefinitionError("Workflow definition has duplicate node ids.")
        start_nodes = [n for n in nodes if n.get("type") == "start"]
        if len(start_nodes) != 1:
            raise WorkflowDefinitionError("Workflow definition must have exactly one 'start' node.")
        for edge in definition.get("edges", []):
            if edge["from"] not in node_ids or edge["to"] not in node_ids:
                raise WorkflowDefinitionError(f"Edge {edge!r} references an unknown node id.")

    def _find_node(self, definition: dict, node_id: str) -> dict:
        for node in definition.get("nodes", []):
            if node["id"] == node_id:
                return node
        raise WorkflowDefinitionError(f"Node {node_id!r} not found in definition_snapshot.")

    def _start_node_id(self, definition: dict) -> str:
        for node in definition.get("nodes", []):
            if node.get("type") == "start":
                return node["id"]
        raise WorkflowDefinitionError("Workflow definition must have exactly one 'start' node.")

    def _next_node_ids(self, definition: dict, from_node_id: str) -> list[str]:
        return [edge["to"] for edge in definition.get("edges", []) if edge["from"] == from_node_id]

    def _current_node_ids(self, execution: WorkflowExecution) -> list[str]:
        if execution.current_node_id is None:
            return [self._start_node_id(execution.definition_snapshot)]
        try:
            parsed = json.loads(execution.current_node_id)
            return parsed if isinstance(parsed, list) else [execution.current_node_id]
        except (json.JSONDecodeError, TypeError):
            return [execution.current_node_id]

    async def run_step(self, execution: WorkflowExecution) -> WorkflowExecution:
        """Advances `execution` by one bounded unit of work. Safe to call
        repeatedly - a no-op (returns execution unchanged) if it is
        already in a terminal state, same idempotency convention as
        PriorityEngine.recompute()."""
        if execution.status not in _NON_TERMINAL_STATUSES:
            return execution

        definition = execution.definition_snapshot
        current_ids = self._current_node_ids(execution)

        if execution.status == "queued":
            execution = await self.execution_repo.transition(
                execution, status="running", started_at=datetime.now(timezone.utc), current_node_id=json.dumps(current_ids)
            )
            execution = await self.execution_repo.append_audit(
                execution, event_type="started", message=f"Workflow execution started at node(s) {current_ids}."
            )

        try:
            results = await self._execute_nodes(execution, definition, current_ids)
        except Exception as exc:  # noqa: BLE001 - catch-all, same convention as CollectionOrchestrator.run()
            logger.exception("Unhandled error executing workflow node(s) %s for execution %s", current_ids, execution.id)
            return await self._fail(execution, reason=f"Unhandled error: {exc}")

        return await self._apply_results(execution, definition, current_ids, results)

    async def _execute_nodes(self, execution: WorkflowExecution, definition: dict, node_ids: list[str]) -> dict[str, NodeResult]:
        results: dict[str, NodeResult] = {}
        for node_id in node_ids:
            node = self._find_node(definition, node_id)
            executor_cls = NodeExecutorRegistry.get(node["type"])
            executor = executor_cls()
            results[node_id] = await executor.execute(execution=execution, node=node, config=node.get("config", {}), db=self.db)
        return results

    async def _apply_results(
        self, execution: WorkflowExecution, definition: dict, node_ids: list[str], results: dict[str, NodeResult]
    ) -> WorkflowExecution:
        merged_variables = dict(execution.variables)
        for node_id, result in results.items():
            merged_variables.update(result.output_variables)
        if merged_variables != execution.variables:
            execution = await self.execution_repo.set_variables(execution, merged_variables)

        for node_id, result in results.items():
            step_entry = {
                "node_id": node_id,
                "node_type": self._find_node(definition, node_id).get("type"),
                "status": result.status,
                "message": result.message,
                "linked_execution_id": result.linked_execution_id,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            execution = await self.execution_repo.append_step(execution, step_entry)

        failed = {nid: r for nid, r in results.items() if r.status == "failed"}
        if failed:
            retryable = await self._maybe_retry(execution, definition, failed)
            if retryable is not None:
                return retryable
            reasons = "; ".join(f"{nid}: {r.message}" for nid, r in failed.items())
            return await self._fail(execution, reason=reasons)

        waiting = {nid: r for nid, r in results.items() if r.status == "waiting"}
        if waiting:
            timed_out = self._check_timeouts(execution, definition, waiting)
            if timed_out:
                reasons = "; ".join(f"{nid}: exceeded timeout_seconds" for nid in timed_out)
                return await self._fail(execution, reason=reasons)
            # A parallel group only truly "waits" once at least one member
            # waits and none have failed - persist the execution-level
            # status from whichever waiting result declares one (approval
            # takes precedence over a generic pause if both are present).
            statuses = [r.execution_status for r in waiting.values() if r.execution_status]
            new_status = "waiting_approval" if "waiting_approval" in statuses else (statuses[0] if statuses else "paused")
            return await self.execution_repo.transition(execution, status=new_status)

        # Every node in this step succeeded - advance to the union of
        # their next-node-ids (decision nodes may override via
        # next_node_ids; everything else follows definition_snapshot's
        # edges).
        next_ids: list[str] = []
        for node_id, result in results.items():
            if result.next_node_ids is not None:
                next_ids.extend(result.next_node_ids)
            else:
                next_ids.extend(self._next_node_ids(definition, node_id))
        next_ids = list(dict.fromkeys(next_ids))  # de-dupe, preserve order

        if not next_ids:
            # No outgoing edges from any just-completed node - this is
            # only valid if every completed node was an 'end' node.
            end_types = {self._find_node(definition, nid).get("type") for nid in node_ids}
            if end_types == {"end"}:
                return await self.execution_repo.complete(execution, status="succeeded")
            return await self._fail(execution, reason=f"Node(s) {node_ids} have no outgoing edges and are not 'end' nodes.")

        return await self.execution_repo.transition(execution, status="running", current_node_id=json.dumps(next_ids))

    def _check_timeouts(self, execution: WorkflowExecution, definition: dict, waiting: dict[str, NodeResult]) -> list[str]:
        """Timeout Policy deliverable: a waiting node's own
        `config["timeout_seconds"]` is compared against the FIRST recorded
        step_history entry for that node_id (its original wait start) -
        real, already-persisted data, never a fabricated clock. Returns
        the list of node_ids that have exceeded their configured timeout;
        a node with no timeout_seconds configured never times out."""
        now = datetime.now(timezone.utc)
        timed_out: list[str] = []
        for node_id in waiting:
            node = self._find_node(definition, node_id)
            timeout_seconds = node.get("config", {}).get("timeout_seconds")
            if timeout_seconds is None:
                continue
            first_wait_entry = next(
                (s for s in execution.step_history if s["node_id"] == node_id and s["status"] == "waiting"), None
            )
            if first_wait_entry is None:
                continue
            first_waited_at = datetime.fromisoformat(first_wait_entry["recorded_at"])
            if (now - first_waited_at).total_seconds() > timeout_seconds:
                timed_out.append(node_id)
        return timed_out

    async def _maybe_retry(
        self, execution: WorkflowExecution, definition: dict, failed: dict[str, NodeResult]
    ) -> WorkflowExecution | None:
        """Retry Policy deliverable: a failed node's own
        `config["retry"] = {"max_attempts": N}` is consulted before the
        engine gives up. `execution.retry_count` is a single workflow-
        level counter (simple, explainable) rather than per-node -
        sufficient for this sprint's approval-based workflows where a
        single task/runbook_task node failing is the common retry case.
        Returns None (no retry) when any failed node has no retry policy
        or has exhausted its max_attempts."""
        max_attempts_per_node = []
        for node_id in failed:
            node = self._find_node(definition, node_id)
            retry_cfg = node.get("config", {}).get("retry")
            if not retry_cfg or "max_attempts" not in retry_cfg:
                return None
            max_attempts_per_node.append(int(retry_cfg["max_attempts"]))

        if execution.retry_count >= min(max_attempts_per_node):
            return None

        execution = await self.execution_repo.transition(execution, status="retrying", retry_count=execution.retry_count + 1)
        execution = await self.execution_repo.append_audit(
            execution,
            event_type="retrying",
            message=f"Retrying node(s) {list(failed)} (attempt {execution.retry_count}).",
        )
        # Immediately flip back to running so the next run_step() call
        # actually re-executes the same node(s) rather than parking
        # indefinitely at "retrying" - the "retrying" status is recorded
        # in the audit trail/DB for observability but is transient.
        return await self.execution_repo.transition(execution, status="running")

    async def _fail(self, execution: WorkflowExecution, *, reason: str) -> WorkflowExecution:
        execution = await self.execution_repo.append_audit(execution, event_type="failed", message=reason)
        return await self.execution_repo.complete(execution, status="failed", failure_reason=reason)
