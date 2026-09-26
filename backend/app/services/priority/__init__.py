"""
AI Priority Engine.

Sits above the Correlation/Incident Engine the same way CorrelationEngine
sits above Alert: `PriorityEngine.recompute(incident)` is invoked after an
incident is created/updated (from `AlertService`, mirroring how
`CorrelationEngine.correlate()` is invoked there today) and persists a
deterministic priority (P1-P4), four risk scores, two confidence scores,
and a set of AI-generated recommendations onto the Incident row.

Every score is computed by plain, inspectable arithmetic over real stored
data (Alert/Incident/Device/Site/BusinessService/MaintenanceWindow rows) -
same "honest heuristic, not ML" philosophy as `CorrelationEngine`/
`EventClassifier`/`AIInsightService`. No external LLM call, per the sprint
brief - `DecisionEngine`'s recommendation rules are a explicit substitution
point for a future model (see that module's docstring) without changing
any caller.
"""
