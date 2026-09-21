# Autonomous Job Agent

Finds, ranks, tailors, and applies to jobs automatically — then emails you a digest of every application.

## Setup

```bash
cd job_agent
pip install -r requirements.txt
playwright install chromium
```

## Configure

Edit `config/profile.yaml`:
- Fill in your name, resume, target roles, salary, and notification email
- Set `llm.provider` to `mistral` or `anthropic` and add the matching API key
- Add your Gmail app password (myaccount.google.com/apppasswords)
- Enable job sources under `sources:` and add API keys under `api:`

### Job sources

| Source | API key | Sign up |
|--------|---------|---------|
| **Adzuna** | `adzuna_app_id`, `adzuna_app_key` | [developer.adzuna.com](https://developer.adzuna.com/signup) |
| **USAJobs** | `usajobs_api_key`, `usajobs_user_agent` (your email) | [developer.usajobs.gov](https://developer.usajobs.gov/APIRequest/Form) |
| **JSearch** | `rapidapi_key` | [RapidAPI JSearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch) |
| **The Muse** | optional `themuse_api_key` | [themuse.com/developers](https://www.themuse.com/developers/api/v2) |
| **Remotive** | none | Public API |
| **Greenhouse** | none | Add company slugs under `sources.greenhouse.companies` |
| **Lever** | none | Add company slugs under `sources.lever.companies` |

Commute drive times use OpenStreetMap (Nominatim) + OSRM — no Google Maps key needed.

## First run (dry run — no actual applications)

```bash
python orchestrator.py --once    # single cycle, then exit
python orchestrator.py           # loop every N hours
```

`dry_run: true` is on by default. Check the logs and your database to confirm everything looks right.

## Go live

Set `dry_run: false` in `config/profile.yaml`, then run again:

```bash
python orchestrator.py
```

The agent runs every N hours (set by `scheduler.run_every_hours`).

## What you get notified about

After each cycle, you'll receive one email digest listing every job applied to, with:
- Job title and company
- Match score (0–100) and reason
- Direct link to the listing

## Web UI (Simple UI)

HTML interface in **`simple_ui/`** — no npm build. Manage people, resumes, employers, jobs, and run cycles from the browser.

### Local development

```bash
pip install -r requirements.txt
uvicorn simple_ui.app:app --host 127.0.0.1 --port 8765 --reload
```

Or `scripts/run-api.ps1` (Windows) / `scripts/run-api.sh` (Linux).

- UI: http://127.0.0.1:8765/
- REST: http://127.0.0.1:8765/api/docs

### Yunohost

Same layout as [AgentTrader](https://nedragaardkeep.quest/AgentTrader/): files in **`/var/www/my_webapp__4/www/`**.

**Guide:** `deploy/INSTALL-YUNOHOST.md` — upload project, run `deploy/install-yunohost.sh`, configure nginx proxy for `/jobagent/`.

The legacy React app under `web/` is optional and not required for deployment.

## Files

```
job_agent/
  orchestrator.py          # Main entry point — run this
  simple_ui/               # HTML UI (templates + static)
  api/                     # REST routes + background worker
  web/                     # Legacy React UI (optional)
  deploy/                  # systemd + nginx samples for Yunohost
  config/profile.yaml      # Your profile, preferences, API keys
  agents/
    crawlers.py            # Crawler coordinator
    sources/               # Adzuna, USAJobs, JSearch, Muse, Remotive, Greenhouse, Lever
    ranker.py              # Claude scoring agent
    doc_gen.py             # Resume + cover letter generator
    applier.py             # Playwright auto-apply agent
    notifier.py            # Email notification agent
  data/
    db.py                  # SQLite database
    jobs.db                # Created on first run
  output/
    resumes/               # Tailored resumes per job
    cover_letters/         # Tailored cover letters per job
  job_agent.log            # Full run log
```

## Adding job boards

Add a module under `agents/sources/`, implement `crawl(config) -> int`, and register it in `agents/sources/__init__.py`.

## Running as a background service (Linux/Mac)

```bash
nohup python orchestrator.py > /dev/null 2>&1 &
```

Or use `systemd` / `launchd` / `cron` for more robust scheduling.
