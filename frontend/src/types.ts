import type { LucideIcon } from "lucide-react";

export type View = "tasks" | "planner" | "case" | "recruitment" | "knowledge" | "skills" | "templates" | "connections" | "approvals" | "quality" | "admin";

export type NavigationItem = {
  id: View;
  label: string;
  icon: LucideIcon;
  badge?: number;
};

export type CaseStatus = "执行中" | "等待审批" | "需要处理" | "已完成";

export type HRCase = {
  id: string;
  title: string;
  type: string;
  status: CaseStatus;
  owner: string;
  due: string;
  progress: number;
  nextAction: string;
  risk: "低" | "中" | "高";
  artifacts: number;
  updatedAt: string;
};

export type Artifact = {
  name: string;
  kind: "Excel" | "Word" | "PDF" | "PowerPoint";
  meta: string;
  state: "已校验" | "待复核" | "生成中";
};

export type TraceEvent = {
  time: string;
  title: string;
  detail: string;
  status: "done" | "active" | "warning";
};

export type PlannerLinkType = "none" | "case" | "policy" | "agent" | "recruitment";

export type PlannerTask = {
  id: string;
  title: string;
  date: string;
  time: string | null;
  durationMinutes: number;
  completed: boolean;
  priority: "normal" | "important";
  linkType: PlannerLinkType;
  linkId?: string;
  linkLabel?: string;
  waitingOn?: "agent" | "approval" | "material" | "external";
  focusSessions: number;
};
