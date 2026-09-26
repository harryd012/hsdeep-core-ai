"""AI Diagnosis schema for API responses."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field



class AiDiagnosisBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    diagnosis_summary: Optional[str] = None
    root_cause_candidates: list = Field(default_factory=list)
    evidence: list = Field(default_factory=list)
    impact_assessment: Optional[str] = None
    recommended_actions: list = Field(default_factory=list)
    risk_assessment: Optional[str] = None
    approval_required: bool = True
    verification_plan: Optional[str] = None
    evidence_sufficiency: str = Field(default="insufficient")


class AiDiagnosisCreate(BaseModel):
    tenant_id: uuid.UUID
    noc_event_id: uuid.UUID
    requested_by: Optional[uuid.UUID] = None


class AiDiagnosisUpdate(BaseModel):
    status: Optional[str] = None
    analyst_approved: Optional[bool] = None
    analyst_feedback: Optional[str] = None
    analyst_revised_diagnosis: Optional[str] = None


class AiDiagnosisRead(AiDiagnosisBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    noc_event_id: uuid.UUID
    status: str
    overall_confidence: int
    context_snapshot: dict
    model_used: str
    prompt_tokens: int
    completion_tokens: int
    analysis_started_at: Optional[datetime] = None
    analysis_completed_at: Optional[datetime] = None
    analyst_approved: Optional[bool] = None
    analyst_feedback: Optional[str] = None
    analyst_revised_diagnosis: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AiDiagnosisSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_diagnoses: int
    by_status: dict[str, int]
    avg_confidence: float
    approval_required_count: int
