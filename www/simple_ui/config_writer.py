from copy import deepcopy
from pathlib import Path
from datetime import datetime
import shutil
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'config' / 'profile.yaml'
BACKUP_DIR = ROOT / 'config' / 'backups'


def _split_keywords(text):
    return [x.strip() for x in (text or '').replace('\r', '').split('\n') if x.strip()]


def list_profile_backups() -> list[Path]:
    if not BACKUP_DIR.is_dir():
        return []
    return sorted(BACKUP_DIR.glob('profile-*.yaml'), reverse=True)


def restore_profile_from_backup(backup_path: Path | None = None) -> Path:
    """Copy a timestamped backup over config/profile.yaml."""
    if backup_path is None:
        backups = list_profile_backups()
        if not backups:
            raise FileNotFoundError('No profile backups in config/backups/')
        backup_path = backups[0]
    if not backup_path.is_file():
        raise FileNotFoundError(str(backup_path))
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    if PROFILE.is_file():
        shutil.copy2(PROFILE, BACKUP_DIR / f'profile-pre-restore-{stamp}.yaml')
    shutil.copy2(backup_path, PROFILE)
    return backup_path


def _candidate_has_employers(candidate: dict) -> bool:
    for e in candidate.get('employers') or []:
        st = (e.get('source_type') or 'workday').strip()
        if st == 'greenhouse' and (e.get('greenhouse_slug') or '').strip():
            return True
        if st == 'lever' and (e.get('lever_slug') or '').strip():
            return True
        if st == 'workday' and (e.get('name') or '').strip():
            return True
    return False


def _apply_employers_to_cfg(cfg: dict, candidate: dict) -> None:
    cfg.setdefault('sources', {})
    cfg['sources'].setdefault('greenhouse', {})
    cfg['sources'].setdefault('lever', {})
    cfg['sources'].setdefault('workday', {})
    cfg['sources']['greenhouse']['companies'] = [
        e['greenhouse_slug'].strip()
        for e in candidate.get('employers', [])
        if e.get('source_type') == 'greenhouse' and (e.get('greenhouse_slug') or '').strip()
    ]
    cfg['sources']['lever']['companies'] = [
        e['lever_slug'].strip()
        for e in candidate.get('employers', [])
        if e.get('source_type') == 'lever' and (e.get('lever_slug') or '').strip()
    ]
    workday = []
    for e in candidate.get('employers', []):
        if e.get('source_type') != 'workday':
            continue
        item = {'name': e.get('name', '')}
        if (e.get('careers_url') or '').strip():
            item['careers_url'] = e['careers_url'].strip()
        if (e.get('workday_url') or '').strip():
            item['workday_url'] = e['workday_url'].strip()
        if len(item) > 1:
            workday.append(item)
    cfg['sources']['workday']['companies'] = workday


def write_active_profile(candidate, min_score=65, salary_min=0, salary_max=0, keywords=''):
    """
    Write the active person's profile/search fields into profile.yaml.

    Preserves existing YAML sections when the candidate row is missing data
    (employers, titles, keywords, resume, HigherEdJobs IDs) so activating a
    sparse profile does not wipe API keys, Workday companies, etc.
    """
    if PROFILE.is_file():
        with open(PROFILE, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    if PROFILE.is_file():
        shutil.copy2(PROFILE, BACKUP_DIR / f'profile-{stamp}.yaml')

    cfg['profile'] = {
        'name': candidate.get('name', ''),
        'email': candidate.get('email', ''),
        'phone': candidate.get('phone', ''),
        'location': candidate.get('location', ''),
        'linkedin': candidate.get('linkedin', ''),
        'github': candidate.get('github', ''),
    }

    resume = (candidate.get('resume_text') or '').strip()
    if resume:
        cfg['resume_text'] = candidate.get('resume_text', '')

    cfg.setdefault('search', {})
    titles_from_db = [t['title'] for t in candidate.get('titles', []) if (t.get('title') or '').strip()]
    if titles_from_db:
        cfg['search']['titles'] = titles_from_db

    if candidate.get('location', ''):
        cfg['search']['location'] = candidate.get('location', '')

    cfg['search']['min_match_score'] = int(min_score if min_score is not None else 65)
    cfg['search']['salary_min'] = int(salary_min if salary_min is not None else 0)
    cfg['search']['salary_max'] = int(salary_max if salary_max is not None else 0)

    kw_from_form = _split_keywords(keywords or '')
    kw_from_db = _split_keywords(candidate.get('keywords_text') or '')
    if kw_from_form:
        cfg['search']['keywords'] = kw_from_form
    elif kw_from_db:
        cfg['search']['keywords'] = kw_from_db

    cfg.setdefault('sources', {})
    if _candidate_has_employers(candidate):
        _apply_employers_to_cfg(cfg, candidate)

    cfg['sources'].setdefault('higheredjobs', {})
    hej_ids = []
    raw_hej = (candidate.get('hej_category_ids') or '').strip()
    if raw_hej:
        from agents.sources.higheredjobs_catalog import parse_category_ids_text
        hej_ids = parse_category_ids_text(raw_hej)
    elif titles_from_db:
        try:
            from agents.sources.higheredjobs_catalog import suggest_category_ids_for_titles
            hej_ids = suggest_category_ids_for_titles(titles_from_db)
        except ImportError:
            hej_ids = []
    if hej_ids:
        cfg['sources']['higheredjobs']['category_ids'] = hej_ids

    with open(PROFILE, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True, width=120)
    return PROFILE
