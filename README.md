# JobAgent 2.0.0-alpha.3

Shared job catalog with **per-candidate matches**. Ranking, commute, review status, generated documents, and application bookkeeping live on the candidate-job match — never on the global job listing.

Auto-apply is **disabled**. The schema can record an application state; nothing submits a form or drives a browser.

The recovered June 25 source tree in `www/` is forensic evidence. Do not edit it in place. Runtime/private artifacts such as its historical SQLite database are intentionally not published.

## Requirements

- Python 3.9+
- SQLite (stdlib)

## Local startup

```bash
cd /path/to/JobAgent
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"

cp .env.example .env        # optional; never commit .env
cp config/settings.example.yaml config/settings.yaml   # optional local overrides
# API keys: Settings page, or config/secrets.yaml (gitignored — do not replace it on deploy)

python -m jobagent.cli init-db
python -m jobagent.cli doctor
python -m jobagent.cli serve --host 127.0.0.1 --port 8765
```

- UI: http://127.0.0.1:8765/
- REST: http://127.0.0.1:8765/api/docs

**Do not bind this UI to a public interface.** Alpha.3 is a local tool. The default bind address is `127.0.0.1`. Bearer-token API protection is not enough on its own because the HTML UI also shows candidate data and has state-changing controls.

If `JOB_AGENT_API_TOKEN` is unset, both UI and API stay open for local development. If it is set, the UI requires a login cookie and the REST API requires `Authorization: Bearer <token>`. There is no user-account system.

People, titles, resume text, and search preferences are edited in the UI and stored in SQLite. `config/settings.yaml` holds global source/scheduler/routing flags only.

API keys are saved from **Settings** into gitignored `config/secrets.yaml`. Deploys must not delete or replace that file (exclude it from `rsync --delete`). A non-blank environment variable or `.env` value overrides the file. If the secrets file has no value, JobAgent also reads `config/settings.yaml` `api:` and legacy `config/profile.yaml` `api:` (and `www/config/profile.yaml` if that file is still on disk). The first serve or Settings visit copies those into `config/secrets.yaml` without replacing a key already stored there. `jobagent doctor` prints which key *names* are set, never the values. Workday does not use an API key.

## Employer discovery

`discover-employers` turns company names into public job-board URLs. It does not scrape LinkedIn, does not read profiles, and does not use a logged-in session. It also does not submit applications.

```bash
python -m jobagent.cli discover-employers
python -m jobagent.cli discover-employers --names-file config/employer_names.txt --delay 1
python -m jobagent.cli discover-employers --near-home
```

Names come from the gitignored company list (`config/employer_names.txt`, edited on **Settings**), employers already stored on collected jobs, and, only with `--near-home`, one OpenStreetMap query around `search.location`. Greenhouse, Lever, Ashby, and SmartRecruiters do not publish a terms-cleared customer index, so discovery does not download a board dump. It generates 28 candidate patterns (compact, hyphen, underscore, and first-token slugs on those four APIs, plus Workday `wd1` / `wd5` / `wd3` with site names `External`, `Careers`, the compact name, and the Pascal name) and keeps a URL only when the official JSON API returns a job-board body. Requests are paced, `robots.txt` is honored, and misses are cached for 14 days.

Confirmed boards are written to gitignored `config/discovered_boards.yaml`. Greenhouse, Lever, Ashby, SmartRecruiters, and Workday crawl the enabled rows in addition to the manual company list. Settings can turn a board off; a later discovery run does not turn it back on. See `config/employer_names.example.txt` and `config/discovered_boards.example.yaml`.

## HigherEdJobs email alerts

HigherEdJobs has no public search API. The official category RSS remains a separate source and is marked blocked when the site returns an HTML bot-check. Saved-search **email alerts** are optional:

1. On HigherEdJobs, save the search and turn on the email alert.
2. Create a mailbox that receives only those alerts, or forward the alerts into one.
3. On **Settings → HigherEdJobs email alerts**, enter the IMAP host, folder, and sender filter. Username and password are stored in `config/secrets.yaml`, masked in the UI, and never written to logs. Leave a box blank to keep the saved value.

The source badge is **not configured** until host, username, and password are set, **ok** when new listings are stored, and **no new alerts** when the mailbox has nothing new. The same job URL is not inserted twice. `jobagent doctor` reports only whether the mailbox is configured.

The `data/` directory starts empty aside from `.gitkeep`. A fresh database is created by `init-db`. Do not commit `data/jobagent.db`.

## Optional import of the recovered legacy database

A fresh clone is a clean JobAgent 2.0 installation and does **not** contain the recovered legacy SQLite database. The historical database contains private/runtime data and is intentionally excluded from GitHub.

If you separately possess the preserved June 25 `jobs.db`, import it by passing its path explicitly. The source is opened read-only and is never modified:

```bash
python -m jobagent.cli init-db
python -m jobagent.cli migrate-legacy --source /path/to/preserved/jobs.db
python -m jobagent.cli reconstruct-candidates
python -m jobagent.cli doctor
```

Canonical post-import counts for the separately preserved frozen file:

- 299 shared jobs
- 2 legacy candidates
- 7 historical run records
- **0** historical candidate/job matches
- 119 ambiguous legacy job-state associations (reported, not guessed)

The importer:

- copies the source read-only and leaves the original file untouched
- upserts shared job listings (catalog fields only)
- upserts both candidate records
- imports match rows **only if they already exist** in the source
- does **not** attach historical jobs to either candidate
- reports jobs whose global score/status/commute/documents cannot be attributed to a person
- does **not** reproduce the old `matches_backfill_v1` bug
- is safe to rerun (idempotent counts)
- writes a JSON report under `data/migration_reports/` (gitignored)

After import, reconstruct people from frozen profiles (`reconstruct-candidates`) so Jeffrey Bowers and Tami Wood exist as separate rows. Stubs Test User and Jeff stay in the DB with search off. See `docs/CANDIDATE_RECONSTRUCTION.md`.

Then open **People → Matches → Attach missing jobs** only if you want catalog jobs on a specific person. That is an explicit operator action, not a guessed historical association.

## New jobs vs import

Crawling unions **search-enabled** candidates’ preferences, inserts listings into the **shared** catalog, then creates a **blank match row for each search-enabled candidate**. Ranking is independent per person. Re-inserting the same job URL does not duplicate matches. Legacy import does not use this linking path.

## Discovery, ranking, evaluation

```bash
python -m jobagent.cli crawl --dry-run
python -m jobagent.cli rank
python -m jobagent.cli evaluate
python -m jobagent.cli evaluate --json
```

Crawl never submits applications. Rank uses Mistral/Anthropic when keys are present, otherwise a deterministic heuristic (or `llm.provider: mock`). Evaluate does not modify the database.

Alpha.3 adds a human-reviewed **application package** step: from an approved match, choose **Build package** to generate candidate/job-specific resume and cover-letter drafts, edit them in the UI, and save the reviewed text. It still never submits an application. Alpha.2 discovery/ranking recipe: `docs/JOBAGENT_2_ALPHA2.md`. Source status: `docs/SOURCE_STATUS.md`.

## Dry-run cycle

```bash
python -m jobagent.cli run-cycle
python -m jobagent.cli run-cycle --candidate-id 1
```

Each cycle crawls into the shared catalog, then ranks and evaluates commute **per candidate**. Auto-apply never runs.

## Doctor

```bash
python -m jobagent.cli doctor
python -m jobagent.cli doctor --json
```

Non-destructive: no crawl, no LLM, no candidate mutations, no submissions. Reports version, database path and integrity, migration versions, row counts, auto-apply (must be off), whether UI/API auth is configured, whether settings parse, enabled sources, and whether API keys are present **without printing their values**.

## Tests

```bash
pytest
```

## Layout

See `docs/JOBAGENT_2_ALPHA2.md` for discovery/ranking/evaluation. Alpha.1 freeze notes: `docs/JOBAGENT_2_ALPHA1.md`. Architecture boundary: `docs/ARCHITECTURE_BOUNDARY.md`.