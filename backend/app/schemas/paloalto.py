"""Pydantic schemas for the Palo Alto Diagnostic module.

Covers: diagnostic workflow executions, evidence references, RCA hypotheses,
SOP/runbook definitions, and the firewall diagnostic workspace summary.

Follows the project convention of explicit, non-defaulted response fields so
an omitted field fails loudly instead of serializing as a fabricated zero.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DiagnosticStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class DiagnosticRisk(str, Enum):
    READ_ONLY = "read_only"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    REQUIRES_APPROVAL = "requires_approval"


class EvidenceKind(str, Enum):
    TRAFFIC_LOG = "traffic_log"
    THREAT_LOG = "threat_log"
    URL_LOG = "url_log"
    DECRYPT_LOG = "decrypt_log"
    SYSTEM_LOG = "system_log"
    GP_LOG = "globalprotect_log"
    SESSION = "session"
    INTERFACE = "interface"
    ROUTE = "route"
    CONFIG = "config"
    MANUAL = "manual"
    INFERRED = "inferred"


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
class EvidenceRef(BaseModel):
    """A single piece of collected evidence. Never fabricated: every ref must
    point at a real collection timestamp and source, or be explicitly marked
    `inferred`/`manual`."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    kind: EvidenceKind
    source_device_id: uuid.UUID | None = None
    collected_at: datetime | None = None
    provider_timestamp: datetime | None = None
    summary: str
    raw_ref: str | None = Field(
        default=None, description="Pointer to stored raw provider payload, if any."
    )
    attributes: dict[str, Any] = Field(default_factory=dict)
    verified: bool = Field(
        default=False,
        description="True only when corroborated by an actual provider payload.",
    )


# ---------------------------------------------------------------------------
# Workflow step + execution
# ---------------------------------------------------------------------------
class DiagnosticStep(BaseModel):
    """One step in the 16-stage Palo Alto diagnostic workflow."""

    id: str
    name: str
    description: str = ""
    preconditions: list[str] = Field(default_factory=list)
    required_evidence: list[EvidenceKind] = Field(default_factory=list)
    risk: DiagnosticRisk = DiagnosticRisk.READ_ONLY
    status: DiagnosticStepStatus = DiagnosticStepStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result_summary: str | None = None
    failure_reason: str | None = None
    next_recommended_action: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)


class DiagnosticExecutionCreate(BaseModel):
    incident_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    note: str | None = None


class DiagnosticExecution(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    incident_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    status: DiagnosticStepStatus
    risk: DiagnosticRisk
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    steps: list[DiagnosticStep] = Field(default_factory=list)
    current_step_id: str | None = None



# ---------------------------------------------------------------------------
# RCA
# ---------------------------------------------------------------------------
class RCAScoreBasis(str, Enum):
    DIRECT_EVIDENCE = "direct_evidence"
    CORROBORATED = "corroborated"
    SINGLE_SOURCE = "single_source"
    INCONCLUSIVE = "inconclusive"
    INSUFFICIENT = "insufficient"


class RCAHypothesis(BaseModel):
    """One root-cause hypothesis with honest confidence and evidence."""

    id: str
    title: str
    probable_cause: str
    supporting_evidence: list[EvidenceRef] = Field(default_factory=list)
    contradicting_evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence_score: float = Field(ge=0.0, le=100.0)
    score_basis: RCAScoreBasis
    missing_evidence: list[str] = Field(default_factory=list)
    next_diagnostic_action: str | None = None
    suggested_remediation: str | None = None
    remediation_risk: DiagnosticRisk = DiagnosticRisk.READ_ONLY


class RCAAssessment(BaseModel):
    incident_id: uuid.UUID
    generated_at: datetime
    hypotheses: list[RCAHypothesis] = Field(default_factory=list)
    ai_interpretation: str | None = Field(
        default=None,
        description="AI-generated narrative. Advisory only; never a verified fact.",
    )


# ---------------------------------------------------------------------------
# SOP / runbook
# ---------------------------------------------------------------------------
class RunbookStep(BaseModel):
    id: str
    instruction: str
    expected_outcome: str
    abnormal_outcome: str | None = None
    evidence_requirement: str | None = None
    risk: DiagnosticRisk = DiagnosticRisk.READ_ONLY


class Runbook(BaseModel):
    """Versioned SOP/runbook for a Palo Alto troubleshooting topic."""

    id: str
    title: str
    topic: str
    version: str
    applicable_symptoms: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    steps: list[RunbookStep] = Field(default_factory=list)
    safe_remediation: list[str] = Field(default_factory=list)
    approval_required: bool = False
    rollback_instructions: str | None = None
    post_fix_verification: list[str] = Field(default_factory=list)
    escalation_criteria: list[str] = Field(default_factory=list)


class RunbookMatch(BaseModel):
    runbook: Runbook
    confidence_score: float = Field(ge=0.0, le=100.0)
    matched_symptoms: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Diagnostic workspace summary
# ---------------------------------------------------------------------------
class FirewallDiagnosticWorkspace(BaseModel):
    """Aggregate view for the Firewall Diagnostic Workspace page."""

    device_id: uuid.UUID | None = None
    hostname: str | None = None
    management_address: str | None = None
    vendor: str | None = None
    model: str | None = None
    panos_version: str | None = None
    site_id: uuid.UUID | None = None
    virtual_system: str | None = None
    health: str | None = None
    last_successful_collection_at: datetime | None = None
    incident_id: uuid.UUID | None = None
    incident_severity: str | None = None
    incident_summary: str | None = None
    affected_assets: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    rca: RCAAssessment | None = None
    matched_runbooks: list[RunbookMatch] = Field(default_factory=list)
    executions: list[DiagnosticExecution] = Field(default_factory=list)
    data_quality: dict[str, str] = Field(
        default_factory=dict,
        description="Per-section staleness/completeness markers, e.g. {'logs':'stale'}",
    )


# ---------------------------------------------------------------------------
# CLI diagnostic reference
# ---------------------------------------------------------------------------
class CLIDiagClass(str, Enum):
    """Whether a command is documentation-only, manual, or backend-executable."""

    DOC_ONLY = "doc_only"
    MANUAL = "manual"
    BACKEND_SAFE = "backend_safe"


class CLIDiagnosticCommand(BaseModel):
    """A version-aware, approved read-only PAN-OS CLI command reference."""

    id: str
    category: str
    command_template: str
    description: str
    min_panos_version: str | None = None
    max_panos_version: str | None = None
    diag_class: CLIDiagClass
    notes: str | None = None
