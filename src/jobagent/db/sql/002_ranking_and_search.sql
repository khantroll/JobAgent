-- JobAgent 2.0.0-alpha.2
-- Adds per-candidate search participation and ranking metadata.
-- Do not edit 001_initial.sql. Ranking fields stay on matches, never on jobs.

PRAGMA foreign_keys = ON;

ALTER TABLE candidates ADD COLUMN search_enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE candidates ADD COLUMN reconstruction_source TEXT DEFAULT '';
ALTER TABLE candidates ADD COLUMN location_accept_remote INTEGER NOT NULL DEFAULT 1;

ALTER TABLE candidate_job_matches ADD COLUMN ranked_at TEXT;
ALTER TABLE candidate_job_matches ADD COLUMN rank_provider TEXT;
ALTER TABLE candidate_job_matches ADD COLUMN rank_model TEXT;

CREATE INDEX IF NOT EXISTS idx_cjm_ranked_at
    ON candidate_job_matches (candidate_id, ranked_at);
CREATE INDEX IF NOT EXISTS idx_candidates_search
    ON candidates (search_enabled);
