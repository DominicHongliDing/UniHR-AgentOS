from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductCapabilityFact:
    capability_id: str
    status: str
    user_scope: str
    fact: str


PRODUCT_CAPABILITY_FACTS = (
    ProductCapabilityFact(
        "vision.evidence",
        "implemented",
        "authorized_user",
        "图片和扫描 PDF 可转换为带页码、坐标、置信度和待复核标记的结构化证据。",
    ),
    ProductCapabilityFact(
        "vision.fixed_evaluation",
        "implemented",
        "admin",
        "管理员可手动运行固定视觉评测，查看字段准确率、字段召回率、坐标 IoU、风险拦截率和误放行率。",
    ),
    ProductCapabilityFact(
        "knowledge.background_ingestion",
        "implemented",
        "admin",
        "管理员建立知识版本草稿后，入库在后台执行，页面显示任务状态、逐文件进度和当前阶段。",
    ),
    ProductCapabilityFact(
        "knowledge.failure_isolation",
        "implemented",
        "admin",
        "失败文件进入隔离区，不阻塞同批其他文件，普通全量重建会跳过历史失败文件。",
    ),
    ProductCapabilityFact(
        "knowledge.single_file_retry",
        "implemented_manual",
        "admin",
        "管理员可手动选择一份失败文件并只重试这一份；成功后该文件移出失败隔离区。",
    ),
    ProductCapabilityFact(
        "knowledge.automatic_failure_retry",
        "not_implemented",
        "none",
        "当前没有自动定时或无限失败重试；系统不会自行反复重试失败文件。",
    ),
)


def render_product_capability_facts() -> str:
    lines = ["UniHR 已注册产品能力事实（这是唯一可用于说明上线状态的依据）："]
    for item in PRODUCT_CAPABILITY_FACTS:
        lines.append(
            f"- {item.capability_id} | status={item.status} | scope={item.user_scope} | {item.fact}"
        )
    lines.extend((
        "回答约束：",
        "- 只能把 status=implemented 或 implemented_manual 的能力描述为已上线。",
        "- 必须准确区分手动操作与自动执行。",
        "- status=not_implemented 必须明确说尚未实现。",
        "- 清单没有说明的能力或参数，必须说当前能力清单未提供依据，不得按通用经验补写。",
    ))
    return "\n".join(lines)
