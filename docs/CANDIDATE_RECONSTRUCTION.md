# Candidate reconstruction (alpha.2)

Frozen evidence lives under `www/`. This command **never writes** that tree and **never copies secrets** (`api:`, notification passwords, key-like fields).

Run after `migrate-legacy`:

```bash
python -m jobagent.cli reconstruct-candidates
```

A JSON report is written under `data/reconstruction_reports/` (gitignored).

## Identities

| Person | Origin | 2.0 action | Search |
|---|---|---|---|
| Test User (id=1, t@t.com, title IT Manager, keyword `python`) | `www/data/jobs.db` | Preserve stub | **off** |
| Jeff (id=2, a@b.com, title `IT`) | `www/data/jobs.db` | Preserve stub; **not** Jeffrey Bowers | **off** |
| Jeffrey Bowers | `www/config/profile.yaml` (current) | Create/update **new** row by email | **on** |
| Tami Wood | `www/config/backups/profile-20260610-113809.yaml` | Create/update **new** row by email | **on** |

The recovered database never contained Jeffrey Bowers or Tami Wood. `migrate-legacy` therefore cannot reconstruct them.

## Jeffrey Bowers — chosen fields

| Field | Value | Source |
|---|---|---|
| name, email, phone, location, linkedin | from current profile | `www/config/profile.yaml` |
| titles | 10-title IT/infrastructure set | current `search.titles` |
| keywords | AD, VMware, Hyper-V, Windows Server, PowerShell, … | current `search.keywords` |
| salary | 80000–150000 | current search |
| min_match_score | 65 | current search |
| HEJ | 144, 161, 162, 173 | current `sources.higheredjobs.category_ids` |
| commute | 30 / 90 minutes | current search (matches schema defaults) |
| resume_text | profile YAML text (ranking input) | current `resume_text` |
| employers | Greenhouse/Lever slugs + Workday boards | current `sources.*` (not a shared profile file in 2.0) |
| location_accept_remote | true | current search |

### Conflicts not silently applied

| Field | Chosen | Rejected |
|---|---|---|
| titles | current 10-title set | 16-title engineer-heavy backups (e.g. `profile-20260605-102749.yaml`) |
| salary | 80k–150k | 0–0 in several June 4 backups (looks like a UI wipe) |
| HEJ | 144/161/162/173 | 171/68/278/144/46/173/162/172 in the engineer-heavy backup |
| resume | YAML `resume_text` | `www/uploads/*.docx` not imported (binary; attach in UI if desired) |
| identity | new row | **Do not** overwrite DB `Jeff` (id=2) |

Phone `479-322-0425` also appears on Tami backups — not used as a unique key.

## Tami Wood — chosen fields

| Field | Value | Source |
|---|---|---|
| name, email, location | Tami Wood / tami.wood@fortsmithar.gov / Fort Smith, AR | latest Tami backup |
| titles | Telecommunications Manager, Project Manager, Services Manager, Mobile Device Manager, Telecommunications Service Manager | same |
| keywords | **empty** | same (not early backups) |
| salary | 80000–150000 | same |
| HEJ | 66, 46, 278, 279 | same |
| commute | 30 / 90 | same |
| employers | none | Tami backups have no ATS company list |
| resume_text | backup YAML text | same |

### Conflicts not silently applied

| Field | Chosen | Rejected |
|---|---|---|
| keywords | empty | Early Tami backups reused Jeffrey’s IT keywords (`profile-20260604-123634.yaml`) |
| employers | none | Jeffrey’s Greenhouse/Lever/Workday boards |
| phone | stored as-is | treating phone as a person discriminator |

## Unresolved / defaults

- Per-candidate source enable flags are **not** stored; global `config/settings.yaml` still enables Adzuna, HEJ, etc.
- `exclude_keywords` (blockchain/crypto/junior) remain **global** in settings, as in the legacy YAML `search` block.
- RSS feeds remain global in settings.
- Test User backup of 2026-06-10 contaminated with Jeffrey-length resume text — **not** copied onto id=1.
- Commute columns did not exist on legacy candidates; 30/90 defaults match every inspected profile that set them.
- `search_enabled` is an alpha.2 policy flag (not the retired global `candidates.active` identity).

## Storage rule

All of the above lives on `candidates` / `candidate_titles` / `candidate_employers`. JobAgent 2.0 does not use a mutable `profile.yaml` as the selected person.
