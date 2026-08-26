from app.domains.workbench.stage_contracts import (
    AgentStageStatus,
    compile_professional_stage_states,
)


def test_spreadsheet_stage_compiler_only_records_phases_backed_by_real_events():
    states = compile_professional_stage_states(
        "spreadsheet",
        [
            {
                "event": "artifact.inspected", "status": "completed",
                "summary": "读取两个工作表", "inspection_payload_ref": {"id": "inspect-1"},
            },
            {
                "event": "model.spreadsheet_plan", "status": "completed",
                "summary": "生成受限操作计划",
            },
            {
                "event": "spreadsheet.transform", "status": "completed",
                "summary": "执行三项操作",
            },
            {
                "event": "artifact.validation.completed", "status": "failed",
                "summary": "公式校验失败", "validation_payload_ref": {"id": "validation-1"},
            },
        ],
        artifact_refs=["artifact://result.xlsx"],
    )

    assert [item.stage_id for item in states] == ["inspect", "plan", "execute", "validate"]
    assert states[0].output_refs == ["payload://inspect-1"]
    assert states[2].output_refs == ["artifact://result.xlsx"]
    assert states[3].status == AgentStageStatus.FAILED
    assert states[3].reusable is False
    assert all(item.stage_id != "harness" for item in states)


def test_document_stage_compiler_records_only_persisted_atomic_phases():
    states = compile_professional_stage_states(
        "document",
        [
            {
                "event": "document.stage.completed", "stage_id": "plan",
                "status": "completed", "summary": "planned",
                "output_ref": {"payload_id": "plan-1"},
            },
            {
                "event": "document.stage.completed", "stage_id": "compose",
                "status": "completed", "summary": "composed",
                "output_ref": {"payload_id": "compose-1"},
            },
        ],
        artifact_refs=["artifact://notice.docx"],
    )

    assert [item.stage_id for item in states] == ["plan", "compose"]
    assert states[0].output_refs == ["payload://plan-1"]
    assert states[1].output_refs == ["payload://compose-1", "artifact://notice.docx"]


def test_recruitment_stage_compiler_records_persisted_business_phases():
    states = compile_professional_stage_states(
        "recruitment",
        [
            {
                "event": "recruitment.stage.completed", "stage_id": "prepare",
                "status": "completed", "summary": "parsed",
                "output_ref": {"payload_id": "sources-1"},
            },
            {
                "event": "recruitment.stage.completed", "stage_id": "batch_map",
                "status": "completed", "summary": "evaluated",
                "output_ref": {"payload_id": "batches-1"},
            },
            {
                "event": "recruitment.stage.completed", "stage_id": "artifact_create",
                "status": "completed", "summary": "delivered",
                "output_ref": {"payload_id": "artifact-stage-1"},
            },
        ],
        artifact_refs=["artifact://screening.xlsx"],
    )

    assert [item.stage_id for item in states] == ["prepare", "batch_map", "artifact_create"]
    assert states[0].output_refs == ["payload://sources-1"]
    assert states[1].output_refs == ["payload://batches-1"]
    assert states[2].output_refs == ["payload://artifact-stage-1", "artifact://screening.xlsx"]
