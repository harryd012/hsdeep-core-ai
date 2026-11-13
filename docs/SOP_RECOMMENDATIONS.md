# SOP recommendations on incidents

Status: **implemented and tested** (`backend/tests/test_sop_incident_recommendations.py`).

An incident's investigation page shows the approved SOPs that best match it, and an operator can
accept or dismiss each one. Accepting records the procedure being followed; it runs nothing.

## How SOPs are matched

Scoring is the existing `SOPRecommendationEngine` (`app/services/sop/recommendation_engine.py`),
the same one root-cause analysis uses; this feature adds no second scoring path.

- **Candidates:** active SOPs in the incident's tenant whose current version is approved. Drafts
  and deprecated versions are never recommended.
- **Hard filter:** the SOP's category must equal the category of the incident's reference alert
  (its root-cause candidate, else its first active alert). A narrow SOP criterion that contradicts
  the incident (another vendor, site, ...) excludes the SOP.
- **Score (0-100):** category 40, vendor / platform / device type / site / business service 10
  each, region 5, minimum priority 5. A criterion the SOP leaves open earns half; one the
  incident cannot confirm earns a quarter. Past runbook success rate shifts the score by up to
  ±15.
- **Business impact** comes from `BusinessImpactEngine`, unchanged.

RCA and this API build the engine through one factory, `build_sop_recommendation_engine(session)`.

## API

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/incidents/{id}/sop-recommendations` | `incidents.read` + `sops.read` | `limit` 1-10 (default 3), `min_confidence` 0-100 (default 30), `include_dismissed` (default false) |
| POST | `/api/incidents/{id}/sop-recommendations/{sop_id}/decision` | `incidents.write` + `sops.read`, active subscription | Body `{"decision": "accepted" \| "dismissed", "reason": "..."}` |

- Each recommendation carries confidence, matched criteria, reasoning, business impact,
  estimated resolution time, required skills, whether approval is needed, the rollback
  procedure, and the recorded `decision` (or `null`).
- Dismissed SOPs are dropped **before** the limit applies, so the next best one takes the slot.
- A decision is accepted only for an SOP that is currently recommended for the incident (any
  confidence); anything else is a 404, as is another tenant's incident.
- Repeating the same answer is a no-op. A changed answer replaces the stored one. Every new or
  changed answer is an audit event (`sop_recommendation.accepted` / `.dismissed`) with old and
  new values.
- The deciding user comes from the token, never the body. API keys record `decided_by = null`.

## Data

`sop_recommendation_decisions` (migration `e7a3c9d1b4f6`): one row per (incident, SOP) with the
decision, optional reason, the SOP version and the confidence score at the time of the
decision, who decided and when. Rows are deleted with their incident, SOP or tenant.

## Frontend

`components/investigation/SopRecommendations.tsx`, rendered in the incident investigation
workspace after Root Cause Analysis. Up to three cards; Accept / Dismiss (with an optional
reason) appear for users with `incidents.write`. The panel loads separately, so a scoring
failure never blanks the rest of the workspace.

## Not done yet

- **SOP → runbook:** nothing links an SOP to a runbook, so accepting cannot start one.
- **Learning from decisions:** stored decisions are not yet used to tune confidence scores.
