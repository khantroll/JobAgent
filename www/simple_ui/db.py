import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "jobs.db"


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def _int_value(value, default=0):
    try:
        text = str(value if value is not None else "").strip()
        return int(text) if text else int(default)
    except (TypeError, ValueError):
        return int(default)


def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init():
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            location TEXT DEFAULT '',
            linkedin TEXT DEFAULT '',
            github TEXT DEFAULT '',
            resume_text TEXT DEFAULT '',
            resume_file TEXT DEFAULT '',
            active INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS candidate_titles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS candidate_employers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            source_type TEXT DEFAULT 'workday',
            careers_url TEXT DEFAULT '',
            workday_url TEXT DEFAULT '',
            greenhouse_slug TEXT DEFAULT '',
            lever_slug TEXT DEFAULT '',
            FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
        );
        ''')
        existing = {row[1] for row in c.execute("PRAGMA table_info(candidates)")}
        for col, defn in [
            ("min_match_score", "INTEGER DEFAULT 65"),
            ("salary_min", "INTEGER DEFAULT 0"),
            ("salary_max", "INTEGER DEFAULT 0"),
            ("keywords_text", "TEXT DEFAULT ''"),
            ("hej_category_ids", "TEXT DEFAULT ''"),
        ]:
            if col not in existing:
                c.execute(f"ALTER TABLE candidates ADD COLUMN {col} {defn}")
    try:
        from data import db as jobs_db

        jobs_db.ensure_job_match_schema()
    except Exception:
        pass


def rows(sql, params=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def row(sql, params=()):
    with conn() as c:
        r = c.execute(sql, params).fetchone()
        return dict(r) if r else None


def execute(sql, params=()):
    with conn() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid


def save_candidate(data, titles, employers, candidate_id=None):
    init()
    now = utcnow()
    with conn() as c:
        search = (
            _int_value(data.get("min_match_score"), 65),
            _int_value(data.get("salary_min"), 0),
            _int_value(data.get("salary_max"), 0),
            data.get("keywords_text") or "",
            data.get("hej_category_ids") or "",
        )
        if candidate_id:
            c.execute(
                """UPDATE candidates SET name=?, email=?, phone=?, location=?, linkedin=?,
                   github=?, resume_text=?, resume_file=?, min_match_score=?, salary_min=?,
                   salary_max=?, keywords_text=?, hej_category_ids=?, updated_at=? WHERE id=?""",
                (
                    data["name"],
                    data.get("email", ""),
                    data.get("phone", ""),
                    data.get("location", ""),
                    data.get("linkedin", ""),
                    data.get("github", ""),
                    data.get("resume_text", ""),
                    data.get("resume_file", ""),
                    *search,
                    now,
                    candidate_id,
                ),
            )
            cid = candidate_id
            c.execute('DELETE FROM candidate_titles WHERE candidate_id=?', (cid,))
            c.execute('DELETE FROM candidate_employers WHERE candidate_id=?', (cid,))
        else:
            cur = c.execute(
                """INSERT INTO candidates
                   (name,email,phone,location,linkedin,github,resume_text,resume_file,
                    min_match_score,salary_min,salary_max,keywords_text,hej_category_ids,
                    active,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
                (
                    data["name"],
                    data.get("email", ""),
                    data.get("phone", ""),
                    data.get("location", ""),
                    data.get("linkedin", ""),
                    data.get("github", ""),
                    data.get("resume_text", ""),
                    data.get("resume_file", ""),
                    *search,
                    now,
                    now,
                ),
            )
            cid = cur.lastrowid
        for title in [t.strip() for t in titles if t.strip()]:
            c.execute('INSERT INTO candidate_titles (candidate_id,title) VALUES (?,?)', (cid, title))
        for emp in employers:
            if not emp.get('name','').strip():
                continue
            c.execute('''INSERT INTO candidate_employers (candidate_id,name,source_type,careers_url,workday_url,greenhouse_slug,lever_slug) VALUES (?,?,?,?,?,?,?)''',
                      (cid, emp.get('name','').strip(), emp.get('source_type','workday'), emp.get('careers_url',''), emp.get('workday_url',''), emp.get('greenhouse_slug',''), emp.get('lever_slug','')))
        return cid


def get_candidate(cid):
    cand = row('SELECT * FROM candidates WHERE id=?', (cid,))
    if not cand:
        return None
    cand['titles'] = rows('SELECT * FROM candidate_titles WHERE candidate_id=? ORDER BY id', (cid,))
    cand['employers'] = rows('SELECT * FROM candidate_employers WHERE candidate_id=? ORDER BY id', (cid,))
    return cand


def delete_candidate(cid: int) -> bool:
    """Delete a person and all their linked data. Returns False if not found."""
    with conn() as c:
        if not c.execute('SELECT id FROM candidates WHERE id=?', (cid,)).fetchone():
            return False
        c.execute('DELETE FROM candidate_titles WHERE candidate_id=?', (cid,))
        c.execute('DELETE FROM candidate_employers WHERE candidate_id=?', (cid,))
        c.execute('DELETE FROM candidates WHERE id=?', (cid,))
    return True


def update_hej_category_ids(candidate_id: int, category_ids_text: str) -> None:
    init()
    with conn() as c:
        c.execute(
            "UPDATE candidates SET hej_category_ids=?, updated_at=? WHERE id=?",
            (category_ids_text or "", utcnow(), candidate_id),
        )


def update_search_prefs(
    candidate_id: int,
    *,
    min_match_score: int,
    salary_min: int,
    salary_max: int,
    keywords_text: str,
) -> None:
    init()
    with conn() as c:
        c.execute(
            """UPDATE candidates SET min_match_score=?, salary_min=?, salary_max=?,
               keywords_text=?, updated_at=? WHERE id=?""",
            (
                int(min_match_score),
                int(salary_min),
                int(salary_max),
                keywords_text or "",
                utcnow(),
                candidate_id,
            ),
        )


def set_active(cid):
    with conn() as c:
        c.execute('UPDATE candidates SET active=0')
        c.execute('UPDATE candidates SET active=1 WHERE id=?', (cid,))


def active_candidate():
    cand = row('SELECT * FROM candidates WHERE active=1 ORDER BY id DESC LIMIT 1')
    return get_candidate(cand['id']) if cand else None
