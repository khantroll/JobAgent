# JobAgent 2.0.0-alpha.2 — Implemented state

Alpha.2 sits on the frozen alpha.1 architecture: **jobs are shared; candidate-specific state lives only on matches.** Auto-apply, deployment, notifications, and scheduling are still out of scope. Do not begin alpha.3.

This release makes discovery and ranking usable for real people reconstructed from the June 25 evidence, with enough visibility to judge recommendation quality.

## What shipped

| Area | Implementation |
|---|---|
| Schema | `002_ranking_and_search.sql` — `search_enabled`, reconstruction provenance, `ranked_at` / `rank_provider` / `rank_model` on **matches only** |
| Candidates | `jobagent reconstruct-candidates` — explicit mapping, conflicts recorded, no silent merge |
| Discovery | `jobagent crawl --dry-run` — union of **search-enabled** prefs; per-source isolation; blank matches for searching people only |
| Ranking | `jobagent rank` — per-candidate; Mistral/Anthropic when keyed; `mock` provider; heuristic fallback without credentials |
| UI | Match review filters: status, source, work type, min score, explanation search; default sort by score |
| Evaluate | `jobagent evaluate` / `--json` — read-only quality summary |
| Sources | Mapping extracted to `sources/normalize.py`; credential skips; unit tests |

`001_initial.sql` remains immutable.

## Ready for human evaluation

1. **Fresh database**
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   python -m pip install --upgrade pip setuptools wheel
   pip install -e ".[dev]"
   cp .env.example .env          # add keys you have; never commit .env
   python -m jobagent.cli init-db
   ```
2. **Import legacy catalog** (copy-only; never writes `www/data/jobs.db`)
   ```bash
   python -m jobagent.cli migrate-legacy
   ```
   Expect 299 jobs, 2 stub candidates, 0 matches, 7 run records.
3. **Inspect reconstructed candidates**
   ```bash
   python -m jobagent.cli reconstruct-candidates
   python -m jobagent.cli serve --host 127.0.0.1 --port 8765
   ```
   Open People. Stubs **Test User** and **Jeff** stay in the DB with search **off**. **Jeffrey Bowers** and **Tami Wood** are created from frozen profiles (not merged onto the Jeff stub). Details: `docs/CANDIDATE_RECONSTRUCTION.md`.
4. **Safe crawl**
   ```bash
   python -m jobagent.cli crawl --dry-run
   ```
   Queries enabled sources using the union of search-enabled titles/HEJ/employers. Inserts into the shared catalog. Creates a blank match for each **search-enabled** candidate. Never applies.
5. **Ranking**
   ```bash
   python -m jobagent.cli rank
   ```
   Scores each candidate’s unscored matches independently. Without an LLM key, uses a deterministic heuristic and records `rank_provider=fallback`. With `llm.provider: mock` in settings, no network. Live Mistral/Anthropic keys stay in `.env`.
6. **UI** — http://127.0.0.1:8765/ — pick a person → Matches. Filter by score, source, work type, status. Read explanations. Do not expose this UI publicly.
7. **Evaluation report**
   ```bash
   python -m jobagent.cli evaluate
   python -m jobagent.cli evaluate --json
   ```
   Does not modify the database.

## Discovery vs import

- **Import:** `link_candidates=False` — historical jobs stay unattached.
- **Crawl:** `link_candidates=True` but only to `search_enabled=1` people.
- Re-crawling the same URL does not duplicate jobs or matches.

## Ranking

Legacy prompt preserved. Provider isolated in `jobagent.llm` (`anthropic` / `mistral` / `mock`). Results written to `candidate_job_matches.score`, `score_reason`, `ranked_at`, `rank_provider`, `rank_model`. Ranking A never writes B’s row.

Joined match rows expose the catalog hash as `job_id`; `id` is the match-row primary key. Ranking and commute persist against `job_id` so `jobagent rank` works on unscored match lists.

## Out of scope (not alpha.3)

Auto-apply, Playwright, notifications, schedulers, public deployment, user accounts.
