# JobAgent Recovery — 2026-09-21

Recovered from the user-uploaded `JobAgent 3.zip` captured 2026-08-28. Historical recovery analysis identifies this as the stabilized JobAgent 2.x reconstruction built from the more-complete server archive of the legacy 2026-06-25 deployment.

## Safety cleanup

This recovery tree intentionally excludes runtime/private material before Git publication: virtual environments, caches, logs, SQLite databases, uploaded resumes, configuration backups, and `.env` files. Example configuration and source-controlled schema/migrations are retained.

## Provenance / state

The historical Phase 2 recovery report found the second server archive to be an exact superset of the first for comparable project files (48 additional, 0 missing, 0 conflicting hashes). The recovered database was healthy and contained 299 jobs, 2 candidates, 7 run-log rows and 0 candidate/job match rows. The report documents an incomplete multi-candidate conversion in the June 25 legacy code.

The August recovery tree contains a newer reconstructed application under `src/jobagent`, tests, configuration examples and recovery/architecture documentation, plus the legacy server application under `www` for reference.

## Next step

Treat this as a preservation baseline. When the known local/Cursor copy is available, import it on a separate branch and diff it against this recovery rather than overwriting the baseline.
