# JobAgent 2.0 Architecture Boundary

## Keep

- FastAPI application layer
- server-rendered Jinja UI
- SQLite for the initial rebuild
- crawler/source adapter concept
- shared canonical `jobs` catalog
- LLM ranking abstraction
- commute classifier logic
- document generation logic
- application-handler abstraction
- run logging
- candidate/person UI concepts
- dry-run-first behavior

## Retire or replace

- React/Vite UI as an active product path
- duplicate `SimpleUI/` legacy tree
- mutable `profile.yaml` as candidate identity/state
- global job score as authoritative candidate score
- global commute decision as candidate state
- global review/apply status as candidate state
- global generated-document paths as candidate state
- global notification flag as candidate state
- migration behavior that marks a migration complete without migrating data

## Core data rule for 2.0

A job listing is shared. Everything representing a person's relationship to that job belongs to the candidate/job match.

Recommended match-owned fields:

- candidate_id
- job_id
- score / score_reason
- work_type
- commute_minutes / commute_note
- decision/status
- status_reason
- resume_path
- cover_path
- applied_at
- notified_at / notification state
- matched_at / updated_at

A source listing may be deduplicated globally, but one candidate's decisions must never mutate another candidate's workflow.

## Configuration rule

`profile.yaml` may remain temporarily as a source for global scheduler/provider/API configuration during migration, but candidate identity/search preferences must be database-owned. The eventual configuration should split into:

1. global application/provider configuration;
2. candidate records/search preferences;
3. secrets supplied outside tracked source.
