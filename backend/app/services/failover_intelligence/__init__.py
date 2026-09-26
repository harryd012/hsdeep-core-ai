"""
WAN Failover Intelligence (detect -> trace -> verify).

Observed-side intelligence around HA / SD-WAN failovers. HSDEEP never drives a
failover: the network's own SD-WAN/Performance-SLA mechanism decides, and this
package records what the decision did, why, what it affected, and whether the
recovery is real.

Module map:

* ``policies``      - operational SLA threshold evaluation (pure)
* ``paths``         - topology links -> the existing ``sdwan.evaluate_paths``
                      decision engine (no competing engine)
* ``loader``        - one bounded, tenant-scoped live snapshot per site edge
* ``detector``      - the round (pure FSM) + the persistence-facing service
* ``blast_radius``  - affected-resource traversal over the real topology graph
* ``replay``        - incident time machine, provenance-tagged
* ``simulation``    - read-only counterfactual over the live snapshot
* ``verify``        - telemetry-only recovery verification
* ``evidence``      - evidence items with finding + provenance per claim
* ``hook``          - the bounded, isolated post-collection entry point
"""
from app.services.failover_intelligence.blast_radius import (
    AffectedNode,
    BlastRadiusService,
    traverse,
)
from app.services.failover_intelligence.detector import (
    ACTION_BASELINE,
    ACTION_BEGIN_RECOVERY,
    ACTION_ESCALATE,
    ACTION_OPEN_BREACH,
    ACTION_OPEN_FAILOVER,
    ACTION_VERIFY,
    FailoverDetectorService,
    RoundResult,
    analyze_round,
    evaluate_breaches,
    evaluate_scope_paths,
)
from app.services.failover_intelligence.evidence import (
    FINDING_CONTRADICTS,
    FINDING_NEUTRAL,
    FINDING_SUPPORTS,
    collect as collect_evidence,
    summarise as summarise_evidence,
)
from app.services.failover_intelligence.loader import (
    ScopeSnapshot,
    dedup_key_for,
    scope_key_for,
)
from app.services.failover_intelligence.replay import (
    PROVENANCE_CAPTURED,
    PROVENANCE_CONTEXT,
    PROVENANCE_DERIVED,
    ReplayService,
    build_timeline,
)
from app.services.failover_intelligence.simulation import (
    CounterfactualSimulator,
    simulate,
)
from app.services.failover_intelligence.verify import (
    CORE_CHECKS,
    OVERALL_FAIL,
    OVERALL_PASS,
    OVERALL_UNKNOWN,
    RecoveryVerificationService,
    verify_recovery,
)

___all__ = [
    # policies / paths
    "analyze_round",
    "evaluate_breaches",
    "evaluate_scope_paths",
    # detector
    "FailoverDetectorService",
    "RoundResult",
    "ACTION_BASELINE",
    "ACTION_BEGIN_RECOVERY",
    "ACTION_ESCALATE",
    "ACTION_OPEN_BREACH",
    "ACTION_OPEN_FAILOVER",
    "ACTION_VERIFY",
    # loader
    "ScopeSnapshot",
    "dedup_key_for",
    "scope_key_for",
    # blast radius
    "AffectedNode",
    "BlastRadiusService",
    "traverse",
    # replay
    "ReplayService",
    "build_timeline",
    "PROVENANCE_CAPTURED",
    "PROVENANCE_DERIVED",
    "PROVENANCE_CONTEXT",
    # simulation
    "CounterfactualSimulator",
    "simulate",
    # verification
    "RecoveryVerificationService",
    "verify_recovery",
    "CORE_CHECKS",
    "OVERALL_PASS",
    "OVERALL_FAIL",
    "OVERALL_UNKNOWN",
    # evidence
    "collect_evidence",
    "summarise_evidence",
    "FINDING_SUPPORTS",
    "FINDING_NEUTRAL",
    "FINDING_CONTRADICTS",
]
