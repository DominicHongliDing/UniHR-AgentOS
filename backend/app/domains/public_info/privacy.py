from __future__ import annotations

import re
from dataclasses import dataclass


_SECRET_PATTERNS = (
    re.compile(r"\b1\d{10}\b"),
    re.compile(r"\b\d{17}[\dXx]\b"),
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?:api[_ -]?key|token|password|密码|密钥)\s*[:：=]\s*\S+", re.I),
    re.compile(r"(?:[A-Za-z]:\\|/Users/|/home/)\S+", re.I),
)
_RESTRICTED_HR_TERMS = (
    "候选人简历", "员工简历", "身份证", "手机号", "个人薪酬", "个人工资",
    "员工工资", "员工薪酬", "体检报告", "婚育", "银行卡", "家庭住址",
)


@dataclass(frozen=True)
class PrivacyDecision:
    allowed: bool
    query: str
    reason: str = ""


def govern_public_query(message: str, *, max_chars: int = 800) -> PrivacyDecision:
    """Accept only the current public query; never enrich it with private HR context."""

    query = " ".join(message.replace("\x00", " ").split()).strip()
    if not query:
        return PrivacyDecision(False, "", "empty_query")
    if any(term in query for term in _RESTRICTED_HR_TERMS):
        return PrivacyDecision(False, "", "restricted_hr_data")
    if any(pattern.search(query) for pattern in _SECRET_PATTERNS):
        return PrivacyDecision(False, "", "personal_or_secret_data")
    return PrivacyDecision(True, query[:max_chars])
