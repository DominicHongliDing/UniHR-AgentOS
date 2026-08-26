from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from app.domains.assistant.context import PreparedContext
from app.domains.conversations.contracts import AttachmentRecord
from app.domains.task_briefs import (
    ArtifactStyleSpec,
    SpreadsheetStyleSpec,
    TaskBrief,
    TaskKind,
)
from app.runtime.container import get_workbench


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


PROMPT = """请把我刚上传的员工明细表处理成一份“2026年8月人力成本与出勤复核工作簿”，不要覆盖原文件，也不要猜测缺失数据。

保留员工明细，并新增三列：出勤率＝实际出勤天数÷应出勤天数，应出勤为0或缺失时留空；月度应发＝基本工资＋绩效奖金；工号为空、工号重复、关键金额为空、实际出勤大于应出勤时，数据状态标记“需复核”，否则标记“正常”。

工号必须非空且唯一；在职状态只允许“在职、试用、离职、停薪留职”；入职日期必须为真实日期；明细按“月度应发”从高到低排序并开启自动筛选；任何无法执行的要求不要静默忽略，要写进未解决问题或异常清单。

新建“部门汇总”工作表，按部门汇总人数、月度应发合计和月度应发平均值；再生成一张部门月度应发合计的柱状图。

统一样式：微软雅黑；表头白字、深蓝底、加粗、水平垂直居中；正文垂直居中；金额用人民币格式，日期用 yyyy-mm-dd，出勤率用 0.00%；冻结表头并自动调整列宽。

最终返回新的 xlsx 文件，并保留“UniHR处理说明”、执行过的操作、原文件未覆盖证明、异常清单和校验结果。先生成可复核版本，不要描述为已经审批通过的正式工资结论。"""


class RecordingGateway:
    """Keep planner replies in this local QA process so schema failures are reproducible."""

    def __init__(self, delegate: object) -> None:
        self.delegate = delegate
        self.replies: list[dict[str, object]] = []

    def complete(self, messages: list[dict[str, str]], **kwargs: object):
        reply = self.delegate.complete(messages, **kwargs)
        self.replies.append({
            "purpose": kwargs.get("purpose"),
            "model": reply.model,
            "latency_ms": reply.latency_ms,
            "content": reply.content,
        })
        return reply


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay the UniHR spreadsheet runtime without HTTP or LangGraph.")
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    source_path = args.source.resolve()

    source = AttachmentRecord(
        id="att_spreadsheet_qa",
        conversation_id="conv_spreadsheet_qa",
        message_id="msg_spreadsheet_qa",
        display_name=source_path.name,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        extension=".xlsx",
        byte_size=source_path.stat().st_size,
        saved_path=str(source_path),
        created_at="2026-08-07T00:00:00+00:00",
    )
    context = PreparedContext(
        messages=[{"role": "user", "content": PROMPT}],
        attachments=[source],
        attachment_text="",
        usage=[],
        compact_summary="",
    )
    brief = TaskBrief(
        kind=TaskKind.SPREADSHEET,
        objective=PROMPT,
        deliverables=["Excel 工作簿", "处理说明", "校验摘要"],
        approval_required=True,
        mode_spec={
            "goal": "",
            "source_sheet": "",
            "header_row": 1,
            "mapping_notes": "",
            "calculation_rules": "",
            "filter_sort_rules": "",
            "data_validation_rules": "",
            "summary_rules": "",
            "chart_type": "none",
        },
        artifact_style=ArtifactStyleSpec(
            spreadsheet=SpreadsheetStyleSpec(
                header_fill="2563EB",
                horizontal_alignment="left",
                freeze_header=True,
                preserve_existing_style=True,
            )
        ),
    )

    workbench = get_workbench()
    recording_gateway = RecordingGateway(workbench.gateway)
    workbench.gateway = recording_gateway
    started = time.perf_counter()
    try:
        answer, artifacts, trace = workbench._process_spreadsheet(
            PROMPT,
            context,
            source,
            "",
            spreadsheet_style=brief.artifact_style.spreadsheet,
            task_brief=brief,
        )
        metadata = artifacts[0][4] if artifacts else {}
        operation_count = len(metadata.get("operation_results") or [])
        result = {
            "verdict": "PASS" if operation_count > 0 else "FAIL",
            "seconds": round(time.perf_counter() - started, 2),
            "answer": answer,
            "artifact": str(artifacts[0][1]) if artifacts else None,
            "operations": operation_count,
            "changed_cells": metadata.get("changed_cell_count"),
            "exceptions": len(metadata.get("exceptions") or []),
            "validation": metadata.get("validation"),
            "trace": trace,
            "model_replies": recording_gateway.replies,
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0 if operation_count > 0 else 1
    except Exception as exc:
        print(json.dumps({
            "verdict": "FAIL",
            "seconds": round(time.perf_counter() - started, 2),
            "type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }, ensure_ascii=False))
        return 1
    finally:
        workbench.close()


if __name__ == "__main__":
    raise SystemExit(main())
