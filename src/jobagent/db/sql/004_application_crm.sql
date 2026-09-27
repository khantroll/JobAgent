-- JobAgent 2.0.0-alpha.3.1
-- Durable manual application CRM: event history, contacts, next actions and submitted package snapshots.

PRAGMA foreign_keys = ON;

ALTER TABLE candidate_job_matches ADD COLUMN next_action TEXT DEFAULT '';
ALTER TABLE candidate_job_matches ADD COLUMN submitted_resume_path TEXT DEFAULT NULL;
ALTER TABLE candidate_job_matches ADD COLUMN submitted_cover_path TEXT DEFAULT NULL;

CREATE TABLE application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    from_stage TEXT DEFAULT '',
    to_stage TEXT DEFAULT '',
    note TEXT DEFAULT '',
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (candidate_id, job_id) REFERENCES candidate_job_matches(candidate_id, job_id) ON DELETE CASCADE
);
CREATE INDEX idx_application_events_match ON application_events(candidate_id, job_id, occurred_at DESC);

CREATE TABLE application_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT DEFAULT '',
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    linkedin TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (candidate_id, job_id) REFERENCES candidate_job_matches(candidate_id, job_id) ON DELETE CASCADE
);
CREATE INDEX idx_application_contacts_match ON application_contacts(candidate_id, job_id);
