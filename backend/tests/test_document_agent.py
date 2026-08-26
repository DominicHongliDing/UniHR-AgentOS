import json
import sqlite3

from app.domains.artifacts import DocumentArtifactBuilder
from app.domains.documents import (
    DocumentAgent,
    DocumentAgentRequest,
    DocumentBrief,
    DocumentContentHarness,
    DocumentFact,
    DocumentPlan,
    DocumentSection,
    EvidenceSource,
)
from app.domains.task_briefs import DocumentStyleSpec, TaskBrief
from app.models.gateway import ModelGatewayError, ModelReply
from app.domains.runs import RunPayloadStore, RunPointer
from app.observability import tool_progress_scope
from app.domains.documents.agent import _public_document_title


class RecordingGateway:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return ModelReply(self.replies.pop(0), kwargs.get("model") or "fake", 8, {})


class UnavailableGateway:
    def complete(self, _messages, **_kwargs):
        raise ModelGatewayError("provider_timeout")


def test_public_document_title_extracts_subject_from_natural_instruction():
    title = _public_document_title(
        "帮我写一份8月14日下午三点的部门面试安排通知，"
        "提醒候选人提前十分钟到场，生成 Word。"
    )

    assert title == "8月14日下午三点的部门面试安排通知"


def test_public_document_title_keeps_a_formal_subject_intact():
    title = _public_document_title("关于召开2026年度人事工作会议的通知")

    assert title == "关于召开2026年度人事工作会议的通知"


def test_public_document_title_locks_explicit_quoted_title_from_original_message():
    title = _public_document_title(
        "标题为《关于开展206年新员工入职材料》",
        "请生成正式 Word：标题《关于开展2026年新员工入职材料核验工作的通知》；正文附表。",
    )

    assert title == "关于开展2026年新员工入职材料核验工作的通知"


def _plan(*, excerpt="会议于2026年8月20日举行", source="会议安排.txt"):
    return json.dumps({
        "title": "关于召开人事工作会议的通知",
        "document_type": "通知",
        "addressee": "各部门",
        "lead": "为推进年度人事工作，现将会议安排通知如下。",
        "sections": [{
            "heading": "一、会议时间",
            "paragraphs": ["会议于2026年8月20日举行，请各部门准时参会。"],
        }],
        "closing": "请做好参会准备。",
        "issuer": "人事处",
        "date_text": "2026年8月7日",
        "facts": [
            {
                "key": "meeting_date", "label": "会议日期", "value": "2026年8月20日",
                "source_name": source, "evidence_excerpt": excerpt,
                "required": True, "status": "supported",
            },
            {
                "key": "issue_date", "label": "成文日期", "value": "2026年8月7日",
                "source_name": "用户需求卡", "evidence_excerpt": "2026-08-07",
                "required": True, "status": "supported",
            },
        ],
        "missing_fields": [],
    }, ensure_ascii=False)


def _request():
    return DocumentAgentRequest(
        message="按附件起草会议通知，必须写明会议于2026年8月20日举行。",
        task_brief=TaskBrief.model_validate({
            "kind": "document",
            "objective": "起草会议通知",
            "constraints": ["会议于2026年8月20日举行"],
            "mode_spec": {
                "document_type": "通知",
                "title": "关于召开人事工作会议的通知",
                "addressee": "各部门",
                "issuer": "人事处",
                "date_text": "2026-08-07",
                "required_points": ["会议于2026年8月20日举行"],
                "required_fields": ["title", "issuer", "date_text"],
            },
        }),
        evidence_sources=[EvidenceSource(
            name="会议安排.txt",
            text="会议于2026年8月20日举行，地点为第一会议室。",
        )],
        style=DocumentStyleSpec(body_font="宋体", body_size_pt=14),
        model="fake-document-model",
    )


def test_document_agent_builds_fact_bound_docx_and_graph_trace(tmp_path):
    gateway = RecordingGateway([_plan()])
    agent = DocumentAgent(
        gateway,
        DocumentArtifactBuilder(tmp_path),
        model="fake-document-model",
    )

    progress_events: list[tuple[str, str, dict]] = []
    with tool_progress_scope(
        lambda event, summary, details: progress_events.append((event, summary, details))
    ):
        result = agent.compose(_request())

    assert result.artifact_path.is_file()
    assert result.validation.passed is True
    assert result.plan.facts[0].status == "supported"
    assert result.graph_path == [
        "prepare", "plan", "fact_gate", "compose", "content_harness",
        "layout_harness", "quality_decision", "finalize",
    ]
    assert result.metadata["human_review_required"] is False
    assert result.metadata["review_recommended"] is True
    assert result.metadata["review_policy"] == "draft_delivery"
    assert result.metadata["validation"]["status"] == "warning"
    assert "word_automation_unavailable" in result.metadata["validation"]["warnings"]
    progress_names = [event for event, _summary, _details in progress_events]
    expected = [
        "tool.document.evidence.started",
        "tool.document.evidence.completed",
        "tool.document.plan.started",
        "tool.document.plan.completed",
        "tool.document.fact_check.started",
        "tool.document.fact_check.completed",
        "tool.document.compose.started",
        "tool.document.compose.completed",
        "tool.document.content_harness.started",
        "tool.document.content_harness.completed",
        "tool.document.layout_harness.started",
        "tool.document.layout_harness.completed",
        "tool.document.quality.completed",
        "tool.document.finalize.completed",
    ]
    assert progress_names == expected
    assert progress_events[3][2]["fact_count"] == 5
    assert progress_events[-1][2]["repair_count"] == 0


def test_document_agent_creates_requested_native_word_table(tmp_path):
    payload = json.loads(_plan())
    payload["tables"] = [{
        "title": "待入职人员信息表",
        "headers": ["姓名", "工号", "身份证", "毕业院校", "所属中心"],
        "rows": [],
        "blank_rows": 3,
        "style": "table_grid",
    }]
    request = _request().model_copy(update={
        "message": "生成待入职人员领导确认单，必须创建人员信息表格，包含姓名、工号、身份证、毕业院校、所属中心，并留三行空白。",
    })
    agent = DocumentAgent(
        RecordingGateway([json.dumps(payload, ensure_ascii=False)]),
        DocumentArtifactBuilder(tmp_path),
        model="fake-document-model",
    )

    result = agent.compose(request)

    document = __import__("docx").Document(result.artifact_path)
    assert len(document.tables) == 1
    assert [cell.text for cell in document.tables[0].rows[0].cells] == [
        "姓名", "工号", "身份证", "毕业院校", "所属中心",
    ]
    assert len(document.tables[0].rows) == 4
    assert result.validation.checks["content.required_tables_present"] is True
    assert result.validation.checks["layout.table_structure_valid"] is True


def test_document_agent_locks_explicit_table_schema_blank_rows_issuer_and_date(tmp_path):
    payload = json.loads(_plan())
    payload["title"] = "被模型改写的标题"
    payload["issuer"] = ""
    payload["date_text"] = ""
    payload["tables"] = [{
        "title": "错误表",
        "headers": ["姓名", "工号", "所属部门", "身份证"],
        "rows": [],
        "blank_rows": 3,
    }]
    message = (
        "请生成正式 Word 通知：标题《关于开展2026年材料核验工作的通知》；"
        "落款人事处，成文日期2026年8月12日。正文后附四列表格："
        "姓名、工号、所属部门、材料是否齐全，预留5行。"
    )
    request = DocumentAgentRequest(
        message=message,
        task_brief=TaskBrief(kind="document", objective=message),
        model="fake-document-model",
    )
    agent = DocumentAgent(
        RecordingGateway([json.dumps(payload, ensure_ascii=False)] * 3),
        DocumentArtifactBuilder(tmp_path),
        model="fake-document-model",
    )

    result = agent.compose(request)

    assert result.plan.title == "关于开展2026年材料核验工作的通知"
    assert result.plan.issuer == "人事处"
    assert result.plan.date_text == "2026年8月12日"
    assert result.plan.tables[0].headers == ["姓名", "工号", "所属部门", "材料是否齐全"]
    assert result.plan.tables[0].blank_rows == 5
    document = __import__("docx").Document(result.artifact_path)
    assert len(document.tables[0].rows) == 6


def test_document_agent_sanitizes_internal_action_title_and_seeds_brief_facts(tmp_path):
    request = DocumentAgentRequest(
        message=(
            "做一份待入职人员领导确认单，表格字段为姓名、工号、身份证号、"
            "毕业院校、所属中心，最后留领导签字和日期，输出 Word 草稿"
        ),
        task_brief=TaskBrief.model_validate({
            "kind": "document",
            "objective": "[上下文理解后的本轮动作] 创建一份待入职人员领导确认单 Word 文档",
            "constraints": ["最后留领导签字和日期"],
            "mode_spec": {
                "document_type": "确认单",
                "title": "[上下文理解后的本轮动作] 创建一份待入职人员领导确认单 Word 文档",
                "required_points": ["领导签字位置", "日期"],
                "requires_table": True,
                "required_table_headers": [
                    "姓名", "工号", "身份证号", "毕业院校", "所属中心",
                ],
            },
        }),
        model="fake-document-model",
    )
    polluted = {
        "title": "[上下文理解后的本轮动作] 创建一份待入职人员领导确认单 Word 文档",
        "document_type": "确认单",
        "addressee": "",
        "lead": "请对下列待入职人员信息进行确认。",
        "sections": [{"heading": "确认要求", "paragraphs": ["请核对后签字确认。"]}],
        "tables": [{
            "title": "待入职人员信息表",
            "headers": ["姓名", "工号", "身份证号", "毕业院校", "所属中心"],
            "rows": [],
            "blank_rows": 3,
        }],
        "closing": "领导签字：____________    日期：____年__月__日",
        "issuer": "",
        "date_text": "",
        "facts": [],
        "missing_fields": ["发文单位", "发文日期", "具体人员数据"],
    }
    agent = DocumentAgent(
        RecordingGateway([json.dumps(polluted, ensure_ascii=False)] * 3),
        DocumentArtifactBuilder(tmp_path),
        model="fake-document-model",
    )

    result = agent.compose(request)

    assert result.plan.title == "待入职人员领导确认单"
    ledger_text = "\n".join(f"{fact.label}:{fact.value}" for fact in result.plan.facts)
    assert "待入职人员领导确认" in ledger_text
    assert "姓名、工号、身份证号、毕业院校、所属中心" in ledger_text
    assert "领导签字" in ledger_text and "日期" in ledger_text


def test_document_agent_uses_typed_brief_fallback_when_model_is_unavailable(tmp_path):
    request = DocumentAgentRequest(
        message=(
            "为已确认候选人生成面试确认材料。\n\n"
            "| 姓名 | 原排名 | AI评分 | 岗位相关优势 | 待核实事项 |\n"
            "|---|---:|---:|---|---|\n"
            "| 候选人甲 | 1 | 61.5 | 具有相关实习经历 | 需核实独立拓展能力 |\n"
            "| 候选人丙 | 2 | 26.8 | 具备组织协调经验 | 需补充项目证据 |"
        ),
        task_brief=TaskBrief.model_validate({
            "kind": "document",
            "objective": "为人工确认的候选人生成面试与领导确认材料",
            "constraints": ["只能使用人工确认快照中的事实"],
            "mode_spec": {
                "document_type": "面试与领导确认材料",
                "title": "候选人面试确认材料",
                "addressee": "面试评审人员",
                "issuer": "人事部门",
                "date_text": "2026-08-10",
                "required_points": ["候选人汇总表", "待核实事项", "面试记录与领导签字位置"],
                "requires_table": True,
                "required_table_headers": [
                    "姓名", "原排名", "AI评分", "岗位相关优势", "待核实事项", "人工意见",
                ],
            },
        }),
        model="unavailable-model",
    )
    agent = DocumentAgent(
        UnavailableGateway(),
        DocumentArtifactBuilder(tmp_path),
        model="unavailable-model",
    )

    result = agent.compose(request)

    assert result.artifact_path.is_file()
    assert result.validation.passed is True
    assert result.metadata["document_plan_source"] == "task_brief_fallback"
    assert result.plan.tables[0].headers[-1] == "人工意见"
    assert result.plan.tables[0].rows[0][:3] == ["候选人甲", "1", "61.5"]
    assert len(__import__("docx").Document(result.artifact_path).tables) == 1


def test_document_agent_uses_source_bound_fallback_after_model_repair_is_exhausted(tmp_path):
    message = (
        "请为人工确认的候选人生成面试材料。\n\n"
        "| 姓名 | 原排名 | AI评分 | 岗位相关优势 | 待核实事项 | 人工意见 |\n"
        "|---|---:|---:|---|---|---|\n"
        "| 候选人甲 | 1 | 76.8 | 具有招聘流程经验 | 核实独立拓展能力 | |\n"
        "| 候选人乙 | 2 | 21.5 | 具有活动组织经验 | 核实岗位动机 | |"
    )
    request = DocumentAgentRequest(
        message=message,
        task_brief=TaskBrief.model_validate({
            "kind": "document",
            "objective": "生成人工确认后的候选人面试材料",
            "constraints": ["只能使用人工确认快照中的事实"],
            "mode_spec": {
                "document_type": "面试材料",
                "title": "候选人面试确认材料",
                "issuer": "人事部门",
                "date_text": "2026-08-10",
                "required_points": ["候选人汇总表", "待核实事项"],
                "requires_table": True,
                "required_table_headers": [
                    "姓名", "原排名", "AI评分", "岗位相关优势", "待核实事项", "人工意见",
                ],
            },
        }),
        model="fake-document-model",
    )
    unsupported = {
        "title": "候选人面试确认材料",
        "document_type": "面试材料",
        "addressee": "",
        "lead": "候选人汇总表如下。",
        "sections": [{"heading": "待核实事项", "paragraphs": ["请逐项核实。"]}],
        "tables": [{
            "title": "候选人汇总表",
            "headers": ["姓名", "原排名", "AI评分", "岗位相关优势", "待核实事项", "人工意见"],
            "rows": [["候选人甲", "1", "76.8", "模型改写且不在来源中的经历", "待核实", ""]],
            "blank_rows": 0,
            "column_widths_cm": [2.5, 2.0, 2.0, 6.0, 6.0, 4.0],
        }],
        "closing": "",
        "issuer": "人事部门",
        "date_text": "2026-08-10",
        "facts": [{
            "key": "candidate_strength",
            "label": "岗位相关优势",
            "value": "模型改写且不在来源中的经历",
            "source_name": "用户需求卡",
            "evidence_excerpt": "...",
            "required": True,
            "status": "supported",
        }],
        "missing_fields": [],
    }
    gateway = RecordingGateway([
        json.dumps(unsupported, ensure_ascii=False),
        json.dumps(unsupported, ensure_ascii=False),
        json.dumps(unsupported, ensure_ascii=False),
    ])
    agent = DocumentAgent(
        gateway,
        DocumentArtifactBuilder(tmp_path),
        model="fake-document-model",
    )

    result = agent.compose(request)

    assert result.validation.passed is True
    assert result.metadata["document_plan_source"] == "task_brief_quality_fallback"
    assert "fallback_content" in result.graph_path
    assert result.plan.tables[0].rows[0][:3] == ["候选人甲", "1", "76.8"]
    assert all(fact.status == "supported" for fact in result.plan.facts)


def test_document_agent_repairs_missing_requested_table(tmp_path):
    repaired = json.loads(_plan())
    repaired["tables"] = [{
        "title": "人员信息表",
        "headers": ["姓名", "工号"],
        "rows": [],
        "blank_rows": 2,
    }]
    request = _request().model_copy(update={
        "message": "请生成包含姓名、工号的人员信息表格，并留两行空白。",
    })
    gateway = RecordingGateway([_plan(), json.dumps(repaired, ensure_ascii=False)])
    agent = DocumentAgent(gateway, DocumentArtifactBuilder(tmp_path), model="fake-document-model")

    result = agent.compose(request)

    assert result.repair_count == 1
    assert "repair_content" in result.graph_path
    assert "required_table_missing" in gateway.calls[1][0][1]["content"]
    assert len(__import__("docx").Document(result.artifact_path).tables) == 1


def test_document_agent_only_hard_pauses_when_task_brief_requests_approval(tmp_path):
    gateway = RecordingGateway([_plan()])
    agent = DocumentAgent(gateway, DocumentArtifactBuilder(tmp_path), model="fake-document-model")
    request = _request().model_copy(update={
        "task_brief": _request().task_brief.model_copy(update={"approval_required": True}),
    })

    result = agent.compose(request)

    assert result.metadata["human_review_required"] is True
    assert result.metadata["review_policy"] == "approval_required"


def test_document_agent_offloads_evidence_sources(tmp_path):
    database = tmp_path / "unihr.db"
    store = RunPayloadStore(database)
    pointer = RunPointer(run_id="run_document", tenant_id="tenant_a", owner_user_id="user_a")
    agent = DocumentAgent(
        RecordingGateway([_plan()]),
        DocumentArtifactBuilder(tmp_path),
        model="fake-document-model",
        payload_store=store,
    )

    result = agent.compose(_request(), payload_scope=pointer)

    assert result.validation.passed is True
    assert any(item["event"] == "graph.payload.offloaded" for item in result.trace)
    with sqlite3.connect(database) as db:
        kinds = {
            row[0] for row in db.execute(
                "SELECT kind FROM run_payloads WHERE run_id=?", (pointer.run_id,)
            ).fetchall()
        }
    assert {
        "document.evidence_sources",
        "professional.document.stage.plan",
        "professional.document.stage.fact_validate",
        "professional.document.stage.compose",
        "professional.document.stage.content_harness",
        "professional.document.stage.layout_harness",
        "professional.document.stage.finalize",
    }.issubset(kinds)
    atomic_stages = [
        item["stage_id"] for item in result.trace
        if item["event"] == "document.stage.completed"
    ]
    assert atomic_stages == [
        "plan", "fact_validate", "compose", "content_harness",
        "layout_harness", "finalize",
    ]


def test_document_agent_repairs_only_after_specific_content_failure(tmp_path):
    gateway = RecordingGateway([
        _plan(excerpt="附件中不存在的句子"),
        _plan(),
    ])
    agent = DocumentAgent(
        gateway,
        DocumentArtifactBuilder(tmp_path),
        model="fake-document-model",
    )

    progress_events: list[str] = []
    with tool_progress_scope(
        lambda event, _summary, _details: progress_events.append(event)
    ):
        result = agent.compose(_request())

    assert result.validation.passed is True
    assert result.repair_count == 1
    assert "repair_content" in result.graph_path
    assert gateway.calls[1][1]["purpose"] == "document_plan_repair"
    assert "fact_evidence_unverified" in gateway.calls[1][0][1]["content"]
    assert "tool.document.repair.started" in progress_events
    assert "tool.document.repair.completed" in progress_events
    assert progress_events.count("tool.document.content_harness.started") == 2


def test_document_agent_tells_repair_model_which_invented_value_to_remove(tmp_path):
    unsafe_plan = json.loads(_plan())
    unsafe_plan["sections"][0]["paragraphs"].append("参会人员须提前10分钟签到。")
    gateway = RecordingGateway([
        json.dumps(unsafe_plan, ensure_ascii=False),
        _plan(),
    ])
    agent = DocumentAgent(
        gateway,
        DocumentArtifactBuilder(tmp_path),
        model="fake-document-model",
    )

    result = agent.compose(_request())

    assert result.validation.passed is True
    assert result.repair_count == 1
    assert "remove_or_source_unattributed_value:10分钟" in gateway.calls[1][0][1]["content"]


def test_content_harness_blocks_unattributed_critical_values_when_fact_ledger_exists():
    brief = TaskBrief.model_validate({
        "kind": "document",
        "objective": "起草通知",
        "mode_spec": {"required_points": ["完成材料报送"]},
    })
    plan = DocumentPlan(
        title="材料报送通知",
        lead="请于2026年9月1日前完成材料报送，涉及30人，并提前10分钟签到。",
        sections=[DocumentSection(heading="要求", paragraphs=["完成材料报送。"])],
        facts=[DocumentFact(
            key="deadline", label="截止日期", value="2026年9月1日",
            source_name="用户需求卡", evidence_excerpt="2026年9月1日", required=True,
        )],
    )
    document_brief = DocumentBrief.from_task_brief(
        brief, "请在2026年9月1日前完成材料报送"
    )

    _, report = DocumentContentHarness().validate(
        plan,
        document_brief,
        [EvidenceSource(name="用户需求卡", text="请在2026年9月1日前完成材料报送")],
    )

    assert report.passed is False
    assert "30人" in report.details["unattributed_critical_values"]
    assert "10分钟" in report.details["unattributed_critical_values"]


def test_content_harness_accepts_formal_paraphrases_but_keeps_locked_value_anchors():
    task_brief = TaskBrief.model_validate({
        "kind": "document",
        "objective": "起草会议通知",
        "mode_spec": {
            "required_points": [
                "会议时间为2026年8月20日14时",
                "地点为第一会议室",
                "请于2026年8月18日17时前提交年度工作要点",
            ],
        },
    })
    plan = DocumentPlan(
        title="关于召开年度人事工作会议的通知",
        lead="会议定于2026年8月20日14时召开。",
        sections=[DocumentSection(
            heading="会议安排",
            paragraphs=[
                "本次会议地点设在第一会议室。",
                "各单位须在2026年8月18日17时之前报送年度工作要点。",
            ],
        )],
    )
    brief = DocumentBrief.from_task_brief(task_brief, "起草会议通知")

    _, report = DocumentContentHarness().validate(
        plan,
        brief,
        [EvidenceSource(name="用户需求卡", text=plan.full_text())],
    )

    assert report.checks["required_points_covered"] is True
    assert "required_points_not_covered" not in report.issues


def test_content_harness_resolves_model_source_alias_from_exact_handoff_evidence():
    row = "| 候选人甲 | 1 | 61.5 | 具有相关实习经历 |"
    plan = DocumentPlan(
        title="候选人面试确认材料",
        lead="以下信息用于人工复核。",
        sections=[DocumentSection(heading="候选人信息", paragraphs=[row])],
        facts=[DocumentFact(
            key="candidate_name",
            label="候选人姓名",
            value="候选人甲",
            source_name="用户补充",
            evidence_excerpt=row,
            required=True,
        )],
    )
    brief = DocumentBrief(title="候选人面试确认材料")

    verified, report = DocumentContentHarness().validate(
        plan,
        brief,
        [EvidenceSource(name="用户需求卡", text=f"人工确认快照：\n{row}")],
    )

    assert verified.facts[0].status == "supported"
    assert verified.facts[0].source_name == "用户需求卡"
    assert report.checks["fact_evidence_verified"] is True


def test_document_brief_extracts_arbitrary_explicit_table_headers():
    objective = (
        "先把冲突项目列为“待核验事项”。"
        "Word 中必须有一张原生表格，列为“核对项目、内部政策口径、公开口径、差异、责任中心、核验结论”，"
        "留三行空白。"
    )
    task_brief = TaskBrief.model_validate({
        "kind": "document",
        "objective": objective,
    })

    brief = DocumentBrief.from_task_brief(
        task_brief,
        "起草通知，列为“核对项目、内部政策；后续问题：请按原要求生成”",
    )

    assert brief.required_table_headers == [
        "核对项目", "内部政策口径", "公开口径", "差异", "责任中心", "核验结论",
    ]


def test_fact_evidence_matches_policy_citation_with_page_suffix():
    plan = DocumentPlan(
        title="薪酬通知",
        lead="行政岗位按月发放岗位绩效。",
        facts=[DocumentFact(
            key="pay_rule", label="发放规则", value="按月发放",
            source_name="《行政岗位薪酬方案.pdf》",
            evidence_excerpt="岗位绩效按月发放", required=True,
        )],
    )
    brief = DocumentBrief(title="薪酬通知", purpose="说明发放规则")

    verified, report = DocumentContentHarness().validate(
        plan,
        brief,
        [EvidenceSource(
            name="《行政岗位薪酬方案.pdf》第 2 页 · 发放规则",
            text="岗位绩效按月发放，新入职员工自次月起享受。",
        )],
    )

    assert verified.facts[0].status.value == "supported"
    assert report.checks["fact_evidence_verified"] is True
