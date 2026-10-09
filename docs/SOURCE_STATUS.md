# Source connector status (alpha.2)

Crawlers are the recovered adapters with import rewrites plus mapping extracted to `jobagent.sources.normalize` for tests. Behavior is not restyled. A source exception does not abort `jobagent crawl`. Missing credentials skip that source with a warning (return 0 / `skipped_reason`).

Keys are read from a non-blank environment variable or `.env`, then gitignored `config/secrets.yaml` (Settings page), then `settings.yaml` `api:`, then legacy `config/profile.yaml` `api:` and `www/config/profile.yaml` `api:` when those files are still on disk. A blank environment variable does not hide `.env`. Placeholder values starting with `YOUR_` count as missing. The secrets file is the copy deploys must not overwrite. Doctor and logs report key names and `status=missing_key`, never the secret.

| source | enabled (example settings) | credentials | validation | known limitations | changes from legacy |
|---|---|---|---|---|---|
| Adzuna | on | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | Unit: payload mapping. Live: needs keys | Title filter may drop loose API hits | Mapping extracted; skip without keys (unchanged) |
| JSearch | on | `RAPIDAPI_KEY` | Unit: mapping incl. remote prefix | RapidAPI quota / paid | Mapping extracted |
| HigherEdJobs | on | none (official category RSS) | Unit: company/location parse + HTML bot-check → blocked | No public search API. `search/rss.cfm` is an HTML instructions page, not a feed. An HTML response marks `status=blocked`. No workaround scraper | Feed-reader User-Agent. Block is explicit |
| Greenhouse | on | none; needs board slugs | Unit: job JSON mapping | Empty slug list → skip. Some boards 404 | Slugs unioned from search-enabled `candidate_employers` plus settings |
| Lever | on | none; needs slugs | Unit: posting JSON mapping | Empty slug list → skip | Same employer union as Greenhouse |
| Workday | on | none (no API key; needs board specs) | Unit: posting map + CSRF extraction | CSRF from `CALYPSO_CSRF_TOKEN` or `csrfToken` in HTML. HTTP 401/403/429/5xx is a WAF block. Playwright is optional and off in the example: `pip install -e ".[playwright]"`, `playwright install chromium`, `use_playwright_fallback: true` | Clear `status=blocked` when HTTP and Playwright both fail |
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

## Dry-run contract

`jobagent crawl --dry-run` (the only mode) writes SQLite + logs only. No applications, no outbound mail, no LLM calls.
