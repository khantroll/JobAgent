-- JobAgent 2.0.0-alpha.3
-- Manual application/outcome ledger. All fields remain candidate/job specific.

PRAGMA foreign_keys = ON;

ALTER TABLE candidate_job_matches ADD COLUMN application_channel TEXT DEFAULT '';
ALTER TABLE candidate_job_matches ADD COLUMN application_stage TEXT NOT NULL DEFAULT 'not_applied';
ALTER TABLE candidate_job_matches ADD COLUMN application_notes TEXT DEFAULT '';
ALTER TABLE candidate_job_matches ADD COLUMN follow_up_at TEXT DEFAULT NULL;
ALTER TABLE candidate_job_matches ADD COLUMN outcome_at TEXT DEFAULT NULL;

UPDATE candidate_job_matches
SET application_stage = 'applied'
WHERE status = 'applied' OR application_state = 'recorded';

CREATE INDEX IF NOT EXISTS idx_cjm_application_stage
    ON candidate_job_matches (candidate_id, application_stage);
CREATE INDEX IF NOT EXISTS idx_cjm_follow_up
    ON candidate_job_matches (candidate_id, follow_up_at);
