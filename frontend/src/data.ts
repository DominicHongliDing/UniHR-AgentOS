import type { Edge, Node } from "@xyflow/react";
import type { Artifact, HRCase, PlannerTask, TraceEvent } from "./types";

export const cases: HRCase[] = [
  {
    id: "HR-2026-0018",
    title: "新入职人员信息台账复核",
    type: "通用表格处理",
    status: "执行中",
    owner: "HR 专员",
    due: "8 月 12 日",
    progress: 68,
    nextAction: "确认 3 条字段异常记录",
    risk: "中",
    artifacts: 4,
    updatedAt: "12 分钟前"
  },
  {
    id: "HR-2026-0016",
    title: "信息学院专任教师招聘简历初筛",
    type: "招聘与甄选",
    status: "等待审批",
    owner: "李老师",
    due: "8 月 9 日",
    progress: 92,
    nextAction: "等待招聘负责人复核筛选标准",
    risk: "低",
    artifacts: 2,
    updatedAt: "1 小时前"
  },
  {
    id: "HR-2026-0014",
    title: "暑期干部培训会议材料",
    type: "会议与公文",
    status: "需要处理",
    owner: "王老师",
    due: "今天",
    progress: 46,
    nextAction: "补充 2026 年培训日程附件",
    risk: "高",
    artifacts: 3,
    updatedAt: "2 小时前"
  },
  {
    id: "HR-2026-0011",
    title: "2026 年博士后进站政策问答汇编",
    type: "政策与知识",
    status: "已完成",
    owner: "陈老师",
    due: "已完成",
    progress: 100,
    nextAction: "已归档",
    risk: "低",
    artifacts: 5,
    updatedAt: "昨天"
  }
];

export const workflowNodes: Node[] = [
  {
    id: "intake",
    position: { x: 20, y: 95 },
    data: { label: "资料识别\n12 个文件" },
    className: "flow-node flow-node--done"
  },
  {
    id: "policy",
    position: { x: 230, y: 20 },
    data: { label: "政策证据\n8 条有效依据" },
    className: "flow-node flow-node--done"
  },
  {
    id: "template",
    position: { x: 230, y: 170 },
    data: { label: "历史模板\n复用 2025 规则" },
    className: "flow-node flow-node--done"
  },
  {
    id: "sheet",
    position: { x: 455, y: 95 },
    data: { label: "表格计算\n正在处理异常" },
    className: "flow-node flow-node--active"
  },
  {
    id: "verify",
    position: { x: 675, y: 20 },
    data: { label: "材料校验\n等待执行" },
    className: "flow-node"
  },
  {
    id: "approval",
    position: { x: 675, y: 170 },
    data: { label: "人工审批\n尚未开始" },
    className: "flow-node"
  }
];

export const workflowEdges: Edge[] = [
  { id: "e1", source: "intake", target: "policy", animated: false },
  { id: "e2", source: "intake", target: "template", animated: false },
  { id: "e3", source: "policy", target: "sheet", animated: false },
  { id: "e4", source: "template", target: "sheet", animated: false },
  { id: "e5", source: "sheet", target: "verify", animated: true },
  { id: "e6", source: "verify", target: "approval", animated: false }
];

export const artifacts: Artifact[] = [
  { name: "新入职人员信息台账.xlsx", kind: "Excel", meta: "326 人 · 8 个工作表 · v3", state: "待复核" },
  { name: "字段异常清单.xlsx", kind: "Excel", meta: "3 条待确认 · v2", state: "待复核" },
  { name: "表格处理说明.docx", kind: "Word", meta: "字段映射 6 处 · v2", state: "已校验" },
  { name: "数据与公式校验报告.pdf", kind: "PDF", meta: "18 页 · 生成于 14:26", state: "已校验" }
];

export const traceEvents: TraceEvent[] = [
  { time: "14:26", title: "生成政策证据包", detail: "8 条引用均处于有效期内", status: "done" },
  { time: "14:24", title: "执行跨表勾稽", detail: "总人数一致；金额差额为 0.00 元", status: "done" },
  { time: "14:22", title: "发现 3 条异常记录", detail: "2 人缺少上年月均工资，1 人超过缴存上限", status: "warning" },
  { time: "14:18", title: "复用 2025 年计算规则", detail: "已分离样式、规则和旧年度数据", status: "done" },
  { time: "14:14", title: "解析新年度人员数据", detail: "326 条记录，主键无重复", status: "done" }
];

export const initialPlannerTasks: PlannerTask[] = [
  {
    id: "todo-001",
    title: "确认 3 条新入职台账字段异常",
    date: "2026-08-05",
    time: "09:30",
    durationMinutes: 30,
    completed: true,
    priority: "important",
    linkType: "case",
    linkId: "HR-2026-0018",
    linkLabel: "表格复核 Case",
    focusSessions: 1
  },
  {
    id: "todo-002",
    title: "核对新入职博士后政策依据",
    date: "2026-08-05",
    time: "10:30",
    durationMinutes: 25,
    completed: false,
    priority: "important",
    linkType: "policy",
    linkLabel: "博士后政策知识库",
    focusSessions: 0
  },
  {
    id: "todo-003",
    title: "回复信息学院教师招聘进度",
    date: "2026-08-05",
    time: "14:00",
    durationMinutes: 20,
    completed: false,
    priority: "normal",
    linkType: "none",
    focusSessions: 0
  },
  {
    id: "todo-004",
    title: "生成暑期干部培训会议议程初稿",
    date: "2026-08-05",
    time: "15:00",
    durationMinutes: 45,
    completed: false,
    priority: "normal",
    linkType: "agent",
    linkId: "HR-2026-0014",
    linkLabel: "会议材料 Agent",
    waitingOn: "agent",
    focusSessions: 0
  },
  {
    id: "todo-005",
    title: "整理明日处务会汇报要点",
    date: "2026-08-05",
    time: null,
    durationMinutes: 25,
    completed: false,
    priority: "normal",
    linkType: "none",
    focusSessions: 0
  },
  {
    id: "todo-006",
    title: "归档本周已完成审批材料",
    date: "2026-08-05",
    time: "16:30",
    durationMinutes: 20,
    completed: true,
    priority: "normal",
    linkType: "case",
    linkId: "HR-2026-0011",
    linkLabel: "博士后政策汇编",
    focusSessions: 1
  }
];
