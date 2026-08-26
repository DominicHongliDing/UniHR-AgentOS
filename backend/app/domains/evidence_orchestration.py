from __future__ import annotations

from dataclasses import dataclass, field
import re

from app.domains.assistant.contracts import RetrievalMode
from app.domains.knowledge import KnowledgeAccessContext, PolicyKnowledgeRetriever
from app.domains.runs import RunPointer
from app.runtime.public_info import PublicInfoExecutor


@dataclass(frozen=True)
class GroundingSource:
    name: str
    text: str
    source_kind: str


@dataclass(frozen=True)
class EvidenceCollection:
    sources: list[GroundingSource] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)
    trace: list[dict[str, object]] = field(default_factory=list)


class EvidenceSourceOrchestrator:
    """Collect governed evidence for any business Agent behind one interface."""

    def __init__(
        self,
        knowledge: PolicyKnowledgeRetriever | None,
        public_info: PublicInfoExecutor,
    ) -> None:
        self.knowledge = knowledge
        self.public_info = public_info

    def collect(
        self,
        query: str,
        *,
        mode: RetrievalMode,
        access: KnowledgeAccessContext,
        payload_scope: RunPointer | None = None,
        resolved_query: str = "",
        query_variants: list[str] | None = None,
    ) -> EvidenceCollection:
        if mode == RetrievalMode.NONE or mode == RetrievalMode.ATTACHMENTS:
            return EvidenceCollection()

        sources: list[GroundingSource] = []
        citations: list[dict[str, str]] = []
        trace: list[dict[str, object]] = [{
            "event": "evidence.orchestration.started",
            "status": "running",
            "summary": f"按 {mode.value} 来源模式收集生成任务证据",
            "retrieval_mode": mode.value,
        }]
        if mode in {RetrievalMode.INTERNAL, RetrievalMode.HYBRID}:
            if self.knowledge is None:
                trace.append({
                    "event": "evidence.internal.unavailable",
                    "status": "waiting",
                    "summary": "当前没有可用的内部知识索引",
                })
            else:
                internal_query = focus_evidence_query(query, "internal")
                governed_resolved = (
                    resolved_query if resolved_query.strip() and resolved_query.strip() != query.strip()
                    else internal_query
                )
                bundle = self.knowledge.retrieve(
                    internal_query,
                    resolved_query=governed_resolved,
                    query_variants=list(dict.fromkeys([
                        internal_query, *(query_variants or []),
                    ])),
                    limit=8,
                    access=access,
                )
                for hit in bundle.hits:
                    sources.append(GroundingSource(
                        name=hit.citation,
                        text=hit.text,
                        source_kind="internal_policy",
                    ))
                trace.append({
                    "event": "evidence.internal.completed",
                    "status": "completed" if bundle.hits else "waiting",
                    "summary": f"内部知识库返回 {len(bundle.hits)} 条可用证据",
                    "evidence_count": len(bundle.hits),
                    "coverage": round(bundle.coverage, 3),
                })

        if mode in {RetrievalMode.WEB, RetrievalMode.HYBRID}:
            public_query = focus_evidence_query(query, "web")
            public_result = self.public_info.answer(public_query, payload_scope=payload_scope)
            citations.extend(public_result.citations)
            if public_result.answer.strip():
                citation_titles = "；".join(
                    str(item.get("title") or item.get("url") or "公开来源")
                    for item in public_result.citations[:8]
                )
                sources.append(GroundingSource(
                    name=f"公开联网检索结果{f'（{citation_titles}）' if citation_titles else ''}",
                    text=public_result.answer,
                    source_kind="public_web",
                ))
            trace.extend(public_result.trace)
            trace.append({
                "event": "evidence.web.completed",
                "status": "completed" if public_result.citations else "waiting",
                "summary": f"公网检索返回 {len(public_result.citations)} 个可点击来源",
                "source_count": len(public_result.citations),
                "capability_status": public_result.capability_status,
                "sources": public_result.citations,
            })

        trace.append({
            "event": "evidence.orchestration.completed",
            "status": "completed" if sources else "waiting",
            "summary": f"已向业务 Agent 提供 {len(sources)} 组受控证据",
            "retrieval_mode": mode.value,
            "evidence_count": len(sources),
        })
        return EvidenceCollection(sources=sources, citations=citations, trace=trace)


def focus_evidence_query(query: str, source_kind: str) -> str:
    """Remove artifact instructions while preserving the source-specific information need."""

    normalized = " ".join(query.split()).strip()
    if not normalized:
        return normalized
    markers = (
        ("内部知识库", "知识库", "内部政策", "现有政策", "政策依据", "根据政策")
        if source_kind == "internal"
        else ("联网", "官网", "公开网页", "公开来源")
    )
    marker_positions = [
        (normalized.find(marker), marker)
        for marker in markers if normalized.find(marker) >= 0
    ]
    focused = normalized
    if marker_positions:
        position, marker = min(marker_positions, key=lambda item: item[0])
        focused = normalized[position + len(marker):]
    if source_kind == "internal":
        focused = re.split(r"[，；。]\s*(?:再|并|然后)?联网", focused, maxsplit=1)[0]
    focused = re.sub(
        r"^(?:现有的?|中的?|里(?:面)?的?|请|先|再|并|从|查询|检索|搜索|查找|核对|获取)+",
        "",
        focused,
    ).strip(" ，。；：")
    focused = re.split(
        r"(?:，|；|。|\n|并且|然后)?(?:请)?(?:生成|创建|制作|撰写|起草|导出|做成|整理成)"
        r"(?:一份|一个|一张)?\s*(?:Word|DOCX|Excel|XLSX|公文|通知|表格|工作簿|材料)",
        focused,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" ，。；：")
    # “并从零生成……”中的“从零”属于产物动作，不属于公开信息检索词。
    focused = re.sub(r"(?:，|,)?(?:并)?从零$", "", focused).strip(" ，。；：")
    return focused[:800] if len(focused) >= 4 else normalized[:800]
