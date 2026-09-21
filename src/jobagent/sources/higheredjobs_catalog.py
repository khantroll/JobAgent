"""HigherEdJobs RSS category catalog and title-based suggestions."""
from __future__ import annotations

import json
import re

from jobagent.paths import config_dir

CATALOG_PATH = config_dir() / "higheredjobs_categories.json"

_catalog_cache: list[dict] | None = None


def load_catalog() -> list[dict]:
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache
    if not CATALOG_PATH.is_file():
        _catalog_cache = []
        return _catalog_cache
    with open(CATALOG_PATH, encoding="utf-8") as f:
        _catalog_cache = json.load(f)
    return _catalog_cache


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _title_words(title: str) -> set[str]:
    words = set(re.split(r"[\s/\-,&]+", _norm(title)))
    return {w for w in words if len(w) > 2}


def _score_category(title: str, category: dict) -> int:
    title_l = _norm(title)
    if not title_l:
        return 0
    score = 0
    name_l = _norm(category.get("name", ""))
    if name_l and name_l in title_l:
        score += 5
    if name_l and title_l in name_l:
        score += 3
    title_words = _title_words(title)
    for tag in category.get("tags") or []:
        tag_l = _norm(tag)
        if not tag_l:
            continue
        if tag_l in title_l:
            score += 3
            continue
        tag_words = _title_words(tag_l)
        if len(tag_words) >= 2 and tag_words.issubset(title_words):
            score += 2
        elif any(w in title_l for w in tag_words if len(w) > 3):
            score += 1
    return score


def suggest_categories_for_titles(titles: list[str], *, min_score: int = 2) -> list[dict]:
    """
    Return catalog entries that match any target job title, sorted by best score.
    Each item: {id, name, score, matched_titles}
    """
    catalog = load_catalog()
    if not catalog or not titles:
        return []

    by_id: dict[int, dict] = {}
    for title in titles:
        title = (title or "").strip()
        if not title:
            continue
        for cat in catalog:
            cid = int(cat["id"])
            sc = _score_category(title, cat)
            if sc < min_score:
                continue
            row = by_id.get(cid)
            if not row or sc > row["score"]:
                by_id[cid] = {
                    "id": cid,
                    "name": cat.get("name", ""),
                    "score": sc,
                    "matched_titles": [title],
                }
            elif row and sc == row["score"] and title not in row["matched_titles"]:
                row["matched_titles"].append(title)

    return sorted(by_id.values(), key=lambda x: (-x["score"], x["name"]))


def suggest_category_ids_for_titles(titles: list[str]) -> list[int]:
    return [row["id"] for row in suggest_categories_for_titles(titles)]


def parse_category_ids_text(text: str) -> list[int]:
    ids = []
    for part in re.split(r"[\s,;]+", text or ""):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return list(dict.fromkeys(ids))


def format_category_ids_text(ids: list[int]) -> str:
    return "\n".join(str(i) for i in ids)


def catalog_names_by_id() -> dict[int, str]:
    return {int(c["id"]): c.get("name", "") for c in load_catalog()}
