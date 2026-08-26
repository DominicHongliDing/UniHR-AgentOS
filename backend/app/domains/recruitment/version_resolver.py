from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from app.domains.conversations.contracts import AttachmentRecord

from .contracts import ResumeSourceProfile, ResumeVersionGroup


_EMAIL_PATTERN = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?(1[3-9]\d{9})(?!\d)")
_LABELED_NAME_PATTERN = re.compile(
    r"(?:姓名|name)\s*[:：]\s*([\u4e00-\u9fff]{2,4}|[A-Za-z][A-Za-z .'-]{1,48})",
    re.IGNORECASE,
)
_VERSION_PATTERN = re.compile(
    r"(?i)(?:^|[\s_\-.(（])(?:v(?:ersion)?\s*([0-9]+)|ver\s*([0-9]+)|"
    r"final|latest|updated|update|copy|最终版?|最新版?|修改版?|定稿|终稿|新版|旧版|初版)"
    r"(?=$|[\s_\-.)）])"
)
_BRACKETED_PREFIX = re.compile(r"^[【\[].*?[】\]]\s*")
_FILENAME_NAME_PATTERN = re.compile(r"^([\u4e00-\u9fff]{2,4})(?=\s|[-_（(]|\d|$)")


@dataclass(frozen=True)
class ResumeVersionResolution:
    representatives: list[AttachmentRecord]
    source_profiles: list[ResumeSourceProfile]
    groups: list[ResumeVersionGroup]


@dataclass(frozen=True)
class _Identity:
    candidate_name: str
    contacts: frozenset[str]
    normalized_text: str


class ResumeVersionResolver:
    """Conservatively collapse resume revisions without collapsing different people.

    Identity information is used only inside this module. Raw e-mail addresses and
    phone numbers are never emitted into the report, trace, workbook, or score input.
    """

    auto_similarity = 0.78
    review_similarity = 0.55

    def resolve(
        self,
        sources: list[AttachmentRecord],
        profiles: list[ResumeSourceProfile],
    ) -> ResumeVersionResolution:
        if len(sources) < 2:
            return ResumeVersionResolution(sources, profiles, [])

        identities = {source.display_name: _identity(source) for source in sources}
        parent = {source.display_name: source.display_name for source in sources}
        auto_pairs: list[tuple[str, str, float, str]] = []
        review_pairs: list[tuple[str, str, float, str]] = []

        def find(name: str) -> str:
            while parent[name] != name:
                parent[name] = parent[parent[name]]
                name = parent[name]
            return name

        def union(left: str, right: str) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for index, left in enumerate(sources):
            for right in sources[index + 1:]:
                left_identity = identities[left.display_name]
                right_identity = identities[right.display_name]
                similarity = _content_similarity(
                    left_identity.normalized_text, right_identity.normalized_text,
                )
                same_name = bool(
                    left_identity.candidate_name
                    and left_identity.candidate_name == right_identity.candidate_name
                )
                shared_contact = bool(left_identity.contacts & right_identity.contacts)
                if shared_contact and (same_name or similarity >= 0.35):
                    auto_pairs.append((left.display_name, right.display_name, similarity, "shared_contact_anchor"))
                    union(left.display_name, right.display_name)
                elif same_name and similarity >= self.auto_similarity:
                    auto_pairs.append((left.display_name, right.display_name, similarity, "same_name_high_similarity"))
                    union(left.display_name, right.display_name)
                elif same_name and similarity >= self.review_similarity:
                    review_pairs.append((left.display_name, right.display_name, similarity, "same_name_ambiguous_similarity"))

        by_root: dict[str, list[AttachmentRecord]] = {}
        for source in sources:
            by_root.setdefault(find(source.display_name), []).append(source)

        profile_by_name = {profile.source_name: profile.model_copy(deep=True) for profile in profiles}
        excluded: set[str] = set()
        groups: list[ResumeVersionGroup] = []
        merged_members: set[str] = set()
        for members in by_root.values():
            if len(members) < 2:
                continue
            representative = max(members, key=_representative_key)
            group_id = _group_id([item.display_name for item in members])
            member_names = {item.display_name for item in members}
            similarities = {
                f"{left} ↔ {right}": round(similarity, 4)
                for left, right, similarity, _ in auto_pairs
                if left in member_names and right in member_names
            }
            bases = sorted({
                basis for left, right, _, basis in auto_pairs
                if left in member_names and right in member_names
            })
            group_similarity = max(similarities.values(), default=0)
            groups.append(ResumeVersionGroup(
                group_id=group_id,
                representative_source=representative.display_name,
                version_sources=[item.display_name for item in members],
                status="merged",
                identity_basis="+".join(bases),
                similarities=similarities,
                selection_reason=_selection_reason(representative),
            ))
            for member in members:
                merged_members.add(member.display_name)
                profile = profile_by_name[member.display_name]
                if member.display_name == representative.display_name:
                    profile_by_name[member.display_name] = profile.model_copy(update={
                        "version_group_id": group_id,
                        "version_status": "representative",
                        "superseded_by": "",
                        "semantic_similarity": group_similarity,
                        "identity_basis": "+".join(bases),
                    })
                else:
                    excluded.add(member.display_name)
                    profile_by_name[member.display_name] = profile.model_copy(update={
                        "version_group_id": group_id,
                        "version_status": "superseded",
                        "superseded_by": representative.display_name,
                        "semantic_similarity": group_similarity,
                        "identity_basis": "+".join(bases),
                    })

        for left, right, similarity, basis in review_pairs:
            if left in merged_members and right in merged_members and find(left) == find(right):
                continue
            group_id = _group_id([left, right, "review"])
            groups.append(ResumeVersionGroup(
                group_id=group_id,
                representative_source="",
                version_sources=[left, right],
                status="review_required",
                identity_basis=basis,
                similarities={f"{left} ↔ {right}": round(similarity, 4)},
                selection_reason="身份与内容相似但未达到自动归并阈值，两份材料均保留参与复核",
            ))
            for name in (left, right):
                if name in merged_members:
                    continue
                profile = profile_by_name[name]
                profile_by_name[name] = profile.model_copy(update={
                    "version_group_id": group_id,
                    "version_status": "review_required",
                    "semantic_similarity": max(profile.semantic_similarity, round(similarity, 4)),
                    "identity_basis": basis,
                })

        representatives = [source for source in sources if source.display_name not in excluded]
        ordered_profiles = [profile_by_name[profile.source_name] for profile in profiles]
        return ResumeVersionResolution(representatives, ordered_profiles, groups)


def _identity(source: AttachmentRecord) -> _Identity:
    text = source.extracted_text
    contacts = {
        "email:" + hashlib.sha256(item.casefold().encode("utf-8")).hexdigest()
        for item in _EMAIL_PATTERN.findall(text)
    }
    contacts.update(
        "phone:" + hashlib.sha256(item.encode("utf-8")).hexdigest()
        for item in _PHONE_PATTERN.findall(text)
    )
    return _Identity(
        candidate_name=_candidate_name(source),
        contacts=frozenset(contacts),
        normalized_text=_normalize_content(text),
    )


def _candidate_name(source: AttachmentRecord) -> str:
    labeled = _LABELED_NAME_PATTERN.search(source.extracted_text[:2_000])
    if labeled:
        return _normalize_name(labeled.group(1))
    stem = _BRACKETED_PREFIX.sub("", Path(source.display_name).stem).strip()
    stem = _VERSION_PATTERN.sub(" ", stem)
    match = _FILENAME_NAME_PATTERN.search(stem)
    if match:
        return _normalize_name(match.group(1))
    return ""


def _normalize_name(value: str) -> str:
    return re.sub(r"[\s.'’-]+", "", value).casefold()


def _normalize_content(value: str) -> str:
    clean = _EMAIL_PATTERN.sub(" ", value)
    clean = _PHONE_PATTERN.sub(" ", clean)
    clean = re.sub(r"(?i)(?:简历|个人简历|resume|curriculum vitae)", " ", clean)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", clean.casefold())


def _content_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    sequence = SequenceMatcher(None, left, right, autojunk=False).ratio()
    left_shingles = _shingles(left)
    right_shingles = _shingles(right)
    jaccard = len(left_shingles & right_shingles) / max(1, len(left_shingles | right_shingles))
    return round(max(sequence, jaccard), 4)


def _shingles(value: str, width: int = 4) -> set[str]:
    if len(value) <= width:
        return {value}
    return {value[index:index + width] for index in range(len(value) - width + 1)}


def _representative_key(source: AttachmentRecord) -> tuple[int, int, str]:
    stem = Path(source.display_name).stem
    lowered = stem.casefold()
    priority = 0
    if re.search(r"(?i)(?:final|latest|最终版?|最新版?|定稿|终稿)", stem):
        priority = 10_000
    numbers = [int(value) for pair in _VERSION_PATTERN.findall(stem) for value in pair if value]
    if numbers:
        priority = max(priority, max(numbers) * 100)
    return priority, len(source.extracted_text.strip()), lowered


def _selection_reason(source: AttachmentRecord) -> str:
    stem = Path(source.display_name).stem
    if re.search(r"(?i)(?:final|latest|最终版?|最新版?|定稿|终稿)", stem):
        return "文件名包含最终/最新版本标记，优先作为代表材料"
    if _VERSION_PATTERN.search(stem):
        return "按文件名版本号与材料完整度选择代表材料"
    return "在同一身份版本组内选择内容更完整的材料作为代表"


def _group_id(names: list[str]) -> str:
    canonical = "\n".join(sorted(names, key=str.casefold))
    return "resume_version_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
