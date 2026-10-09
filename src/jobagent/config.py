"""Load global settings from YAML and overlay secrets from the environment."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from jobagent.paths import config_dir, project_root, settings_path

_ENV_API_KEYS = {
    "anthropic_key": "ANTHROPIC_API_KEY",
    "mistral_key": "MISTRAL_API_KEY",
    "adzuna_app_id": "ADZUNA_APP_ID",
    "adzuna_app_key": "ADZUNA_APP_KEY",
    "usajobs_api_key": "USAJOBS_API_KEY",
    "usajobs_user_agent": "USAJOBS_USER_AGENT",
    "rapidapi_key": "RAPIDAPI_KEY",
    "themuse_api_key": "THEMUSE_API_KEY",
    "google_maps_key": "GOOGLE_MAPS_KEY",
}

# Shown on Settings. `dest` matches secrets.yaml api: and the legacy profile.yaml names.
API_KEY_FIELDS: tuple[dict[str, str], ...] = (
    {
        "dest": "adzuna_app_id",
        "env": "ADZUNA_APP_ID",
        "label": "Adzuna app id",
        "source": "Adzuna",
        "hint": "Required with the app key. From the Adzuna developer dashboard.",
    },
    {
        "dest": "adzuna_app_key",
        "env": "ADZUNA_APP_KEY",
        "label": "Adzuna app key",
        "source": "Adzuna",
        "hint": "Required with the app id.",
    },
    {
        "dest": "rapidapi_key",
        "env": "RAPIDAPI_KEY",
        "label": "JSearch RapidAPI key",
        "source": "JSearch",
        "hint": "Required. Sent as x-rapidapi-key to jsearch.p.rapidapi.com.",
    },
    {
        "dest": "usajobs_api_key",
        "env": "USAJOBS_API_KEY",
        "label": "USAJOBS API key",
        "source": "USAJOBS",
        "hint": "Required. From developer.usajobs.gov. Authorization-Key header.",
    },
    {
        "dest": "usajobs_user_agent",
        "env": "USAJOBS_USER_AGENT",
        "label": "USAJOBS contact email",
        "source": "USAJOBS",
        "hint": "Required. The email registered with USAJOBS, sent as User-Agent.",
    },
    {
        "dest": "themuse_api_key",
        "env": "THEMUSE_API_KEY",
        "label": "The Muse API key",
        "source": "The Muse",
        "hint": "Optional. The public jobs API works without a key.",
    },
    {
        "dest": "google_maps_key",
        "env": "GOOGLE_MAPS_KEY",
        "label": "Google Maps key",
        "source": "Commute",
        "hint": "Optional. Drive-time lookups. OSRM is used when this is empty.",
    },
    {
        "dest": "anthropic_key",
        "env": "ANTHROPIC_API_KEY",
        "label": "Anthropic API key",
        "source": "Ranking",
        "hint": "Optional. Used only when llm.provider is anthropic.",
    },
    {
        "dest": "mistral_key",
        "env": "MISTRAL_API_KEY",
        "label": "Mistral API key",
        "source": "Ranking",
        "hint": "Optional. Used only when llm.provider is mistral.",
    },
)


def credential_usable(value: Any) -> bool:
    """True for a real credential. Placeholders such as YOUR_... count as missing."""
    text = str(value or "").strip()
    return bool(text) and not text.upper().startswith("YOUR_")


def mask_secret(value: Any) -> str:
    """Last four characters only, and only when the value is long enough to mask."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "•" * len(text)
    return "••••" + text[-4:]


def secrets_file_path() -> Path:
    """Gitignored key store. Deploys must not replace this file."""
    override = os.environ.get("JOBAGENT_SECRETS_PATH", "").strip()
    if override:
        return Path(override)
    return config_dir() / "secrets.yaml"


def _profile_yaml_paths() -> list[Path]:
    """Legacy api: blocks. Lowest priority last in this list (www, then config)."""
    candidates = [
        project_root() / "www" / "config" / "profile.yaml",
        project_root() / "config" / "profile.yaml",
        config_dir() / "profile.yaml",
    ]
    seen: set[str] = set()
    paths: list[Path] = []
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _api_from_yaml(path: Path) -> dict[str, Any]:
    api = _read_yaml_mapping(path).get("api") or {}
    return dict(api) if isinstance(api, dict) else {}


def _fill_blank_env_from_dotenv(env_path: Path | None = None) -> None:
    """Load .env without letting a blank process variable hide the file value."""
    path = env_path or (project_root() / ".env")
    if not path.is_file():
        return
    from dotenv import dotenv_values

    parsed = dotenv_values(path)
    for key, value in parsed.items():
        if not key or value is None or not str(value).strip():
            continue
        current = os.environ.get(key)
        if current is None or not str(current).strip():
            os.environ[key] = str(value)


def _origin_for_profile(path: Path) -> str:
    parts = set(path.parts)
    if "www" in parts:
        return "www/config/profile.yaml"
    return "config/profile.yaml"


def _layered_api(settings_api: dict[str, Any] | None) -> dict[str, tuple[str, str]]:
    """Winning credential per key, with the place it came from.

    Non-blank environment (after .env fills blanks) wins, then secrets.yaml,
    then settings.yaml api:, then legacy profile.yaml api:. YOUR_ placeholders
    are ignored so they cannot wipe a real key.
    """
    found: dict[str, tuple[str, str]] = {}

    def take(mapping: dict[str, Any], origin: str) -> None:
        for dest in _ENV_API_KEYS:
            value = mapping.get(dest)
            if credential_usable(value):
                found[dest] = (str(value).strip(), origin)

    for path in _profile_yaml_paths():
        take(_api_from_yaml(path), _origin_for_profile(path))
    take(dict(settings_api or {}), "settings.yaml")
    take(_api_from_yaml(secrets_file_path()), "config/secrets.yaml")
    env_values = {
        dest: os.environ.get(env_name, "")
        for dest, env_name in _ENV_API_KEYS.items()
    }
    take(env_values, "environment")
    return found


def _settings_api_on_disk() -> dict[str, Any]:
    """api: from the real settings file, never the committed example."""
    path = writable_settings_path()
    if not path.is_file():
        return {}
    example = config_dir() / "settings.example.yaml"
    try:
        if example.is_file() and path.resolve() == example.resolve():
            return {}
    except OSError:
        return {}
    return _api_from_yaml(path)


def _recovered_api_keys() -> dict[str, str]:
    """Best key from places a deploy may overwrite. Process env included."""
    _fill_blank_env_from_dotenv()
    found: dict[str, str] = {}

    def take(mapping: dict[str, Any]) -> None:
        for dest in _ENV_API_KEYS:
            if dest in found:
                continue
            value = mapping.get(dest)
            if credential_usable(value):
                found[dest] = str(value).strip()

    take({dest: os.environ.get(env_name, "") for dest, env_name in _ENV_API_KEYS.items()})
    from dotenv import dotenv_values

    env_file = project_root() / ".env"
    if env_file.is_file():
        parsed = dotenv_values(env_file)
        take({dest: parsed.get(env_name) for dest, env_name in _ENV_API_KEYS.items()})
    take(_settings_api_on_disk())
    for path in reversed(_profile_yaml_paths()):
        take(_api_from_yaml(path))
    return found


def ensure_secrets_file() -> Path:
    """Copy missing keys into config/secrets.yaml. Never replaces a saved value."""
    path = secrets_file_path()
    data = _read_yaml_mapping(path)
    api = dict(data.get("api") or {}) if isinstance(data.get("api"), dict) else {}
    changed = False
    for dest, value in _recovered_api_keys().items():
        if credential_usable(api.get(dest)):
            continue
        api[dest] = value
        changed = True
    if not changed:
        return path
    stored = {dest: str(api[dest]).strip() for dest in _ENV_API_KEYS if credential_usable(api.get(dest))}
    _write_secrets(path, stored)
    return path


def _write_secrets(path: Path, api: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump({"api": api}, sort_keys=False, allow_unicode=True)
    header = (
        "# JobAgent API keys. Gitignored. Do not commit this file.\n"
        "# Deploys and updates must not replace it. A non-blank environment\n"
        "# variable or .env value overrides a key stored here.\n"
    )
    path.write_text(header + body, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def save_api_secrets(updates: dict[str, str]) -> Path:
    """Persist non-blank Settings fields. A blank field keeps the stored key."""
    path = secrets_file_path()
    data = _read_yaml_mapping(path)
    api = dict(data.get("api") or {}) if isinstance(data.get("api"), dict) else {}
    for dest, value in _recovered_api_keys().items():
        if not credential_usable(api.get(dest)):
            api[dest] = value
    for dest, raw in updates.items():
        if dest not in _ENV_API_KEYS:
            continue
        text = str(raw or "").strip()
        if not credential_usable(text):
            continue
        api[dest] = text
    stored = {dest: str(api[dest]).strip() for dest in _ENV_API_KEYS if credential_usable(api.get(dest))}
    if not stored and not path.is_file():
        return path
    _write_secrets(path, stored)
    return path


def api_key_form_rows() -> list[dict[str, Any]]:
    """Settings-page rows. Masked display only — never the raw credential."""
    _fill_blank_env_from_dotenv()
    cfg_path = settings_path()
    settings_api: dict[str, Any] = {}
    if cfg_path.is_file():
        settings_api = _api_from_yaml(cfg_path)
    layered = _layered_api(settings_api)
    rows: list[dict[str, Any]] = []
    for field in API_KEY_FIELDS:
        value, origin = layered.get(field["dest"], ("", "missing"))
        usable = credential_usable(value)
        rows.append(
            {
                "dest": field["dest"],
                "env": field["env"],
                "label": field["label"],
                "source": field["source"],
                "hint": field["hint"],
                "configured": usable,
                "masked": mask_secret(value) if usable else "",
                "origin": origin if usable else "missing",
            }
        )
    return rows


def as_bool(value: Any, default: bool) -> bool:
    """Parse a settings flag. Missing values use default; only explicit values flip it."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(int(value))
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def scheduler_dry_run(config: dict[str, Any] | None) -> bool:
    """Dry-run defaults ON. A saved false is honored; it does not submit applications."""
    scheduler = (config or {}).get("scheduler") or {}
    if "dry_run" not in scheduler:
        return True
    return as_bool(scheduler.get("dry_run"), True)


def scheduler_auto_apply(config: dict[str, Any] | None) -> bool:
    """Auto-apply is a separate opt-in and stays OFF unless explicitly set true."""
    scheduler = (config or {}).get("scheduler") or {}
    if "auto_apply" not in scheduler:
        return False
    return as_bool(scheduler.get("auto_apply"), False)


def submission_allowed(config: dict[str, Any] | None) -> bool:
    """True only when dry-run is off, auto-apply was opted in, and a sender exists.

    The sender switch stays off in this build, so this remains false even after
    an explicit opt-in. Nothing in this process submits or emails.
    """
    from jobagent import AUTO_APPLY_ENABLED

    if scheduler_dry_run(config):
        return False
    if not scheduler_auto_apply(config):
        return False
    return bool(AUTO_APPLY_ENABLED)


def writable_settings_path() -> Path:
    """Path the settings UI writes. Never the example file unless it was overridden."""
    override = os.environ.get("JOBAGENT_SETTINGS_PATH", "").strip()
    if override:
        return Path(override)
    return config_dir() / "settings.yaml"


def load_settings(path: Path | None = None) -> dict[str, Any]:
    """Return global config. Candidate identity is never read from this file.

    Credentials resolve from a non-blank environment variable or .env, then
    config/secrets.yaml, then this file's api: block, then legacy profile.yaml.
    This function does not write the secrets file.
    """
    _fill_blank_env_from_dotenv()
    cfg_path = path or settings_path()
    data: dict[str, Any] = {}
    if cfg_path.is_file():
        with open(cfg_path, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
            if isinstance(loaded, dict):
                data = loaded
    cfg = copy.deepcopy(data)
    layered = _layered_api(dict(cfg.get("api") or {}))
    cfg["api"] = {dest: value for dest, (value, _origin) in layered.items()}
    scheduler = dict(cfg.get("scheduler") or {})
    scheduler["dry_run"] = scheduler_dry_run({"scheduler": scheduler})
    scheduler["auto_apply"] = scheduler_auto_apply({"scheduler": scheduler})
    cfg["scheduler"] = scheduler
    return cfg


def save_scheduler_settings(*, dry_run: bool, auto_apply: bool) -> Path:
    """Persist scheduler.dry_run and scheduler.auto_apply without writing secrets.

    Reads the on-disk YAML (or the example as a seed) so environment API keys
    that load_settings overlays are not copied into the file.
    """
    path = writable_settings_path()
    data: dict[str, Any] = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = loaded
    else:
        example = config_dir() / "settings.example.yaml"
        if not example.is_file():
            example = project_root() / "config" / "settings.example.yaml"
        if example.is_file() and example.resolve() != path.resolve():
            loaded = yaml.safe_load(example.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                data = loaded
    scheduler = dict(data.get("scheduler") or {})
    scheduler["dry_run"] = bool(dry_run)
    scheduler["auto_apply"] = bool(auto_apply)
    data["scheduler"] = scheduler
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def candidate_runtime_config(candidate: dict, base: dict | None = None) -> dict[str, Any]:
    """Merge a candidate DB record into a crawl/rank/commute config.

    Global scheduler, sources, routing, and API keys come from settings.
    Profile identity and search prefs come only from the candidate row.
    """
    cfg = copy.deepcopy(base or load_settings())
    cfg["profile"] = {
        "name": candidate.get("name") or "",
        "email": candidate.get("email") or "",
        "phone": candidate.get("phone") or "",
        "location": candidate.get("location") or "",
        "linkedin": candidate.get("linkedin") or "",
        "github": candidate.get("github") or "",
    }
    if candidate.get("resume_text"):
        cfg["resume_text"] = candidate["resume_text"]
    else:
        cfg["resume_text"] = cfg.get("resume_text") or ""

    search = dict(cfg.get("search") or {})
    titles = [t["title"] if isinstance(t, dict) else str(t) for t in (candidate.get("titles") or [])]
    titles = [t.strip() for t in titles if str(t).strip()]
    if titles:
        search["titles"] = titles
    if candidate.get("min_match_score") is not None:
        search["min_match_score"] = int(candidate["min_match_score"])
    if candidate.get("salary_min") is not None:
        search["salary_min"] = int(candidate["salary_min"])
    if candidate.get("salary_max") is not None:
        search["salary_max"] = int(candidate["salary_max"])
    keywords_text = candidate.get("keywords_text") or ""
    if str(keywords_text).strip():
        search["keywords"] = [k.strip() for k in str(keywords_text).splitlines() if k.strip()]
    if candidate.get("location"):
        search["location"] = candidate["location"]
    if candidate.get("commute_auto_apply_minutes") is not None:
        search["commute_auto_apply_minutes"] = int(candidate["commute_auto_apply_minutes"])
    if candidate.get("commute_review_minutes") is not None:
        search["commute_review_minutes"] = int(candidate["commute_review_minutes"])
    if candidate.get("location_accept_remote") is not None:
        search["location_accept_remote"] = bool(int(candidate["location_accept_remote"]))
    cfg["search"] = search

    hej = candidate.get("hej_category_ids") or ""
    if str(hej).strip():
        cfg["_hej_category_ids"] = str(hej)
    return cfg


def crawl_config_for_candidates(candidates: list[dict], base: dict | None = None) -> dict[str, Any]:
    """Build a single crawl config whose titles/HEJ IDs are the union of all people."""
    cfg = copy.deepcopy(base or load_settings())
    titles: list[str] = []
    seen_titles: set[str] = set()
    hej_ids: list[str] = []
    locations: list[str] = []
    greenhouse: list[str] = []
    seen_gh: set[str] = set()
    lever: list[str] = []
    seen_lv: set[str] = set()
    workday: list[dict] = []
    seen_wd: set[str] = set()
    for cand in candidates:
        for raw in cand.get("titles") or []:
            title = (raw["title"] if isinstance(raw, dict) else str(raw)).strip()
            key = title.lower()
            if title and key not in seen_titles:
                seen_titles.add(key)
                titles.append(title)
        hej = str(cand.get("hej_category_ids") or "").strip()
        if hej:
            hej_ids.append(hej)
        loc = str(cand.get("location") or "").strip()
        if loc:
            locations.append(loc)
        for emp in cand.get("employers") or []:
            gh = str(emp.get("greenhouse_slug") or "").strip()
            if gh and gh.lower() not in seen_gh:
                seen_gh.add(gh.lower())
                greenhouse.append(gh)
            lv = str(emp.get("lever_slug") or "").strip()
            if lv and lv.lower() not in seen_lv:
                seen_lv.add(lv.lower())
                lever.append(lv)
            wd_url = str(emp.get("workday_url") or "").strip()
            careers = str(emp.get("careers_url") or "").strip()
            source_type = str(emp.get("source_type") or "").strip().lower()
            if wd_url or (source_type == "workday" and careers):
                key = (wd_url or careers).lower()
                if key and key not in seen_wd:
                    seen_wd.add(key)
                    workday.append(
                        {
                            "name": emp.get("name") or "Unknown",
                            "careers_url": careers,
                            "workday_url": wd_url,
                        }
                    )
    search = dict(cfg.get("search") or {})
    if titles:
        search["titles"] = titles
    if locations and not search.get("location"):
        search["location"] = locations[0]
    cfg["search"] = search
    if hej_ids:
        cfg["_hej_category_ids"] = "\n".join(hej_ids)
    sources = cfg.get("sources") or {}
    adzuna = dict(sources.get("adzuna") or {})
    if locations and not adzuna.get("where"):
        adzuna["where"] = locations[0]
        sources["adzuna"] = adzuna
        cfg["sources"] = sources
    jsearch = dict(sources.get("jsearch") or {})
    if locations and not jsearch.get("location"):
        jsearch["location"] = locations[0]
        sources["jsearch"] = jsearch
        cfg["sources"] = sources
    usajobs = dict(sources.get("usajobs") or {})
    if locations and not usajobs.get("location"):
        usajobs["location"] = locations[0]
        sources["usajobs"] = usajobs
        cfg["sources"] = sources
    themuse = dict(sources.get("themuse") or {})
    if locations and not str(themuse.get("location") or "").strip():
        themuse["location"] = locations[0]
        sources["themuse"] = themuse
        cfg["sources"] = sources
    if greenhouse:
        gh_cfg = dict(sources.get("greenhouse") or {})
        existing = [str(x) for x in (gh_cfg.get("companies") or [])]
        merged = list(dict.fromkeys(existing + greenhouse))
        gh_cfg["companies"] = merged
        sources["greenhouse"] = gh_cfg
        cfg["sources"] = sources
    if lever:
        lv_cfg = dict(sources.get("lever") or {})
        existing = [str(x) for x in (lv_cfg.get("companies") or [])]
        merged = list(dict.fromkeys(existing + lever))
        lv_cfg["companies"] = merged
        sources["lever"] = lv_cfg
        cfg["sources"] = sources
    if workday:
        wd_cfg = dict(sources.get("workday") or {})
        existing = list(wd_cfg.get("companies") or [])
        wd_cfg["companies"] = list(existing) + workday
        sources["workday"] = wd_cfg
        cfg["sources"] = sources
    return cfg
