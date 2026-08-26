from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domains.assistant.contracts import BusinessIntent, TaskIntent

from .contracts import SkillRecord
from .matching import score_skill_match
from .semantic_selection import (
    SkillCandidateReranker,
    SkillSemanticRetriever,
    rerank_candidate_for,
)


SkillInputKind = Literal[
    "text", "task_brief", "attachment", "existing_result", "knowledge", "artifact", "confirmation",
]
SkillPlanStatus = Literal["ready", "needs_input", "no_match", "rejected"]


_CAPABILITY_INTENTS: dict[str, tuple[BusinessIntent, ...]] = {
    "artifact.create": (BusinessIntent.DOCUMENT_GENERATION, BusinessIntent.SPREADSHEET_PROCESSING),
    "document.compose": (BusinessIntent.DOCUMENT_GENERATION,),
    "evidence.answer": (BusinessIntent.POLICY_QUESTION,),
    "file.extract": (BusinessIntent.RECRUITMENT_SCREENING, BusinessIntent.SPREADSHEET_PROCESSING),
    "image.analyze": (BusinessIntent.IMAGE_UNDERSTANDING,),
    "knowledge.retrieve": (BusinessIntent.POLICY_QUESTION,),
    "recruitment.evaluate": (BusinessIntent.RECRUITMENT_SCREENING,),
    "spreadsheet.inspect": (BusinessIntent.SPREADSHEET_PROCESSING,),
    "spreadsheet.transform": (BusinessIntent.SPREADSHEET_PROCESSING,),
    "weather.current": (BusinessIntent.PUBLIC_CURRENT_INFO,),
    "web.search": (BusinessIntent.PUBLIC_CURRENT_INFO,),
}

# These are the minimum outcome capabilities for each governed intent.  The
# broader _CAPABILITY_INTENTS table describes compatibility; using that table
# as a shopping list made the planner "complete" a simple spreadsheet task by
# adding unrelated file-extraction/recruitment Skills.
_INTENT_REQUIRED_CAPABILITIES: dict[BusinessIntent, set[str]] = {
    BusinessIntent.RECRUITMENT_SCREENING: {
        "file.extract", "recruitment.evaluate", "artifact.create",
    },
    BusinessIntent.SPREADSHEET_PROCESSING: {
        "spreadsheet.transform", "artifact.create",
    },
    BusinessIntent.DOCUMENT_GENERATION: {"document.compose", "artifact.create"},
    BusinessIntent.POLICY_QUESTION: {"knowledge.retrieve", "evidence.answer"},
    BusinessIntent.PUBLIC_CURRENT_INFO: {"web.search"},
    BusinessIntent.IMAGE_UNDERSTANDING: {"image.analyze"},
}

_CAPABILITY_STAGE = {
    "file.extract": 10,
    "knowledge.retrieve": 10,
    "web.search": 10,
    "weather.current": 10,
    "spreadsheet.inspect": 10,
    "image.analyze": 20,
    "recruitment.evaluate": 20,
    "evidence.answer": 20,
    "spreadsheet.transform": 30,
    "document.compose": 30,
    "artifact.create": 40,
    "skill.validate": 40,
    "skill.publish": 50,
}


class SkillInputContract(BaseModel):
    name: str
    description: str = ""
    kind: SkillInputKind = "text"
    required: bool = True


class SkillManifest(BaseModel):
    schema_version: int = 1
    intents: list[BusinessIntent] = Field(default_factory=list)
    provides: list[str] = Field(default_factory=list)
    requires_capabilities: list[str] = Field(default_factory=list)
    inputs: list[SkillInputContract] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    compatible_with: list[str] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "medium"
    human_review_required: bool = True
    max_loop_rounds: int = Field(default=2, ge=1, le=5)


class SkillPlanningRequest(BaseModel):
    query: str
    task_intent: TaskIntent | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    explicit_skill_ids: list[str] = Field(default_factory=list)
    available_input_kinds: set[SkillInputKind] = Field(default_factory=lambda: {"text", "task_brief"})
    max_nodes: int = Field(default=5, ge=1, le=5)


class SkillPlanNode(BaseModel):
    skill_id: str
    skill_version: int
    content_sha256: str = ""
    display_name: str
    score: float
    selection_mode: Literal["explicit", "automatic"]
    matched_trigger: str = ""
    lexical_score: float = 0
    semantic_score: float = 0
    rerank_score: float = 0
    selection_signals: list[str] = Field(default_factory=list)
    provides: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    reason: str
    risk_level: str = "medium"
    human_review_required: bool = True


class SkillExecutionPlan(BaseModel):
    status: SkillPlanStatus
    nodes: list[SkillPlanNode] = Field(default_factory=list)
    covered_capabilities: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    human_review_required: bool = False
    max_loop_rounds: int = 1
    candidate_strategy: str = "deterministic"
    semantic_retrieval_used: bool = False
    rerank_used: bool = False
    fallback_used: bool = False
    semantic_provider: str = ""
    rerank_provider: str = ""


def ensure_orchestration_metadata(
    metadata: dict[str, Any],
    *,
    capabilities: list[str],
    inputs: list[dict[str, Any]] | None = None,
    outputs: list[str] | None = None,
) -> dict[str, Any]:
    """Return metadata with a normalized, backward-compatible orchestration manifest."""
    existing = metadata.get("orchestration")
    raw = dict(existing) if isinstance(existing, dict) else {}
    normalized_inputs = []
    for item in inputs if inputs is not None else metadata.get("inputs") or []:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            continue
        normalized_inputs.append({
            "name": str(item["name"]).strip()[:80],
            "description": str(item.get("description") or "").strip()[:240],
            "kind": str(item.get("kind") or "text"),
            "required": bool(item.get("required", True)),
        })
    provides = list(dict.fromkeys([
        *[str(item).strip() for item in raw.get("provides") or [] if str(item).strip()],
        *[str(item).strip() for item in capabilities if str(item).strip()],
    ]))
    manifest = {
        "schema_version": 1,
        "intents": raw.get("intents") or [item.value for item in infer_intents(provides)],
        "provides": provides,
        "requires_capabilities": list(raw.get("requires_capabilities") or []),
        "inputs": raw.get("inputs") or normalized_inputs,
        "outputs": list(raw.get("outputs") or outputs or metadata.get("outputs") or []),
        "compatible_with": list(raw.get("compatible_with") or []),
        "conflicts_with": list(raw.get("conflicts_with") or []),
        "risk_level": str(raw.get("risk_level") or metadata.get("risk_level") or "medium"),
        "human_review_required": bool(
            raw.get("human_review_required", metadata.get("approval_required", True))
        ),
        "max_loop_rounds": int(raw.get("max_loop_rounds") or 2),
    }
    validated = SkillManifest.model_validate(manifest)
    return {**metadata, "orchestration": validated.model_dump(mode="json")}


def infer_intents(capabilities: list[str]) -> list[BusinessIntent]:
    result: list[BusinessIntent] = []
    for capability in capabilities:
        for intent in _CAPABILITY_INTENTS.get(capability, ()):
            if intent not in result:
                result.append(intent)
    return result


class SkillOrchestrator:
    """Select and validate a version-locked Skill plan behind one interface."""

    def __init__(
        self,
        semantic_retriever: SkillSemanticRetriever | None = None,
        candidate_reranker: SkillCandidateReranker | None = None,
    ) -> None:
        self.semantic_retriever = semantic_retriever
        self.candidate_reranker = candidate_reranker

    def plan(
        self,
        request: SkillPlanningRequest,
        candidates: list[SkillRecord],
    ) -> SkillExecutionPlan:
        visible = {
            skill.id: skill for skill in candidates
            if skill.enabled and skill.status == "active"
        }
        explicit: list[SkillRecord] = []
        warnings: list[str] = []
        semantic_scores: dict[str, float] = {}
        semantic_provider = ""
        rerank_provider = ""
        semantic_retrieval_used = False
        rerank_used = False
        fallback_used = False
        for skill_id in request.explicit_skill_ids:
            skill = visible.get(skill_id)
            if skill is None:
                warnings.append(f"explicit_skill_unavailable:{skill_id}")
                continue
            explicit.append(skill)

        automatic_candidates = [
            skill for skill in visible.values()
            if skill.id not in request.explicit_skill_ids and _auto_selectable(skill)
        ]
        if self.semantic_retriever is not None and automatic_candidates:
            try:
                semantic_batch = self.semantic_retriever.retrieve(
                    request.query,
                    automatic_candidates,
                    task_intent=request.task_intent,
                    required_capabilities=request.required_capabilities,
                    limit=max(request.max_nodes * 3, 10),
                )
                semantic_scores = {item.skill_id: item.score for item in semantic_batch.matches}
                semantic_provider = semantic_batch.provider
                semantic_retrieval_used = not semantic_batch.fallback_used
                fallback_used = semantic_batch.fallback_used
                if semantic_batch.fallback_used:
                    warnings.append("semantic_retrieval_fallback")
            except Exception as exc:
                fallback_used = True
                warnings.append(f"semantic_retrieval_unavailable:{type(exc).__name__}")

        ranked: list[tuple[float, str, SkillRecord, SkillManifest, float, float, float]] = []
        required = set(request.required_capabilities)
        if request.task_intent is not None:
            required.update(_capabilities_for_intent(request.task_intent.domain))
        for skill in automatic_candidates:
            manifest = _manifest_for(skill)
            score, trigger = score_skill_match(skill, request.query)
            semantic = 0.0
            if request.task_intent is not None and request.task_intent.domain in manifest.intents:
                semantic += 30.0
            coverage = required.intersection(manifest.provides)
            semantic += 14.0 * len(coverage)
            output_score = _output_overlap(
                request.task_intent.deliverables if request.task_intent is not None else [],
                manifest.outputs,
            )
            semantic_score = semantic_scores.get(skill.id, 0.0)
            total = score * 2.0 + semantic + output_score * 12.0 + semantic_score * 36.0
            missing_kinds = {
                item.kind for item in manifest.inputs
                if item.required and item.kind not in request.available_input_kinds
            }
            total -= 12.0 * len(missing_kinds)
            if total > 0:
                ranked.append((total, trigger, skill, manifest, score, semantic_score, 0.0))
        ranked.sort(key=lambda item: (-item[0], item[2].display_name.casefold()))

        if self.candidate_reranker is not None and len(ranked) > 1:
            rerank_pool = ranked[:max(request.max_nodes * 3, 10)]
            try:
                rerank_batch = self.candidate_reranker.rerank(
                    request.query,
                    [rerank_candidate_for(item[2], item[0]) for item in rerank_pool],
                    limit=len(rerank_pool),
                )
                rerank_scores = {item.skill_id: item.score for item in rerank_batch.matches}
                rerank_provider = rerank_batch.provider
                rerank_used = not rerank_batch.fallback_used
                fallback_used = fallback_used or rerank_batch.fallback_used
                if rerank_batch.fallback_used:
                    warnings.append("skill_rerank_fallback")
                ranked = [
                    (
                        total + rerank_scores.get(skill.id, 0.0) * 22.0,
                        trigger,
                        skill,
                        manifest,
                        lexical_score,
                        semantic_score,
                        rerank_scores.get(skill.id, 0.0),
                    )
                    for (
                        total, trigger, skill, manifest,
                        lexical_score, semantic_score, _,
                    ) in ranked
                ]
                ranked.sort(key=lambda item: (-item[0], item[2].display_name.casefold()))
            except Exception as exc:
                fallback_used = True
                warnings.append(f"skill_rerank_unavailable:{type(exc).__name__}")

        semantic_intent_override_id = ""
        if request.task_intent is not None and ranked:
            discovery_ranked = sorted(
                ranked,
                key=lambda item: (-(item[5] * 0.6 + item[6] * 0.4), item[2].name),
            )
            discovery_top = discovery_ranked[0]
            contract_candidates = [
                item for item in discovery_ranked
                if request.task_intent.domain in item[3].intents
            ]
            contract_score = (
                contract_candidates[0][5] * 0.6 + contract_candidates[0][6] * 0.4
                if contract_candidates else 0.0
            )
            discovery_score = discovery_top[5] * 0.6 + discovery_top[6] * 0.4
            if (
                request.task_intent.domain not in discovery_top[3].intents
                and discovery_score >= 0.72
                and discovery_score - contract_score >= 0.06
            ):
                semantic_intent_override_id = discovery_top[2].id
                warnings.append(f"semantic_intent_override:{semantic_intent_override_id}")
                required = set(discovery_top[3].provides)
                ranked = [
                    (
                        total + (120.0 if skill.id == semantic_intent_override_id else 0.0),
                        trigger, skill, manifest, lexical_score, semantic_score, rerank_score,
                    )
                    for (
                        total, trigger, skill, manifest,
                        lexical_score, semantic_score, rerank_score,
                    ) in ranked
                ]
                ranked.sort(key=lambda item: (-item[0], item[2].display_name.casefold()))

        selected: list[
            tuple[float, str, str, SkillRecord, SkillManifest, float, float, float]
        ] = []
        covered: set[str] = set()
        for skill in explicit:
            manifest = _manifest_for(skill)
            selected.append((1000.0, "explicit", "explicit", skill, manifest, 0.0, 0.0, 0.0))
            covered.update(manifest.provides)
        selection_required = (
            {
                capability
                for _, _, _, _, manifest, _, _, _ in selected
                for capability in manifest.requires_capabilities
            }.difference(covered)
            if explicit else required
        )
        for score, trigger, skill, manifest, lexical_score, semantic_score, rerank_score in ranked:
            if len(selected) >= request.max_nodes:
                break
            new_capabilities = set(manifest.provides).difference(covered)
            primary_needed = not selected
            explicit_lexical_match = bool(trigger) and not explicit
            semantic_match = semantic_score >= 0.55 and not explicit
            semantic_intent_override = skill.id == semantic_intent_override_id
            covers_required = bool(selection_required.intersection(new_capabilities))
            intent_match = bool(
                request.task_intent is not None
                and request.task_intent.domain in manifest.intents
            )
            if primary_needed:
                # Semantic similarity may discover the primary Skill only when the
                # governed task contract has not already supplied a domain/capability gate.
                unconstrained_request = request.task_intent is None and not required
                governed_match = (
                    intent_match or semantic_intent_override
                    if request.task_intent is not None
                    else covers_required or explicit_lexical_match or semantic_match
                )
                if not (governed_match or (unconstrained_request and semantic_match)):
                    continue
            elif not (
                covers_required
                or (
                    semantic_score >= 0.82
                    and rerank_score >= 0.55
                    and bool(new_capabilities)
                )
            ):
                # Once a primary Skill exists, extra Skills must fill a still-missing
                # required capability. A very-high-confidence cross-domain candidate
                # may expand a composite task, but still passes input/conflict gates.
                continue
            selected.append((
                score, trigger, "automatic", skill, manifest,
                lexical_score, semantic_score, rerank_score,
            ))
            covered.update(manifest.provides)

        if not selected:
            return SkillExecutionPlan(
                status="no_match",
                missing_capabilities=sorted(required),
                warnings=warnings,
                candidate_strategy=_candidate_strategy(
                    semantic_retrieval_used, rerank_used, fallback_used,
                ),
                semantic_retrieval_used=semantic_retrieval_used,
                rerank_used=rerank_used,
                fallback_used=fallback_used,
                semantic_provider=semantic_provider,
                rerank_provider=rerank_provider,
            )

        conflicts = _find_conflicts(selected)
        ordered = _topological_order(selected)
        selected_ids = {item[3].id for item in ordered}
        nodes: list[SkillPlanNode] = []
        missing_inputs: list[str] = []
        for (
            score, trigger, mode, skill, manifest,
            lexical_score, semantic_score, rerank_score,
        ) in ordered:
            required_inputs = [
                item for item in manifest.inputs
                if item.required and item.kind not in request.available_input_kinds
            ]
            missing_inputs.extend(f"{skill.display_name}:{item.name}" for item in required_inputs)
            dependencies = [
                other[3].id for other in ordered
                if other[3].id != skill.id
                and set(other[4].provides).intersection(manifest.requires_capabilities)
            ]
            dependencies = [item for item in dependencies if item in selected_ids]
            reason_parts = [
                "用户显式选择" if mode == "explicit" else "任务意图与能力合同匹配",
            ]
            if trigger and mode != "explicit":
                reason_parts.append(f"命中示例“{trigger}”")
            if semantic_score > 0:
                reason_parts.append(f"语义召回 {semantic_score:.2f}")
            if rerank_score > 0:
                reason_parts.append(f"候选重排 {rerank_score:.2f}")
            contribution = sorted(set(manifest.provides).intersection(required))
            if contribution:
                reason_parts.append("覆盖 " + "、".join(contribution))
            nodes.append(SkillPlanNode(
                skill_id=skill.id,
                skill_version=skill.version,
                content_sha256=str(skill.metadata.get("content_sha256") or ""),
                display_name=skill.display_name,
                score=round(score, 3),
                selection_mode=mode,
                matched_trigger=trigger,
                lexical_score=round(lexical_score, 4),
                semantic_score=round(semantic_score, 4),
                rerank_score=round(rerank_score, 4),
                selection_signals=[
                    signal for signal, enabled in (
                        ("explicit", mode == "explicit"),
                        ("lexical", lexical_score > 0),
                        ("semantic", semantic_score > 0),
                        ("rerank", rerank_score > 0),
                    ) if enabled
                ],
                provides=manifest.provides,
                outputs=manifest.outputs,
                depends_on=list(dict.fromkeys(dependencies)),
                reason="；".join(reason_parts),
                risk_level=manifest.risk_level,
                human_review_required=manifest.human_review_required,
            ))

        covered_required = required.intersection(covered)
        missing_capabilities = sorted(required.difference(covered_required))
        if missing_capabilities:
            warnings.append("uncovered_capabilities:" + ",".join(missing_capabilities))
        status: SkillPlanStatus = "rejected" if conflicts else "needs_input" if missing_inputs else "ready"
        return SkillExecutionPlan(
            status=status,
            nodes=nodes,
            covered_capabilities=sorted(covered),
            missing_capabilities=missing_capabilities,
            missing_inputs=list(dict.fromkeys(missing_inputs)),
            conflicts=conflicts,
            warnings=list(dict.fromkeys(warnings)),
            human_review_required=any(item.human_review_required for item in nodes),
            max_loop_rounds=max(_manifest_for(item[3]).max_loop_rounds for item in ordered),
            candidate_strategy=_candidate_strategy(
                semantic_retrieval_used, rerank_used, fallback_used,
            ),
            semantic_retrieval_used=semantic_retrieval_used,
            rerank_used=rerank_used,
            fallback_used=fallback_used,
            semantic_provider=semantic_provider,
            rerank_provider=rerank_provider,
        )


def _manifest_for(skill: SkillRecord) -> SkillManifest:
    metadata = ensure_orchestration_metadata(
        skill.metadata,
        capabilities=skill.capabilities,
        inputs=skill.metadata.get("inputs") if isinstance(skill.metadata.get("inputs"), list) else None,
        outputs=skill.metadata.get("outputs") if isinstance(skill.metadata.get("outputs"), list) else None,
    )
    return SkillManifest.model_validate(metadata["orchestration"])


def _auto_selectable(skill: SkillRecord) -> bool:
    if skill.source_type == "system":
        return True
    if skill.metadata.get("package_format") != "agent-skills-v1":
        return False
    validation = skill.metadata.get("validation")
    return isinstance(validation, dict) and bool(validation.get("publishable"))


def _capabilities_for_intent(intent: BusinessIntent) -> set[str]:
    return set(_INTENT_REQUIRED_CAPABILITIES.get(intent, set()))


def _output_overlap(deliverables: list[str], outputs: list[str]) -> float:
    if not deliverables or not outputs:
        return 0.0
    left = set("".join(deliverables).casefold())
    right = set("".join(outputs).casefold())
    left.discard(" ")
    right.discard(" ")
    return len(left.intersection(right)) / max(1, len(left))


def _find_conflicts(
    selected: list[tuple[Any, ...]],
) -> list[str]:
    conflicts: list[str] = []
    selected_names = {item[3].name for item in selected}
    selected_ids = {item[3].id for item in selected}
    for item in selected:
        skill = item[3]
        manifest = item[4]
        for target in manifest.conflicts_with:
            if target in selected_names or target in selected_ids:
                conflicts.append(f"{skill.name}<->{target}")
    return sorted(set(conflicts))


def _topological_order(
    selected: list[tuple[Any, ...]],
) -> list[tuple[Any, ...]]:
    by_id = {item[3].id: item for item in selected}
    edges: dict[str, set[str]] = defaultdict(set)
    indegree = {skill_id: 0 for skill_id in by_id}
    for target_id, target in by_id.items():
        needed = set(target[4].requires_capabilities)
        for source_id, source in by_id.items():
            if source_id == target_id or not needed.intersection(source[4].provides):
                continue
            if target_id not in edges[source_id]:
                edges[source_id].add(target_id)
                indegree[target_id] += 1
    queue = deque(sorted(
        (skill_id for skill_id, degree in indegree.items() if degree == 0),
        key=lambda skill_id: (
            0 if by_id[skill_id][2] == "explicit" else 1,
            _stage(by_id[skill_id][4]),
            -by_id[skill_id][0],
        ),
    ))
    ordered_ids: list[str] = []
    while queue:
        skill_id = queue.popleft()
        ordered_ids.append(skill_id)
        for target_id in sorted(edges.get(skill_id, set())):
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                queue.append(target_id)
    if len(ordered_ids) != len(by_id):
        return sorted(selected, key=lambda item: (
            0 if item[2] == "explicit" else 1,
            _stage(item[4]),
            -item[0],
        ))
    return [by_id[skill_id] for skill_id in ordered_ids]


def _stage(manifest: SkillManifest) -> int:
    return min((_CAPABILITY_STAGE.get(item, 25) for item in manifest.provides), default=25)


def _candidate_strategy(
    semantic_retrieval_used: bool,
    rerank_used: bool,
    fallback_used: bool,
) -> str:
    if rerank_used:
        return "hybrid_semantic_rerank"
    if semantic_retrieval_used:
        return "hybrid_semantic"
    if fallback_used:
        return "deterministic_fallback"
    return "deterministic"
