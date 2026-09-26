"""
Node executor interface + registry - Enterprise Workflow Automation
Engine sprint.

Same Adapter + Registry pattern as `CollectorRegistry`
(`app/collectors/base.py`): every node TYPE (approval, decision, delay,
notification, runbook_task, ...) is a class implementing `BaseNodeExecutor`
that registers itself via `@NodeExecutorRegistry.register("approval")`.
`WorkflowEngine` never imports a node executor class directly - it only
ever asks `NodeExecutorRegistry.get(node["type"])`, so adding a future
node type (servicenow, jira, teams, slack, webhook, email, self_healing,
ai) means adding one new file, not editing the engine.

Every executor's `execute()` is called with the CURRENT `WorkflowExecution`
row and the node's own `config` dict from `definition_snapshot`. It must
never mutate the execution row directly - it returns a `NodeResult` and
`WorkflowEngine` (the only writer) applies it. This keeps every node type
trivially unit-testable in isolation, same rationale as `BaseCollector`
splitting `collect`/`normalize`.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, ClassVar

# Terminal-for-this-step statuses a node executor can return. "waiting"
# means the node is legitimately paused on an external actor (a human
# approval, a runbook execution, a delay timer) - WorkflowEngine does NOT
# keep advancing past a "waiting" result; it persists the execution's
# status and returns control to the caller (API request / scheduler tick).
NodeExecutionStatus = str  # "succeeded" | "waiting" | "failed"


@dataclass
class NodeResult:
    status: NodeExecutionStatus
    message: str
    output_variables: dict[str, Any] = field(default_factory=dict)
    # Decision nodes set this to override normal "follow every matching
    # edge" behavior with an explicit next-node-id list; None means "let
    # WorkflowEngine evaluate edges normally".
    next_node_ids: list[str] | None = None
    # Set by executors that delegate to another real execution record
    # (RunbookTaskNodeExecutor -> a real RunbookExecution.id) so
    # step_history can link back to it - never fabricated.
    linked_execution_id: str | None = None
    # Only meaningful when status="waiting": tells WorkflowEngine which
    # WorkflowExecution-level status to persist while parked at this node
    # (e.g. "waiting_approval" for an Approval node, "paused" for a Delay
    # node awaiting a timer). WorkflowEngine defaults to "paused" if unset.
    execution_status: str | None = None


class BaseNodeExecutor(abc.ABC):
    node_type: ClassVar[str]

    @abc.abstractmethod
    async def execute(self, *, execution, node: dict, config: dict, db) -> NodeResult:
        """`execution` is the live WorkflowExecution ORM row (read-only
        contract - see this module's docstring), `node` is the full node
        dict from definition_snapshot (id/type/config), `config` is
        node["config"] for convenience, `db` is the AsyncSession for
        executors that need to resolve real repository data (e.g.
        RunbookTaskNodeExecutor calling RunbookRepository). Must never
        raise for expected failures - return NodeResult(status="failed",
        ...) instead, same "expected failures are values, not
        exceptions" convention as BaseCollector.authenticate()."""
        raise NotImplementedError


class NodeExecutorRegistry:
    _executors: dict[str, type[BaseNodeExecutor]] = {}

    @classmethod
    def register(cls, node_type: str):
        def _decorator(executor_cls: type[BaseNodeExecutor]):
            executor_cls.node_type = node_type
            cls._executors[node_type] = executor_cls
            return executor_cls
        return _decorator

    @classmethod
    def get(cls, node_type: str) -> type[BaseNodeExecutor]:
        try:
            return cls._executors[node_type]
        except KeyError as exc:
            raise ValueError(
                f"No node executor registered for node_type={node_type!r}. "
                f"Supported: {sorted(cls._executors)}"
            ) from exc

    @classmethod
    def list_types(cls) -> list[str]:
        return sorted(cls._executors)
