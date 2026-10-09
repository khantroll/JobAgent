# Source connector status (alpha.2)

Crawlers are the recovered adapters with import rewrites plus mapping extracted to `jobagent.sources.normalize` for tests. Behavior is not restyled. A source exception does not abort `jobagent crawl`. Missing credentials skip that source with a warning (return 0 / `skipped_reason`).

Keys are read from a non-blank environment variable or `.env`, then gitignored `config/secrets.yaml` (Settings page), then `settings.yaml` `api:`, then legacy `config/profile.yaml` `api:` and `www/config/profile.yaml` `api:` when those files are still on disk. A blank environment variable does not hide `.env`. Placeholder values starting with `YOUR_` count as missing. The secrets file is the copy deploys must not overwrite. Doctor and logs report key names and `status=missing_key`, never the secret.

| source | enabled (example settings) | credentials | validation | known limitations | changes from legacy |
|---|---|---|---|---|---|
| Adzuna | on | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | Unit: payload mapping. Live: needs keys | Title filter may drop loose API hits | Mapping extracted; skip without keys (unchanged) |
| JSearch | on | `RAPIDAPI_KEY` | Unit: mapping incl. remote prefix | RapidAPI quota / paid | Mapping extracted |
| HigherEdJobs | on | none (official category RSS) | Unit: company/location parse + HTML bot-check → blocked | No public search API. `search/rss.cfm` is an HTML instructions page, not a feed. An HTML response marks `status=blocked`. No workaround scraper | Feed-reader User-Agent. Block is explicit |
| HigherEdJobs mail | on | IMAP host, username, and password in `config/secrets.yaml` (`imap:`) or `HEJ_IMAP_*` | Unit: alert HTML fixture, dedupe, masked settings | Optional. Badge is `not configured`, `ok`, or `no new alerts`. Does not log the mailbox secret | Reads saved-search alert mail only |
| Greenhouse | on | none; needs board slugs | Unit: job JSON mapping | Empty slug list → skip. Some boards 404 | Manual slugs plus enabled rows in `config/discovered_boards.yaml` |
| Lever | on | none; needs slugs | Unit: posting JSON mapping | Empty slug list → skip | Manual slugs plus enabled discovered boards |
| Ashby | on | none; needs board slugs | Unit: public job-board JSON shape | Empty slug list → blocked. No public customer index | Official `posting-api/job-board/{slug}` only |
| SmartRecruiters | on | none; needs company tokens | Unit: postings `content` list | Empty token list → blocked. No public customer index | Official company postings API only |
| Workday | on | none (no API key; needs board specs) | Unit: posting map + CSRF extraction | CSRF from `CALYPSO_CSRF_TOKEN` or `csrfToken` in HTML. HTTP 401/403/429/5xx is a WAF block. Playwright is optional and off in the example: `pip install -e ".[playwright]"`, `playwright install chromium`, `use_playwright_fallback: true` | Manual boards plus enabled discovered tenants. Clear `status=blocked` when HTTP and Playwright both fail |
| USAJOBS | on | `USAJOBS_API_KEY` + `USAJOBS_USER_AGENT` | Unit: SearchResult mapping | Registration email required as User-Agent | Mapping extracted; location filter unchanged |
| The Muse | on | optional `THEMUSE_API_KEY` | Unit: public job JSON, repeated level/category params, candidate location | Public endpoint may throttle without key. Location comes from the candidate when `sources.themuse.location` is blank. Every configured level is sent | Seniority filter no longer dropped when more than one level is set |
| Remotive | on | none | Unit: HTML strip + fields | Remote-only API | Mapping extracted |
| RSS / We Work Remotely | on via `search.rss_feeds` | none | Unit: title `at` company parse | Company heuristic is naive | Mapping extracted; still `source=rss` |

## Cannot fully validate without live credentials / network

- Adzuna, JSearch, USAJOBS — paid/registered APIs.
- Workday — many tenants block non-browser HTTP; Playwright extra (`pip install -e ".[playwright]"` + `playwright install chromium`) is the legacy fallback, still optional and **not** auto-apply.
- The Muse without a key is best-effort public API.

## Live smoke (2026-08-28, no paid API keys)

Against a fresh import + reconstructed Jeffrey/Tami, `jobagent crawl --dry-run` completed in ~82s:

- Attempted: Greenhouse, HigherEdJobs, Lever, Remotive, RSS, The Muse, Workday — all succeeded
- Skipped (missing keys): Adzuna, JSearch, USAJOBS
- 192 jobs newly inserted; 384 blank matches (two search-enabled people); stubs Test User / Jeff received none
- Auto-apply remained off

Workday returned a small number of postings over HTTP without Playwright; tenant WAF behavior will vary.

## Employer discovery

`python -m jobagent.cli discover-employers` checks public JSON only. There are **28** candidate patterns: four slug shapes (compact, hyphen, underscore, first token) on Greenhouse, Lever, Ashby, and SmartRecruiters, and Workday clusters `wd1`, `wd5`, and `wd3` with sites `External`, `Careers`, the compact tenant, and the Pascal site name. A candidate is kept only when that API returns a job-board body. `robots.txt` disallow rules skip the request. Positive and negative results, including robots decisions, cache for 14 days in `data/employer_probe_cache.json`. Confirmed boards go to gitignored `config/discovered_boards.yaml`. Names that do not match stay on the unmatched list shown in Settings.

Company names come from `config/employer_names.txt` (Settings), companies already on collected jobs, and optionally one OpenStreetMap Overpass query (`--near-home`) around the configured home location. Third-party slug dumps are not imported.

## HigherEdJobs mailbox setup

1. Create the saved search on HigherEdJobs and enable its email alert.
2. Create a dedicated mailbox, or forward the alert into one.
3. Save the IMAP host, port, folder, and sender filter on Settings. Put the username and password there too. They are stored in gitignored `config/secrets.yaml`, shown masked, and omitted from logs and `jobagent doctor`.
4. Run a cycle. New HigherEdJobs links are inserted like any other source and deduped by URL.

## Dry-run contract

`jobagent crawl --dry-run` (the only mode) writes SQLite + logs only. No applications, no outbound mail, no LLM calls.
