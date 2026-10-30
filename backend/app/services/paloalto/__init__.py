"""Palo Alto Firewall Diagnostic, Analysis & SOP Intelligence module.

Public surface:
  - DiagnosticWorkflowEngine  provider-aware 16-stage diagnostic workflow
  - LogNormalizer             event -> internal evidence model + correlation
  - RCAEngine                 evidence-backed hypothesis generation
  - RunbookRegistry           versioned SOP/runbook intelligence
  - CLIReference              approved read-only PAN-OS command catalogue
  - SafetyPolicy              authorization + approval controls

All engines are advisory/read-only unless an explicit, audited approval path
is exercised. AI-generated conclusions are never represented as verified facts.
"""
from app.services.paloalto.workflow import (
    DIAGNOSTIC_STEPS,
    DiagnosticWorkflowEngine,
)
from app.services.paloalto.log_normalizer import LogNormalizer
from app.services.paloalto.rca_engine import RCAEngine
from app.services.paloalto.runbooks import RunbookRegistry
from app.services.paloalto.cli_reference import CLIReference
from app.services.paloalto.safety import SafetyPolicy, SafetyDecision

__all__ = [
    "DIAGNOSTIC_STEPS",
    "DiagnosticWorkflowEngine",
    "LogNormalizer",
    "RCAEngine",
    "RunbookRegistry",
    "CLIReference",
    "SafetyPolicy",
    "SafetyDecision",
]
