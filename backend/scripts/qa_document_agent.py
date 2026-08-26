from __future__ import annotations

import json
from pathlib import Path

from app.domains.artifacts.documents import DocumentArtifactBuilder, DocumentDraft, DraftSection
from app.domains.documents import WordAutomationVerifier
from app.domains.task_briefs import DocumentStyleSpec


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    output_dir = project_root / "output" / "document-agent-qa-utf8"
    output_dir.mkdir(parents=True, exist_ok=True)
    builder = DocumentArtifactBuilder(output_dir)
    path, metadata = builder.build(
        DocumentDraft(
            title="关于召开2026年度人事工作会议的通知",
            document_type="通知",
            addressee="各部门、各二级学院",
            lead="为系统总结年度人事工作，研究部署下一阶段重点任务，现将有关事项通知如下。",
            sections=[
                DraftSection(
                    "一、会议时间与地点",
                    ["会议时间为2026年8月20日14时，地点为第一会议室。"],
                ),
                DraftSection(
                    "二、参会人员",
                    ["各部门负责人及人事联络员参加会议。"],
                ),
                DraftSection(
                    "三、材料要求",
                    ["请于2026年8月18日17时前提交年度工作要点，并确保数据真实、口径一致。"],
                ),
            ],
            closing="请各单位提前安排工作，按时参会。",
            issuer="人事处",
            date_text="2026年8月7日",
        ),
        style=DocumentStyleSpec(body_font="宋体", body_size_pt=14),
    )
    report = WordAutomationVerifier(output_dir / "previews").verify(path)
    print(json.dumps({
        "path": str(path),
        "metadata": metadata,
        "word_report": report.model_dump(mode="json"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

