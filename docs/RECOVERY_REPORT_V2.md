# JobAgent Recovery Report — Phase 2

## Executive finding

The second server archive materially changes the recovery picture. For all comparable project files (excluding virtualenv/generated runtime output), it is an **exact superset** of the first archive: **48 additional files, 0 files missing, 0 conflicting hashes**.

This is therefore not a different branch. It is a more complete capture of the same **2026-06-25 development/deployment state**.

The defensible legacy identifier remains:

**JobAgent legacy-2026-06-25**

There is still no trustworthy historical semantic version number in the source, Git metadata, deployment manifest, or database.

## Major files recovered in Phase 2

Previously source-missing components are now present, including:

- `orchestrator.py`
- `agents/apply_pipeline.py`
- `agents/ranker.py`
- `agents/crawlers.py`
- `agents/doc_gen.py`
- `agents/sources/lever.py`
- `agents/sources/remotive.py`
- `agents/sources/themuse.py`
- `agents/sources/usajobs.py`
- `agents/sources/workday.py`
- `agents/sources/higheredjobs_catalog.py`
- `api/auth.py`
- `api/worker.py`
- `api/routes/jobs.py`
- `api/routes/matches.py`
- `api/routes/stats.py`
- `simple_ui/db.py`
- `simple_ui/profile_import.py`
- missing newer Jinja templates/static assets
- the actual `data/jobs.db`

This removes most of the uncertainty caused by orphaned `.pyc` files in Phase 1.

## Database evidence

The recovered SQLite database is healthy: `PRAGMA integrity_check` returns `ok`.

State captured in the database:

- 299 retained jobs
- 2 candidate records
- 2 candidate title records
- 0 candidate employer records
- 0 candidate/job match records
- 7 run-log rows
- run history spans 2026-05-29 through 2026-06-03
- job sources represented: The Muse, Adzuna, Greenhouse, Workday, Remotive, JSearch, USAJOBS, RSS/We Work Remotely, HigherEdJobs, Lever, plus test data
- job status at snapshot: 180 `new`, 118 `skipped`, 1 `applied`
- `app_meta.matches_backfill_v1 = 1`

## Why candidate_job_matches is empty

This is explainable directly from the June 25 migration code.

`_backfill_candidate_matches()` checks for one active candidate. If none exists, it writes `matches_backfill_v1=1` and returns without inserting any match rows. In the recovered database both candidate rows have `active=0`.

That means the migration permanently marked itself complete without migrating the 299 jobs.

This is a software migration edge case, not evidence that candidate match data was later deleted. The underlying jobs remain intact.

## Exact state of the multi-candidate conversion

The June 25 work successfully introduced:

- candidate records and candidate-specific search preferences;
- candidate-specific match rows;
- candidate-specific match score/reason;
- candidate-specific review/reject/ignore/apply status vocabulary;
- candidate match listing/filtering/export;
- duplicate annotation and duplicate ignore operations;
- history transfer and bulk link operations;
- a per-candidate ranking path in the orchestrator;
- profile construction using candidate identity/resume/search preferences.

But the conversion was incomplete. These values were still stored globally on `jobs` and used globally by the orchestrator:

- work type;
- commute minutes/note;
- auto-apply/review selection;
- resume path;
- cover-letter path;
- applied timestamp/global job status;
- notification state.

Most importantly, `run_candidate_cycle()` ranks with candidate-specific matches, then calls legacy `get_approved_jobs()`, `get_review_jobs()`, and `get_unapplied_notifications()`, which query the global `jobs` table rather than the candidate match.

Therefore the June 25 implementation cannot safely run multiple candidates independently. One candidate's commute/application state can affect another candidate's workflow.

## Configuration/profile state

The active YAML profile in the snapshot is for Jeffrey Bowers and has scheduler `dry_run: true`, a four-hour run interval, and nine configured source families: Adzuna, Greenhouse, HigherEdJobs, JSearch, Lever, Remotive, The Muse, USAJOBS, and Workday.

The People table contains older abbreviated/test candidate records and neither is active. The startup importer only activates a YAML profile if it creates/updates it through the synchronization path; if it decides the person already exists, it can leave the DB with no active candidate. This explains why the application could continue to work in legacy/global-profile fallback mode while the new candidate-match layer stayed unused.

## What this changes from Phase 1

Phase 1 said several core modules and the database had been lost. That statement is superseded by this report. They were absent only from the first archive.

We now have enough evidence to reconstruct the June 25 code state essentially exactly, without bytecode decompilation.

## Remaining uncertainties

- No original Git history survives, so intermediate commits and semantic version numbers cannot be recovered exactly.
- We cannot prove whether every June 25 file was actually loaded by the running service at the same instant without the service/unit process state from that date.
- Generated artifacts after June 19 and the code timestamps through June 25 show development continuing beyond the last recorded `run_log` cycle, so not every newest path is proven by a completed production run.

## Recovery decision

Do not continue patching the live legacy deployment as the primary development strategy.

Freeze it as `legacy-2026-06-25`, preserve its database as evidence/data to migrate, and use the recovered source to construct JobAgent 2.0 with a normalized candidate/job workflow.
