from __future__ import annotations

import re

from .contracts import SkillDraft


def score_skill_match(skill: SkillDraft, query: str) -> tuple[float, str]:
    """Return the production trigger score used by both runtime selection and replay evals."""
    clean = query.casefold()
    compact_query = _compact(clean)
    best_score = 0.0
    best_trigger = ""
    for trigger in skill.trigger_phrases:
        phrase = trigger.strip().casefold()
        if not phrase:
            continue
        if phrase in clean:
            score = 10 + min(5, len(phrase) / 4)
        else:
            compact_phrase = _compact(phrase)
            overlap = _character_overlap(compact_phrase, compact_query)
            score = 5 + overlap if len(compact_phrase) >= 4 and overlap >= 0.72 else 0.0
        if score > best_score:
            best_score, best_trigger = score, trigger
    if skill.display_name.casefold() in clean or skill.name.casefold() in clean:
        best_score = max(best_score, 12)
        best_trigger = best_trigger or skill.display_name
    return best_score, best_trigger


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", value.casefold())


def _character_overlap(needle: str, haystack: str) -> float:
    if not needle:
        return 0.0
    needle_chars = set(needle)
    return len(needle_chars.intersection(set(haystack))) / len(needle_chars)
