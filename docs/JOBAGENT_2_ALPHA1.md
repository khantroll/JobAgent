# JobAgent 2.0.0-alpha.1 — Implemented state

Frozen evidence remains in `www/` (`legacy-2026-06-25`). This document records what alpha.1 **ships**, not a proposal. Do not modify the legacy tree.

Current development line: **2.0.0-alpha.2** (`docs/JOBAGENT_2_ALPHA2.md`). This file remains the alpha.1 freeze record.

Alpha.1 is a local dry-run tool: shared jobs, per-candidate matches, Simple UI, SQLite migrations, legacy import, and crawlers/ranking/commute. Auto-apply, deployment, scheduling, notifications, and user accounts are out of scope.

## 1. Directory tree

```
JobAgent/
  README.md
  pyproject.toml                 # jobagent 2.0.0-alpha.1
  .env.example
  .gitignore
  docs/
    RECOVERY_REPORT_V2.md        # forensic (unchanged)
    ARCHITECTURE_BOUNDARY.md     # forensic (unchanged)
    JOBAGENT_2_ALPHA1.md         # this file
  www/                           # frozen legacy — do not edit
  config/
    settings.example.yaml
    higheredjobs_categories.json
  src/jobagent/
    cli.py                       # serve | init-db | migrate-legacy | run-cycle | doctor
    doctor.py                    # non-destructive checks
    pipeline.py                  # dry-run: crawl then per-candidate rank/commute
    migrate_legacy.py
    db/sql/001_initial.sql       # FROZEN after this release
    web/app.py                   # FastAPI + Jinja + HTMX; optional token
  tests/
  data/.gitkeep                  # runtime DB is generated, never bundled
```

Abandoned React/Vite (`www/web/`) and duplicate `www/SimpleUI/` stay in the legacy tree. Playwright auto-apply stays quarantined in `www/` and is not wired.

## 2. Database schema

Jobs are a shared catalog. Every candidate-specific field lives on `candidate_job_matches`. There is no `candidates.active` flag and no global profile rewrite.

**jobs** (shared listing): `id`, `title`, `company`, `location`, `url`, `description`, `source`, `salary_raw`, `posted_at`, `found_at`, `created_at`, `updated_at`

**candidates**: identity, resume, search prefs. No mutable global “active” identity.

**candidate_job_matches** (unique `candidate_id, job_id`): ranking (`score`, `score_reason`), workflow status, commute, `auto_apply_eligible` (stored, never executed), review, documents, notification columns, application bookkeeping.

FKs: `candidate_id → candidates.id`, `job_id → jobs.id`.

**run_history**, **schema_migrations**, **import_reports**.

## 3. Migration policy (frozen 001)

`src/jobagent/db/sql/001_initial.sql` is **immutable** after alpha.1 stabilization.

- Never edit an already-released migration to evolve the schema.
- Future schema changes are new files: `002_*.sql`, `003_*.sql`, …
- Migrations are applied in filename order. Versions are stored in `schema_migrations`.
- Re-running `init-db` / `apply_migrations` is idempotent: applied versions are skipped and existing rows are not altered.

Tests assert that a second `init-db` does not reapply `001_initial` and does not change catalog, candidate, match, or `schema_migrations.applied_at` data.

## 4. Canonical database setup

`data/` contains only `.gitkeep` in the deliverable. The bundled runtime DB is **not** shipped.

```bash
python -m jobagent.cli init-db          # creates data/jobagent.db via 001_initial
python -m jobagent.cli migrate-legacy   # optional; imports www/data/jobs.db
```

A previous smoke test attached 299 match rows to candidate 1 (“Test User”). That is **not** historical evidence. The recovered source has zero match rows; import must not invent them.

### Post-import semantics (frozen recovered `www/data/jobs.db`)

| Item | Count |
|---|---|
| Shared jobs | 299 |
| Legacy candidates | 2 |
| Historical run records | 7 |
| Candidate/job matches | **0** |
| Ambiguous legacy job-state associations | 119 |

Import never attaches historical jobs to either candidate. Ambiguous global score/status/commute/document fields are reported, not guessed. The old `matches_backfill_v1` “complete with zero matches” behavior is not reproduced. Rerunning import is idempotent and does not write to the source file.

## 5. New-job candidate linking (distinct from import)

Implemented crawl/upsert path for alpha.1:

1. Candidate search preferences are **unioned** for crawling (`crawl_config_for_candidates`).
2. Newly discovered jobs enter the shared global catalog.
3. `upsert_job(..., link_candidates=True)` (the default, used by crawlers) creates a **blank match row for every candidate**.
4. Each candidate is ranked and commute-evaluated independently afterward.

Re-upserting the same job URL does not insert a second catalog row and does not duplicate match rows (`INSERT OR IGNORE` + link-on-insert-only). Changing or ranking candidate A’s match never writes candidate B’s match.

Legacy import calls `upsert_job(..., link_candidates=False)`. Blank matches for imported jobs are created only by the explicit UI action “Attach missing jobs” (`link_all_jobs_to_candidate`).

## 6. UI exposure

- Default bind address: `127.0.0.1:8765`.
- The HTML UI contains candidate PII and state-changing controls. **Do not expose it on a public interface.** An API bearer token alone is not sufficient if the UI is reachable.
- `jobagent serve --host` values other than loopback log a warning.
- Optional auth: set `JOB_AGENT_API_TOKEN`.
  - Unset: UI and API stay open for local development.
  - Set: UI requires a login cookie (`jobagent_token`); REST requires `Authorization: Bearer`. `/health`, `/login`, and `/static` stay reachable so login can work.
- No user-account system in alpha.1.

## 7. `jobagent doctor`

Non-destructive environment report: application version, database path, SQLite integrity, applied migration versions, job/candidate/match/run-history counts, auto-apply disabled, whether UI/API auth is configured, whether settings parse, enabled source names, and whether relevant API keys are present **without displaying values**.

It does not crawl, call an LLM, modify candidate state, or submit anything.

## 8. Mapping of legacy modules

| Legacy | Decision |
|---|---|
| `agents/sources/*`, `agents/crawlers.py` | **Adapted** — shared jobs + link all candidates on insert |
| `agents/ranker.py`, `agents/commute.py`, `agents/llm.py` | **Adapted** — persist onto the match row only |
| `agents/doc_gen.py` | **Adapted** — helper only; not used for apply |
| `simple_ui/` | **Adapted** — candidate pages, match browsing; no profile.yaml activate |
| `orchestrator.py` | **Replaced** — dry-run pipeline; no apply/notify |
| Job score/commute/status/docs on `jobs` | **Retired** from the catalog |
| `_backfill_candidate_matches` | **Retired** |
| `profile.yaml` as candidate identity | **Retired** |
| `agents/applier.py`, Playwright apply | **Quarantined** in `www/` |
| `agents/notifier.py` | **Deferred** |
| Yunohost deploy | **Deferred** (not alpha.1) |

## 9. Tests

- Schema FKs, forbidden job columns, unique matches
- Repeated `init-db` does not reapply 001 or alter data
- Multi-candidate isolation
- New-job linking, independent ranking, no duplicate matches on re-upsert
- Failed legacy backfill is not reproduced
- Recovered DB import: 299 / 2 / 7 / 0 matches / 119 ambiguous
- UI/API unprotected vs token-protected modes
- Doctor does not mutate data or leak key values
- Auto-apply remains hard-disabled
