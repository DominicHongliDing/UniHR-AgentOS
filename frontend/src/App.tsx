import { lazy, Suspense, useEffect, useRef, useState, type CSSProperties, type FormEvent, type ReactNode } from "react";
import {
  Activity,
  Archive,
  AlertTriangle,
  BarChart3,
  ArrowLeft,
  ArrowRight,
  Bell,
  BookOpen,
  Bot,
  BriefcaseBusiness,
  CalendarClock,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Circle,
  CircleDot,
  Clock3,
  Copy,
  Download,
  FileChartColumn,
  FileCheck2,
  FileSpreadsheet,
  FileText,
  FolderClock,
  Folder,
  FolderPlus,
  Gauge,
  GitBranch,
  Globe2,
  GraduationCap,
  Grid2X2,
  History,
  Inbox,
  LayoutTemplate,
  Link2,
  ListTodo,
  LogOut,
  Menu,
  MessageSquareText,
  MoreHorizontal,
  PanelLeftClose,
  Paperclip,
  Pause,
  Pencil,
  Pin,
  PinOff,
  Play,
  Plus,
  Puzzle,
  RotateCcw,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  SquarePen,
  Timer,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Upload,
  Users,
  WandSparkles,
  X,
  ExternalLink
} from "lucide-react";
import { artifacts, cases, initialPlannerTasks, traceEvents, workflowEdges, workflowNodes } from "./data";
import QualityCenter from "./QualityCenter";
import {
  unihrApi,
  type ApprovalQueueItem,
  type AdminConversationSummary,
  type AdminFeedbackRecord,
  type Conversation,
  type ConversationDetail,
  type ContextOperationsDashboard,
  type ContextRetentionReport,
  type FeedbackStats,
  type FeishuConnectionStatus,
  type FeishuChannelStatus,
  type KnowledgeCatalog,
  type KnowledgeEvaluation,
  type PolicyEvaluationCandidate,
  type KnowledgeEvidence,
  type KnowledgeSourceVersion,
  type KnowledgeVersionComparison,
  type KnowledgeIngestionJob,
  type KnowledgeIngestionFailure,
  type VisionEvaluation,
  type WorkflowCase,
  type Principal,
  type CapabilityOperationsSnapshot,
  type RoutingGoldenReport,
  type ProductHelpRouteEvalReport,
  type RewriteEvaluationReport,
  type RecruitmentCandidateReview,
  type RecruitmentCaseRecord,
  type RecruitmentFolderRecord,
  type RecruitmentInterviewerRecord,
  type RecruitmentInterviewFeedback,
  type RecruitmentInterviewSession,
  type RecruitmentPackagePreview,
  type RecruitmentPackageRecord,
  type RecruitmentPackageType,
  type ResourceWorkspaceManifest,
  type ResourceQueryEvalReport,
  type SkillRecord,
  type SkillDryRunRecord,
  type SkillResourceRecord,
  type SkillVersionRecord,
  type TaskBrief,
  type TurnExecutionEvent,
  type TurnExecutionRecord
} from "./api";

const MarkdownContent = lazy(() => import("./MarkdownContent"));
const WorkflowCanvas = lazy(() => import("./WorkflowCanvas"));

function LazyModuleFallback({ label }: { label: string }) {
  return <div className="lazy-module-loading" role="status"><span />{label}</div>;
}
import type { CaseStatus, HRCase, NavigationItem, PlannerLinkType, PlannerTask, View } from "./types";

type ConversationHistory = {
  id: string;
  title: string;
  preview: string;
  pinned: boolean;
  updatedAt: string;
};

const ACTIVE_CONVERSATION_KEY = "unihr.conversations.active.v1";
const ACTIVE_RECRUITMENT_CASE_KEY = "unihr.recruitment.active.v1";

const toConversationHistory = (item: Conversation): ConversationHistory => ({
  id: item.id,
  title: item.title,
  preview: item.preview,
  pinned: item.pinned,
  updatedAt: new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(item.updated_at))
});

const formatFileBytes = (bytes: number) => bytes < 1024 * 1024
  ? `${Math.max(1, Math.round(bytes / 1024))} KB`
  : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

const graphNodeLabels: Record<string, string> = {
  prepare: "准备任务",
  execute: "执行工具",
  mutate: "修改结果",
  reuse_result: "复用结果",
  clarify: "补充信息",
  no_op: "无需执行",
  quality_gate: "质量检查",
  repair: "自动修复",
  human_review_gate: "人工确认",
  "approval.resume": "确认后继续",
  finalize: "完成交付",
};

const traceEventLabels: Record<string, string> = {
  "skill.graph.started": "启动 Skill 子图",
  "skill.graph.node.started": "开始 Skill 节点",
  "skill.graph.node.completed": "完成 Skill 节点",
  "skill.graph.node.failed": "Skill 节点失败",
  "skill.graph.node.waiting_input": "Skill 节点等待资料",
  "skill.graph.node.reused": "复用 Skill 节点",
  "skill.graph.stage.recorded": "记录专业 Agent 阶段",
  "skill.graph.node.result_offloaded": "外置专业 Agent 节点结果",
  "spreadsheet.stage.graph.started": "启动表格原子阶段图",
  "spreadsheet.stage.completed": "保存表格原子阶段",
  "spreadsheet.stage.reused": "复用表格原子阶段",
  "spreadsheet.stage.graph.reused": "复用表格原子阶段图",
  "skill.graph.handoff": "交接 Skill 结果",
  "skill.graph.recovery.started": "恢复失败节点",
  "skill.graph.recovery.completed": "节点恢复完成",
  "skill.graph.completed": "Skill 子图完成",
  "skill.graph.failed": "Skill 子图停止",
  "skill.semantic_retrieved": "语义召回 Skill",
  "skill.candidates.reranked": "重排 Skill 候选",
  "skill.selection.fallback": "启用 Skill 安全兜底",
  "skill.plan.created": "规划 Skill 组合",
  "skill.plan.validated": "校验 Skill 计划",
  "skill.selected": "选择执行 Skill",
  "task_intent.compiled": "形成 TaskIntent",
  "capability.candidates": "比较候选能力",
  "capability.selected": "选择执行能力",
  "capability.fallback": "启用最后兜底",
  "artifact.inspected": "读取表格结构",
  "model.spreadsheet_plan": "规划表格操作",
  "artifact.plan.created": "锁定操作计划",
  "spreadsheet.transform": "执行表格处理",
  "spreadsheet.repair.started": "分析质量问题",
  "spreadsheet.repair.plan_created": "生成修复计划",
  "spreadsheet.repair.failed": "自动修复未通过",
  "artifact.validation.completed": "校验公式与结构",
  "artifact.harness.completed": "重开并渲染文件",
  "graph.quality_gate": "Graph 质量门",
  "graph.run.completed": "完成本轮运行",
  "graph.node.prepare.started": "准备任务",
  "graph.node.prepare.completed": "准备任务",
  "graph.node.execute.started": "执行工具",
  "graph.node.execute.completed": "执行工具",
  "graph.node.mutate.started": "修改结果",
  "graph.node.mutate.completed": "修改结果",
  "graph.node.reuse_result.started": "复用已有结果",
  "graph.node.reuse_result.completed": "复用已有结果",
  "graph.node.clarify.started": "补充信息",
  "graph.node.clarify.completed": "补充信息",
  "graph.node.no_op.started": "判断任务动作",
  "graph.node.no_op.completed": "判断任务动作",
  "graph.node.quality_gate.started": "质量检查",
  "graph.node.quality_gate.completed": "质量检查",
  "graph.node.repair.started": "自动修复",
  "graph.node.repair.completed": "自动修复",
  "graph.node.human_review_gate.started": "人工确认检查",
  "graph.node.human_review_gate.completed": "人工确认检查",
  "graph.node.await_human_decision.started": "保存确认检查点",
  "graph.node.await_human_decision.completed": "保存确认检查点",
  "graph.node.finalize.started": "整理交付结果",
  "graph.node.finalize.completed": "整理交付结果",
  "tool.spreadsheet.inspect.started": "读取表格结构",
  "tool.spreadsheet.inspect.completed": "读取表格结构",
  "tool.spreadsheet.plan.started": "规划表格操作",
  "tool.spreadsheet.plan.completed": "规划表格操作",
  "tool.spreadsheet.build.started": "生成工作簿",
  "tool.spreadsheet.build.completed": "生成工作簿",
  "tool.spreadsheet.build.waiting": "等待补充建表要求",
  "tool.spreadsheet.build.retrying": "生成安全准备稿",
  "tool.spreadsheet.build.failed": "生成工作簿",
  "tool.artifact.harness.started": "文件 Harness",
  "tool.artifact.harness.completed": "文件 Harness",
  "tool.spreadsheet.recalculate.started": "Excel 公式重算",
  "tool.spreadsheet.recalculate.completed": "Excel 公式重算",
  "tool.spreadsheet.recalculate.failed": "Excel 公式重算",
  "tool.spreadsheet.render.started": "渲染预览",
  "tool.spreadsheet.render.completed": "渲染预览",
  "tool.document.evidence.started": "整理公文依据",
  "tool.document.evidence.completed": "整理公文依据",
  "tool.document.plan.started": "生成公文计划",
  "tool.document.plan.completed": "生成公文计划",
  "tool.document.fact_check.started": "核对事实来源",
  "tool.document.fact_check.completed": "核对事实来源",
  "tool.document.compose.started": "生成 Word 正文",
  "tool.document.compose.completed": "生成 Word 正文",
  "tool.document.content_harness.started": "检查公文内容",
  "tool.document.content_harness.completed": "检查公文内容",
  "tool.document.layout_harness.started": "检查 Word 版式",
  "tool.document.layout_harness.completed": "检查 Word 版式",
  "tool.document.word.started": "Word 重开与导出",
  "tool.document.word.completed": "Word 重开与导出",
  "tool.document.word.failed": "Word 重开与导出",
  "tool.document.render.started": "检查 Word 预览",
  "tool.document.render.completed": "检查 Word 预览",
  "tool.document.quality.completed": "公文质量门",
  "tool.document.repair.started": "定向修订公文",
  "tool.document.repair.completed": "定向修订公文",
  "tool.document.finalize.completed": "固定公文版本",
  "tool.recruitment.parse.started": "解析候选人材料",
  "tool.recruitment.parse.completed": "解析候选人材料",
  "tool.recruitment.parse.failed": "解析候选人材料",
  "tool.recruitment.deduplicate.started": "识别重复材料",
  "tool.recruitment.deduplicate.completed": "识别重复材料",
  "tool.recruitment.version.started": "归并简历版本",
  "tool.recruitment.version.completed": "归并简历版本",
  "tool.recruitment.rubric.started": "设计评分 Rubric",
  "tool.recruitment.rubric.completed": "锁定评分 Rubric",
  "tool.recruitment.batch.started": "准备批量评分",
  "tool.recruitment.batch_item.started": "批次评分",
  "tool.recruitment.batch_item.completed": "批次评分",
  "tool.recruitment.batch.completed": "完成批量评分",
  "tool.recruitment.reduce.started": "合并评分结果",
  "tool.recruitment.reduce.completed": "合并评分结果",
  "tool.recruitment.fairness.started": "公平性检查",
  "tool.recruitment.fairness.completed": "公平性检查",
  "tool.recruitment.evidence.started": "定位简历证据",
  "tool.recruitment.evidence.completed": "定位简历证据",
  "tool.recruitment.harness.started": "招聘 Harness",
  "tool.recruitment.harness.completed": "招聘 Harness",
  "tool.recruitment.quality.completed": "招聘质量门",
  "tool.recruitment.repair.started": "确定性修复",
  "tool.recruitment.repair.completed": "确定性修复",
  "tool.recruitment.report.started": "生成复核报告",
  "tool.recruitment.report.completed": "生成复核报告",
  "tool.recruitment.finalize.completed": "进入人工复核",
  "tool.policy.plan.started": "规划政策证据",
  "tool.policy.plan.completed": "规划政策证据",
  "tool.policy.hybrid_recall.started": "向量与 BM25 混合召回",
  "tool.policy.hybrid_recall.completed": "向量与 BM25 混合召回",
  "tool.policy.rerank.started": "重排候选证据",
  "tool.policy.rerank.completed": "重排候选证据",
  "tool.policy.select.completed": "选择 Top-K 证据",
  "tool.policy.sufficiency.completed": "检查证据覆盖",
  "tool.policy.rewrite.completed": "改写缺失证据查询",
  "tool.policy.temporal.completed": "检查政策有效期",
  "tool.policy.conflict.completed": "检查政策冲突",
  "tool.policy.answer.started": "生成证据化回答",
  "tool.policy.answer.completed": "生成证据化回答",
  "tool.policy.alignment.completed": "核对结论与引用",
  "tool.policy.finalize.completed": "完成政策证据链",
  "tool.public_info.prepare.completed": "准备公开查询",
  "tool.public_info.privacy.completed": "检查公网数据边界",
  "tool.public_info.search.started": "检索公开来源",
  "tool.public_info.search.completed": "检索公开来源",
  "tool.public_info.open.started": "安全打开网页",
  "tool.public_info.open.completed": "安全打开网页",
  "tool.public_info.synthesize.started": "整理公开证据",
  "tool.public_info.synthesize.completed": "整理公开证据",
  "tool.public_info.quality.completed": "联网回答质量门",
  "tool.public_info.rewrite.completed": "改写公开查询",
  "tool.public_info.finalize.completed": "完成联网信息回答",
  "recruitment.sourcing.started": "启动招聘联网寻源",
  "recruitment.sourcing.sources": "整理公开候选线索",
  "recruitment.sourcing.completed": "完成招聘联网寻源",
  "evidence.orchestration.started": "编排任务证据来源",
  "evidence.internal.completed": "载入内部政策证据",
  "evidence.internal.unavailable": "内部知识库暂不可用",
  "evidence.web.completed": "载入公开网页证据",
  "evidence.orchestration.completed": "向业务 Agent 交付证据",
};

const formatGraphNode = (node: string) => graphNodeLabels[node] ?? node;
const formatTraceEvent = (event: unknown) => {
  const value = String(event ?? "执行步骤");
  return traceEventLabels[value] ?? value;
};

const primaryNav: NavigationItem[] = [
  { id: "planner", label: "今日工作", icon: CalendarDays },
  { id: "recruitment", label: "招聘工作台", icon: BriefcaseBusiness },
  { id: "approvals", label: "待我审批", icon: Inbox },
  { id: "knowledge", label: "政策知识库", icon: BookOpen },
  { id: "skills", label: "Skill 中心", icon: Puzzle },
  { id: "templates", label: "模板与经验", icon: LayoutTemplate }
];

const adminNav: NavigationItem = { id: "admin", label: "回答历史与反馈", icon: BarChart3 };
const qualityNav: NavigationItem = { id: "quality", label: "质量运营", icon: Gauge };

const statusTone: Record<CaseStatus, string> = {
  执行中: "blue",
  等待审批: "violet",
  需要处理: "amber",
  已完成: "green"
};

const viewFromHash = (): View => {
  const candidate = window.location.hash.replace("#", "").split("?", 1)[0] as View;
  return ["planner", "case", "recruitment", "knowledge", "skills", "templates", "connections", "approvals", "quality", "admin"].includes(candidate) ? candidate : "tasks";
};

function SessionSplash() {
  return (
    <div className="session-splash" role="status">
      <div className="brand-mark"><GraduationCap size={22} /></div>
      <strong>UniHR AgentOS</strong>
      <span>正在验证安全会话…</span>
    </div>
  );
}

function LoginPage({ onLogin }: { onLogin: (user: Principal) => void }) {
  const [username, setUsername] = useState("hr-user");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const result = await unihrApi.login(username.trim(), password);
      onLogin(result.user);
    } catch {
      setError("账号或密码不正确");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="login-page">
      <section className="login-card">
        <div className="login-brand"><div className="brand-mark"><GraduationCap size={22} /></div><div><strong>UniHR</strong><span>AgentOS</span></div></div>
        <div className="login-copy">
          <p>高校人事 AI 工作台</p>
          <h1>登录你的安全工作区</h1>
          <span>会话、附件、生成文件和个人 Skill 均按账号隔离。</span>
        </div>
        <form onSubmit={submit}>
          <label>账号<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></label>
          <label>密码<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="输入登录密码" /></label>
          {error ? <div className="login-error" role="alert"><AlertTriangle size={15} />{error}</div> : null}
          <button className="primary-button login-submit" disabled={submitting}>{submitting ? "正在登录…" : "登录"}</button>
        </form>
        <div className="login-security"><ShieldCheck size={15} /><span>管理员仅额外拥有回答历史与反馈管理权限；不会进入普通用户的个人对话列表。</span></div>
      </section>
    </main>
  );
}

function App() {
  const [activeView, setActiveView] = useState<View>(viewFromHash);
  const [selectedCase, setSelectedCase] = useState<HRCase>(cases[0]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [currentUser, setCurrentUser] = useState<Principal | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [conversations, setConversations] = useState<ConversationHistory[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [activeConversationScope, setActiveConversationScope] = useState<string | null>(null);
  const [activeRecruitmentCaseId, setActiveRecruitmentCaseId] = useState<string | null>(null);

  useEffect(() => {
    void unihrApi.getMe()
      .then(setCurrentUser)
      .catch(() => setCurrentUser(null))
      .finally(() => setSessionLoading(false));
  }, []);

  useEffect(() => {
    if (!currentUser) {
      setConversations([]);
      return;
    }
    void unihrApi.listConversations()
      .then((items) => setConversations(items.map(toConversationHistory)))
      .catch(() => showToast("后端尚未连接，请确认 UniHR 服务已启动"));
  }, [currentUser?.user_id]);

  useEffect(() => {
    const syncView = () => setActiveView(viewFromHash());
    window.addEventListener("hashchange", syncView);
    return () => window.removeEventListener("hashchange", syncView);
  }, []);

  useEffect(() => {
    if (currentUser && ["admin", "quality"].includes(activeView) && !currentUser.permissions.includes("answer_history.manage")) {
      setActiveView("tasks");
      window.location.hash = "";
    }
    if (currentUser && activeView === "approvals" && !currentUser.permissions.includes("run.approve")) {
      setActiveView("tasks");
      window.location.hash = "";
    }
  }, [activeView, currentUser]);

  useEffect(() => {
    if (!currentUser || activeConversationScope !== currentUser.user_id) return;
    const scopedKey = `${ACTIVE_CONVERSATION_KEY}.${currentUser.user_id}`;
    if (activeConversationId) {
      window.localStorage.setItem(scopedKey, activeConversationId);
    } else {
      window.localStorage.removeItem(scopedKey);
    }
  }, [activeConversationId, activeConversationScope, currentUser]);

  useEffect(() => {
    if (!currentUser) return;
    setActiveConversationId(
      window.localStorage.getItem(`${ACTIVE_CONVERSATION_KEY}.${currentUser.user_id}`),
    );
    setActiveConversationScope(currentUser.user_id);
    setActiveRecruitmentCaseId(
      window.localStorage.getItem(`${ACTIVE_RECRUITMENT_CASE_KEY}.${currentUser.user_id}`),
    );
  }, [currentUser?.user_id]);

  useEffect(() => {
    if (!currentUser) return;
    const key = `${ACTIVE_RECRUITMENT_CASE_KEY}.${currentUser.user_id}`;
    if (activeRecruitmentCaseId) window.localStorage.setItem(key, activeRecruitmentCaseId);
    else window.localStorage.removeItem(key);
  }, [activeRecruitmentCaseId, currentUser?.user_id]);

  const openCase = (item: HRCase) => {
    setSelectedCase(item);
    setActiveView("case");
    window.location.hash = "case";
    setSidebarOpen(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const showToast = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 2400);
  };

  const navigateTo = (view: View) => {
    setActiveView(view);
    window.location.hash = view === "tasks" ? "" : view;
    setSidebarOpen(false);
  };

  const openConversation = (conversationId: string) => {
    setActiveConversationId(conversationId);
    setActiveView("tasks");
    window.location.hash = "";
    setSidebarOpen(false);
  };

  const openRecruitmentCase = (caseId: string) => {
    setActiveRecruitmentCaseId(caseId);
    setActiveView("recruitment");
    window.location.hash = "recruitment";
    setSidebarOpen(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const startNewConversation = () => {
    setActiveConversationId(null);
    setActiveView("tasks");
    window.location.hash = "";
    setSidebarOpen(false);
  };

  const mergeConversation = (conversation: Conversation) => {
    const next = toConversationHistory(conversation);
    setConversations((current) => [next, ...current.filter((item) => item.id !== next.id)]);
    setActiveConversationId(next.id);
  };

  if (sessionLoading) return <SessionSplash />;
  if (!currentUser) {
    return <LoginPage onLogin={(user) => {
      setCurrentUser(user);
      setActiveConversationId(null);
    }} />;
  }

  return (
    <div className="app-shell">
      <Sidebar
        activeView={activeView}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onNavigate={navigateTo}
        conversations={conversations}
        activeConversationId={activeConversationId}
        onNewConversation={startNewConversation}
        onOpenConversation={openConversation}
        onRenameConversation={(id, title) => {
          void unihrApi.updateConversation(id, { title })
            .then((item) => setConversations((current) => current.map((entry) => entry.id === id ? toConversationHistory(item) : entry)))
            .catch((error: Error) => showToast(error.message));
        }}
        onTogglePin={(id) => {
          const currentItem = conversations.find((item) => item.id === id);
          if (!currentItem) return;
          void unihrApi.updateConversation(id, { pinned: !currentItem.pinned })
            .then((item) => setConversations((current) => current.map((entry) => entry.id === id ? toConversationHistory(item) : entry)))
            .catch((error: Error) => showToast(error.message));
        }}
        onDeleteConversation={(id) => {
          void unihrApi.deleteConversation(id)
            .then(() => {
              setConversations((current) => current.filter((item) => item.id !== id));
              if (activeConversationId === id) setActiveConversationId(null);
            })
            .catch((error: Error) => showToast(error.message));
        }}
        user={currentUser}
        onLogout={() => {
          void unihrApi.logout().finally(() => {
            setCurrentUser(null);
            setConversations([]);
            setActiveConversationId(null);
          });
        }}
      />

      <main className="main-shell">
        {activeView !== "tasks" ? (
          <Header
            activeView={activeView}
            onMenu={() => setSidebarOpen(true)}
            onCreate={() => setCreateOpen(true)}
            onNotify={() => showToast("暂时没有新的系统通知")}
          />
        ) : null}

        <div className={`page-canvas ${activeView === "tasks" ? "page-canvas--conversation" : ""}`}>
          {activeView === "tasks" && (
            <TaskCenter
              activeConversation={conversations.find((item) => item.id === activeConversationId) ?? null}
              onTurnComplete={mergeConversation}
              onMenu={() => setSidebarOpen(true)}
              onOpenCase={() => openCase(cases[1] ?? cases[0])}
              onOpenRecruitmentCase={openRecruitmentCase}
              onNavigate={navigateTo}
              user={currentUser}
            />
          )}
          {activeView === "planner" && (
            <DailyPlanner
              storageScope={currentUser.user_id}
              onOpenCase={openCase}
              onOpenRecruitmentCase={openRecruitmentCase}
              onNavigate={navigateTo}
              onAction={showToast}
            />
          )}
          {activeView === "case" && (
            <CaseWorkspace
              item={selectedCase}
              onBack={() => {
                setActiveView("tasks");
                window.location.hash = "";
              }}
              onAction={showToast}
            />
          )}
          {activeView === "recruitment" && (
            <RecruitmentWorkspace
              activeCaseId={activeRecruitmentCaseId}
              onSelectCase={setActiveRecruitmentCaseId}
              onOpenConversation={openConversation}
              onTurnComplete={mergeConversation}
              onBack={() => navigateTo("tasks")}
              user={currentUser}
            />
          )}
          {activeView === "knowledge" && <KnowledgeCenter onBack={() => navigateTo("tasks")} user={currentUser} />}
          {activeView === "skills" && <SkillCenter onBack={() => navigateTo("tasks")} onAction={showToast} user={currentUser} />}
          {activeView === "connections" && <WorkplaceConnections user={currentUser} onBack={() => navigateTo("tasks")} />}
          {activeView === "approvals" && currentUser.permissions.includes("run.approve") && (
            <ApprovalCenter onBack={() => navigateTo("tasks")} onAction={showToast} />
          )}
          {activeView === "admin" && currentUser.permissions.includes("answer_history.manage") && (
            <AdminCenter onBack={() => navigateTo("tasks")} onAction={showToast} />
          )}
          {activeView === "quality" && currentUser.permissions.includes("answer_history.manage") && (
            <QualityCenter onBack={() => navigateTo("tasks")} />
          )}
          {activeView !== "tasks" && activeView !== "planner" && activeView !== "case" && activeView !== "recruitment" && activeView !== "knowledge" && activeView !== "skills" && activeView !== "connections" && activeView !== "approvals" && activeView !== "quality" && activeView !== "admin" && (
            <ComingSoon view={activeView} onBack={() => navigateTo("tasks")} />
          )}
        </div>
      </main>

      {createOpen && (
        <CreateTaskDialog
          onClose={() => setCreateOpen(false)}
          onSubmit={(title) => {
            setCreateOpen(false);
            showToast(`已创建任务草稿：${title}`);
          }}
        />
      )}

      <div className={`toast ${toast ? "toast--visible" : ""}`} role="status" aria-live="polite">
        <CheckCircle2 size={17} aria-hidden="true" />
        {toast}
      </div>
    </div>
  );
}

function Sidebar({
  activeView,
  open,
  onClose,
  onNavigate,
  conversations,
  activeConversationId,
  onNewConversation,
  onOpenConversation,
  onRenameConversation,
  onTogglePin,
  onDeleteConversation,
  user,
  onLogout
}: {
  activeView: View;
  open: boolean;
  onClose: () => void;
  onNavigate: (view: View) => void;
  conversations: ConversationHistory[];
  activeConversationId: string | null;
  onNewConversation: () => void;
  onOpenConversation: (id: string) => void;
  onRenameConversation: (id: string, title: string) => void;
  onTogglePin: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  user: Principal;
  onLogout: () => void;
}) {
  const [menuId, setMenuId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const pinned = conversations.filter((item) => item.pinned);
  const recent = conversations.filter((item) => !item.pinned);

  const beginRename = (conversation: ConversationHistory) => {
    setMenuId(null);
    setRenamingId(conversation.id);
    setRenameValue(conversation.title);
  };

  const finishRename = () => {
    const cleanTitle = renameValue.trim();
    if (renamingId && cleanTitle) onRenameConversation(renamingId, cleanTitle);
    setRenamingId(null);
  };

  const renderHistoryGroup = (label: string, items: ConversationHistory[]) => items.length ? (
    <section className="conversation-history-group" aria-label={label}>
      <p className="conversation-history-label">{label}</p>
      {items.map((item) => (
        <div className={`conversation-history-row ${activeConversationId === item.id && activeView === "tasks" ? "conversation-history-row--active" : ""}`} key={item.id}>
          {renamingId === item.id ? (
            <input
              className="conversation-rename-input"
              value={renameValue}
              onChange={(event) => setRenameValue(event.target.value)}
              onBlur={finishRename}
              onKeyDown={(event) => {
                if (event.key === "Enter") finishRename();
                if (event.key === "Escape") setRenamingId(null);
              }}
              autoFocus
              aria-label="修改对话名称"
            />
          ) : (
            <button className="conversation-history-main" onClick={() => onOpenConversation(item.id)} title={item.title}>
              <MessageSquareText size={16} aria-hidden="true" />
              <span>{item.title}</span>
            </button>
          )}
          <button
            className="conversation-menu-button"
            onClick={() => setMenuId(menuId === item.id ? null : item.id)}
            aria-label={`管理对话：${item.title}`}
            aria-expanded={menuId === item.id}
          >
            <MoreHorizontal size={16} />
          </button>
          {menuId === item.id ? (
            <div className="conversation-menu" role="menu">
              <button onClick={() => { onTogglePin(item.id); setMenuId(null); }} role="menuitem">
                {item.pinned ? <PinOff size={15} /> : <Pin size={15} />}
                {item.pinned ? "取消置顶" : "置顶"}
              </button>
              <button onClick={() => beginRename(item)} role="menuitem"><Pencil size={15} />重命名</button>
              <button
                className="conversation-menu-danger"
                onClick={() => {
                  if (window.confirm(`删除对话“${item.title}”？删除后无法恢复。`)) onDeleteConversation(item.id);
                  setMenuId(null);
                }}
                role="menuitem"
              >
                <Trash2 size={15} />删除
              </button>
            </div>
          ) : null}
        </div>
      ))}
    </section>
  ) : null;

  return (
    <>
      <button
        className={`mobile-scrim ${open ? "mobile-scrim--open" : ""}`}
        onClick={onClose}
        aria-label="关闭导航"
      />
      <aside className={`sidebar ${open ? "sidebar--open" : ""}`}>
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <GraduationCap size={21} />
          </div>
          <div>
            <strong>UniHR</strong>
            <span>AgentOS</span>
          </div>
          <button className="icon-button sidebar-close" onClick={onClose} aria-label="关闭导航">
            <PanelLeftClose size={19} />
          </button>
        </div>

        <nav className="navigation" aria-label="主导航">
          <button className="new-conversation-button" onClick={onNewConversation}>
            <SquarePen size={18} aria-hidden="true" />
            <span>新建对话</span>
          </button>
          <button className={`nav-item ${activeView === "tasks" ? "nav-item--active" : ""}`} onClick={onNewConversation}>
            <MessageSquareText size={18} aria-hidden="true" />
            <span>对话</span>
          </button>
          {[
            ...primaryNav.filter((item) => item.id !== "approvals" || user.permissions.includes("run.approve")),
            ...(user.permissions.includes("answer_history.manage") ? [qualityNav, adminNav] : [])
          ].map((item) => {
            const Icon = item.icon;
            const active = item.id === activeView;
            return (
              <button
                key={item.id}
                className={`nav-item ${active ? "nav-item--active" : ""}`}
                onClick={() => onNavigate(item.id)}
              >
                <Icon size={18} aria-hidden="true" />
                <span>{item.label}</span>
                {item.badge ? <span className="nav-badge">{item.badge}</span> : null}
              </button>
            );
          })}
        </nav>

        <div className="conversation-history" onClick={(event) => {
          if (event.target === event.currentTarget) setMenuId(null);
        }}>
          {renderHistoryGroup("置顶", pinned)}
          {renderHistoryGroup("最近对话", recent)}
          {!conversations.length ? (
            <div className="conversation-history-empty">对话会显示在这里</div>
          ) : null}
        </div>

        <div className="sidebar-footer">
          <button className={`nav-item ${activeView === "connections" ? "nav-item--active" : ""}`} onClick={() => onNavigate("connections")}>
            <Settings size={18} aria-hidden="true" />
            <span>系统设置</span>
          </button>
          <div className="user-card">
            <div className="avatar">{user.display_name.slice(0, 1)}</div>
            <div>
              <strong>{user.display_name}</strong>
              <span>{user.role === "admin" ? "管理员 · 回答历史管理" : "普通用户 · 个人工作区"}</span>
            </div>
            <button className="user-logout-button" onClick={onLogout} aria-label="退出登录" title="退出登录"><LogOut size={17} /></button>
          </div>
        </div>
      </aside>
    </>
  );
}

function Header({
  activeView,
  onMenu,
  onCreate,
  onNotify
}: {
  activeView: View;
  onMenu: () => void;
  onCreate: () => void;
  onNotify: () => void;
}) {
  const pageTitle: Record<View, string> = {
    tasks: "任务中心",
    planner: "今日工作",
    case: "任务工作台",
    recruitment: "招聘工作台",
    approvals: "待我审批",
    knowledge: "政策知识库",
    skills: "Skill 中心",
    templates: "模板与经验",
    connections: "办公连接",
    quality: "质量运营中心",
    admin: "回答历史与反馈"
  };

  return (
    <header className="topbar">
      <div className="topbar-title">
        <button className="icon-button mobile-menu" onClick={onMenu} aria-label="打开导航">
          <Menu size={20} />
        </button>
        <span>{pageTitle[activeView]}</span>
      </div>
      <div className="topbar-actions">
        <label className="global-search">
          <Search size={17} aria-hidden="true" />
          <span className="sr-only">搜索任务、政策或材料</span>
          <input placeholder="搜索任务、政策或材料" />
          <kbd>⌘ K</kbd>
        </label>
        <button className="icon-button notification-button" onClick={onNotify} aria-label="系统通知">
          <Bell size={19} />
          <span />
        </button>
        <button className="primary-button compact" onClick={onCreate}>
          <Plus size={17} />
          新建任务
        </button>
      </div>
    </header>
  );
}

const PLANNER_TASKS_KEY = "unihr.planner.tasks.v1";
const PLANNER_NOTE_PREFIX = "unihr.planner.note";
const PLANNER_DEMO_DATE = "2026-08-05";
const PLANNER_DEFAULT_NOTE = "今天优先完成公积金异常确认；下午把会议议程初稿交给王老师复核。\n\n明日提醒：核对新入职人员材料清单。";
const plannerWeekdayLabels = ["一", "二", "三", "四", "五", "六", "日"];
const plannerYearOptions = Array.from({ length: 31 }, (_, index) => 2015 + index);

function formatDateKey(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatDocumentValidationIssue(issue: string) {
  const labels: Record<string, string> = {
    document_title_missing: "缺少标题",
    document_body_missing: "缺少正文",
    required_points_not_covered: "必写要点尚未全部覆盖",
    prohibited_content_present: "出现了禁止内容",
    fact_evidence_unverified: "部分事实未能回查原文",
    critical_values_without_source: "日期、金额或数量存在无来源项",
    fact_ledger_missing_for_critical_values: "旧草稿缺少结构化事实台账",
    document_contains_visible_placeholders: "仍含待补充占位符",
    word_automation_unavailable: "当前机器未启用 Word 渲染验证"
  };
  if (issue.startsWith("missing_required_fact:")) return `待补充：${issue.split(":").slice(1).join(":")}`;
  return labels[issue] ?? issue;
}

function formatRecruitmentLocator(locator: Record<string, unknown>) {
  if (String(locator.status ?? "unlocated") === "unlocated") return "尚未定位到连续原文";
  const parts = [String(locator.source_name ?? "")].filter(Boolean);
  if (Number(locator.page_number ?? 0) > 0) parts.push(`第 ${Number(locator.page_number)} 页`);
  if (Number(locator.paragraph_number ?? 0) > 0) parts.push(`段落 ${Number(locator.paragraph_number)}`);
  if (Number(locator.table_number ?? 0) > 0) parts.push(`表格 ${Number(locator.table_number)}`);
  if (String(locator.sheet_name ?? "")) parts.push(`Sheet ${String(locator.sheet_name)}`);
  const bbox = Array.isArray(locator.bbox) ? locator.bbox : [];
  if (bbox.length === 4) parts.push(`坐标 ${bbox.join(",")}`);
  return parts.join(" · ") || "已定位原文";
}

function parseDateKey(dateKey: string) {
  const [year, month, day] = dateKey.split("-").map(Number);
  return new Date(year, month - 1, day, 12);
}

function moveDate(date: Date, days: number) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function startOfPlannerWeek(date: Date) {
  const mondayOffset = (date.getDay() + 6) % 7;
  return moveDate(date, -mondayOffset);
}

function getPlannerWeekNumber(date: Date) {
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 12);
  const dayNumber = (target.getDay() + 6) % 7;
  target.setDate(target.getDate() - dayNumber + 3);
  const firstThursday = new Date(target.getFullYear(), 0, 4, 12);
  const firstDayNumber = (firstThursday.getDay() + 6) % 7;
  firstThursday.setDate(firstThursday.getDate() - firstDayNumber + 3);
  return 1 + Math.round((target.getTime() - firstThursday.getTime()) / 604800000);
}

function getPlannerNote(storageScope: string, dateKey: string) {
  return window.localStorage.getItem(`${PLANNER_NOTE_PREFIX}.${storageScope}.${dateKey}`)
    ?? (dateKey === PLANNER_DEMO_DATE ? PLANNER_DEFAULT_NOTE : "");
}

function getPlannerLinkOptions(linkType: PlannerLinkType) {
  if (linkType === "case") return cases.map((item) => ({ id: item.id, label: `${item.title} · ${item.id}` }));
  if (linkType === "policy") return [
    { id: "policy-postdoc", label: "博士后管理政策库" },
    { id: "policy-provident-fund", label: "住房公积金政策库" },
    { id: "policy-recruitment", label: "教师招聘政策库" }
  ];
  if (linkType === "agent") return cases.map((item) => ({ id: item.id, label: `${item.title} Agent` }));
  return [];
}

function DailyPlanner({
  storageScope,
  onOpenCase,
  onOpenRecruitmentCase,
  onNavigate,
  onAction
}: {
  storageScope: string;
  onOpenCase: (item: HRCase) => void;
  onOpenRecruitmentCase: (caseId: string) => void;
  onNavigate: (view: View) => void;
  onAction: (message: string) => void;
}) {
  const [selectedDate, setSelectedDate] = useState(() => formatDateKey(new Date()));
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [calendarCursor, setCalendarCursor] = useState(() => parseDateKey(formatDateKey(new Date())));
  const [tasks, setTasks] = useState<PlannerTask[]>(() => {
    try {
      const saved = window.localStorage.getItem(`${PLANNER_TASKS_KEY}.${storageScope}`);
      return saved ? (JSON.parse(saved) as PlannerTask[]) : initialPlannerTasks;
    } catch {
      return initialPlannerTasks;
    }
  });
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [newLinkType, setNewLinkType] = useState<PlannerLinkType>("none");
  const [newLinkTarget, setNewLinkTarget] = useState("");
  const [taskFilter, setTaskFilter] = useState<"all" | "pending" | "completed">("all");
  const [dailyNote, setDailyNote] = useState(() => getPlannerNote(storageScope, formatDateKey(new Date())));
  const [activeTaskId, setActiveTaskId] = useState("todo-002");
  const [secondsLeft, setSecondsLeft] = useState(25 * 60);
  const [timerRunning, setTimerRunning] = useState(false);
  const [openTaskMenuId, setOpenTaskMenuId] = useState<string | null>(null);
  const [deletedTask, setDeletedTask] = useState<{ task: PlannerTask; index: number } | null>(null);
  const [editingTask, setEditingTask] = useState<PlannerTask | null>(null);

  useEffect(() => {
    window.localStorage.setItem(`${PLANNER_TASKS_KEY}.${storageScope}`, JSON.stringify(tasks));
  }, [storageScope, tasks]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setCalendarOpen(false);
      setOpenTaskMenuId(null);
      setEditingTask(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (!timerRunning) return;
    const timer = window.setInterval(() => {
      setSecondsLeft((current) => Math.max(0, current - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [timerRunning]);

  useEffect(() => {
    if (secondsLeft !== 0 || !timerRunning) return;
    setTimerRunning(false);
    setTasks((current) => current.map((task) => task.id === activeTaskId ? { ...task, focusSessions: task.focusSessions + 1 } : task));
    onAction("一个专注时段已完成，已记录到今日工作");
  }, [activeTaskId, onAction, secondsLeft, timerRunning]);

  const visibleTasks = tasks
    .filter((task) => task.date === selectedDate)
    .sort((a, b) => (a.time ?? "99:99").localeCompare(b.time ?? "99:99"));
  const filteredTasks = visibleTasks.filter((task) => taskFilter === "all" || (taskFilter === "completed" ? task.completed : !task.completed));
  const completedCount = visibleTasks.filter((task) => task.completed).length;
  const plannedMinutes = visibleTasks.reduce((total, task) => total + task.durationMinutes, 0);
  const focusMinutes = visibleTasks.reduce((total, task) => total + task.focusSessions * 25, 0);
  const activeTask = visibleTasks.find((task) => task.id === activeTaskId) ?? visibleTasks.find((task) => !task.completed) ?? visibleTasks[0];
  const timerText = `${String(Math.floor(secondsLeft / 60)).padStart(2, "0")}:${String(secondsLeft % 60).padStart(2, "0")}`;
  const timerProgress = ((25 * 60 - secondsLeft) / (25 * 60)) * 360;
  const selectedDateValue = parseDateKey(selectedDate);
  const weekStart = startOfPlannerWeek(selectedDateValue);
  const plannerWeek = Array.from({ length: 7 }, (_, index) => {
    const date = moveDate(weekStart, index);
    const dateKey = formatDateKey(date);
    const taskCount = tasks.filter((task) => task.date === dateKey).length;
    return {
      date: dateKey,
      weekday: `周${plannerWeekdayLabels[index]}`,
      day: String(date.getDate()),
      count: taskCount
    };
  });
  const calendarMonthStart = new Date(calendarCursor.getFullYear(), calendarCursor.getMonth(), 1, 12);
  const calendarGridStart = startOfPlannerWeek(calendarMonthStart);
  const calendarDays = Array.from({ length: 42 }, (_, index) => moveDate(calendarGridStart, index));
  const todayKey = formatDateKey(new Date());
  const newLinkOptions = getPlannerLinkOptions(newLinkType);
  const editingLinkOptions = getPlannerLinkOptions(editingTask?.linkType ?? "none");
  const suggestedTask = visibleTasks.find((task) => !task.completed && !task.waitingOn) ?? visibleTasks.find((task) => !task.completed);

  const chooseDate = (date: Date | string, closeCalendar = true) => {
    const dateKey = typeof date === "string" ? date : formatDateKey(date);
    window.localStorage.setItem(`${PLANNER_NOTE_PREFIX}.${storageScope}.${selectedDate}`, dailyNote);
    setSelectedDate(dateKey);
    const nextDate = parseDateKey(dateKey);
    setCalendarCursor(new Date(nextDate.getFullYear(), nextDate.getMonth(), 1, 12));
    setDailyNote(getPlannerNote(storageScope, dateKey));
    const nextTask = tasks.find((task) => task.date === dateKey && !task.completed);
    setActiveTaskId(nextTask?.id ?? "");
    setTaskFilter("all");
    if (closeCalendar) setCalendarOpen(false);
  };

  const changeCalendarMonth = (monthOffset: number) => {
    setCalendarCursor((current) => new Date(current.getFullYear(), current.getMonth() + monthOffset, 1, 12));
  };

  const addTask = (event: FormEvent) => {
    event.preventDefault();
    const title = newTaskTitle.trim();
    if (!title) return;
    const selectedLink = newLinkOptions.find((option) => option.id === newLinkTarget) ?? newLinkOptions[0];
    const linkConfig = newLinkType === "none" ? {} : { linkId: selectedLink?.id, linkLabel: selectedLink?.label };
    const task: PlannerTask = {
      id: `todo-${crypto.randomUUID()}`,
      title,
      date: selectedDate,
      time: null,
      durationMinutes: 25,
      completed: false,
      priority: "normal",
      linkType: newLinkType,
      focusSessions: 0,
      ...linkConfig
    };
    setTasks((current) => [...current, task]);
    setNewTaskTitle("");
    setNewLinkType("none");
    setNewLinkTarget("");
    onAction("待办已加入今日工作");
  };

  const toggleTask = (taskId: string) => {
    setTasks((current) => current.map((task) => task.id === taskId ? { ...task, completed: !task.completed } : task));
  };

  const deleteTask = (task: PlannerTask) => {
    const taskIndex = tasks.findIndex((item) => item.id === task.id);
    if (taskIndex < 0) return;
    setDeletedTask({ task, index: taskIndex });
    setTasks((current) => current.filter((item) => item.id !== task.id));
    setOpenTaskMenuId(null);
    if (activeTaskId === task.id) {
      const replacement = tasks.find((item) => item.id !== task.id && item.date === selectedDate && !item.completed);
      setActiveTaskId(replacement?.id ?? "");
      setTimerRunning(false);
      setSecondsLeft(25 * 60);
    }
    onAction("待办已删除，可在列表中撤销");
  };

  const undoDeleteTask = () => {
    if (!deletedTask) return;
    setTasks((current) => {
      const restored = [...current];
      restored.splice(Math.min(deletedTask.index, restored.length), 0, deletedTask.task);
      return restored;
    });
    setDeletedTask(null);
    onAction("已恢复待办");
  };

  const duplicateTaskToTomorrow = (task: PlannerTask) => {
    const tomorrow = formatDateKey(moveDate(parseDateKey(task.date), 1));
    const duplicate: PlannerTask = {
      ...task,
      id: `todo-${crypto.randomUUID()}`,
      date: tomorrow,
      completed: false,
      focusSessions: 0
    };
    setTasks((current) => [...current, duplicate]);
    setOpenTaskMenuId(null);
    onAction(`已复制到 ${parseDateKey(tomorrow).getMonth() + 1} 月 ${parseDateKey(tomorrow).getDate()} 日`);
  };

  const saveEditedTask = (event: FormEvent) => {
    event.preventDefault();
    if (!editingTask?.title.trim()) return;
    const selectedLink = editingTask.linkType === "recruitment"
      ? { id: editingTask.linkId, label: editingTask.linkLabel }
      : editingLinkOptions.find((option) => option.id === editingTask.linkId) ?? editingLinkOptions[0];
    const updatedTask: PlannerTask = {
      ...editingTask,
      title: editingTask.title.trim(),
      durationMinutes: Math.max(5, editingTask.durationMinutes),
      linkId: editingTask.linkType === "none" ? undefined : selectedLink?.id,
      linkLabel: editingTask.linkType === "none" ? undefined : selectedLink?.label
    };
    setTasks((current) => current.map((task) => task.id === updatedTask.id ? updatedTask : task));
    setEditingTask(null);
    onAction("待办信息已更新");
  };

  const followTaskLink = (task: PlannerTask) => {
    if (task.linkType === "case" && task.linkId) {
      const linkedCase = cases.find((item) => item.id === task.linkId);
      if (linkedCase) onOpenCase(linkedCase);
      return;
    }
    if (task.linkType === "recruitment" && task.linkId) {
      onOpenRecruitmentCase(task.linkId);
      onAction("已打开关联招聘 Case");
      return;
    }
    if (task.linkType === "policy") {
      onNavigate("knowledge");
      onAction("已打开与待办关联的政策知识库");
      return;
    }
    if (task.linkType === "agent") {
      const linkedCase = cases.find((item) => item.id === task.linkId) ?? cases[2];
      onOpenCase(linkedCase);
      onAction("已进入关联 Case，可从当前节点继续 Agent");
    }
  };

  const startFocusFor = (task: PlannerTask) => {
    setActiveTaskId(task.id);
    setSecondsLeft(25 * 60);
    setTimerRunning(true);
    onAction(`已开始专注：${task.title}`);
  };

  return (
    <div className="daily-planner">
      <section className="planner-heading">
        <div>
          <p className="eyebrow">我的工作节奏</p>
          <h1>今日工作</h1>
          <p>把零散事项、正式 Case 和 Agent 执行集中到一天的时间安排中。</p>
        </div>
        <div className="planner-heading-actions">
          <button className="secondary-button" onClick={() => { chooseDate(new Date()); onAction("已回到今天"); }}><CalendarDays size={16} /> 今天</button>
          <button className="primary-button" onClick={() => document.getElementById("quick-add-task")?.focus()}><Plus size={16} /> 添加待办</button>
        </div>
      </section>

      <section className="week-strip card" aria-label="本周日历">
        <button className="icon-button week-prev-button" aria-label="上一周" onClick={() => chooseDate(moveDate(selectedDateValue, -7), false)}><ChevronLeft size={18} /></button>
        <button className="week-month" aria-label="选择年月和日期" aria-expanded={calendarOpen} aria-haspopup="dialog" onClick={() => setCalendarOpen((current) => !current)}>
          <strong>{selectedDateValue.getFullYear()} 年 {selectedDateValue.getMonth() + 1} 月 <ChevronDown size={15} /></strong>
          <span>第 {getPlannerWeekNumber(selectedDateValue)} 周 · 点击选择日期</span>
        </button>
        <div className="week-days">
          {plannerWeek.map((day) => (
            <button key={day.date} className={`week-day ${selectedDate === day.date ? "week-day--active" : ""}`} onClick={() => chooseDate(day.date, false)}>
              <span>{day.weekday}</span><strong>{day.day}</strong>{day.count ? <small>{day.count}</small> : <i />}
            </button>
          ))}
        </div>
        <button className="icon-button week-next-button" aria-label="下一周" onClick={() => chooseDate(moveDate(selectedDateValue, 7), false)}><ChevronRight size={18} /></button>

        {calendarOpen ? (
          <div className="calendar-popover" role="dialog" aria-label="选择日期">
            <div className="calendar-popover-header">
              <button className="icon-button" aria-label="上一个月" onClick={() => changeCalendarMonth(-1)}><ChevronLeft size={18} /></button>
              <div className="calendar-selectors">
                <label><span className="sr-only">选择年份</span><select aria-label="选择年份" value={calendarCursor.getFullYear()} onChange={(event) => setCalendarCursor(new Date(Number(event.target.value), calendarCursor.getMonth(), 1, 12))}>{plannerYearOptions.map((year) => <option key={year} value={year}>{year} 年</option>)}</select></label>
                <label><span className="sr-only">选择月份</span><select aria-label="选择月份" value={calendarCursor.getMonth()} onChange={(event) => setCalendarCursor(new Date(calendarCursor.getFullYear(), Number(event.target.value), 1, 12))}>{Array.from({ length: 12 }, (_, month) => <option key={month} value={month}>{month + 1} 月</option>)}</select></label>
              </div>
              <button className="icon-button" aria-label="下一个月" onClick={() => changeCalendarMonth(1)}><ChevronRight size={18} /></button>
            </div>
            <div className="calendar-weekdays" aria-hidden="true">{plannerWeekdayLabels.map((label) => <span key={label}>周{label}</span>)}</div>
            <div className="calendar-grid">
              {calendarDays.map((date) => {
                const dateKey = formatDateKey(date);
                const outsideMonth = date.getMonth() !== calendarCursor.getMonth();
                return (
                  <button
                    key={dateKey}
                    className={`calendar-date ${outsideMonth ? "calendar-date--outside" : ""} ${dateKey === selectedDate ? "calendar-date--selected" : ""} ${dateKey === todayKey ? "calendar-date--today" : ""}`}
                    aria-label={`${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`}
                    aria-current={dateKey === todayKey ? "date" : undefined}
                    onClick={() => chooseDate(date)}
                  >
                    <span>{date.getDate()}</span>
                    {tasks.some((task) => task.date === dateKey) ? <i /> : null}
                  </button>
                );
              })}
            </div>
            <div className="calendar-popover-footer">
              <button className="text-button" onClick={() => chooseDate(new Date())}>回到今天</button>
              <button className="secondary-button compact" onClick={() => setCalendarOpen(false)}>关闭</button>
            </div>
          </div>
        ) : null}
      </section>

      <section className="planner-stats" aria-label="今日工作统计">
        <div><ListTodo size={16} /><span>{selectedDate === todayKey ? "今日待办" : "当日待办"}</span><strong>{visibleTasks.length}</strong></div>
        <div><CheckCircle2 size={16} /><span>已完成</span><strong>{completedCount}</strong></div>
        <div><CalendarClock size={16} /><span>计划时长</span><strong>{Math.floor(plannedMinutes / 60)}h {plannedMinutes % 60}m</strong></div>
        <div><Timer size={16} /><span>专注时间</span><strong>{focusMinutes}m</strong></div>
      </section>

      <div className="planner-layout">
        <section className="card day-plan-card">
          <div className="section-heading planner-section-heading">
            <div><h2>{selectedDateValue.getFullYear()} 年 {selectedDateValue.getMonth() + 1} 月 {selectedDateValue.getDate()} 日安排</h2><p>{visibleTasks.length ? `${completedCount}/${visibleTasks.length} 项已完成` : "还没有安排"}</p></div>
            <button className="text-button" onClick={() => { setCalendarCursor(new Date(selectedDateValue.getFullYear(), selectedDateValue.getMonth(), 1, 12)); setCalendarOpen(true); }}>日历视图 <CalendarDays size={15} /></button>
          </div>

          <form className="quick-add-task" onSubmit={addTask}>
            <Plus size={17} aria-hidden="true" />
            <label className="sr-only" htmlFor="quick-add-task">添加待办</label>
            <input id="quick-add-task" value={newTaskTitle} onChange={(event) => setNewTaskTitle(event.target.value)} placeholder="记下一件今天要完成的事…" />
            <label className="sr-only" htmlFor="task-link-type">关联类型</label>
            <select id="task-link-type" value={newLinkType} onChange={(event) => { const nextType = event.target.value as PlannerLinkType; setNewLinkType(nextType); setNewLinkTarget(getPlannerLinkOptions(nextType)[0]?.id ?? ""); }}>
              <option value="none">普通待办</option>
              <option value="case">关联 Case</option>
              <option value="policy">关联政策</option>
              <option value="agent">关联 Agent</option>
            </select>
            <label className="sr-only" htmlFor="task-link-target">关联对象</label>
            <select id="task-link-target" aria-label="关联对象" disabled={newLinkType === "none"} value={newLinkTarget} onChange={(event) => setNewLinkTarget(event.target.value)}>
              {newLinkType === "none" ? <option value="">无需关联</option> : newLinkOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
            </select>
            <button type="submit" className="quick-add-button">添加</button>
          </form>

          <div className="task-filter" aria-label="筛选待办">
            {(["all", "pending", "completed"] as const).map((filter) => (
              <button key={filter} className={taskFilter === filter ? "task-filter--active" : ""} onClick={() => setTaskFilter(filter)}>
                {filter === "all" ? `全部 ${visibleTasks.length}` : filter === "pending" ? `待完成 ${visibleTasks.length - completedCount}` : `已完成 ${completedCount}`}
              </button>
            ))}
          </div>

          {deletedTask ? (
            <div className="undo-delete" role="status">
              <span>已删除“{deletedTask.task.title}”</span>
              <button onClick={undoDeleteTask}>撤销</button>
              <button className="icon-button" aria-label="关闭撤销提示" onClick={() => setDeletedTask(null)}><X size={16} /></button>
            </div>
          ) : null}

          {filteredTasks.length ? (
            <div className="planner-task-list">
              {filteredTasks.map((task) => (
                <article className={`planner-task ${task.completed ? "planner-task--done" : ""}`} key={task.id}>
                  <div className="planner-task-time">
                    <strong>{task.time ?? "待安排"}</strong>
                    <span>{task.durationMinutes} 分钟</span>
                  </div>
                  <button className="task-check" onClick={() => toggleTask(task.id)} aria-label={task.completed ? `恢复待办：${task.title}` : `完成待办：${task.title}`}>
                    {task.completed ? <CheckCircle2 size={20} /> : <Circle size={20} />}
                  </button>
                  <div className="planner-task-copy">
                    <strong>{task.title}</strong>
                    <div className="planner-task-meta">
                      {task.priority === "important" ? <span className="priority-chip">优先</span> : null}
                      {task.linkType !== "none" ? (
                        <button className={`link-chip link-chip--${task.linkType}`} onClick={() => followTaskLink(task)}>
                          {task.linkType === "agent" ? <Bot size={12} /> : <Link2 size={12} />}
                          {task.linkLabel}
                        </button>
                      ) : <span className="plain-chip">个人记录</span>}
                      {task.waitingOn ? <span className="waiting-chip"><Clock3 size={11} /> {task.waitingOn === "agent" ? "等待 Agent" : "等待中"}</span> : null}
                      {task.focusSessions ? <span className="focus-chip"><Timer size={11} /> {task.focusSessions} 个专注</span> : null}
                    </div>
                  </div>
                  {!task.completed && task.waitingOn === "agent" ? <button className="agent-task-button" onClick={() => followTaskLink(task)}><Bot size={14} /> 查看执行</button> : null}
                  {!task.completed && !task.waitingOn ? <button className="focus-task-button" onClick={() => startFocusFor(task)} aria-label={`专注处理：${task.title}`}><Play size={15} /> 专注</button> : null}
                  {task.completed ? <span className="completed-label"><Check size={14} /> 已完成</span> : null}
                  <button className="icon-button task-more-button" aria-label={`更多待办操作：${task.title}`} aria-expanded={openTaskMenuId === task.id} aria-haspopup="menu" onClick={() => setOpenTaskMenuId((current) => current === task.id ? null : task.id)}><MoreHorizontal size={18} /></button>
                  {openTaskMenuId === task.id ? (
                    <div className="task-overflow-menu" role="menu">
                      <button role="menuitem" className="task-menu-edit" onClick={() => { setEditingTask({ ...task }); setOpenTaskMenuId(null); }}><Pencil size={16} /> 编辑待办</button>
                      <button role="menuitem" className="task-menu-copy" onClick={() => duplicateTaskToTomorrow(task)}><Copy size={16} /> 复制到明天</button>
                      <button role="menuitem" onClick={() => deleteTask(task)}><Trash2 size={16} /> 删除待办</button>
                      <small>{task.linkType === "none" ? "从当天列表移除，可立即撤销" : "只移除待办，不影响关联 Case 或 Agent"}</small>
                    </div>
                  ) : null}
                </article>
              ))}
            </div>
          ) : (
            <div className="planner-empty"><CalendarDays size={25} /><h3>{visibleTasks.length ? "这个筛选条件下没有待办" : "这一天还没有安排"}</h3><p>{visibleTasks.length ? "切换到“全部”查看其他待办。" : "添加普通待办，或者关联一个 Case、政策或 Agent。"}</p></div>
          )}

          <div className="day-plan-footer">
            <span><ShieldCheck size={15} /> 普通待办仅作个人记录；关联操作仍遵守 Case 权限和审批规则</span>
          </div>
        </section>

        <aside className="planner-side">
          <section className="focus-card card">
            <div className="section-heading"><div><h2>专注计时</h2><p>一次只推进一件事</p></div><Timer size={18} /></div>
            <div className="focus-dial" style={{ "--focus-progress": `${timerProgress}deg` } as CSSProperties}>
              <div><strong>{timerText}</strong><span>{timerRunning ? "正在专注" : secondsLeft === 0 ? "已完成" : "25 分钟专注"}</span></div>
            </div>
            <div className="focus-current"><span>当前事项</span><strong>{activeTask?.title ?? "选择一项待办开始专注"}</strong></div>
            <div className="focus-controls">
              <button className="icon-button" onClick={() => { setTimerRunning(false); setSecondsLeft(25 * 60); }} aria-label="重置计时"><RotateCcw size={17} /></button>
              <button className="focus-main-button" onClick={() => setTimerRunning((current) => !current)}>{timerRunning ? <Pause size={18} /> : <Play size={18} />}{timerRunning ? "暂停" : "开始专注"}</button>
              <button className="icon-button" onClick={() => { setTimerRunning(false); setSecondsLeft(5 * 60); }} aria-label="切换为短休息"><Clock3 size={17} /></button>
            </div>
            <div className="focus-hint"><Sparkles size={14} /> 完成后自动记录一个专注时段</div>
          </section>

          <section className="daily-note-card card">
            <div className="section-heading"><div><h2>每日记事</h2><p>随手记录，不进入正式 Case</p></div><FileText size={17} /></div>
            <label className="sr-only" htmlFor="daily-note">每日记事</label>
            <textarea id="daily-note" value={dailyNote} onChange={(event) => { setDailyNote(event.target.value); window.localStorage.setItem(`${PLANNER_NOTE_PREFIX}.${storageScope}.${selectedDate}`, event.target.value); }} />
            <div className="note-status"><Check size={13} /> 已自动保存在本机</div>
          </section>

          <section className="day-insight-card card">
            <span className="section-kicker"><Bot size={14} /> AI 建议</span>
            <h3>{suggestedTask ? `建议优先：${suggestedTask.title}` : "今天的安排已处理完毕"}</h3>
            <p>{suggestedTask ? (suggestedTask.linkType === "agent" ? "该事项正在等待 Agent，可先查看执行状态或准备复核。" : "优先处理尚未完成且未被外部流程阻塞的事项，可以减少后续返工。") : "可以记录明日待办，或整理今天的工作备注。"}</p>
            {suggestedTask ? <button className="text-button" onClick={() => suggestedTask.linkType === "none" ? startFocusFor(suggestedTask) : followTaskLink(suggestedTask)}>{suggestedTask.linkType === "none" ? "开始处理" : "打开关联任务"} <ArrowRight size={14} /></button> : null}
          </section>
        </aside>
      </div>

      {editingTask ? (
        <div className="dialog-scrim" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditingTask(null); }}>
          <form className="dialog planner-task-dialog" role="dialog" aria-modal="true" aria-labelledby="edit-task-title" onSubmit={saveEditedTask}>
            <div className="dialog-header">
              <div className="dialog-icon"><Pencil size={20} /></div>
              <div><h2 id="edit-task-title">编辑待办</h2><p>调整日期、时间、时长和关联对象。</p></div>
              <button type="button" className="icon-button" aria-label="关闭编辑待办" onClick={() => setEditingTask(null)}><X size={18} /></button>
            </div>
            <div className="planner-task-form">
              <label className="form-field planner-form-wide"><span>待办内容</span><input value={editingTask.title} onChange={(event) => setEditingTask({ ...editingTask, title: event.target.value })} autoFocus /></label>
              <label className="form-field"><span>日期</span><input type="date" value={editingTask.date} onChange={(event) => setEditingTask({ ...editingTask, date: event.target.value })} /></label>
              <label className="form-field"><span>开始时间</span><input type="time" value={editingTask.time ?? ""} onChange={(event) => setEditingTask({ ...editingTask, time: event.target.value || null })} /></label>
              <label className="form-field"><span>预计时长（分钟）</span><input type="number" min="5" step="5" value={editingTask.durationMinutes} onChange={(event) => setEditingTask({ ...editingTask, durationMinutes: Number(event.target.value) })} /></label>
              <label className="form-field"><span>优先级</span><select value={editingTask.priority} onChange={(event) => setEditingTask({ ...editingTask, priority: event.target.value as PlannerTask["priority"] })}><option value="normal">普通</option><option value="important">优先</option></select></label>
              <label className="form-field"><span>关联类型</span><select value={editingTask.linkType} onChange={(event) => { const linkType = event.target.value as PlannerLinkType; const firstOption = getPlannerLinkOptions(linkType)[0]; setEditingTask({ ...editingTask, linkType, linkId: firstOption?.id, linkLabel: firstOption?.label }); }}><option value="none">普通待办</option><option value="case">关联 Case</option><option value="policy">关联政策</option><option value="agent">关联 Agent</option>{editingTask.linkType === "recruitment" ? <option value="recruitment">招聘面试</option> : null}</select></label>
              <label className="form-field"><span>关联对象</span><select disabled={editingTask.linkType === "none" || editingTask.linkType === "recruitment"} value={editingTask.linkId ?? ""} onChange={(event) => { const selected = editingLinkOptions.find((option) => option.id === event.target.value); setEditingTask({ ...editingTask, linkId: selected?.id, linkLabel: selected?.label }); }}>{editingTask.linkType === "none" ? <option value="">无需关联</option> : editingTask.linkType === "recruitment" ? <option value={editingTask.linkId}>{editingTask.linkLabel}</option> : editingLinkOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>
            </div>
            <div className="dialog-note"><ShieldCheck size={15} /> 修改个人待办不会直接修改关联 Case、政策或 Agent 数据。</div>
            <div className="dialog-actions"><button type="button" className="secondary-button" onClick={() => setEditingTask(null)}>取消</button><button type="submit" className="primary-button">保存修改</button></div>
          </form>
        </div>
      ) : null}
    </div>
  );
}

function TaskCenter({
  activeConversation,
  onTurnComplete,
  onMenu,
  onOpenCase,
  onOpenRecruitmentCase,
  onNavigate,
  user
}: {
  activeConversation: ConversationHistory | null;
  onTurnComplete: (conversation: Conversation) => void;
  onMenu: () => void;
  onOpenCase: () => void;
  onOpenRecruitmentCase: (caseId: string) => void;
  onNavigate: (view: View) => void;
  user: Principal;
}) {
  const [message, setMessage] = useState("");
  const [mode, setMode] = useState("自动选择");
  const [documentFont, setDocumentFont] = useState("仿宋_GB2312");
  const [documentFontSize, setDocumentFontSize] = useState(16);
  const [jobTitle, setJobTitle] = useState("");
  const [mustHaves, setMustHaves] = useState("");
  const [niceToHaves, setNiceToHaves] = useState("");
  const [selectionCount, setSelectionCount] = useState(3);
  const [policyTopic, setPolicyTopic] = useState("");
  const [policyScope, setPolicyScope] = useState("");
  const [policyAsOfDate, setPolicyAsOfDate] = useState("");
  const [knowledgeScope, setKnowledgeScope] = useState("");
  const [spreadsheetGoal, setSpreadsheetGoal] = useState("");
  const [sourceSheet, setSourceSheet] = useState("");
  const [spreadsheetHeaderRow, setSpreadsheetHeaderRow] = useState(1);
  const [spreadsheetHeaderFill, setSpreadsheetHeaderFill] = useState("2563EB");
  const [spreadsheetAlignment, setSpreadsheetAlignment] = useState("left");
  const [spreadsheetFreezeHeader, setSpreadsheetFreezeHeader] = useState(true);
  const [spreadsheetPreserveStyle, setSpreadsheetPreserveStyle] = useState(true);
  const [mappingNotes, setMappingNotes] = useState("");
  const [calculationRules, setCalculationRules] = useState("");
  const [spreadsheetFilterSortRules, setSpreadsheetFilterSortRules] = useState("");
  const [spreadsheetValidationRules, setSpreadsheetValidationRules] = useState("");
  const [spreadsheetSummaryRules, setSpreadsheetSummaryRules] = useState("");
  const [spreadsheetChartType, setSpreadsheetChartType] = useState("none");
  const [documentType, setDocumentType] = useState("通知");
  const [documentTitle, setDocumentTitle] = useState("");
  const [documentAddressee, setDocumentAddressee] = useState("");
  const [documentIssuer, setDocumentIssuer] = useState("");
  const [documentDate, setDocumentDate] = useState("");
  const [documentPurpose, setDocumentPurpose] = useState("");
  const [documentRequiredPoints, setDocumentRequiredPoints] = useState("");
  const [documentProhibitedContent, setDocumentProhibitedContent] = useState("");
  const [documentTone, setDocumentTone] = useState("正式、简洁、审慎");
  const [documentPreset, setDocumentPreset] = useState("official");
  const [documentAlignment, setDocumentAlignment] = useState("justify");
  const [documentLineSpacing, setDocumentLineSpacing] = useState(1.5);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeExecution, setActiveExecution] = useState<TurnExecutionRecord | null>(null);
  const [executionEvents, setExecutionEvents] = useState<TurnExecutionEvent[]>([]);
  const [pendingMessage, setPendingMessage] = useState("");
  const [loadingThread, setLoadingThread] = useState(false);
  const [error, setError] = useState("");
  const [skillNotice, setSkillNotice] = useState("");
  const [buildingSkill, setBuildingSkill] = useState(false);
  const [availableSkills, setAvailableSkills] = useState<SkillRecord[]>([]);
  const [selectedSkills, setSelectedSkills] = useState<SkillRecord[]>([]);
  const [slashDismissedFor, setSlashDismissedFor] = useState("");
  const [activeSlashIndex, setActiveSlashIndex] = useState(0);
  const [decidingRunId, setDecidingRunId] = useState<string | null>(null);
  const [feedbackMessageId, setFeedbackMessageId] = useState("");
  const [feedbackComment, setFeedbackComment] = useState("");
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [resourceWorkspace, setResourceWorkspace] = useState<ResourceWorkspaceManifest | null>(null);
  const [contextDashboard, setContextDashboard] = useState<ContextOperationsDashboard | null>(null);
  const [resourceOpen, setResourceOpen] = useState(false);
  const [loadingResources, setLoadingResources] = useState(false);
  const [showArchivedResources, setShowArchivedResources] = useState(false);
  const [resourceBusyId, setResourceBusyId] = useState("");
  const [resolvingFactId, setResolvingFactId] = useState("");
  const [workflowBusy, setWorkflowBusy] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const threadEndRef = useRef<HTMLDivElement>(null);
  const executionAbortRef = useRef<AbortController | null>(null);
  const executionCursorRef = useRef(0);

  const executionStorageKey = `unihr-active-execution:${user.user_id}`;

  const finishExecution = async (executionId: string) => {
    const current = await unihrApi.getTurnExecution(executionId);
    setActiveExecution(current);
    if (current.status === "succeeded" && current.result) {
      onTurnComplete(current.result.conversation);
      setDetail(await unihrApi.getConversation(current.result.conversation.id));
    } else if (current.status === "failed") {
      setError(`任务执行失败（${current.error_code ?? "unknown_error"}），输入和审计记录已保留。`);
    } else if (current.status === "cancelled") {
      setError("本轮任务已取消，没有发布新的回答或文件。");
    }
    if (["succeeded", "failed", "cancelled"].includes(current.status)) {
      localStorage.removeItem(executionStorageKey);
      setLoading(false);
      setPendingMessage("");
      setActiveExecution(null);
    }
  };

  const observeExecution = async (executionId: string) => {
    executionAbortRef.current?.abort();
    const controller = new AbortController();
    executionAbortRef.current = controller;
    try {
      await unihrApi.watchTurnExecution(
        executionId,
        executionCursorRef.current,
        (item) => {
          executionCursorRef.current = Math.max(executionCursorRef.current, item.sequence);
          setExecutionEvents((current) => current.some((event) => event.sequence === item.sequence)
            ? current
            : [...current, item]);
        },
        controller.signal
      );
      await finishExecution(executionId);
    } catch (reason) {
      if (controller.signal.aborted) return;
      try {
        const current = await unihrApi.getTurnExecution(executionId);
        if (["queued", "running"].includes(current.status)) {
          window.setTimeout(() => void observeExecution(executionId), 1200);
        } else {
          await finishExecution(executionId);
        }
      } catch {
        setLoading(false);
        setError(reason instanceof Error ? reason.message : "无法恢复任务进度");
      }
    }
  };

  useEffect(() => {
    if (!activeConversation) {
      setDetail(null);
      setError("");
      setMessage("");
      setMode("自动选择");
      setSelectedFiles([]);
      setSelectedSkills([]);
      setJobTitle("");
      setMustHaves("");
      setNiceToHaves("");
      setSelectionCount(3);
      setPolicyTopic("");
      setPolicyScope("");
      setPolicyAsOfDate("");
      setKnowledgeScope("");
      setSpreadsheetGoal("");
      setSourceSheet("");
      setSpreadsheetHeaderRow(1);
      setMappingNotes("");
      setCalculationRules("");
      setSpreadsheetFilterSortRules("");
      setSpreadsheetValidationRules("");
      setSpreadsheetSummaryRules("");
      setSpreadsheetChartType("none");
      setDocumentType("通知");
      setDocumentTitle("");
      setDocumentAddressee("");
      setDocumentIssuer("");
      setDocumentDate("");
      setDocumentPurpose("");
      setDocumentRequiredPoints("");
      setDocumentProhibitedContent("");
      setDocumentTone("正式、简洁、审慎");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    let cancelled = false;
    setLoadingThread(true);
    void unihrApi.getConversation(activeConversation.id)
      .then((item) => {
        if (!cancelled) setDetail(item);
      })
      .catch((reason: Error) => {
        if (!cancelled) setError(reason.message);
      })
      .finally(() => {
        if (!cancelled) setLoadingThread(false);
      });
    return () => { cancelled = true; };
  }, [activeConversation?.id]);

  useEffect(() => {
    void unihrApi.listSkills()
      .then((items) => setAvailableSkills(items.filter((item) => item.enabled && item.status === "active")))
      .catch(() => setAvailableSkills([]));
  }, []);

  useEffect(() => {
    const executionId = localStorage.getItem(executionStorageKey);
    if (!executionId) return;
    let disposed = false;
    void unihrApi.getTurnExecution(executionId)
      .then((current) => {
        if (disposed) return;
        setActiveExecution(current);
        setLoading(["queued", "running"].includes(current.status));
        if (["queued", "running"].includes(current.status)) {
          void observeExecution(executionId);
        } else {
          void finishExecution(executionId);
        }
      })
      .catch(() => localStorage.removeItem(executionStorageKey));
    return () => {
      disposed = true;
      executionAbortRef.current?.abort();
    };
    // Recovery is scoped to the signed-in user; execution changes are handled by the stream.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user.user_id]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [detail?.messages.length, loading]);

  const submitMessage = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    const builderSkill = selectedSkills.find((item) => item.name === "build-reusable-skill");
    if (builderSkill) {
      if (!activeConversation) {
        setError("请先在一段已经完成的任务对话中调用“沉淀为可复用 Skill”。");
        return;
      }
      setLoading(true);
      setError("");
      try {
        const created = await unihrApi.createSkillFromConversation(activeConversation.id);
        setSkillNotice(`已由 Skill Builder Agent 生成“${created.display_name}”草稿，请到 Skill 中心检查并启用。`);
        setMessage("");
        setSelectedSkills([]);
      } catch (reason) {
        setError(reason instanceof Error ? skillErrorMessage(reason) : "Skill 生成失败，请稍后重试");
      } finally {
        setLoading(false);
      }
      return;
    }
    const kindByMode: Record<string, TaskBrief["kind"]> = {
      "简历筛选": "recruitment",
      "政策问答": "policy",
      "表格处理": "spreadsheet",
      "公文材料": "document",
      "图片理解": "image",
      "联网信息": "general",
      "自动选择": "general"
    };
    const defaultObjective: Record<TaskBrief["kind"], string> = {
      recruitment: "按岗位要求批量筛选上传的简历，并生成证据化排名与 Excel 报告。",
      policy: "基于指定知识范围回答政策问题，并给出可定位的原文证据。",
      spreadsheet: "处理上传的表格，生成不覆盖原件的可复核工作簿。",
      document: "根据要求生成可复核的 Word 文档。",
      image: "读取并分析上传的图片材料。",
      general: "分析上传的材料或处理本轮人事工作请求。"
    };
    const kind = kindByMode[mode] ?? "general";
    const modeSpec: Record<string, unknown> = mode === "简历筛选" ? {
      job_title: jobTitle.trim(), must_haves: mustHaves.trim(), nice_to_haves: niceToHaves.trim(), selection_count: selectionCount
    } : mode === "政策问答" ? {
      topic: policyTopic.trim(), scope: policyScope.trim(), as_of_date: policyAsOfDate, knowledge_scope: knowledgeScope.trim()
    } : mode === "表格处理" ? {
      goal: spreadsheetGoal.trim(), source_sheet: sourceSheet.trim(), header_row: spreadsheetHeaderRow,
      mapping_notes: mappingNotes.trim(), calculation_rules: calculationRules.trim(),
      filter_sort_rules: spreadsheetFilterSortRules.trim(), data_validation_rules: spreadsheetValidationRules.trim(),
      summary_rules: spreadsheetSummaryRules.trim(), chart_type: spreadsheetChartType
    } : mode === "公文材料" ? {
      document_type: documentType, title: documentTitle.trim(), addressee: documentAddressee.trim(),
      issuer: documentIssuer.trim(), date_text: documentDate, purpose: documentPurpose.trim(),
      required_points: documentRequiredPoints.trim(), prohibited_content: documentProhibitedContent.trim(),
      tone: documentTone, required_fields: ["title", "issuer", "date_text"]
    } : {};
    const taskBrief: TaskBrief = {
      version: "1.0",
      kind,
      objective: message.trim() || defaultObjective[kind],
      constraints: mode === "简历筛选" ? [mustHaves.trim()].filter(Boolean)
        : mode === "表格处理" ? [
            calculationRules.trim(), spreadsheetFilterSortRules.trim(), spreadsheetValidationRules.trim(),
            spreadsheetSummaryRules.trim()
          ].filter(Boolean)
          : [],
      deliverables: mode === "简历筛选" ? ["证据化排名", "Excel 报告"]
        : mode === "公文材料" ? ["Word 文档", "事实台账", "内容与版式校验摘要"]
          : mode === "表格处理" ? ["Excel 工作簿", "处理说明", "校验摘要"]
            : mode === "政策问答" ? ["带证据的政策回答"] : [],
      selected_skill_ids: selectedSkills.map((item) => item.id),
      approval_required: ["简历筛选", "公文材料", "表格处理"].includes(mode),
      mode_spec: modeSpec,
      artifact_style: {
        ...(mode === "公文材料" ? { document: {
          preset: documentPreset,
          body_font: documentFont,
          body_size_pt: documentFontSize,
          body_alignment: documentAlignment,
          line_spacing: documentLineSpacing
        } } : {}),
        ...(mode === "表格处理" ? { spreadsheet: {
          target_sheet: sourceSheet.trim(),
          header_row: spreadsheetHeaderRow,
          header_fill: spreadsheetHeaderFill,
          horizontal_alignment: spreadsheetAlignment,
          freeze_header: spreadsheetFreezeHeader,
          preserve_existing_style: spreadsheetPreserveStyle
        } } : {})
      }
    };
    setLoading(true);
    setError("");
    try {
      const submittedMessage = message.trim() || taskBrief.objective;
      const turnInput = {
        message: message.trim(),
        conversationId: activeConversation?.id ?? null,
        taskMode: mode,
        files: selectedFiles,
        documentFont,
        documentFontSize,
        taskBrief
      };
      const isSteering = Boolean(
        loading && activeExecution && ["queued", "running"].includes(activeExecution.status)
      );
      executionAbortRef.current?.abort();
      const execution = isSteering && activeExecution
        ? await unihrApi.steerTurn(activeExecution.id, turnInput)
        : await unihrApi.startTurn(turnInput);
      executionCursorRef.current = 0;
      setExecutionEvents([]);
      setActiveExecution(execution);
      setPendingMessage(submittedMessage);
      localStorage.setItem(executionStorageKey, execution.id);
      setMessage("");
      setSelectedSkills([]);
      setSelectedFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      void observeExecution(execution.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务执行失败，请稍后重试");
      setLoading(false);
    }
  };

  const suggestions = [
    { label: "批量筛选 10 份简历", icon: Users, mode: "简历筛选" },
    { label: "根据数据生成带公式的复核表", icon: FileSpreadsheet, mode: "表格处理" },
    { label: "查询政策依据与适用范围", icon: BookOpen, mode: "政策问答" },
    { label: "联网查询近期公开信息", icon: Globe2, mode: "联网信息" },
    { label: "按旧模板生成会议材料", icon: FileText, mode: "公文材料" },
    { label: "读取并分析一张材料图片", icon: FileChartColumn, mode: "图片理解" },
    { label: "检查表格结构并生成副本", icon: FileSpreadsheet, mode: "表格处理" }
  ];

  const shownConversation = detail?.conversation;
  const hasThread = Boolean(activeConversation || shownConversation || pendingMessage || activeExecution);
  const steeringEvents = executionEvents.filter((item) => item.event.startsWith("turn.steering."));
  const slashMatch = message.match(/(?:^|\s)\/([^\s/]*)$/);
  const slashQuery = slashMatch?.[1]?.toLowerCase() ?? "";
  const slashSkills = slashMatch && slashDismissedFor !== message
    ? availableSkills.filter((skill) => (
        `${skill.display_name} ${skill.name} ${skill.trigger_phrases.join(" ")}`
          .toLowerCase().includes(slashQuery)
        && !selectedSkills.some((selected) => selected.id === skill.id)
      )).slice(0, 8)
    : [];
  const chooseSlashSkill = (skill: SkillRecord) => {
    setSelectedSkills((items) => [...items, skill]);
    setMessage((current) => current.replace(/(^|\s)\/[^\s/]*$/, "$1"));
    setSlashDismissedFor("");
    setActiveSlashIndex(0);
  };
  const hasBriefInput = mode === "简历筛选" ? Boolean(jobTitle.trim() || mustHaves.trim() || niceToHaves.trim())
    : mode === "政策问答" ? Boolean(policyTopic.trim() || policyScope.trim() || policyAsOfDate || knowledgeScope.trim())
      : mode === "表格处理" ? Boolean(spreadsheetGoal.trim() || sourceSheet.trim() || mappingNotes.trim() || calculationRules.trim() || spreadsheetFilterSortRules.trim() || spreadsheetValidationRules.trim() || spreadsheetSummaryRules.trim() || spreadsheetChartType !== "none")
          : mode === "公文材料" ? Boolean(documentType || documentTitle.trim() || documentAddressee.trim() || documentIssuer.trim())
            : false;
  const canSubmit = Boolean(message.trim() || selectedFiles.length || hasBriefInput || selectedSkills.length);
  const attachmentsFor = (messageId: string) => detail?.attachments.filter((item) => item.message_id === messageId) ?? [];
  const artifactsFor = (messageId: string) => detail?.artifacts.filter((item) => item.message_id === messageId) ?? [];
  const feedbackFor = (messageId: string) => detail?.feedback.find((item) => item.message_id === messageId) ?? null;
  const runFor = (messageId: string) => detail?.runs.find((item) => item.message_id === messageId) ?? null;
  const decideRun = async (runId: string, approved: boolean) => {
    const comment = approved ? "" : window.prompt("请写下退回原因，Agent 将保留原结果，方便后续修改。", "");
    if (comment === null) return;
    setDecidingRunId(runId);
    setError("");
    try {
      await unihrApi.decideRun(runId, approved, comment);
      if (detail) setDetail(await unihrApi.getConversation(detail.conversation.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "复核决定提交失败");
    } finally {
      setDecidingRunId(null);
    }
  };
  const recoverSkillRun = async (runId: string, waitingInput: boolean) => {
    const comment = window.prompt(
      waitingInput
        ? "请填写已经补充的资料或说明；Agent 会从等待节点继续，不重跑前序步骤。"
        : "请说明失败原因已经如何修复；Agent 只重试失败节点。",
      ""
    );
    if (comment === null) return;
    setDecidingRunId(runId);
    setError("");
    try {
      await unihrApi.recoverSkillRun(runId, {
        supplied_inputs: waitingInput ? { user_supplement: comment.trim() } : {},
        comment: comment.trim()
      });
      if (detail) setDetail(await unihrApi.getConversation(detail.conversation.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Skill 节点恢复失败");
    } finally {
      setDecidingRunId(null);
    }
  };
  const rateAnswer = async (messageId: string, helpful: boolean, comment = "") => {
    setFeedbackSubmitting(true);
    try {
      const feedback = await unihrApi.rateAnswer(messageId, helpful, comment);
      setDetail((current) => current ? {
        ...current,
        feedback: [feedback, ...current.feedback.filter((item) => item.message_id !== messageId)]
      } : current);
      setFeedbackMessageId("");
      setFeedbackComment("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "反馈提交失败");
    } finally {
      setFeedbackSubmitting(false);
    }
  };
  const formatBytes = (bytes: number) => bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  const loadResourceWorkspace = (includeArchived = showArchivedResources) => {
    if (!detail?.conversation.id) return;
    setLoadingResources(true);
    void Promise.all([
      unihrApi.getResourceWorkspace(detail.conversation.id, includeArchived),
      unihrApi.getConversationContextDashboard(detail.conversation.id)
    ])
      .then(([workspace, dashboard]) => {
        setResourceWorkspace(workspace);
        setContextDashboard(dashboard);
      })
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoadingResources(false));
  };
  const openResourceWorkspace = () => {
    if (!detail?.conversation.id) return;
    setResourceOpen(true);
    loadResourceWorkspace();
  };
  const toggleArchivedResources = () => {
    const next = !showArchivedResources;
    setShowArchivedResources(next);
    loadResourceWorkspace(next);
  };
  const setResourceArchived = async (resourceId: string, archived: boolean) => {
    setResourceBusyId(resourceId);
    try {
      if (archived) await unihrApi.archiveResource(resourceId);
      else await unihrApi.restoreResource(resourceId);
      loadResourceWorkspace(showArchivedResources);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "资源状态更新失败");
    } finally {
      setResourceBusyId("");
    }
  };

  const resolveSemanticFact = async (factId: string, approved: boolean) => {
    if (!detail?.conversation.id) return;
    setResolvingFactId(factId);
    setError("");
    try {
      await unihrApi.resolveSemanticFact(factId, approved);
      setContextDashboard(await unihrApi.getConversationContextDashboard(detail.conversation.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "业务事实确认失败");
    } finally {
      setResolvingFactId("");
    }
  };

  const cancelActiveExecution = async () => {
    if (!activeExecution) return;
    try {
      const current = await unihrApi.cancelTurnExecution(activeExecution.id);
      setActiveExecution(current);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "取消任务失败");
    }
  };

  const applyWorkflowDecision = async (workflow: WorkflowCase, action: "approve" | "revise" | "retry") => {
    const comment = action === "revise"
      ? window.prompt("请说明需要修改的筛选标准或候选人建议。", "")
      : action === "retry"
        ? window.prompt("将只重试未完成的 Agent 分支，已经成功的结果不会重做。可补充说明。", "")
        : window.prompt("确认采用当前建议并交给后续 Agent；可补充本次人工确认说明。", "");
    if (comment === null || !detail) return;
    setWorkflowBusy(workflow.id);
    setError("");
    try {
      await unihrApi.applyWorkflowCaseAction(workflow.id, action, comment);
      setDetail(await unihrApi.getConversation(detail.conversation.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "跨 Agent 工作流执行失败");
    } finally {
      setWorkflowBusy("");
    }
  };

  return (
    <div className={`conversation-home ${hasThread ? "conversation-home--thread" : ""}`}>
      <header className="conversation-topbar">
        <button className="icon-button mobile-menu" onClick={onMenu} aria-label="打开导航"><Menu size={20} /></button>
        <button className="conversation-agent-select">UniHR Assistant <ChevronDown size={15} /></button>
        <div className="conversation-topbar-actions">
          {detail?.conversation.id ? (
            <button className="conversation-quiet-button" disabled={buildingSkill} onClick={() => {
              setBuildingSkill(true);
              setSkillNotice("");
              void unihrApi.createSkillFromConversation(detail.conversation.id)
                .then((skill) => setSkillNotice(`已生成“${skill.display_name}”草稿，请到 Skill 中心检查并启用。`))
                .catch((reason: Error) => setError(reason.message))
                .finally(() => setBuildingSkill(false));
            }}><Puzzle size={17} />{buildingSkill ? "正在总结" : "总结为 Skill"}</button>
          ) : null}
          <button className="conversation-quiet-button" disabled={!detail?.conversation.id} onClick={openResourceWorkspace}><FolderClock size={17} />资源工作区</button>
          <button className="conversation-quiet-button" onClick={() => onNavigate("planner")}><CalendarDays size={17} />今日工作</button>
          <button className="conversation-quiet-button" onClick={onOpenCase}><BriefcaseBusiness size={17} />打开 Case</button>
        </div>
      </header>

      {hasThread ? (
        <section className="conversation-thread" aria-label="对话内容">
          <div className="conversation-thread-inner">
            <div className="conversation-thread-title">
              <span>安全工作会话</span>
              <h1>{shownConversation?.title ?? activeConversation?.title ?? "新对话"}</h1>
            </div>
            {detail?.workflow_cases?.[0] ? (
              <WorkflowCasePanel
                item={detail.workflow_cases[0]}
                artifacts={detail.artifacts}
                busy={workflowBusy === detail.workflow_cases[0].id}
                onApprove={() => void applyWorkflowDecision(detail.workflow_cases[0], "approve")}
                onRevise={() => void applyWorkflowDecision(detail.workflow_cases[0], "revise")}
                onRetry={() => void applyWorkflowDecision(detail.workflow_cases[0], "retry")}
                onOpenRecruitmentCase={onOpenRecruitmentCase}
              />
            ) : null}
            {loadingThread && !detail ? <div className="conversation-loading">正在读取对话…</div> : null}
            {detail?.messages.map((item) => {
              const messageAttachments = attachmentsFor(item.id);
              const messageArtifacts = artifactsFor(item.id);
              const currentFeedback = feedbackFor(item.id);
              const messageRun = runFor(item.id);
              const sourceStep = item.trace.find((step) => step.event === "web.sources");
              const publicSources = Array.isArray(sourceStep?.sources)
                ? sourceStep.sources.filter((source): source is Record<string, unknown> => Boolean(source) && typeof source === "object")
                : [];
              const evidenceStep = item.trace.find((step) => step.event === "evidence.sources");
              const policySources = Array.isArray(evidenceStep?.sources)
                ? evidenceStep.sources.filter((source): source is Record<string, unknown> => Boolean(source) && typeof source === "object")
                : [];
              const conflictStep = item.trace.find((step) => step.event === "policy.conflict_gate");
              const conflictDetails = (
                conflictStep?.details && typeof conflictStep.details === "object" ? conflictStep.details : conflictStep ?? {}
              ) as Record<string, unknown>;
              const policyConflictCount = Number(conflictDetails.conflict_count ?? 0);
              const visionStep = item.trace.find((step) => step.event === "vision.evidence.extracted");
              const visionDetails = (
                visionStep?.details && typeof visionStep.details === "object" ? visionStep.details : visionStep ?? {}
              ) as Record<string, unknown>;
              const visionPageCount = Number(visionDetails.page_count ?? 0);
              const visionBlockCount = Number(visionDetails.block_count ?? 0);
              const visionReviewCount = Number(visionDetails.review_block_count ?? 0);
              const visionReviewPageCount = Number(visionDetails.review_page_count ?? 0);
              const visionNeedsReview = visionReviewCount > 0 || visionReviewPageCount > 0;
              return (
                <article className={`conversation-message conversation-message--${item.role}`} key={item.id}>
                  {item.role === "user" ? <div className="avatar">{user.display_name.slice(0, 1)}</div> : <div className="assistant-avatar">{item.role === "system" ? <GitBranch size={17} /> : <Sparkles size={17} />}</div>}
                  <div>
                    <strong>{item.role === "user" ? user.display_name : item.role === "system" ? "Agent 交接" : "UniHR Assistant"}</strong>
                    {item.role === "assistant" ? (
                      <div className="conversation-markdown"><Suspense fallback={<LazyModuleFallback label="正在整理回答…" />}><MarkdownContent>{item.content}</MarkdownContent></Suspense></div>
                    ) : <p>{item.content}</p>}
                    {item.role === "assistant" && publicSources.length ? (
                      <section className="web-source-panel" aria-label="联网回答来源">
                        <header><span><Globe2 size={15} />公开来源</span><small>网页内容仅作公开信息证据，不作为组织内部正式政策</small></header>
                        <div className="web-source-grid">
                          {publicSources.slice(0, 6).map((source, index) => (
                            <a href={String(source.url ?? "#")} target="_blank" rel="noreferrer" key={`${item.id}-source-${index}`}>
                              <span className="web-source-index">{String(source.index ?? index + 1)}</span>
                              <span className="web-source-copy">
                                <strong>{String(source.title ?? source.url ?? "公开来源")}</strong>
                                <small>{String(source.site_name ?? "公开网页")}{source.published_at ? ` · ${String(source.published_at)}` : ""}</small>
                                {source.excerpt ? <em>{String(source.excerpt).slice(0, 120)}</em> : null}
                              </span>
                              <ExternalLink size={14} />
                            </a>
                          ))}
                        </div>
                      </section>
                    ) : null}
                    {item.role === "assistant" && policySources.length ? (
                      <section className="policy-evidence-panel" aria-label="政策回答依据">
                        <header><span><BookOpen size={15} />受控政策依据</span><small>仅展示当前账号有权检索的已发布版本</small></header>
                        <div className="policy-evidence-grid">
                          {policySources.slice(0, 8).map((source, index) => (
                            <article key={`${item.id}-evidence-${index}`}>
                              <span className="web-source-index">{String(source.reference_id ?? `E${index + 1}`)}</span>
                              <div><strong>{String(source.title ?? "政策依据")}</strong><small>{String(source.citation ?? "文档正文")}</small><em>{String(source.source_version ?? "版本号待确认")} · {String(source.policy_status ?? "unknown")} · {String(source.authority_level ?? "unknown")}</em><small>生效：{String(source.effective_date ?? "待确认")} · 失效：{String(source.expiry_date ?? "待确认")}</small></div>
                            </article>
                          ))}
                        </div>
                      </section>
                    ) : null}
                    {item.role === "assistant" && policyConflictCount > 0 ? (
                      <section className="policy-conflict-panel" aria-label="政策冲突提示">
                        <AlertTriangle size={17} />
                        <div><strong>发现 {policyConflictCount} 组政策证据冲突</strong><p>系统没有自动选择对用户更有利的版本；请复核有效期、效力层级和适用范围。</p></div>
                      </section>
                    ) : null}
                    {item.role === "assistant" && visionStep && (visionPageCount > 0 || visionBlockCount > 0) ? (
                      <section className={`vision-evidence-panel${visionNeedsReview ? " vision-evidence-panel--review" : ""}`} aria-label="图片与扫描文档证据摘要">
                        <header><span><FileCheck2 size={15} />图片证据已结构化</span><small>坐标与模型自评已保存，可回到原图人工核对</small></header>
                        <div>
                          <span>处理页数 <b>{visionPageCount}</b></span>
                          <span>证据块 <b>{visionBlockCount}</b></span>
                          <span className={visionNeedsReview ? "needs-review" : ""}>待核对 <b>{visionReviewCount} 块 / {visionReviewPageCount} 页</b></span>
                        </div>
                        {visionNeedsReview ? <p><AlertTriangle size={14} />涉及姓名、日期、数字或金额时，请以原图为准。</p> : <p><ShieldCheck size={14} />当前未发现低置信证据块，正式使用前仍建议抽样复核。</p>}
                      </section>
                    ) : null}
                    {messageAttachments.length ? (
                      <div className="conversation-file-list" aria-label="本轮附件">
                        {messageAttachments.map((file) => (
                          <span className="conversation-file-chip" key={file.id}><Paperclip size={14} />{file.display_name}<small>{formatBytes(file.byte_size)}{file.summary.converted_format ? ` · 已安全转换为 ${String(file.summary.converted_format)}` : ""}</small></span>
                        ))}
                      </div>
                    ) : null}
                    {messageArtifacts.length ? (
                      <div className="conversation-artifacts" aria-label="生成的文件">
                        {messageArtifacts.map((artifact) => {
                          const operationResults = Array.isArray(artifact.metadata.operation_results)
                            ? artifact.metadata.operation_results as Array<Record<string, unknown>> : [];
                          const exceptions = Array.isArray(artifact.metadata.exceptions)
                            ? artifact.metadata.exceptions as Array<Record<string, unknown>> : [];
                          const changedCellCount = Number(artifact.metadata.changed_cell_count ?? 0);
                          const workbookDiff = (artifact.metadata.workbook_diff ?? {}) as Record<string, unknown>;
                          const diffSamples = Array.isArray(workbookDiff.samples)
                            ? workbookDiff.samples as Array<Record<string, unknown>> : [];
                          const hasWorkbookDiff = artifact.kind === "spreadsheet" && Number(workbookDiff.changed_cells ?? 0) > 0;
                          const artifactHarness = (artifact.metadata.artifact_harness ?? {}) as Record<string, unknown>;
                          const harnessDetails = (artifactHarness.details ?? {}) as Record<string, unknown>;
                          const previewFiles = Array.isArray(harnessDetails.preview_files) ? harnessDetails.preview_files : [];
                          const isSpreadsheet = artifact.kind === "spreadsheet" || artifact.kind === "recruitment_report";
                          const isDocument = artifact.kind === "document";
                          const recruitmentHarness = (artifact.metadata.recruitment_harness ?? {}) as Record<string, unknown>;
                          const recruitmentHarnessMetrics = (recruitmentHarness.metrics ?? {}) as Record<string, unknown>;
                          const recruitmentLocatedEvidence = Number(recruitmentHarnessMetrics.located_evidence_rows ?? 0);
                          const recruitmentReviewEvidence = Number(recruitmentHarnessMetrics.review_evidence_rows ?? 0);
                          const recruitmentUnlocatedEvidence = Number(recruitmentHarnessMetrics.unlocated_evidence_rows ?? 0);
                          const recruitmentCase = (artifact.metadata.recruitment_case ?? {}) as Record<string, unknown>;
                          const recruitmentRubric = (artifact.metadata.rubric ?? {}) as Record<string, unknown>;
                          const recruitmentCandidates = Array.isArray(artifact.metadata.candidate_summary)
                            ? artifact.metadata.candidate_summary as Array<Record<string, unknown>> : [];
                          const recruitmentDuplicates = Array.isArray(artifact.metadata.duplicate_groups)
                            ? artifact.metadata.duplicate_groups as Array<Record<string, unknown>> : [];
                          const recruitmentExactDuplicateCount = recruitmentDuplicates.reduce((total, group) => (
                            total + (Array.isArray(group.duplicate_sources) ? group.duplicate_sources.length : 0)
                          ), 0);
                          const recruitmentVersionGroups = Array.isArray(artifact.metadata.version_groups)
                            ? artifact.metadata.version_groups as Array<Record<string, unknown>> : [];
                          const recruitmentMergedVersions = Number(
                            recruitmentHarnessMetrics.semantic_merged_source_count
                              ?? artifact.metadata.semantic_merged_source_count ?? 0,
                          );
                          const recruitmentVersionReviewGroups = Number(
                            recruitmentHarnessMetrics.version_review_group_count
                              ?? artifact.metadata.version_review_group_count ?? 0,
                          );
                          const documentValidation = (artifact.metadata.validation ?? {}) as Record<string, unknown>;
                          const documentFacts = Array.isArray(artifact.metadata.fact_matrix)
                            ? artifact.metadata.fact_matrix as Array<Record<string, unknown>> : [];
                          const documentIssues = Array.isArray(documentValidation.issues)
                            ? documentValidation.issues as string[] : [];
                          const documentWarnings = Array.isArray(documentValidation.warnings)
                            ? documentValidation.warnings as string[] : [];
                          const spreadsheetRepairCount = Number(artifact.metadata.repair_count ?? 0);
                          const artifactReviewRecommended = artifact.metadata.review_recommended === true;
                          const artifactStatusLabel = messageRun?.status === "waiting_review"
                            ? "待人工确认"
                            : messageRun?.status === "rejected"
                              ? "已退回"
                              : messageRun?.status === "succeeded"
                                ? artifactReviewRecommended ? "已生成 · 建议复核" : "已完成"
                                : "生成中";
                          return (
                            <div className="conversation-artifact-item" key={artifact.id}>
                              <a href={unihrApi.artifactDownloadUrl(artifact.id)}>
                                {isSpreadsheet ? <FileSpreadsheet size={19} /> : <FileText size={19} />}
                                <span><strong>{artifact.display_name}</strong><small>{formatBytes(artifact.byte_size)} · {artifactStatusLabel}</small></span>
                                <Download size={17} />
                              </a>
                              {artifact.kind === "recruitment_report" ? (
                                <details className="recruitment-review-summary" open>
                                  <summary>招聘复核工作台摘要</summary>
                                  <div className="recruitment-review-metrics">
                                    <span>候选人 <b>{recruitmentCandidates.length}</b></span>
                                    <span>唯一材料 <b>{Number(recruitmentCase.unique_source_count ?? artifact.metadata.unique_source_count ?? 0)}</b></span>
                                    <span>完全重复 <b>{recruitmentExactDuplicateCount}</b></span>
                                    <span>旧版归并 <b>{recruitmentMergedVersions}</b></span>
                                    <span>建议复核 <b>{Number(recruitmentCase.selection_count ?? artifact.metadata.selection_count ?? 3)}</b></span>
                                    <span className={`recruitment-harness-badge recruitment-harness-badge--${String(recruitmentHarness.status ?? "unknown")}`}>
                                      Harness {String(recruitmentHarness.status ?? "待检查")} · {Number(recruitmentHarness.score ?? 0).toFixed(0)}
                                    </span>
                                    <span className={`recruitment-harness-badge recruitment-harness-badge--${String(recruitmentHarnessMetrics.ranking_stability ?? "stable") === "stable" ? "passed" : "warning"}`}>
                                      排名稳定性 {String(recruitmentHarnessMetrics.ranking_stability ?? "待检查") === "stable" ? "结构稳定" : "近分需复核"}
                                      {Number(recruitmentHarnessMetrics.near_score_groups ?? 0) > 0 ? ` · ${Number(recruitmentHarnessMetrics.near_score_groups)} 组` : ""}
                                    </span>
                                    <span className={`recruitment-harness-badge recruitment-harness-badge--${recruitmentReviewEvidence || recruitmentUnlocatedEvidence ? "warning" : "passed"}`}>
                                      原文定位 {recruitmentLocatedEvidence}/{recruitmentLocatedEvidence + recruitmentReviewEvidence + recruitmentUnlocatedEvidence}
                                      {recruitmentReviewEvidence ? ` · ${recruitmentReviewEvidence} 条需核对` : ""}
                                    </span>
                                  </div>
                                  <div className="recruitment-rubric-line">
                                    <ShieldCheck size={14} />Rubric 已锁定 · {String(recruitmentRubric.rubric_hash ?? "未记录").slice(0, 12)}
                                  </div>
                                  {recruitmentCandidates.length ? (
                                    <div className="recruitment-candidate-table" role="table" aria-label="候选人复核摘要">
                                      <div role="row"><b role="columnheader">排名</b><b role="columnheader">候选人</b><b role="columnheader">综合分</b><b role="columnheader">证据覆盖</b><b role="columnheader">复核建议</b></div>
                                      {recruitmentCandidates.slice(0, Math.max(3, Number(recruitmentCase.selection_count ?? 3))).map((candidate, index) => (
                                        <div role="row" key={`${artifact.id}-candidate-${index}`}>
                                          <span>{String(candidate.rank ?? index + 1)}</span>
                                          <span><strong>{String(candidate.candidate_name ?? "候选人")}</strong><small>{String(candidate.source_name ?? "")}</small></span>
                                          <span>{Number(candidate.total_score ?? 0).toFixed(1)}</span>
                                          <span>{Math.round(Number(candidate.evidence_coverage ?? 0) * 100)}%</span>
                                          <span>{String(candidate.recommendation ?? "待人工复核")}</span>
                                        </div>
                                      ))}
                                    </div>
                                  ) : <p>尚无可展示的候选人结构化摘要，请下载报告人工核对。</p>}
                                  {recruitmentCandidates.some((candidate) => Array.isArray(candidate.evidence_locations)) ? (
                                    <div className="recruitment-evidence-locations">
                                      {recruitmentCandidates.slice(0, Math.max(3, Number(recruitmentCase.selection_count ?? 3))).map((candidate, candidateIndex) => {
                                        const locations = Array.isArray(candidate.evidence_locations)
                                          ? candidate.evidence_locations as Array<Record<string, unknown>> : [];
                                        return (
                                          <details key={`${artifact.id}-evidence-${candidateIndex}`}>
                                            <summary>{String(candidate.candidate_name ?? "候选人")} · 查看逐项原文证据</summary>
                                            {locations.map((item, locationIndex) => {
                                              const locator = (item.locator ?? {}) as Record<string, unknown>;
                                              const status = String(locator.status ?? "unlocated");
                                              const sourceId = String(locator.source_id ?? "");
                                              return (
                                                <div className={`recruitment-evidence-location recruitment-evidence-location--${status}`} key={`${artifact.id}-location-${candidateIndex}-${locationIndex}`}>
                                                  <div><strong>{String(item.criterion_label ?? "评分维度")}</strong><span>{status === "located" ? "已定位" : status === "review_required" ? "需核对" : "未定位"}</span></div>
                                                  <p>{String(item.evidence ?? "未提供证据")}</p>
                                                  {String(locator.quote ?? "") ? <blockquote>{String(locator.quote)}</blockquote> : null}
                                                  <footer>
                                                    <span>{formatRecruitmentLocator(locator)}</span>
                                                    {sourceId ? <a href={unihrApi.attachmentDownloadUrl(sourceId)} target="_blank" rel="noreferrer">打开原文件</a> : null}
                                                  </footer>
                                                </div>
                                              );
                                            })}
                                          </details>
                                        );
                                      })}
                                    </div>
                                  ) : null}
                                  {recruitmentDuplicates.length ? <p className="recruitment-duplicate-note">已识别完全重复材料；重复文件不会再次参与评分，明细见 Excel“重复材料”工作表。</p> : null}
                                  {recruitmentVersionGroups.length ? (
                                    <details className="recruitment-version-groups">
                                      <summary>
                                        简历版本关系 · 已归并 {recruitmentMergedVersions} 份旧版
                                        {recruitmentVersionReviewGroups ? ` · ${recruitmentVersionReviewGroups} 组待确认` : ""}
                                      </summary>
                                      {recruitmentVersionGroups.map((group, groupIndex) => {
                                        const sources = Array.isArray(group.version_sources) ? group.version_sources as unknown[] : [];
                                        const reviewRequired = String(group.status ?? "") === "review_required";
                                        return (
                                          <div className={`recruitment-version-group ${reviewRequired ? "recruitment-version-group--warning" : ""}`} key={`${artifact.id}-version-${groupIndex}`}>
                                            <div><strong>{reviewRequired ? "需人工确认" : "已自动归并"}</strong><span>{String(group.representative_source ?? "未选代表材料")}</span></div>
                                            <p>{sources.map(String).join(" → ")}</p>
                                            <small>{String(group.selection_reason ?? group.identity_basis ?? "")}</small>
                                          </div>
                                        );
                                      })}
                                    </details>
                                  ) : null}
                                  {String(recruitmentCase.case_id ?? "") ? (
                                    <RecruitmentCasePanel
                                      caseId={String(recruitmentCase.case_id)}
                                      onOpenWorkspace={() => onOpenRecruitmentCase(String(recruitmentCase.case_id))}
                                      user={user}
                                    />
                                  ) : null}
                                </details>
                              ) : null}
                              {artifact.kind === "spreadsheet" && (operationResults.length > 0 || exceptions.length > 0) ? (
                                <details className="spreadsheet-plan-summary">
                                  <summary>查看表格执行摘要</summary>
                                  <div className="spreadsheet-plan-metrics">
                                    <span>白名单操作 <b>{operationResults.length}</b></span>
                                    <span>变更单元格 <b>{changedCellCount}</b></span>
                                    {spreadsheetRepairCount ? <span>自动修复 <b>{spreadsheetRepairCount}</b></span> : null}
                                    <span className={exceptions.length ? "has-exceptions" : ""}>待处理异常 <b>{exceptions.length}</b></span>
                                  </div>
                                  {operationResults.length ? <ol>{operationResults.map((operation, index) => (
                                    <li key={`${artifact.id}-operation-${index}`}><b>{String(operation.type ?? "operation")}</b><span>{String(operation.summary ?? "已完成")}</span></li>
                                  ))}</ol> : null}
                                  {exceptions.length ? <p>请下载文件并查看“UniHR异常清单”，修复后再确认通过。</p> : null}
                                </details>
                              ) : null}
                              {hasWorkbookDiff ? (
                                <details className="spreadsheet-diff-summary">
                                  <summary>查看源文件与结果文件差异</summary>
                                  <div className="spreadsheet-diff-metrics">
                                    <span><b>{Number(workbookDiff.data_changes ?? 0)}</b> 数据</span>
                                    <span><b>{Number(workbookDiff.formula_changes ?? 0)}</b> 公式</span>
                                    <span><b>{Number(workbookDiff.style_changes ?? 0)}</b> 样式</span>
                                    <span><b>{Number(workbookDiff.changed_cells ?? 0)}</b> 变更单元格</span>
                                  </div>
                                  {diffSamples.length ? (
                                    <div className="spreadsheet-diff-table" role="table" aria-label="表格差异样例">
                                      <div role="row"><b role="columnheader">位置</b><b role="columnheader">类型</b><b role="columnheader">源值</b><b role="columnheader">结果值</b></div>
                                      {diffSamples.slice(0, 12).map((sample, index) => (
                                        <div role="row" key={`${artifact.id}-diff-${index}`}>
                                          <span>{String(sample.sheet ?? "")}!{String(sample.cell ?? "")}</span>
                                          <span>{(sample.change_types as unknown[] ?? []).map((type) => type === "formula" ? "公式" : type === "style" ? "样式" : "数据").join(" + ")}</span>
                                          <span title={String(sample.before ?? "空")}>{String(sample.before ?? "空")}</span>
                                          <span title={String(sample.after ?? "空")}>{String(sample.after ?? "空")}</span>
                                        </div>
                                      ))}
                                    </div>
                                  ) : null}
                                  {Boolean(workbookDiff.truncated) ? <p>工作簿较大，当前仅展示受控扫描范围内的差异。</p> : null}
                                </details>
                              ) : null}
                              {isDocument ? (
                                <details className="document-validation-summary">
                                  <summary>查看公文事实与校验摘要</summary>
                                  <div className="document-validation-metrics">
                                    <span>事实台账 <b>{documentFacts.length}</b></span>
                                    <span>已核对 <b>{documentFacts.filter((fact) => fact.status === "supported").length}</b></span>
                                    <span>待补充 <b>{documentFacts.filter((fact) => fact.status === "missing").length}</b></span>
                                    <span>定向返修 <b>{Number(artifact.metadata.repair_count ?? 0)}</b></span>
                                  </div>
                                  {documentFacts.length ? (
                                    <ul>{documentFacts.slice(0, 8).map((fact, index) => (
                                      <li key={`${artifact.id}-fact-${index}`}>
                                        <b>{String(fact.label ?? fact.key ?? "事实")}</b>
                                        <span>{String(fact.value ?? "待补充")}</span>
                                        <small>{fact.status === "supported" ? `来源：${String(fact.source_name ?? "用户需求卡")}` : "需要人工补充或核实"}</small>
                                      </li>
                                    ))}</ul>
                                  ) : <p>这是兼容旧格式生成的草稿，尚无结构化事实台账，请重点人工复核。</p>}
                                  {documentIssues.length || documentWarnings.length ? (
                                    <p>校验提示：{[...documentIssues, ...documentWarnings].slice(0, 6).map(formatDocumentValidationIssue).join("；")}</p>
                                  ) : <p>内容约束、事实来源和版式检查均已通过。</p>}
                                </details>
                              ) : null}
                              {(artifact.kind === "spreadsheet" || isDocument) && artifactHarness.status ? (
                                <div className={`artifact-harness-strip artifact-harness-strip--${String(artifactHarness.status)}`}>
                                  <span><ShieldCheck size={14} />Artifact Harness：{artifactHarness.status === "passed" ? isDocument ? "文档结构与渲染通过" : "实际重算与渲染通过" : "未通过"}</span>
                                  {previewFiles.length ? <a href={unihrApi.artifactPreviewUrl(artifact.id)} target="_blank" rel="noreferrer"><FileCheck2 size={14} />查看 PDF 渲染预览</a> : null}
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    ) : null}
                    {item.role === "assistant" && messageRun ? (
                      <section className={`run-status-card run-status-card--${messageRun.status}`} aria-label="本轮 Agent 运行状态">
                        <div className="run-status-heading">
                          <span><GitBranch size={15} />Agent Run</span>
                          <strong>{messageRun.status === "waiting_review" ? "等待人工复核" : messageRun.status === "waiting_input" ? "等待补充资料" : messageRun.status === "succeeded" ? "运行已完成" : messageRun.status === "rejected" ? "已退回修改" : messageRun.status === "failed" || messageRun.status === "exhausted" ? "运行未通过" : "运行中"}</strong>
                        </div>
                        <p>已执行 {messageRun.iteration}/{messageRun.max_iterations} 轮 · {messageRun.graph_path.map(formatGraphNode).join(" → ")}</p>
                        {messageRun.approval_comment ? <p className="run-review-comment">复核意见：{messageRun.approval_comment}</p> : null}
                        {messageRun.status === "waiting_review" && user.permissions.includes("run.approve") ? (
                          <div className="run-review-actions">
                            <button className="secondary-button" disabled={decidingRunId === messageRun.id} onClick={() => void decideRun(messageRun.id, false)}>退回修改</button>
                            <button className="primary-button" disabled={decidingRunId === messageRun.id} onClick={() => void decideRun(messageRun.id, true)}><Check size={15} />{decidingRunId === messageRun.id ? "正在提交" : "确认通过"}</button>
                          </div>
                        ) : messageRun.status === "waiting_review" ? (
                          <p className="run-review-comment">结果已提交复核；有审批权限的账户确认后才会进入正式完成状态。</p>
                        ) : messageRun.status === "waiting_input" ? (
                          <div className="run-review-actions">
                            <button className="primary-button" disabled={decidingRunId === messageRun.id} onClick={() => void recoverSkillRun(messageRun.id, true)}>
                              <RotateCcw size={15} />{decidingRunId === messageRun.id ? "正在恢复" : "补充资料并继续"}
                            </button>
                          </div>
                        ) : (messageRun.status === "failed" || messageRun.status === "exhausted") && messageRun.error_code === "SkillGraphExecutionFailed" ? (
                          <div className="run-review-actions">
                            <button className="primary-button" disabled={decidingRunId === messageRun.id} onClick={() => void recoverSkillRun(messageRun.id, false)}>
                              <RotateCcw size={15} />{decidingRunId === messageRun.id ? "正在恢复" : "仅重试失败节点"}
                            </button>
                          </div>
                        ) : null}
                      </section>
                    ) : null}
                    {item.role === "assistant" ? <>
                      <div className="answer-feedback" aria-label="评价这条回答">
                        <span>{currentFeedback ? "感谢反馈" : "这条回答有帮助吗？"}</span>
                        <button
                          className={currentFeedback?.helpful === true ? "answer-feedback--selected" : ""}
                          disabled={feedbackSubmitting}
                          onClick={() => void rateAnswer(item.id, true)}
                          aria-pressed={currentFeedback?.helpful === true}
                        ><ThumbsUp size={14} />有帮助</button>
                        <button
                          className={currentFeedback?.helpful === false ? "answer-feedback--selected answer-feedback--negative" : ""}
                          disabled={feedbackSubmitting}
                          onClick={() => { setFeedbackMessageId(item.id); setFeedbackComment(currentFeedback?.comment ?? ""); }}
                          aria-pressed={currentFeedback?.helpful === false}
                        ><ThumbsDown size={14} />没帮助</button>
                      </div>
                      {feedbackMessageId === item.id ? <div className="answer-feedback-editor">
                        <label htmlFor={`feedback-${item.id}`}>哪里没有帮助？<small>可选填；管理员会脱敏后把问题沉淀为回归测试候选。</small></label>
                        <textarea id={`feedback-${item.id}`} value={feedbackComment} onChange={(event) => setFeedbackComment(event.target.value)} placeholder="例如：识别到了冲突，但仍然过早建议按某个版本执行。" maxLength={1000} autoFocus />
                        <div><button className="secondary-button" onClick={() => { setFeedbackMessageId(""); setFeedbackComment(""); }}>取消</button><button className="primary-button" disabled={feedbackSubmitting} onClick={() => void rateAnswer(item.id, false, feedbackComment.trim())}>{feedbackSubmitting ? "正在提交" : "提交反馈"}</button></div>
                      </div> : null}
                    </> : null}
                    {item.role === "assistant" && item.trace.length ? (
                      <details className="conversation-trace">
                        <summary><GitBranch size={14} />查看本轮执行轨迹（{item.trace.length} 步）</summary>
                        <ol>{item.trace.map((step, index) => <li key={`${item.id}-${index}`}><strong>{formatTraceEvent(step.event ?? step.stage ?? step.title ?? `步骤 ${index + 1}`)}</strong><span>{String(step.summary ?? step.detail ?? step.status ?? "已完成")}</span></li>)}</ol>
                      </details>
                    ) : null}
                  </div>
                </article>
              );
            })}
            {pendingMessage ? (
              <article className="conversation-message conversation-message--user conversation-message--pending">
                <div className="avatar">{user.display_name.slice(0, 1)}</div>
                <div><strong>{user.display_name}</strong><p>{pendingMessage}</p></div>
              </article>
            ) : null}
            {!loading && steeringEvents.length ? (
              <article className="conversation-message conversation-message--assistant conversation-message--steering">
                <div className="assistant-avatar"><GitBranch size={17} /></div>
                <div className="steering-handoff-card">
                  <strong>最新指令已接管上一任务</strong>
                  <p>旧计划已安全停止且没有发布；本轮已按你最后发送的要求重新规划并完成。</p>
                  <ol>{steeringEvents.map((item) => (
                    <li key={item.id} className={`steering-handoff-step steering-handoff-step--${item.status}`}>
                      <span>{item.status === "completed" ? <Check size={12} /> : <CircleDot size={12} />}</span>
                      <div><b>{item.summary}</b><small>{formatTraceEvent(item.event)}</small></div>
                    </li>
                  ))}</ol>
                </div>
              </article>
            ) : null}
            {loading ? (
              <article className="conversation-message conversation-message--assistant conversation-message--thinking">
                <div className="assistant-avatar"><Sparkles size={17} /></div>
                <div className="live-run-card">
                  <div className="live-run-heading">
                    <span><RotateCcw className="spin" size={16} /><strong>{steeringEvents.length ? "最新指令正在接管任务" : "UniHR Assistant 正在执行"}</strong></span>
                    <button type="button" onClick={() => void cancelActiveExecution()} disabled={activeExecution?.cancel_requested}>
                      <X size={14} />{activeExecution?.cancel_requested ? "正在安全停止" : "停止"}
                    </button>
                  </div>
                  <p>{executionEvents.at(-1)?.summary ?? "正在接收任务并准备执行环境…"}</p>
                  {executionEvents.length ? (
                    <details className="live-run-events" open>
                      <summary>
                        已完成 {executionEvents.filter((item) => item.status === "completed").length} 个阶段
                        {executionEvents.at(-1)?.status === "active" ? " · 当前仍在执行" : ""}
                      </summary>
                      <ol>{executionEvents.slice(-8).map((item) => (
                        <li key={item.id} className={`live-run-event live-run-event--${item.status}`}>
                          <span>{item.status === "completed"
                            ? <Check size={12} />
                            : item.status === "active"
                              ? <RotateCcw className="spin" size={12} />
                              : <CircleDot size={12} />}</span>
                          <div><strong>{item.summary}</strong><small>{formatTraceEvent(item.event)}</small></div>
                        </li>
                      ))}</ol>
                    </details>
                  ) : null}
                  <small className="live-run-disclosure">这里只展示可审计的执行事实，不展示模型私有思维过程。</small>
                </div>
              </article>
            ) : null}
            <div ref={threadEndRef} />
          </div>
        </section>
      ) : (
        <section className="conversation-empty-state">
          <div className="assistant-home-mark"><Sparkles size={25} /></div>
          <h1>今天想处理哪项人事工作？</h1>
          <p>直接描述目标并添加材料，我会帮你确认规则、建立 Case，再调用合适的 Skill。</p>
        </section>
      )}

      <div className={`conversation-composer-wrap ${hasThread ? "conversation-composer-wrap--thread" : ""}`}>
        {error ? <div className="conversation-error" role="alert"><AlertTriangle size={16} />{error}</div> : null}
        {skillNotice ? <div className="conversation-skill-notice"><CheckCircle2 size={16} /><span>{skillNotice}</span><button onClick={() => onNavigate("skills")}>去管理</button></div> : null}
        {selectedFiles.length ? (
          <div className="composer-selected-files" aria-label="待上传附件">
            {selectedFiles.map((file, index) => (
              <span key={`${file.name}-${file.lastModified}`}><Paperclip size={14} />{file.name}<button type="button" onClick={() => setSelectedFiles((items) => items.filter((_, itemIndex) => itemIndex !== index))} aria-label={`移除 ${file.name}`}><X size={13} /></button></span>
            ))}
          </div>
        ) : null}
        <form className="conversation-composer" onSubmit={submitMessage}>
          <input
            ref={fileInputRef}
            className="sr-only"
            type="file"
            multiple
            accept=".pdf,.docx,.xlsx,.csv,.tsv,.txt,.md,.png,.jpg,.jpeg,.webp"
            onChange={(event) => setSelectedFiles(Array.from(event.target.files ?? []))}
          />
          {selectedSkills.length ? (
            <div className="composer-skill-chips" aria-label="本轮显式调用的 Skill">
              {selectedSkills.map((skill) => (
                <span key={skill.id}><Puzzle size={14} />{skill.display_name}<button type="button" onClick={() => setSelectedSkills((items) => items.filter((item) => item.id !== skill.id))} aria-label={`移除 ${skill.display_name}`}><X size={12} /></button></span>
              ))}
            </div>
          ) : null}
          {slashSkills.length ? (
            <div className="composer-skill-menu" role="listbox" aria-label="选择 Skill">
              <div className="composer-skill-menu-title"><span>调用 Skill</span><small>继续输入可筛选，回车选择</small></div>
              {slashSkills.map((skill, index) => (
                <button
                  type="button"
                  role="option"
                  aria-selected={index === activeSlashIndex}
                  className={index === activeSlashIndex ? "active" : ""}
                  key={skill.id}
                  onMouseEnter={() => setActiveSlashIndex(index)}
                  onClick={() => chooseSlashSkill(skill)}
                >
                  <span><Puzzle size={16} /></span>
                  <div><strong>{skill.display_name}</strong><small>/{skill.name} · {skill.description}</small></div>
                </button>
              ))}
            </div>
          ) : null}
          <textarea
            value={message}
            onChange={(event) => { setMessage(event.target.value); setSlashDismissedFor(""); setActiveSlashIndex(0); }}
            onKeyDown={(event) => {
              if (slashSkills.length && event.key === "ArrowDown") {
                event.preventDefault();
                setActiveSlashIndex((current) => (current + 1) % slashSkills.length);
                return;
              }
              if (slashSkills.length && event.key === "ArrowUp") {
                event.preventDefault();
                setActiveSlashIndex((current) => (current - 1 + slashSkills.length) % slashSkills.length);
                return;
              }
              if (slashSkills.length && event.key === "Escape") {
                event.preventDefault();
                setSlashDismissedFor(message);
                return;
              }
              if (slashSkills.length && event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                chooseSlashSkill(slashSkills[activeSlashIndex] ?? slashSkills[0]);
                return;
              }
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (canSubmit) event.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder={loading ? "输入新指令可调整当前任务优先级…" : "描述你想完成的人事工作…"}
            rows={2}
            aria-label="发送给 UniHR Assistant"
          />
          <div className="conversation-composer-actions">
            <div>
              <button className="composer-icon-button" type="button" onClick={() => fileInputRef.current?.click()}><Paperclip size={19} />添加附件</button>
              <label className="conversation-mode-select">
                <span className="sr-only">选择任务模式</span>
                <select value={mode} onChange={(event) => setMode(event.target.value)}>
                  <option>自动选择</option>
                  <option>简历筛选</option>
                  <option>政策问答</option>
                  <option>联网信息</option>
                  <option>公文材料</option>
                  <option>表格处理</option>
                  <option>图片理解</option>
                </select>
                <ChevronDown size={14} aria-hidden="true" />
              </label>
            </div>
            <button className="conversation-send-button" type="submit" disabled={!canSubmit} aria-label={loading ? "调整当前任务" : "发送"}>
              <Send size={18} />
            </button>
          </div>
          {loading ? (
            <small className="conversation-steering-hint">
              当前任务仍在执行；发送新消息会安全停止旧计划，并按最新要求重新规划。
            </small>
          ) : null}
          {mode === "简历筛选" ? (
            <section className="task-brief-panel recruitment-brief-panel" aria-label="岗位筛选要求">
              <div className="recruitment-brief-heading">
                <div><strong>岗位筛选要求</strong><span>最多 30 份 · PDF / Word / Excel / 文本</span></div>
                <span><ShieldCheck size={14} />只辅助人工复核</span>
              </div>
              <div className="recruitment-brief-grid">
                <label>岗位名称<input value={jobTitle} onChange={(event) => setJobTitle(event.target.value)} placeholder="例如：研发工程师" /></label>
                <label>必须条件<textarea value={mustHaves} onChange={(event) => setMustHaves(event.target.value)} placeholder="学历、年限、技能、资格等" rows={2} /></label>
                <label>加分项<textarea value={niceToHaves} onChange={(event) => setNiceToHaves(event.target.value)} placeholder="项目成果、行业经验等" rows={2} /></label>
                <label>建议人数<input type="number" min="1" max="30" value={selectionCount} onChange={(event) => setSelectionCount(Number(event.target.value))} /></label>
              </div>
              <div className="recruitment-upload-status"><Users size={15} />已选择 {selectedFiles.length} 份材料；评分将展示原文证据、缺口和待核实问题，不使用性别、年龄、婚育等属性。</div>
            </section>
          ) : null}
          {mode === "政策问答" ? (
            <section className="task-brief-panel" aria-label="政策检索要求">
              <div className="recruitment-brief-heading">
                <div><strong>政策检索要求</strong><span>锁定适用范围，回答附原文证据</span></div>
                <span><BookOpen size={14} />混合检索 + 重排</span>
              </div>
              <div className="task-brief-grid">
                <label>政策主题<input value={policyTopic} onChange={(event) => setPolicyTopic(event.target.value)} placeholder="例如：博士后进站备案" /></label>
                <label>适用对象 / 地区<input value={policyScope} onChange={(event) => setPolicyScope(event.target.value)} placeholder="例如：浙江省高校教职工" /></label>
                <label>政策时间点<input type="date" value={policyAsOfDate} onChange={(event) => setPolicyAsOfDate(event.target.value)} /></label>
                <label>知识库范围<input value={knowledgeScope} onChange={(event) => setKnowledgeScope(event.target.value)} placeholder="例如：国家政策 / 组织制度 / 薪酬制度" /></label>
              </div>
            </section>
          ) : null}
          {mode === "联网信息" ? (
            <section className="task-brief-panel public-info-brief-panel" aria-label="联网公开信息说明">
              <div className="recruitment-brief-heading">
                <div><strong>联网公开信息</strong><span>检索公开网页并返回可点击来源</span></div>
                <span><ShieldCheck size={14} />只读 · 查询脱敏</span>
              </div>
              <p>本模式只发送当前输入的问题，不发送会话附件、简历、薪酬数据或内部政策全文。涉及内部正式口径时请改用“政策问答”。</p>
            </section>
          ) : null}
          {mode === "表格处理" ? (
            <section className="task-brief-panel" aria-label="表格处理要求">
              <div className="recruitment-brief-heading">
                <div><strong>表格处理需求</strong><span>原件不覆盖 · 代码执行 · 结果可校验</span></div>
                <span><FileSpreadsheet size={14} />输出新工作簿</span>
              </div>
              <div className="task-brief-grid">
                <label>处理目标<input value={spreadsheetGoal} onChange={(event) => setSpreadsheetGoal(event.target.value)} placeholder="例如：汇总各部门数据并计算完成率" /></label>
                <label>源工作表<input value={sourceSheet} onChange={(event) => setSourceSheet(event.target.value)} placeholder="留空则处理全部来源工作表" /></label>
                <label>表头行<input type="number" min="1" max="200" value={spreadsheetHeaderRow} onChange={(event) => setSpreadsheetHeaderRow(Number(event.target.value))} /></label>
                <label>字段映射<textarea value={mappingNotes} onChange={(event) => setMappingNotes(event.target.value)} placeholder="例如：工号→员工编号，原基数→2025基数" rows={2} /></label>
                <label>计算规则<textarea value={calculationRules} onChange={(event) => setCalculationRules(event.target.value)} placeholder="例如：新基数按上限封顶，金额保留两位" rows={2} /></label>
                <label>筛选与排序<textarea value={spreadsheetFilterSortRules} onChange={(event) => setSpreadsheetFilterSortRules(event.target.value)} placeholder="例如：只显示状态为在职的人员，并按部门、工号升序" rows={2} /></label>
                <label>数据校验<textarea value={spreadsheetValidationRules} onChange={(event) => setSpreadsheetValidationRules(event.target.value)} placeholder="例如：状态只能选择在职/离职；比例必须在0到1之间" rows={2} /></label>
                <label>汇总要求<textarea value={spreadsheetSummaryRules} onChange={(event) => setSpreadsheetSummaryRules(event.target.value)} placeholder="例如：按部门汇总人数和月缴存额，并计算平均值" rows={2} /></label>
                <label>图表<select value={spreadsheetChartType} onChange={(event) => setSpreadsheetChartType(event.target.value)}><option value="none">不生成图表</option><option value="column">柱状图</option><option value="bar">条形图</option><option value="line">折线图</option><option value="pie">饼图</option></select></label>
              </div>
              <details className="task-brief-advanced">
                <summary>版式设置</summary>
                <div className="task-brief-grid task-brief-grid--compact">
                  <label>表头颜色<input type="color" value={`#${spreadsheetHeaderFill}`} onChange={(event) => setSpreadsheetHeaderFill(event.target.value.slice(1).toUpperCase())} /></label>
                  <label>单元格对齐<select value={spreadsheetAlignment} onChange={(event) => setSpreadsheetAlignment(event.target.value)}><option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option><option value="general">自动</option></select></label>
                  <label className="task-brief-check"><input type="checkbox" checked={spreadsheetFreezeHeader} onChange={(event) => setSpreadsheetFreezeHeader(event.target.checked)} />冻结表头</label>
                  <label className="task-brief-check"><input type="checkbox" checked={spreadsheetPreserveStyle} onChange={(event) => setSpreadsheetPreserveStyle(event.target.checked)} />尽量保留原样式</label>
                </div>
              </details>
            </section>
          ) : null}
          {mode === "公文材料" ? (
            <section className="task-brief-panel" aria-label="公文材料要求">
              <div className="recruitment-brief-heading">
                <div><strong>公文材料要求</strong><span>内容与版式分离，导出前自动校验</span></div>
                <span><FileText size={14} />生成 Word 草稿</span>
              </div>
              <div className="task-brief-grid">
                <label>文种<select value={documentType} onChange={(event) => setDocumentType(event.target.value)}><option>通知</option><option>请示</option><option>报告</option><option>函</option><option>会议材料</option><option>纪要</option></select></label>
                <label>标题<input value={documentTitle} onChange={(event) => setDocumentTitle(event.target.value)} placeholder="例如：关于召开年度人事工作会议的通知" /></label>
                <label>主送 / 参会对象<input value={documentAddressee} onChange={(event) => setDocumentAddressee(event.target.value)} placeholder="例如：各部门、各二级学院" /></label>
                <label>落款单位<input value={documentIssuer} onChange={(event) => setDocumentIssuer(event.target.value)} placeholder="例如：人事处" /></label>
                <label>成文日期<input type="date" value={documentDate} onChange={(event) => setDocumentDate(event.target.value)} /></label>
                <label>材料目的<textarea value={documentPurpose} onChange={(event) => setDocumentPurpose(event.target.value)} placeholder="希望通知、请示或会议材料达成什么结果" rows={2} /></label>
                <label>必须写入<textarea value={documentRequiredPoints} onChange={(event) => setDocumentRequiredPoints(event.target.value)} placeholder="每行一点，例如时间、对象、办理要求" rows={2} /></label>
                <label>禁止出现<textarea value={documentProhibitedContent} onChange={(event) => setDocumentProhibitedContent(event.target.value)} placeholder="例如：不写未确认金额，不出现个人联系方式" rows={2} /></label>
                <label>语气<select value={documentTone} onChange={(event) => setDocumentTone(event.target.value)}><option>正式、简洁、审慎</option><option>庄重、规范、明确</option><option>友好、清晰、便于执行</option><option>精炼、适合会议宣读</option></select></label>
              </div>
              <details className="task-brief-advanced">
                <summary>版式设置</summary>
                <div className="task-brief-grid task-brief-grid--compact">
                  <label>版式预设<select value={documentPreset} onChange={(event) => setDocumentPreset(event.target.value)}><option value="official">正式公文</option><option value="meeting">会议材料</option><option value="internal">内部通知</option><option value="freeform">自由材料</option></select></label>
                  <label>正文字体<select value={documentFont} onChange={(event) => setDocumentFont(event.target.value)}><option>仿宋_GB2312</option><option>仿宋</option><option>宋体</option><option>微软雅黑</option><option>黑体</option><option>楷体</option></select></label>
                  <label>字号<select value={documentFontSize} onChange={(event) => setDocumentFontSize(Number(event.target.value))}><option value="14">14 磅</option><option value="16">16 磅</option><option value="18">18 磅</option><option value="22">22 磅</option></select></label>
                  <label>正文对齐<select value={documentAlignment} onChange={(event) => setDocumentAlignment(event.target.value)}><option value="justify">两端对齐</option><option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option></select></label>
                  <label>行距<select value={documentLineSpacing} onChange={(event) => setDocumentLineSpacing(Number(event.target.value))}><option value="1">单倍</option><option value="1.5">1.5 倍</option><option value="2">双倍</option></select></label>
                </div>
              </details>
            </section>
          ) : null}
        </form>
        {!hasThread ? (
          <div className="conversation-suggestions" aria-label="常用任务示例">
            {suggestions.map(({ label, icon: Icon, mode: suggestedMode }) => (
              <button key={label} onClick={() => { setMessage(label); setMode(suggestedMode); }}>
                <Icon size={17} aria-hidden="true" />
                <span>{label}</span>
              </button>
            ))}
          </div>
        ) : null}
        <p className="conversation-privacy-note"><ShieldCheck size={14} />敏感人事资料按权限处理；正式结果需复核后提交。</p>
      </div>
      {resourceOpen ? (
        <div className="resource-workspace-scrim" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setResourceOpen(false); }}>
          <aside className="resource-workspace" role="dialog" aria-modal="true" aria-labelledby="resource-workspace-title">
            <header>
              <div><span>当前安全工作会话</span><h2 id="resource-workspace-title">Resource Workspace</h2><p>集中查看源文件、生成结果、来源关系与真实产物校验。</p></div>
              <button className="icon-button" onClick={() => setResourceOpen(false)} aria-label="关闭资源工作区"><X size={19} /></button>
            </header>
            {loadingResources ? <div className="resource-workspace-loading">正在读取资源清单与 Harness 结果…</div> : resourceWorkspace ? (
              <>
                <div className="resource-workspace-metrics">
                  <span><b>{resourceWorkspace.source_count}</b> 个源文件</span>
                  <span><b>{resourceWorkspace.artifact_count}</b> 个结果文件</span>
                  <span><b>{formatBytes(resourceWorkspace.total_bytes)}</b> 总空间</span>
                </div>
                <div className="resource-workspace-toolbar">
                  <span>{resourceWorkspace.active_count} 个使用中 · {resourceWorkspace.archived_count} 个已归档</span>
                  <button className={showArchivedResources ? "active" : ""} onClick={toggleArchivedResources}><Archive size={14} />{showArchivedResources ? "隐藏归档" : "查看归档"}</button>
                </div>
                {contextDashboard ? (
                  <section className="context-ops-panel" aria-label="本会话上下文运行指标">
                    <div className="context-ops-heading">
                      <div><span>Context Operations</span><strong>本会话上下文生命周期</strong></div>
                      <small>{contextDashboard.snapshot_count} 个持久快照</small>
                    </div>
                    <div className="context-ops-metrics">
                      <article><span>原始内容</span><strong>{contextDashboard.raw_chars.toLocaleString()}</strong><small>字符</small></article>
                      <article><span>实际注入</span><strong>{contextDashboard.injected_chars.toLocaleString()}</strong><small>字符</small></article>
                      <article><span>减少比例</span><strong>{Math.round(contextDashboard.compression_ratio * 100)}%</strong><small>压缩与渐进披露</small></article>
                      <article><span>水合次数</span><strong>{contextDashboard.hydration_count}</strong><small>从外置状态恢复</small></article>
                    </div>
                    <div className="context-ops-query">
                      <span><Search size={15} />Resource Query</span>
                      <p>执行 {contextDashboard.resource_query.executed_calls} 次 · 拒绝 {contextDashboard.resource_query.rejected_calls} 次 · 局部读取 {contextDashboard.resource_query.characters.toLocaleString()} 字符</p>
                      <small>定位有效率 {contextDashboard.resource_query.locator_precision == null ? "暂无调用" : `${Math.round(contextDashboard.resource_query.locator_precision * 100)}%`} · 截断 {contextDashboard.resource_query.truncated_calls} 次</small>
                    </div>
                    {contextDashboard.pending_semantic_facts.length ? (
                      <section className="semantic-facts semantic-facts--pending" aria-label="待确认业务事实">
                        <div className="semantic-facts__heading">
                          <strong>待确认业务事实</strong>
                          <small>确认后才会替代当前值</small>
                        </div>
                        <div className="semantic-facts__pending-list">
                          {contextDashboard.pending_semantic_facts.map((fact) => (
                            <article key={fact.id}>
                              <div>
                                <span>{fact.label}</span>
                                <strong>{fact.display_value}</strong>
                                <small>{fact.status === "conflicted" ? "本轮出现多个不同值" : `置信度 ${Math.round(fact.confidence * 100)}%`}</small>
                              </div>
                              <div className="semantic-facts__actions">
                                <button disabled={resolvingFactId === fact.id} onClick={() => void resolveSemanticFact(fact.id, false)}>拒绝</button>
                                <button disabled={resolvingFactId === fact.id} onClick={() => void resolveSemanticFact(fact.id, true)}>确认采用</button>
                              </div>
                            </article>
                          ))}
                        </div>
                      </section>
                    ) : null}
                    {contextDashboard.semantic_facts.length ? (
                      <section className="semantic-facts" aria-label="当前有效业务事实">
                        <div className="semantic-facts__heading">
                          <strong>当前有效业务事实</strong>
                          <small>字段级版本状态 · 可追溯来源</small>
                        </div>
                        <div className="semantic-facts__grid">
                          {contextDashboard.semantic_facts.map((fact) => (
                            <article key={fact.id}>
                              <span>{fact.label}</span>
                              <strong>{fact.display_value}</strong>
                              <small>v{fact.version} · {fact.extraction_method.startsWith("task_brief") ? "来自任务卡" : "来自本轮对话"}</small>
                            </article>
                          ))}
                        </div>
                      </section>
                    ) : null}
                    {contextDashboard.snapshots.length ? (
                      <details className="context-ops-snapshots">
                        <summary>查看最近快照</summary>
                        <div>{contextDashboard.snapshots.slice(0, 6).map((snapshot) => (
                          <article key={snapshot.id}>
                            <span>{snapshot.compaction_applied ? "已压缩" : "按需装配"}</span>
                            <strong>{snapshot.injected_chars.toLocaleString()} / {snapshot.raw_chars.toLocaleString()} 字符</strong>
                            <small>{new Date(snapshot.created_at).toLocaleString("zh-CN")} · 水合 {snapshot.hydration_count} 次 · {snapshot.fidelity_status}</small>
                          </article>
                        ))}</div>
                      </details>
                    ) : null}
                  </section>
                ) : null}
                <div className="resource-workspace-list">
                  {resourceWorkspace.items.map((resource) => {
                    const harness = (resource.metadata.harness ?? {}) as Record<string, unknown>;
                    const metrics = (harness.metrics ?? {}) as Record<string, unknown>;
                    return (
                      <article className="resource-workspace-item" key={resource.id}>
                        <div className={`resource-workspace-icon resource-workspace-icon--${resource.resource_type}`}>
                          {resource.mime_type.includes("spreadsheet") ? <FileSpreadsheet size={19} /> : resource.resource_type === "source" ? <Paperclip size={19} /> : <FileText size={19} />}
                        </div>
                        <div className="resource-workspace-copy">
                          <div><strong>{resource.display_name}</strong><span className={`resource-status resource-status--${resource.status}`}>{resource.resource_type === "source" ? "源文件" : resource.status === "passed" ? "Harness 通过" : resource.status === "failed" ? "Harness 未通过" : "待检查"}</span></div>
                          <div className="resource-version-row"><span>v{resource.version_number}{resource.is_current ? " · 当前版本" : " · 历史版本"}</span>{resource.archived_at ? <span>已归档</span> : null}{resource.preview_url ? <a href={unihrApi.resourceFileUrl(resource.preview_url)} target="_blank" rel="noreferrer"><FileCheck2 size={12} />预览</a> : null}</div>
                          <p>{formatBytes(resource.byte_size)}{resource.parent_resource_ids.length ? ` · 由 ${resource.parent_resource_ids.length} 个源文件生成` : resource.resource_type === "artifact" && resource.metadata.created_from_scratch ? " · 纯文字从零创建" : ""}</p>
                          {resource.resource_type === "artifact" ? <small>实际检查：{String(metrics.sheet_count ?? metrics.page_count ?? "—")} 个工作表/页面 · {String(metrics.formula_count ?? 0)} 个公式 · {String(resource.metadata.changed_cell_count ?? 0)} 个变更单元格</small> : null}
                        </div>
                        <div className="resource-workspace-actions"><a href={unihrApi.resourceFileUrl(resource.download_url)} aria-label={`下载 ${resource.display_name}`}><Download size={17} /></a><button disabled={resourceBusyId === resource.id} onClick={() => void setResourceArchived(resource.id, !resource.archived_at)} aria-label={`${resource.archived_at ? "恢复" : "归档"} ${resource.display_name}`}><Archive size={15} /></button></div>
                      </article>
                    );
                  })}
                  {!resourceWorkspace.items.length ? <div className="resource-workspace-empty">当前对话还没有源文件或结果文件。</div> : null}
                </div>
              </>
            ) : <div className="resource-workspace-empty">资源工作区暂时不可用。</div>}
          </aside>
        </div>
      ) : null}
    </div>
  );
}

const WORKFLOW_AGENT_LABELS: Record<string, string> = {
  planner: "Planner",
  recruitment: "Recruitment Agent",
  human_review: "人工确认",
  document: "Document Agent",
  spreadsheet: "Spreadsheet Agent",
  public_info: "联网信息 Agent",
  policy: "Policy Agent"
};

function WorkflowCasePanel({
  item,
  artifacts,
  busy,
  onApprove,
  onRevise,
  onRetry,
  onOpenRecruitmentCase
}: {
  item: WorkflowCase;
  artifacts: ConversationDetail["artifacts"];
  busy: boolean;
  onApprove: () => void;
  onRevise: () => void;
  onRetry: () => void;
  onOpenRecruitmentCase: (caseId: string) => void;
}) {
  const completed = item.steps.filter((step) => step.status === "succeeded" || step.status === "skipped").length;
  const artifactMap = new Map(artifacts.map((artifact) => [artifact.id, artifact]));
  return (
    <section className={`workflow-case-card workflow-case-card--${item.status}`} aria-label="跨 Agent 协同 Case">
      <header>
        <div><span><GitBranch size={15} />跨 Agent 协同 Case</span><h2>{item.title}</h2><p>{item.objective}</p></div>
        <div className="workflow-case-progress"><strong>{completed}/{item.steps.length}</strong><small>{item.status === "waiting_human" ? "等待你的确认" : item.status === "succeeded" ? "已完成" : item.status === "failed" ? "需要处理" : "执行中"}</small></div>
      </header>
      <div className="workflow-step-list">
        {item.steps.map((step) => (
          <article className={`workflow-step workflow-step--${step.status}`} key={step.id}>
            <span className="workflow-step-marker">{step.status === "succeeded" ? <Check size={13} /> : step.status === "failed" ? <X size={13} /> : step.status === "running" ? <Activity size={13} /> : step.status === "waiting_human" ? <Clock3 size={13} /> : step.sequence}</span>
            <div><strong>{step.title}</strong><small>{WORKFLOW_AGENT_LABELS[step.agent] ?? step.agent}{step.run_ids.length ? ` · ${step.run_ids.length} 个 Run` : ""}</small>{step.summary ? <p>{step.summary}</p> : null}</div>
            {step.artifact_ids.length ? <div className="workflow-step-artifacts">{step.artifact_ids.map((artifactId) => {
              const artifact = artifactMap.get(artifactId);
              return artifact ? <a href={unihrApi.artifactDownloadUrl(artifact.id)} key={artifact.id}><Download size={12} />{artifact.display_name}</a> : null;
            })}</div> : null}
          </article>
        ))}
      </div>
      <footer>
        <span><ShieldCheck size={14} />{item.rationale}</span>
        <div>
          {item.recruitment_case_id ? <button className="secondary-button" onClick={() => onOpenRecruitmentCase(item.recruitment_case_id)}><BriefcaseBusiness size={14} />复核候选人</button> : null}
          {item.status === "waiting_human" ? <>
            <button className="secondary-button" disabled={busy} onClick={onRevise}><RotateCcw size={14} />退回修改</button>
            <button className="primary-button" disabled={busy} onClick={onApprove}>{busy ? <Activity size={14} /> : <Play size={14} />}{busy ? "正在交接…" : "确认建议并继续"}</button>
          </> : null}
          {item.status === "failed" ? (
            <button className="primary-button" disabled={busy} onClick={onRetry}>
              {busy ? <Activity size={14} /> : <RotateCcw size={14} />}
              {busy ? "正在重试…" : "重试未完成步骤"}
            </button>
          ) : null}
        </div>
      </footer>
    </section>
  );
}

function RecruitmentWorkspace({
  activeCaseId,
  onSelectCase,
  onOpenConversation,
  onTurnComplete,
  onBack,
  user
}: {
  activeCaseId: string | null;
  onSelectCase: (caseId: string) => void;
  onOpenConversation: (conversationId: string) => void;
  onTurnComplete: (conversation: Conversation) => void;
  onBack: () => void;
  user: Principal;
}) {
  const [items, setItems] = useState<RecruitmentCaseRecord[]>([]);
  const [folders, setFolders] = useState<RecruitmentFolderRecord[]>([]);
  const [filter, setFilter] = useState<"all" | RecruitmentCaseRecord["status"]>("all");
  const [folderFilter, setFolderFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [showFolderCreate, setShowFolderCreate] = useState(false);
  const [folderDraft, setFolderDraft] = useState("");
  const [editingFolderId, setEditingFolderId] = useState("");
  const [folderNameDraft, setFolderNameDraft] = useState("");
  const [editingCaseName, setEditingCaseName] = useState(false);
  const [caseNameDraft, setCaseNameDraft] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [newJobTitle, setNewJobTitle] = useState("");
  const [newMustHaves, setNewMustHaves] = useState("");
  const [newNiceToHaves, setNewNiceToHaves] = useState("");
  const [newSelectionCount, setNewSelectionCount] = useState(3);
  const [newFolderId, setNewFolderId] = useState("");
  const [newFiles, setNewFiles] = useState<File[]>([]);
  const recruitmentFileInputRef = useRef<HTMLInputElement>(null);

  const refreshCases = async (preferredId = "") => {
    const [records, folderRecords] = await Promise.all([
      unihrApi.listRecruitmentCases({ archived: showArchived }),
      unihrApi.listRecruitmentFolders()
    ]);
    setItems(records);
    setFolders(folderRecords);
    const nextId = preferredId || activeCaseId || records[0]?.id || "";
    if (nextId && records.some((item) => item.id === nextId)) onSelectCase(nextId);
    else if (records[0]) onSelectCase(records[0].id);
    return records;
  };

  useEffect(() => {
    setLoading(true);
    setError("");
    void refreshCases()
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false));
  }, [showArchived]);

  const selected = items.find((item) => item.id === activeCaseId) ?? null;
  const visibleItems = items.filter((item) => (
    (filter === "all" || item.status === filter)
    && (folderFilter === "all" || (folderFilter === "unfiled" ? !item.folder_id : item.folder_id === folderFilter))
    && (!query.trim() || `${item.case_name} ${item.job_title}`.toLowerCase().includes(query.trim().toLowerCase()))
  ));
  const reviewingCount = items.filter((item) => item.status === "reviewing").length;
  const interviewingCount = items.filter((item) => item.status === "interviewing").length;
  const finalizedCount = items.filter((item) => item.status === "finalized").length;
  const selectedCount = selected?.final_candidate_ids.length ?? 0;

  const updateCase = (record: RecruitmentCaseRecord) => {
    setItems((current) => current.map((item) => item.id === record.id ? record : item));
  };

  const applyManagement = async (action: Parameters<typeof unihrApi.applyRecruitmentCaseAction>[1]) => {
    if (!selected) return;
    setError("");
    try {
      const updated = await unihrApi.applyRecruitmentCaseAction(selected.id, action);
      updateCase(updated);
      if (action.action === "archive_case" || action.action === "restore_case") await refreshCases();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Case 管理操作失败");
    }
  };

  const createFolder = async () => {
    if (!folderDraft.trim()) return;
    try {
      const folder = await unihrApi.createRecruitmentFolder(folderDraft.trim());
      setFolders((current) => [...current, folder].sort((left, right) => left.name.localeCompare(right.name, "zh-CN")));
      setFolderFilter(folder.id);
      setFolderDraft("");
      setShowFolderCreate(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文件夹创建失败");
    }
  };

  const renameFolder = async (folder: RecruitmentFolderRecord) => {
    if (!folderNameDraft.trim() || folderNameDraft.trim() === folder.name) {
      setEditingFolderId("");
      return;
    }
    try {
      const updated = await unihrApi.renameRecruitmentFolder(folder.id, folderNameDraft.trim());
      setFolders((current) => current.map((item) => item.id === updated.id ? updated : item));
      setEditingFolderId("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文件夹重命名失败");
    }
  };

  const deleteFolder = async (folder: RecruitmentFolderRecord) => {
    try {
      await unihrApi.deleteRecruitmentFolder(folder.id);
      if (folderFilter === folder.id) setFolderFilter("unfiled");
      await refreshCases();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文件夹删除失败");
    }
  };

  const submitNewCase = async (event: FormEvent) => {
    event.preventDefault();
    if (!newJobTitle.trim()) {
      setCreateError("请填写岗位名称。");
      return;
    }
    if (!newFiles.length) {
      setCreateError("请至少上传一份简历。");
      return;
    }
    setCreating(true);
    setCreateError("");
    const objective = `为${newJobTitle.trim()}岗位分析 ${newFiles.length} 份简历，建议 ${newSelectionCount} 人进入后续人工复核。`;
    const taskBrief: TaskBrief = {
      version: "1.0",
      kind: "recruitment",
      objective,
      constraints: [newMustHaves.trim()].filter(Boolean),
      deliverables: ["证据化排名", "Excel 报告", "可持续人工复核 Case"],
      selected_skill_ids: [],
      approval_required: true,
      mode_spec: {
        job_title: newJobTitle.trim(),
        must_haves: newMustHaves.trim(),
        nice_to_haves: newNiceToHaves.trim(),
        selection_count: Math.min(30, Math.max(1, newSelectionCount))
      },
      artifact_style: {}
    };
    try {
      const result = await unihrApi.sendTurn({
        message: objective,
        conversationId: null,
        taskMode: "简历筛选",
        files: newFiles,
        documentFont: "仿宋_GB2312",
        documentFontSize: 16,
        taskBrief
      });
      onTurnComplete(result.conversation);
      let records = await unihrApi.listRecruitmentCases();
      let created = records.find((item) => item.run_id === result.run.id) ?? null;
      if (!created) throw new Error("分析已完成，但招聘 Case 尚未建档，请稍后刷新。");
      if (newFolderId) {
        created = await unihrApi.applyRecruitmentCaseAction(created.id, { action: "move_case", folder_id: newFolderId });
        records = records.map((item) => item.id === created?.id ? created : item);
      }
      setItems(records);
      onSelectCase(created.id);
      setShowArchived(false);
      setShowCreate(false);
      setNewJobTitle("");
      setNewMustHaves("");
      setNewNiceToHaves("");
      setNewSelectionCount(3);
      setNewFolderId("");
      setNewFiles([]);
      if (recruitmentFileInputRef.current) recruitmentFileInputRef.current.value = "";
    } catch (reason) {
      setCreateError(reason instanceof Error ? reason.message : "招聘分析失败，请稍后重试");
    } finally {
      setCreating(false);
    }
  };

  const duplicateNames = new Set(newFiles.filter((file, index) => newFiles.findIndex((item) => item.name === file.name) !== index).map((file) => file.name));

  return (
    <div className="recruitment-workspace-page">
      <section className="recruitment-workspace-heading">
        <div>
          <button className="back-link" onClick={onBack}><ArrowLeft size={16} />返回对话</button>
          <span className="section-kicker"><BriefcaseBusiness size={14} /> Recruitment Case Workspace</span>
          <h1>招聘复核工作台</h1>
          <p>集中管理 AI 证据、人工复核、结构化面试和最终建议。所有记录与原始对话、Rubric 和报告保持关联。</p>
        </div>
        <div className="recruitment-workspace-heading-actions">
          {selected ? <button className="secondary-button" onClick={() => onOpenConversation(selected.conversation_id)}><MessageSquareText size={15} />打开来源对话</button> : null}
          <button className="primary-button" onClick={() => setShowCreate(true)}><Plus size={16} />新建招聘 Case</button>
        </div>
      </section>

      <section className="recruitment-workspace-metrics">
        <article><span>全部 Case</span><strong>{items.length}</strong><small>当前账号可访问</small></article>
        <article><span>逐人复核</span><strong>{reviewingCount}</strong><small>尚未进入面试</small></article>
        <article><span>面试中</span><strong>{interviewingCount}</strong><small>已有候选人进入面试</small></article>
        <article><span>已形成建议</span><strong>{finalizedCount}</strong><small>可重新打开复核</small></article>
      </section>

      <div className="recruitment-workspace-layout">
        <aside className="recruitment-case-browser card">
          <header><div><strong>招聘 Case 文件柜</strong><span>按文件夹与最近更新管理</span></div><span>{visibleItems.length}</span></header>
          <label className="recruitment-case-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 Case 或岗位" /></label>
          <div className="recruitment-folder-heading"><span>文件夹</span><button onClick={() => setShowFolderCreate((current) => !current)} aria-label="新建招聘文件夹"><FolderPlus size={15} /></button></div>
          {showFolderCreate ? <div className="recruitment-folder-editor"><input autoFocus value={folderDraft} onChange={(event) => setFolderDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void createFolder(); if (event.key === "Escape") setShowFolderCreate(false); }} placeholder="例如：2026 校招" aria-label="新文件夹名称" /><button onClick={() => void createFolder()} aria-label="保存新文件夹"><Check size={14} /></button><button onClick={() => setShowFolderCreate(false)} aria-label="取消新文件夹"><X size={14} /></button></div> : null}
          <div className="recruitment-folder-list">
            <button className={folderFilter === "all" ? "active" : ""} onClick={() => setFolderFilter("all")}><Folder size={15} />全部 Case <span>{items.length}</span></button>
            <button className={folderFilter === "unfiled" ? "active" : ""} onClick={() => setFolderFilter("unfiled")}><Folder size={15} />未分类 <span>{items.filter((item) => !item.folder_id).length}</span></button>
            {folders.map((folder) => (
              editingFolderId === folder.id ? <div className="recruitment-folder-editor" key={folder.id}><input autoFocus value={folderNameDraft} onChange={(event) => setFolderNameDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void renameFolder(folder); if (event.key === "Escape") setEditingFolderId(""); }} aria-label={`文件夹名称 ${folder.name}`} /><button onClick={() => void renameFolder(folder)} aria-label={`保存文件夹 ${folder.name}`}><Check size={14} /></button><button onClick={() => setEditingFolderId("")} aria-label={`取消重命名 ${folder.name}`}><X size={14} /></button></div>
                : <div className={`recruitment-folder-row ${folderFilter === folder.id ? "active" : ""}`} key={folder.id}>
                  <button onClick={() => setFolderFilter(folder.id)}><Folder size={15} /><span>{folder.name}</span><small>{items.filter((item) => item.folder_id === folder.id).length}</small></button>
                  <button onClick={() => { setEditingFolderId(folder.id); setFolderNameDraft(folder.name); }} aria-label={`重命名文件夹 ${folder.name}`}><Pencil size={13} /></button>
                  <button onClick={() => void deleteFolder(folder)} aria-label={`删除文件夹 ${folder.name}`} title="删除文件夹；Case 会移到未分类"><Trash2 size={13} /></button>
                </div>
            ))}
          </div>
          <button className={`recruitment-archive-toggle ${showArchived ? "active" : ""}`} onClick={() => setShowArchived((current) => !current)}><Archive size={15} />{showArchived ? "返回进行中的 Case" : "查看归档"}</button>
          <div className="recruitment-case-filters" role="tablist" aria-label="筛选招聘 Case">
            {(["all", "reviewing", "interviewing", "finalized"] as const).map((value) => (
              <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{value === "all" ? "全部" : value === "reviewing" ? "复核中" : value === "interviewing" ? "面试中" : "已完成"}</button>
            ))}
          </div>
          <div className="recruitment-case-browser-list">
            {visibleItems.map((item) => {
              const reviewed = item.candidates.filter((candidate) => candidate.review_status !== "pending").length;
              return (
                <button key={item.id} className={item.id === activeCaseId ? "active" : ""} onClick={() => onSelectCase(item.id)}>
                  <div><strong>{item.case_name}</strong><span className={`recruitment-case-status recruitment-case-status--${item.status}`}>{item.status === "reviewing" ? "复核中" : item.status === "interviewing" ? "面试中" : "已形成建议"}</span></div>
                  <p>{item.candidates.length} 位候选人 · 已复核 {reviewed} · 建议 {item.selection_count} 人</p>
                  <small>更新于 {formatDateTime(item.updated_at)}</small>
                </button>
              );
            })}
            {!loading && !visibleItems.length ? <div className="recruitment-case-browser-empty">这个筛选条件下还没有招聘 Case。</div> : null}
            {loading ? <div className="recruitment-case-browser-empty">正在读取招聘 Case…</div> : null}
          </div>
        </aside>

        <main className="recruitment-workspace-main">
          {selected ? (
            <>
              <section className="recruitment-workspace-case-head card">
                <div>
                  <span>当前招聘 Case</span>
                  <h2>{selected.case_name}</h2>
                  <p>Case {selected.id.slice(-8)} · Rubric {selected.rubric_hash.slice(0, 12)} · 更新于 {formatDateTime(selected.updated_at)}</p>
                  <div className="recruitment-case-manage-row">
                    {editingCaseName ? <><input autoFocus value={caseNameDraft} onChange={(event) => setCaseNameDraft(event.target.value)} aria-label="Case 名称" /><button onClick={() => { if (caseNameDraft.trim()) void applyManagement({ action: "rename_case", case_name: caseNameDraft.trim() }); setEditingCaseName(false); }}><Check size={14} />保存名称</button><button onClick={() => setEditingCaseName(false)}><X size={14} />取消</button></> : <button onClick={() => { setCaseNameDraft(selected.case_name); setEditingCaseName(true); }}><Pencil size={14} />重命名</button>}
                    <label><Folder size={14} /><select value={selected.folder_id} onChange={(event) => void applyManagement({ action: "move_case", folder_id: event.target.value })}><option value="">未分类</option>{folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select></label>
                    <button onClick={() => void applyManagement({ action: showArchived ? "restore_case" : "archive_case" })}><Archive size={14} />{showArchived ? "恢复" : "归档"}</button>
                  </div>
                </div>
                <div className="recruitment-workspace-case-stats">
                  <span><b>{selected.candidates.length}</b>候选人</span>
                  <span><b>{selected.candidates.filter((candidate) => candidate.review_status !== "pending").length}</b>已复核</span>
                  <span><b>{selected.interview_sessions.reduce((count, session) => count + session.feedback.length, 0)}</b>面试反馈</span>
                  <span><b>{selectedCount}</b>最终建议</span>
                </div>
              </section>

              <section className="recruitment-candidate-overview card">
                <header><div><strong>候选人对比</strong><span>AI 证据与人工状态并列展示，不自动替代人工判断</span></div></header>
                <div className="recruitment-workspace-table" role="table" aria-label="招聘候选人总览">
                  <div role="row"><b>排名</b><b>候选人</b><b>AI 分数</b><b>证据覆盖</b><b>人工复核</b><b>面试状态</b></div>
                  {selected.candidates.map((candidate) => (
                    <div role="row" key={`${selected.id}-${candidate.candidate_id}`}>
                      <span>{candidate.rank}</span>
                      <span><strong>{candidate.candidate_name}</strong><small>{candidate.source_name}</small></span>
                      <span>{candidate.ai_score.toFixed(1)}</span>
                      <span>{Math.round(candidate.evidence_coverage * 100)}%</span>
                      <span>{reviewStatusLabel(candidate.review_status)}</span>
                      <span>{interviewOutcomeLabel(candidate.interview_outcome)}</span>
                    </div>
                  ))}
                </div>
                {selected.candidates.some((candidate) => candidate.evidence_locations.length) ? (
                  <div className="recruitment-workspace-evidence">
                    <header><strong>逐项原文证据</strong><span>评分摘录必须能回到原文件；需核对和未定位项不能直接作为最终决定依据</span></header>
                    {selected.candidates.map((candidate) => (
                      <details key={`${selected.id}-${candidate.candidate_id}-evidence`}>
                        <summary>{candidate.candidate_name} · {candidate.evidence_locations.filter((item) => String(item.locator?.status ?? "") === "located").length}/{candidate.evidence_locations.length} 条已定位</summary>
                        {candidate.evidence_locations.map((item, index) => {
                          const locator = item.locator ?? {};
                          const status = String(locator.status ?? "unlocated");
                          const sourceId = String(locator.source_id ?? "");
                          return (
                            <div className={`recruitment-evidence-location recruitment-evidence-location--${status}`} key={`${candidate.candidate_id}-workspace-location-${index}`}>
                              <div><strong>{String(item.criterion_label ?? "评分维度")}</strong><span>{status === "located" ? "已定位" : status === "review_required" ? "需核对" : "未定位"}</span></div>
                              <p>{String(item.evidence ?? "未提供证据")}</p>
                              {String(locator.quote ?? "") ? <blockquote>{String(locator.quote)}</blockquote> : null}
                              <footer><span>{formatRecruitmentLocator(locator)}</span>{sourceId ? <a href={unihrApi.attachmentDownloadUrl(sourceId)} target="_blank" rel="noreferrer">打开原文件</a> : null}</footer>
                            </div>
                          );
                        })}
                      </details>
                    ))}
                  </div>
                ) : null}
              </section>

              <section className="recruitment-workspace-review card">
                <RecruitmentCasePanel caseId={selected.id} onChange={updateCase} user={user} />
              </section>
            </>
          ) : (
            <section className="recruitment-workspace-empty card">
              <BriefcaseBusiness size={30} />
              <h2>{error ? "招聘 Case 暂时不可用" : "还没有招聘 Case"}</h2>
              <p>{error || (showArchived ? "归档中没有招聘 Case。" : "可以从这里直接新建，也可以从对话上传简历发起。")}</p>
              <button className="primary-button" onClick={() => setShowCreate(true)}>新建招聘 Case</button>
            </section>
          )}
        </main>
      </div>
      {showCreate ? (
        <div className="recruitment-create-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !creating) setShowCreate(false); }}>
          <form className="recruitment-create-dialog" onSubmit={submitNewCase} aria-label="新建招聘 Case">
            <header><div><span>双入口 · 同一 Recruitment Case</span><h2>新建招聘 Case</h2><p>填写岗位要求并批量导入简历；系统将使用与对话入口相同的 Graph、Harness 和证据规则。</p></div><button type="button" disabled={creating} onClick={() => setShowCreate(false)} aria-label="关闭"><X size={19} /></button></header>
            <div className="recruitment-create-grid">
              <label>岗位名称<input value={newJobTitle} onChange={(event) => setNewJobTitle(event.target.value)} placeholder="例如：人力资源专员（宁波）" /></label>
              <label>建议人数<input type="number" min="1" max="30" value={newSelectionCount} onChange={(event) => setNewSelectionCount(Number(event.target.value))} /></label>
              <label className="wide">必须条件<textarea rows={3} value={newMustHaves} onChange={(event) => setNewMustHaves(event.target.value)} placeholder="学历、经验、技能、资格、到岗要求等" /></label>
              <label className="wide">加分项<textarea rows={3} value={newNiceToHaves} onChange={(event) => setNewNiceToHaves(event.target.value)} placeholder="行业经历、项目成果、工具能力等" /></label>
              <label className="wide">保存到文件夹<select value={newFolderId} onChange={(event) => setNewFolderId(event.target.value)}><option value="">未分类</option>{folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select></label>
            </div>
            <input ref={recruitmentFileInputRef} className="sr-only" type="file" multiple accept=".pdf,.doc,.docx,.xls,.xlsx,.csv,.txt" onChange={(event) => setNewFiles(Array.from(event.target.files ?? []).slice(0, 30))} />
            <button className="recruitment-create-upload" type="button" onClick={() => recruitmentFileInputRef.current?.click()}><Upload size={22} /><strong>选择简历文件</strong><span>最多 30 份，支持 PDF、Word、Excel 和文本</span></button>
            <div className="recruitment-create-preflight">
              <div><strong>导入预检</strong><span>{newFiles.length} 份文件 · {duplicateNames.size ? `${duplicateNames.size} 个重复文件名待分析时核验` : "未发现重复文件名"}</span></div>
              {newFiles.length ? <ul>{newFiles.map((file, index) => <li key={`${file.name}-${index}`}><FileText size={14} /><span>{file.name}</span><small>{formatFileBytes(file.size)}{duplicateNames.has(file.name) ? " · 重复名称" : ""}</small></li>)}</ul> : <p>尚未选择简历。正式分析时还会执行内容指纹去重、解析状态检查和公平性防线。</p>}
            </div>
            {createError ? <div className="form-error"><AlertTriangle size={15} />{createError}</div> : null}
            <footer><button type="button" className="secondary-button" disabled={creating} onClick={() => setShowCreate(false)}>取消</button><button type="submit" className="primary-button" disabled={creating}>{creating ? <><RotateCcw className="spin" size={16} />正在解析并分析…</> : <><Sparkles size={16} />开始分析并建立 Case</>}</button></footer>
          </form>
        </div>
      ) : null}
    </div>
  );
}

type RecruitmentReviewDraft = {
  status: RecruitmentCandidateReview["review_status"];
  note: string;
};

function RecruitmentCasePanel({
  caseId,
  onOpenWorkspace,
  onChange,
  user
}: {
  caseId: string;
  onOpenWorkspace?: () => void;
  onChange?: (record: RecruitmentCaseRecord) => void;
  user: Principal;
}) {
  const [item, setItem] = useState<RecruitmentCaseRecord | null>(null);
  const [reviewDrafts, setReviewDrafts] = useState<Record<string, RecruitmentReviewDraft>>({});
  const [finalIds, setFinalIds] = useState<string[]>([]);
  const [finalRationale, setFinalRationale] = useState("");
  const [busyKey, setBusyKey] = useState("");
  const [error, setError] = useState("");
  const [legacy, setLegacy] = useState(false);

  const hydrate = (record: RecruitmentCaseRecord) => {
    setItem(record);
    onChange?.(record);
    setFinalIds(record.final_candidate_ids);
    setFinalRationale(record.final_rationale);
    setReviewDrafts(Object.fromEntries(record.candidates.map((candidate) => [candidate.candidate_id, {
      status: candidate.review_status,
      note: candidate.reviewer_note
    }])));
  };

  useEffect(() => {
    let cancelled = false;
    void unihrApi.getRecruitmentCase(caseId)
      .then((record) => { if (!cancelled) hydrate(record); })
      .catch((reason: Error) => {
        if (!cancelled) {
          setLegacy(reason.message === "recruitment_case_not_found");
          if (reason.message !== "recruitment_case_not_found") setError(reason.message);
        }
      });
    return () => { cancelled = true; };
  }, [caseId]);

  const apply = async (key: string, action: Parameters<typeof unihrApi.applyRecruitmentCaseAction>[1]) => {
    setBusyKey(key);
    setError("");
    try {
      hydrate(await unihrApi.applyRecruitmentCaseAction(caseId, action));
    } catch (reason) {
      setError(reason instanceof Error ? recruitmentCaseErrorMessage(reason.message) : "保存失败，请稍后重试");
    } finally {
      setBusyKey("");
    }
  };

  if (legacy) return <p className="recruitment-case-legacy">这份报告生成于复核记录上线前；重新发起一次筛选后即可使用逐人复核和面试记录。</p>;
  if (!item) return <div className="recruitment-case-loading">{error || "正在读取招聘 Case…"}</div>;

  const selectedCandidates = item.candidates.filter((candidate) => item.final_candidate_ids.includes(candidate.candidate_id));
  const canManageCase = item.owner_user_id === user.user_id || user.permissions.includes("run.approve");
  return (
    <section className="recruitment-case-panel" aria-label="招聘人工复核记录">
      <header>
        <div><strong>人工复核记录</strong><span>AI 排名与人工决定分开保存，可继续记录面试结论</span></div>
        <div className="recruitment-case-header-actions">
          <span className={`recruitment-case-status recruitment-case-status--${item.status}`}>
            {item.status === "finalized" ? "已形成最终建议" : item.status === "interviewing" ? "面试复核中" : "逐人复核中"}
          </span>
          {onOpenWorkspace ? <button onClick={onOpenWorkspace}><BriefcaseBusiness size={14} />打开全屏工作台</button> : null}
        </div>
      </header>
      {item.status === "finalized" ? (
        <div className="recruitment-finalized-card">
          <span><CheckCircle2 size={16} />最终建议人选</span>
          <strong>{selectedCandidates.map((candidate) => candidate.candidate_name).join("、")}</strong>
          <p>{item.final_rationale}</p>
          <small>{item.finalized_by} · {formatDateTime(item.finalized_at)}</small>
          {canManageCase ? <button disabled={busyKey === "reopen"} onClick={() => void apply("reopen", { action: "reopen" })}><RotateCcw size={14} />重新打开复核</button> : null}
        </div>
      ) : (
        <>
          <div className="recruitment-review-list">
            {item.candidates.map((candidate) => {
              const review = reviewDrafts[candidate.candidate_id] ?? { status: candidate.review_status, note: candidate.reviewer_note };
              return (
                <article key={candidate.candidate_id}>
                  <div className="recruitment-review-person">
                    <span className="recruitment-review-rank">{candidate.rank}</span>
                    <div><strong>{candidate.candidate_name}</strong><small>AI {candidate.ai_score.toFixed(1)} 分 · 证据覆盖 {Math.round(candidate.evidence_coverage * 100)}%</small></div>
                    <span className={`recruitment-review-state recruitment-review-state--${candidate.review_status}`}>{reviewStatusLabel(candidate.review_status)}</span>
                  </div>
                  {canManageCase ? <div className="recruitment-review-fields">
                    <label>人工结论<select value={review.status} onChange={(event) => setReviewDrafts((current) => ({ ...current, [candidate.candidate_id]: { ...review, status: event.target.value as RecruitmentCandidateReview["review_status"] } }))}><option value="pending">待复核</option><option value="shortlisted">进入面试</option><option value="hold">待核实</option><option value="not_advanced">本轮不进入</option></select></label>
                    <label className="recruitment-review-note">复核依据<input value={review.note} placeholder="写下人工判断依据或待核实事项" onChange={(event) => setReviewDrafts((current) => ({ ...current, [candidate.candidate_id]: { ...review, note: event.target.value } }))} /></label>
                    <button disabled={busyKey === `review-${candidate.candidate_id}`} onClick={() => void apply(`review-${candidate.candidate_id}`, { action: "review_candidate", candidate_id: candidate.candidate_id, review_status: review.status, reviewer_note: review.note })}><Check size={14} />保存复核</button>
                  </div> : <small className="recruitment-collaborator-scope">你以面试官身份参与此 Case；候选人短名单和最终建议由 Case 负责人维护。</small>}
                  {candidate.verification_questions.length ? <details><summary>查看面试核实问题（{candidate.verification_questions.length}）</summary><ul>{candidate.verification_questions.map((question, index) => <li key={`${candidate.candidate_id}-question-${index}`}>{question}</li>)}</ul></details> : null}
                  {candidate.interview_notes.length ? <small className="recruitment-legacy-interview-note">历史面试记录 {candidate.interview_notes.length} 条，最近：{candidate.interview_notes.at(-1)?.note}</small> : null}
                </article>
              );
            })}
          </div>
          <RecruitmentInterviewCollaboration caseRecord={item} user={user} busyKey={busyKey} onApply={apply} />
          {canManageCase ? <div className="recruitment-final-decision">
            <strong>形成最终人工建议</strong>
            <p>最多选择 {item.selection_count} 人；只有人工选择会写入最终建议，AI 不会自动录用或淘汰。</p>
            <div>{item.candidates.map((candidate) => <label key={`${candidate.candidate_id}-final`}><input type="checkbox" checked={finalIds.includes(candidate.candidate_id)} disabled={!finalIds.includes(candidate.candidate_id) && finalIds.length >= item.selection_count} onChange={(event) => setFinalIds((current) => event.target.checked ? [...current, candidate.candidate_id] : current.filter((id) => id !== candidate.candidate_id))} />{candidate.candidate_name}</label>)}</div>
            <textarea value={finalRationale} placeholder="说明最终选择依据、仍需核实的风险和下一步安排" onChange={(event) => setFinalRationale(event.target.value)} />
            <button disabled={!finalIds.length || !finalRationale.trim() || busyKey === "finalize"} onClick={() => void apply("finalize", { action: "finalize", final_candidate_ids: finalIds, final_rationale: finalRationale })}><ShieldCheck size={15} />保存最终人工建议</button>
          </div> : null}
        </>
      )}
      <RecruitmentEvidencePackages caseRecord={item} user={user} />
      {error ? <p className="recruitment-case-error"><AlertTriangle size={14} />{error}</p> : null}
    </section>
  );
}

function RecruitmentEvidencePackages({ caseRecord, user }: { caseRecord: RecruitmentCaseRecord; user: Principal }) {
  const [previews, setPreviews] = useState<Partial<Record<RecruitmentPackageType, RecruitmentPackagePreview>>>({});
  const [packages, setPackages] = useState<RecruitmentPackageRecord[]>([]);
  const [busy, setBusy] = useState<RecruitmentPackageType | "">("");
  const [error, setError] = useState("");
  const canManage = caseRecord.owner_user_id === user.user_id || user.permissions.includes("run.approve");

  const load = async () => {
    const [interview, decision, records] = await Promise.all([
      unihrApi.previewRecruitmentPackage(caseRecord.id, "interview_evaluation"),
      unihrApi.previewRecruitmentPackage(caseRecord.id, "final_decision"),
      unihrApi.listRecruitmentPackages(caseRecord.id)
    ]);
    setPreviews({ interview_evaluation: interview, final_decision: decision });
    setPackages(records);
  };

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      unihrApi.previewRecruitmentPackage(caseRecord.id, "interview_evaluation"),
      unihrApi.previewRecruitmentPackage(caseRecord.id, "final_decision"),
      unihrApi.listRecruitmentPackages(caseRecord.id)
    ]).then(([interview, decision, records]) => {
      if (!cancelled) {
        setPreviews({ interview_evaluation: interview, final_decision: decision });
        setPackages(records);
      }
    }).catch((reason: Error) => { if (!cancelled) setError(reason.message); });
    return () => { cancelled = true; };
  }, [caseRecord.id, caseRecord.status, caseRecord.updated_at]);

  const generate = async (packageType: RecruitmentPackageType) => {
    setBusy(packageType);
    setError("");
    try {
      await unihrApi.generateRecruitmentPackage(caseRecord.id, packageType);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? recruitmentPackageErrorMessage(reason.message) : "证据包生成失败，请稍后重试。");
    } finally {
      setBusy("");
    }
  };

  return (
    <section className="recruitment-evidence-packages" aria-label="面试与决策证据包">
      <header>
        <div><strong>面试与决策证据包</strong><span>把 Case 中的简历证据、多人反馈和最终人工结论整理成可归档材料</span></div>
        <span><FileCheck2 size={14} />{packages.length} 个版本</span>
      </header>
      <div className="recruitment-package-actions">
        {(["interview_evaluation", "final_decision"] as RecruitmentPackageType[]).map((packageType) => {
          const preview = previews[packageType];
          const isDecision = packageType === "final_decision";
          return (
            <article key={packageType}>
              <div className={`recruitment-package-icon recruitment-package-icon--${isDecision ? "decision" : "interview"}`}>
                {isDecision ? <ShieldCheck size={18} /> : <ClipboardCheckIcon />}
              </div>
              <div className="recruitment-package-copy">
                <strong>{isDecision ? "最终人工决策证据包" : "结构化面试评价包"}</strong>
                <p>{isDecision ? "汇总最终人选、人工理由、全部反馈和审计信息" : "带着统一问题和评分表进入多人面试"}</p>
                {preview ? <small>{preview.sections.slice(0, 3).join(" · ")}</small> : <small>正在检查生成条件…</small>}
              </div>
              {canManage ? (
                <button disabled={!preview?.can_generate || busy === packageType} onClick={() => void generate(packageType)}>
                  {busy === packageType ? <><span className="button-spinner" />正在生成</> : <><FileCheck2 size={14} />生成 Word + Excel</>}
                </button>
              ) : <span className="recruitment-package-readonly">负责人生成后可下载</span>}
              {preview && !preview.can_generate ? <small className="recruitment-package-blocker">{recruitmentPackageErrorMessage(preview.blockers[0] ?? "")}</small> : null}
            </article>
          );
        })}
      </div>
      {packages.length ? (
        <div className="recruitment-package-history">
          <strong>历史版本</strong>
          {packages.map((item) => (
            <article key={item.id}>
              <div><span>{item.title}</span><small>{item.created_by} · {formatDateTime(item.created_at)}</small></div>
              <span className={item.harness.passed ? "passed" : "warning"}>{item.harness.passed ? "文件校验通过" : "需检查校验结果"}</span>
              <a href={unihrApi.recruitmentPackageDownloadUrl(caseRecord.id, item.id, "docx")} download><FileText size={14} />Word</a>
              <a href={unihrApi.recruitmentPackageDownloadUrl(caseRecord.id, item.id, "xlsx")} download><FileSpreadsheet size={14} />Excel</a>
            </article>
          ))}
        </div>
      ) : <p className="recruitment-package-empty">还没有生成过证据包。首次生成会锁定当前 Case 快照，后续变更请生成新版本。</p>}
      {error ? <p className="recruitment-case-error"><AlertTriangle size={14} />{error}</p> : null}
    </section>
  );
}

function ClipboardCheckIcon() {
  return <FileCheck2 size={18} />;
}

type RecruitmentScheduleDraft = {
  candidateId: string;
  title: string;
  startAt: string;
  endAt: string;
  mode: RecruitmentInterviewSession["mode"];
  location: string;
  meetingUrl: string;
  interviewerIds: string[];
  focusText: string;
};

type RecruitmentFeedbackDraft = {
  recommendation: RecruitmentInterviewFeedback["recommendation"];
  scores: Record<string, number>;
  evidenceNote: string;
  riskNote: string;
};

function localDateTimeInput(value: Date) {
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(0, 16);
}

function defaultRecruitmentSchedule(candidateId = ""): RecruitmentScheduleDraft {
  const start = new Date();
  start.setDate(start.getDate() + 1);
  start.setHours(10, 0, 0, 0);
  const end = new Date(start.getTime() + 60 * 60_000);
  return {
    candidateId, title: "", startAt: localDateTimeInput(start), endAt: localDateTimeInput(end),
    mode: "online", location: "", meetingUrl: "", interviewerIds: [],
    focusText: "岗位动机\n核心项目中的本人贡献\n关键风险与待核实事项"
  };
}

function saveInterviewToPlanner(userId: string, caseRecord: RecruitmentCaseRecord, session: RecruitmentInterviewSession) {
  const start = new Date(session.start_at);
  const end = new Date(session.end_at);
  const storageKey = `${PLANNER_TASKS_KEY}.${userId}`;
  let tasks: PlannerTask[] = [];
  try {
    tasks = JSON.parse(window.localStorage.getItem(storageKey) ?? "[]") as PlannerTask[];
  } catch {
    tasks = [];
  }
  const task: PlannerTask = {
    id: `recruitment-interview-${session.id}`,
    title: session.title,
    date: formatDateKey(start),
    time: `${String(start.getHours()).padStart(2, "0")}:${String(start.getMinutes()).padStart(2, "0")}`,
    durationMinutes: Math.max(5, Math.round((end.getTime() - start.getTime()) / 60_000)),
    completed: session.status === "completed",
    priority: "important",
    linkType: "recruitment",
    linkId: caseRecord.id,
    linkLabel: `${caseRecord.case_name} · 面试`,
    focusSessions: 0
  };
  window.localStorage.setItem(storageKey, JSON.stringify([...tasks.filter((item) => item.id !== task.id), task]));
}

function RecruitmentInterviewCollaboration({
  caseRecord,
  user,
  busyKey,
  onApply
}: {
  caseRecord: RecruitmentCaseRecord;
  user: Principal;
  busyKey: string;
  onApply: (key: string, action: Parameters<typeof unihrApi.applyRecruitmentCaseAction>[1]) => Promise<void>;
}) {
  const shortlisted = caseRecord.candidates.filter((candidate) => candidate.review_status === "shortlisted");
  const [interviewers, setInterviewers] = useState<RecruitmentInterviewerRecord[]>([]);
  const [schedule, setSchedule] = useState<RecruitmentScheduleDraft>(() => defaultRecruitmentSchedule(shortlisted[0]?.candidate_id));
  const [feedbackDrafts, setFeedbackDrafts] = useState<Record<string, RecruitmentFeedbackDraft>>({});
  const [notice, setNotice] = useState("");
  const canManage = caseRecord.owner_user_id === user.user_id || user.permissions.includes("run.approve");

  useEffect(() => {
    void unihrApi.listRecruitmentInterviewers().then((items) => {
      setInterviewers(items);
      setSchedule((current) => ({
        ...current,
        candidateId: current.candidateId || shortlisted[0]?.candidate_id || "",
        interviewerIds: current.interviewerIds.length ? current.interviewerIds : items.some((item) => item.user_id === user.user_id) ? [user.user_id] : []
      }));
    }).catch(() => setNotice("暂时无法读取同组织面试官目录。"));
  }, [caseRecord.id, user.user_id]);

  const submitSchedule = async (event: FormEvent) => {
    event.preventDefault();
    const candidate = caseRecord.candidates.find((item) => item.candidate_id === schedule.candidateId);
    if (!candidate || !schedule.interviewerIds.length) {
      setNotice("请选择候选人和至少一名面试官。");
      return;
    }
    const focusDimensions = schedule.focusText.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean);
    await onApply("schedule-interview", {
      action: "schedule_interview", candidate_id: candidate.candidate_id,
      interview_title: schedule.title.trim() || `${candidate.candidate_name} · 结构化面试`,
      interview_start_at: new Date(schedule.startAt).toISOString(),
      interview_end_at: new Date(schedule.endAt).toISOString(),
      interview_timezone: "Asia/Shanghai", interview_mode: schedule.mode,
      interview_location: schedule.location, interview_meeting_url: schedule.meetingUrl,
      interviewer_ids: schedule.interviewerIds, focus_dimensions: focusDimensions
    });
    setNotice("面试场次已保存；面试官现在可以在自己的招聘工作台查看。 ");
  };

  const draftFor = (session: RecruitmentInterviewSession) => feedbackDrafts[session.id] ?? {
    recommendation: "hold",
    scores: Object.fromEntries(session.focus_dimensions.map((item) => [item, 3])),
    evidenceNote: "",
    riskNote: ""
  };

  const submitFeedback = async (session: RecruitmentInterviewSession) => {
    const draft = draftFor(session);
    if (!draft.evidenceNote.trim()) {
      setNotice("请先填写基于面试回答的事实依据。");
      return;
    }
    await onApply(`feedback-${session.id}`, {
      action: "submit_interview_feedback", interview_session_id: session.id,
      recommendation: draft.recommendation, dimension_scores: draft.scores,
      evidence_note: draft.evidenceNote, risk_note: draft.riskNote
    });
    setNotice("你的结构化面试反馈已保存。 ");
  };

  return (
    <section className="recruitment-interview-collaboration" aria-label="多人面试协作">
      <header>
        <div><strong>多人面试协作</strong><span>分配场次、分别记录证据，最后仍由 Case 负责人形成建议</span></div>
        <span>{caseRecord.interview_sessions.filter((item) => item.status !== "cancelled").length} 个有效场次</span>
      </header>

      {canManage && caseRecord.status !== "finalized" ? (
        <form className="recruitment-interview-scheduler" onSubmit={submitSchedule}>
          <label>候选人<select value={schedule.candidateId} onChange={(event) => setSchedule((current) => ({ ...current, candidateId: event.target.value }))}><option value="">请选择</option>{shortlisted.map((candidate) => <option key={candidate.candidate_id} value={candidate.candidate_id}>{candidate.candidate_name}</option>)}</select></label>
          <label>开始时间<input type="datetime-local" value={schedule.startAt} onChange={(event) => setSchedule((current) => ({ ...current, startAt: event.target.value }))} /></label>
          <label>结束时间<input type="datetime-local" value={schedule.endAt} onChange={(event) => setSchedule((current) => ({ ...current, endAt: event.target.value }))} /></label>
          <label>面试方式<select value={schedule.mode} onChange={(event) => setSchedule((current) => ({ ...current, mode: event.target.value as RecruitmentInterviewSession["mode"] }))}><option value="online">线上会议</option><option value="onsite">现场面试</option><option value="phone">电话沟通</option></select></label>
          {schedule.mode === "online" ? <label className="wide">会议链接<input value={schedule.meetingUrl} onChange={(event) => setSchedule((current) => ({ ...current, meetingUrl: event.target.value }))} placeholder="https://…" /></label> : null}
          {schedule.mode === "onsite" ? <label className="wide">面试地点<input value={schedule.location} onChange={(event) => setSchedule((current) => ({ ...current, location: event.target.value }))} placeholder="会议室或办公地点" /></label> : null}
          <fieldset className="wide"><legend>面试官</legend><div>{interviewers.map((interviewer) => <label key={interviewer.user_id}><input type="checkbox" checked={schedule.interviewerIds.includes(interviewer.user_id)} onChange={(event) => setSchedule((current) => ({ ...current, interviewerIds: event.target.checked ? [...current.interviewerIds, interviewer.user_id] : current.interviewerIds.filter((id) => id !== interviewer.user_id) }))} />{interviewer.display_name}<small>{interviewer.role === "admin" ? "管理员" : "HR 用户"}</small></label>)}</div></fieldset>
          <label className="wide">结构化核实维度<textarea rows={3} value={schedule.focusText} onChange={(event) => setSchedule((current) => ({ ...current, focusText: event.target.value }))} /></label>
          <button className="primary-button" type="submit" disabled={!shortlisted.length || busyKey === "schedule-interview"}><CalendarClock size={15} />安排面试</button>
          {!shortlisted.length ? <small>先在上方把候选人的人工结论设为“进入面试”。</small> : null}
        </form>
      ) : null}

      <div className="recruitment-interview-session-list">
        {caseRecord.interview_sessions.length ? caseRecord.interview_sessions.map((session) => {
          const candidate = caseRecord.candidates.find((item) => item.candidate_id === session.candidate_id);
          const assignedToMe = session.interviewers.some((item) => item.user_id === user.user_id);
          const draft = draftFor(session);
          return (
            <article className={`recruitment-interview-session recruitment-interview-session--${session.status}`} key={session.id}>
              <div className="recruitment-interview-session-heading">
                <div><strong>{session.title}</strong><span>{candidate?.candidate_name} · {formatDateTime(session.start_at)}–{new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(session.end_at))}</span></div>
                <span>{session.status === "completed" ? "反馈已齐" : session.status === "cancelled" ? "已取消" : `待反馈 ${Math.max(0, session.interviewers.length - session.feedback.length)}`}</span>
              </div>
              <div className="recruitment-interview-session-meta"><span>{session.mode === "online" ? "线上" : session.mode === "onsite" ? "现场" : "电话"}</span><span>{session.interviewers.map((item) => item.display_name).join("、")}</span><span>核实：{session.focus_dimensions.join("、")}</span></div>
              <div className="recruitment-interview-session-actions">
                <a href={unihrApi.recruitmentInterviewCalendarUrl(caseRecord.id, session.id)} download><CalendarDays size={14} />下载日历</a>
                <button onClick={() => { saveInterviewToPlanner(user.user_id, caseRecord, session); setNotice("已加入你的‘今日工作’，点击关联任务可返回招聘 Case。 "); }}><ListTodo size={14} />加入今日工作</button>
                {session.meeting_url ? <a href={session.meeting_url} target="_blank" rel="noreferrer"><ExternalLink size={14} />进入会议</a> : null}
                {canManage && session.status !== "cancelled" && caseRecord.status !== "finalized" ? <button className="danger" disabled={busyKey === `cancel-${session.id}`} onClick={() => void onApply(`cancel-${session.id}`, { action: "cancel_interview", interview_session_id: session.id })}>取消场次</button> : null}
              </div>
              {session.feedback.length ? <div className="recruitment-interview-feedback-list">{session.feedback.map((feedback) => <article key={feedback.id}><div><strong>{feedback.interviewer_name}</strong><span>{interviewRecommendationLabel(feedback.recommendation)}</span></div><p>{feedback.evidence_note}</p>{feedback.risk_note ? <small>风险：{feedback.risk_note}</small> : null}<footer>{Object.entries(feedback.dimension_scores).map(([label, score]) => <span key={label}>{label} {score}/5</span>)}</footer></article>)}</div> : null}
              {assignedToMe && session.status !== "cancelled" && caseRecord.status !== "finalized" ? (
                <div className="recruitment-my-interview-feedback">
                  <strong>提交我的结构化反馈</strong>
                  <label>总体建议<select value={draft.recommendation} onChange={(event) => setFeedbackDrafts((current) => ({ ...current, [session.id]: { ...draft, recommendation: event.target.value as RecruitmentInterviewFeedback["recommendation"] } }))}><option value="strong_yes">强烈建议</option><option value="yes">建议进入后续</option><option value="hold">保留观察</option><option value="no">不建议</option><option value="strong_no">明确不建议</option></select></label>
                  <div className="recruitment-dimension-scores">{session.focus_dimensions.map((dimension) => <label key={dimension}>{dimension}<input type="number" min="1" max="5" value={draft.scores[dimension] ?? 3} onChange={(event) => setFeedbackDrafts((current) => ({ ...current, [session.id]: { ...draft, scores: { ...draft.scores, [dimension]: Number(event.target.value) } } }))} /></label>)}</div>
                  <label>事实依据<textarea rows={2} value={draft.evidenceNote} onChange={(event) => setFeedbackDrafts((current) => ({ ...current, [session.id]: { ...draft, evidenceNote: event.target.value } }))} placeholder="记录候选人的原回答、案例和本人贡献，不写空泛印象" /></label>
                  <label>风险与待核实<textarea rows={2} value={draft.riskNote} onChange={(event) => setFeedbackDrafts((current) => ({ ...current, [session.id]: { ...draft, riskNote: event.target.value } }))} /></label>
                  <button disabled={!draft.evidenceNote.trim() || busyKey === `feedback-${session.id}`} onClick={() => void submitFeedback(session)}><ShieldCheck size={14} />保存我的反馈</button>
                </div>
              ) : null}
            </article>
          );
        }) : <p className="recruitment-interview-empty">还没有正式面试场次。进入面试的候选人会在这里完成分配、反馈和日历联动。</p>}
      </div>
      {notice ? <p className="recruitment-interview-notice"><CheckCircle2 size={14} />{notice}</p> : null}
    </section>
  );
}

function interviewRecommendationLabel(value: RecruitmentInterviewFeedback["recommendation"]) {
  return { strong_yes: "强烈建议", yes: "建议进入后续", hold: "保留观察", no: "不建议", strong_no: "明确不建议" }[value];
}

function reviewStatusLabel(status: RecruitmentCandidateReview["review_status"]) {
  return { pending: "待复核", shortlisted: "进入面试", hold: "待核实", not_advanced: "本轮不进入" }[status];
}

function interviewOutcomeLabel(outcome: RecruitmentCandidateReview["interview_outcome"]) {
  return { not_scheduled: "未安排", scheduled: "已安排", passed: "建议进入后续", hold: "继续观察", not_advanced: "不进入后续" }[outcome];
}

function formatDateTime(value: string) {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function recruitmentCaseErrorMessage(value: string) {
  const messages: Record<string, string> = {
    recruitment_case_finalized: "这项复核已经形成最终建议；如需修改，请先重新打开。",
    interview_outcome_and_note_required: "请填写面试记录并选择结论。",
    final_candidates_required: "请至少选择一名最终建议人选。",
    final_candidates_exceed_selection_count: "最终建议人数超过需求卡中锁定的人数。",
    final_rationale_required: "请填写最终人工判断依据。",
    candidate_not_found: "候选人记录已变化，请刷新后重试。"
  };
  return messages[value] ?? value;
}

function recruitmentPackageErrorMessage(value: string) {
  const messages: Record<string, string> = {
    recruitment_case_owner_required: "只有 Case 负责人或管理员可以生成新版本。",
    recruitment_case_not_finalized: "请先在上方保存最终人工建议，再生成最终决策证据包。",
    recruitment_candidates_required: "当前 Case 还没有可整理的候选人。",
    recruitment_package_type_invalid: "证据包类型无效。"
  };
  return messages[value] ?? value;
}

function MetricCard({ icon, label, value, note, tone }: { icon: ReactNode; label: string; value: string; note: string; tone: string }) {
  return (
    <article className="metric-card card">
      <div className={`metric-icon metric-icon--${tone}`}>{icon}</div>
      <div className="metric-copy">
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{note}</small>
      </div>
    </article>
  );
}

function QuickAction({ icon, label, tone, onClick }: { icon: ReactNode; label: string; tone: string; onClick: () => void }) {
  return (
    <button className="quick-action" onClick={onClick}>
      <span className={`quick-icon quick-icon--${tone}`}>{icon}</span>
      <strong>{label}</strong>
      <ArrowRight size={15} />
    </button>
  );
}

function CaseWorkspace({ item, onBack, onAction }: { item: HRCase; onBack: () => void; onAction: (message: string) => void }) {
  const [artifactView, setArtifactView] = useState<"artifacts" | "validation">("artifacts");

  return (
    <div className="case-workspace">
      <div className="case-breadcrumb">
        <button onClick={onBack}><ArrowLeft size={16} /> 返回任务中心</button>
        <span>/</span><span>{item.id}</span>
      </div>

      <section className="case-header">
        <div>
          <div className="case-header-line">
            <span className={`status-badge status-badge--${statusTone[item.status]}`}><span className="live-dot" />{item.status}</span>
            <span className="risk-badge"><ShieldCheck size={14} /> 中风险 · 审批保护</span>
          </div>
          <h1>{item.title}</h1>
          <p>{item.id} · 通用表格处理 · 创建于 2026 年 8 月 4 日</p>
        </div>
        <div className="case-header-actions">
          <button className="secondary-button" onClick={() => onAction("已保存当前任务快照")}><History size={16} /> 版本记录</button>
          <button className="primary-button" onClick={() => onAction("AI 已继续处理 3 条异常记录")}><Play size={16} /> 继续执行</button>
        </div>
      </section>

      <div className="workspace-grid">
        <section className="workspace-panel brief-panel">
          <PanelTitle icon={<BriefcaseBusiness />} title="任务简报" action={<button className="icon-button" aria-label="更多操作"><MoreHorizontal size={17} /></button>} />
          <div className="panel-body">
            <div className="brief-block">
              <span className="field-label">工作目标</span>
              <p>合并多份新入职人员数据，统一字段并生成可复核台账与异常清单。</p>
            </div>
            <div className="brief-block">
              <span className="field-label">预期产物</span>
              <ul className="check-list">
                <li><Check size={14} /> 调整明细表</li>
                <li><Check size={14} /> 汇总与异常清单</li>
                <li><Check size={14} /> 调整说明文档</li>
                <li><Check size={14} /> 政策证据与校验报告</li>
              </ul>
            </div>
            <div className="brief-block">
              <div className="field-row"><span className="field-label">输入资料</span><button>查看全部 12</button></div>
              <div className="file-stack">
                <FileChip icon={<FileSpreadsheet />} title="2026人员基础数据.xlsx" meta="326 条记录" tone="green" />
                <FileChip icon={<FileSpreadsheet />} title="2025调整材料包.xlsx" meta="历史模板" tone="green" />
                <FileChip icon={<FileText />} title="2026缴存基数通知.pdf" meta="政策依据" tone="red" />
              </div>
            </div>
            <div className="owner-row">
              <div className="avatar avatar--small">张</div>
              <div><span className="field-label">任务负责人</span><strong>HR 专员 · 人事部门</strong></div>
              <ChevronDown size={16} />
            </div>
          </div>
        </section>

        <section className="workspace-panel graph-panel">
          <PanelTitle icon={<GitBranch />} title="执行流程" action={<div className="graph-legend"><span className="legend-done" />已完成 <span className="legend-active" />进行中</div>} />
          <div className="graph-body">
            <Suspense fallback={<LazyModuleFallback label="正在加载执行流程…" />}>
              <WorkflowCanvas nodes={workflowNodes} edges={workflowEdges} />
            </Suspense>
          </div>
          <div className="graph-status">
            <div className="pulse-icon"><Gauge size={17} /></div>
            <div><strong>正在处理表格异常</strong><span>已暂停自动提交，等待经办人确认 3 条记录</span></div>
            <button className="text-button" onClick={() => onAction("已聚焦当前执行节点")}>查看节点 <ArrowRight size={14} /></button>
          </div>
        </section>

        <section className="workspace-panel trace-panel">
          <PanelTitle icon={<Activity />} title="Trace 与风险" action={<button className="text-button">筛选</button>} />
          <div className="trace-summary">
            <div><strong>18</strong><span>执行事件</span></div>
            <div><strong>3</strong><span>待确认</span></div>
            <div><strong>0</strong><span>阻断错误</span></div>
          </div>
          <div className="trace-list">
            {traceEvents.map((event, index) => (
              <article className="trace-event" key={`${event.time}-${event.title}`}>
                <div className={`trace-marker trace-marker--${event.status}`}>
                  {event.status === "warning" ? <AlertTriangle size={12} /> : <Check size={12} />}
                </div>
                {index < traceEvents.length - 1 ? <span className="trace-line" /> : null}
                <div><div className="trace-title"><strong>{event.title}</strong><time>{event.time}</time></div><p>{event.detail}</p></div>
              </article>
            ))}
          </div>
          <button className="full-text-button" onClick={() => onAction("已打开完整 Trace 回放")}>查看完整运行记录 <ArrowRight size={15} /></button>
        </section>
      </div>

      <section className="workspace-panel artifact-panel">
        <div className="artifact-tabs" role="tablist" aria-label="产物工作室">
          <button className={artifactView === "artifacts" ? "active" : ""} onClick={() => setArtifactView("artifacts")} role="tab">
            <FileCheck2 size={17} /> 产物工作室 <span>4</span>
          </button>
          <button className={artifactView === "validation" ? "active" : ""} onClick={() => setArtifactView("validation")} role="tab">
            <ShieldCheck size={17} /> 校验报告 <span className="tab-warning">3</span>
          </button>
        </div>
        {artifactView === "artifacts" ? (
          <div className="artifact-grid">
            {artifacts.map((artifact) => (
              <button className="artifact-card" key={artifact.name} onClick={() => onAction(`正在预览：${artifact.name}`)}>
                <span className={`artifact-icon artifact-icon--${artifact.kind.toLowerCase()}`}>
                  {artifact.kind === "Excel" ? <FileSpreadsheet /> : artifact.kind === "Word" ? <FileText /> : <FileCheck2 />}
                </span>
                <span className="artifact-info"><strong>{artifact.name}</strong><small>{artifact.meta}</small></span>
                <span className={`artifact-state ${artifact.state === "已校验" ? "artifact-state--done" : ""}`}>{artifact.state}</span>
                <MoreHorizontal size={17} />
              </button>
            ))}
          </div>
        ) : (
          <div className="validation-view">
            <div className="validation-score"><strong>23/26</strong><span>检查项通过</span></div>
            <div className="validation-copy"><h3>3 项需要人工确认</h3><p>没有阻断性错误。确认两名缺少工资数据的人员，以及一名超过缴存上限的人员后，即可重新执行完整校验。</p></div>
            <button className="primary-button" onClick={() => onAction("已进入异常处理模式")}>处理异常 <ArrowRight size={16} /></button>
          </div>
        )}
        <div className="artifact-footer">
          <span><ShieldCheck size={15} /> 最终材料将在审批通过后冻结版本</span>
          <button className="secondary-button" onClick={() => onAction("材料包正在打包，尚未正式提交")}><Download size={16} /> 下载当前材料包</button>
        </div>
      </section>
    </div>
  );
}

function WorkplaceConnections({ onBack, user }: { onBack: () => void; user: Principal }) {
  const [status, setStatus] = useState<FeishuConnectionStatus | null>(null);
  const [channelStatus, setChannelStatus] = useState<FeishuChannelStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [contextPolicies, setContextPolicies] = useState<import("./api").FeishuGroupContextPolicy[]>([]);
  const [externalGroups, setExternalGroups] = useState<import("./api").FeishuExternalGroup[]>([]);
  const [contextTenant, setContextTenant] = useState("");
  const [contextChat, setContextChat] = useState("");
  const [externalGroupId, setExternalGroupId] = useState("");
  const [externalGroupName, setExternalGroupName] = useState("");
  const [externalWebhookUrl, setExternalWebhookUrl] = useState("");
  const [externalWebhookSecret, setExternalWebhookSecret] = useState("");

  const reload = async () => {
    const [connection, channel, policies, groups] = await Promise.all([
      unihrApi.getFeishuConnection(),
      unihrApi.getFeishuChannelStatus(),
      user.role === "admin" ? unihrApi.listFeishuGroupContext() : Promise.resolve([]),
      user.role === "admin" ? unihrApi.listFeishuExternalGroups() : Promise.resolve([])
    ]);
    setStatus(connection);
    setChannelStatus(channel);
    setContextPolicies(policies);
    setExternalGroups(groups);
  };

  useEffect(() => {
    void reload().catch((reason: Error) => setError(reason.message));
  }, []);

  const connect = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await unihrApi.startFeishuAuthorization();
      window.location.assign(result.authorization_url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法启动飞书授权");
      setBusy(false);
    }
  };

  const disconnect = async () => {
    if (!window.confirm("断开飞书连接？UniHR 将停止读取你的日历和云文档。")) return;
    setBusy(true);
    setError("");
    try {
      setStatus(await unihrApi.disconnectFeishu());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "断开连接失败");
    } finally {
      setBusy(false);
    }
  };

  const saveContextPolicy = async () => {
    if (!contextTenant.trim() || !contextChat.trim()) {
      setError("请填写飞书 Tenant Key 和群 Chat ID");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await unihrApi.saveFeishuGroupContext({
        external_tenant_key: contextTenant.trim(),
        external_conversation_id: contextChat.trim(),
        enabled: true,
        max_messages: 12,
        retention_hours: 72
      });
      await reload();
      setContextChat("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法保存群聊上下文策略");
    } finally {
      setBusy(false);
    }
  };

  const saveExternalGroup = async () => {
    if (!externalGroupId.trim() || !externalGroupName.trim()) {
      setError("请填写外部群标识和群名称；企业机器人直接入群时无需 Webhook");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await unihrApi.saveFeishuExternalGroup({
        group_id: externalGroupId.trim(),
        display_name: externalGroupName.trim(),
        owner_user_id: "usr_unihr_demo",
        webhook_url: externalWebhookUrl.trim(),
        webhook_secret: externalWebhookSecret.trim(),
        enabled: true
      });
      await reload();
      setExternalGroupId("");
      setExternalGroupName("");
      setExternalWebhookUrl("");
      setExternalWebhookSecret("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法保存外部群配置");
    } finally {
      setBusy(false);
    }
  };

  const missingLabels: Record<string, string> = {
    app_id: "飞书应用 ID",
    app_secret: "飞书应用密钥",
    token_encryption_key: "连接凭证加密密钥"
  };

  return (
    <div className="connections-page">
      <div className="connections-heading">
        <div>
          <button className="back-link" onClick={onBack}><ArrowLeft size={16} />返回对话</button>
          <span className="section-kicker">受控办公连接</span>
          <h1>办公连接</h1>
          <p>连接后，Agent 可以在你的授权范围内读取工作信息；创建日程、发送消息和写入表格仍需你逐项确认。</p>
        </div>
      </div>
      {error ? <div className="conversation-error"><AlertTriangle size={16} />{error}</div> : null}
      <section className="digital-employee-card card">
        <div className="digital-employee-card__heading">
          <span><Bot size={22} /></span>
          <div>
            <strong>飞书 AI 人事数字员工</strong>
            <p>把 UniHR 放进飞书单聊与群聊；消息仍进入同一套路由、Agent、Skill、Memory、Graph 和质量门。</p>
          </div>
          <em className={channelStatus?.enabled && channelStatus.credentials_configured ? "is-ready" : "is-waiting"}>
            {!channelStatus ? "检查中" : channelStatus.enabled && channelStatus.credentials_configured ? "通道已就绪" : "等待管理员配置"}
          </em>
        </div>
        {channelStatus ? (
          <div className="digital-employee-card__facts">
            <span><small>接入方式</small><strong>{channelStatus.mode === "long_connection" ? "官方长连接" : "受验证 Webhook"}</strong></span>
            <span><small>已完成投递</small><strong>{channelStatus.queue.delivered || 0}</strong></span>
            <span><small>待绑定身份</small><strong>{channelStatus.pending_bindings.length}</strong></span>
            <span><small>失败待重试</small><strong>{(channelStatus.queue.failed || 0) + (channelStatus.queue.ready_to_deliver || 0)}</strong></span>
          </div>
        ) : null}
        <div className="connection-guardrails">
          <span><ShieldCheck size={16} />群内不同用户默认隔离记忆</span>
          <span><History size={16} />重复事件不会重复运行 Agent</span>
          <span><CheckCircle2 size={16} />生成失败与投递失败可恢复</span>
        </div>
        {channelStatus ? (
          <div className="digital-employee-card__facts">
            <span><small>审批动作完成</small><strong>{channelStatus.action_queue.completed || 0}</strong></span>
            <span><small>未授权点击</small><strong>{channelStatus.action_queue.denied || 0}</strong></span>
            <span><small>群聊上下文</small><strong>{user.role === "admin" ? `${contextPolicies.filter((item) => item.enabled).length} 个已授权群` : "按群授权"}</strong></span>
            <span><small>外部公共群</small><strong>{channelStatus.external_group_count || 0}</strong></span>
          </div>
        ) : null}
      </section>
      {user.role === "admin" ? (
        <section className="connection-card card group-context-admin">
          <div>
            <span className="section-kicker">显式群聊上下文</span>
            <h2>按群授权，不默认监听</h2>
            <p>只有列入策略的群会保留最多 12 条、72 小时的短期文本；普通消息只形成上下文，私聊或明确 @UniHR 才执行 Agent。</p>
          </div>
          <div className="group-context-admin__form">
            <input value={contextTenant} onChange={(event) => setContextTenant(event.target.value)} placeholder="飞书 Tenant Key" />
            <input value={contextChat} onChange={(event) => setContextChat(event.target.value)} placeholder="群 Chat ID（oc_...）" />
            <button className="primary-button" disabled={busy} onClick={() => void saveContextPolicy()}>授权该群</button>
          </div>
          {contextPolicies.length ? (
            <div className="group-context-admin__list">
              {contextPolicies.map((item) => (
                <span key={`${item.external_tenant_key}:${item.external_conversation_id}`}>
                  <strong>{item.external_conversation_id}</strong>
                  <small>{item.enabled ? `已启用 · ${item.max_messages} 条 · ${item.retention_hours} 小时` : "已停用"}</small>
                </span>
              ))}
            </div>
          ) : <small>尚未授权任何群；当前不会保存普通群消息。</small>}
        </section>
      ) : null}
      {user.role === "admin" ? (
        <section className="connection-card card group-context-admin external-group-admin">
          <div>
            <span className="section-kicker">外部群公共入口</span>
            <h2>让外部联系人也能使用，但不继承内部权限</h2>
            <p>外部群只开放公共问答、联网信息和聊天内润色。内部政策、简历、正式文件、审批及组织资料会在工具调用前被阻断。</p>
          </div>
          <div className="external-group-admin__status">
            <span className={channelStatus?.external_relay_enabled && channelStatus.external_relay_secret_configured ? "is-ready" : "is-waiting"}>
              {channelStatus?.external_relay_enabled && channelStatus.external_relay_secret_configured ? "Relay 已就绪" : "等待配置 Relay 环境变量"}
            </span>
            <small>Webhook 与签名密钥只保存在后端，浏览器不会再次读取明文。</small>
          </div>
          <div className="external-group-admin__form">
            <input value={externalGroupId} onChange={(event) => setExternalGroupId(event.target.value)} placeholder="外部群标识，例如 partner-recruitment" />
            <input value={externalGroupName} onChange={(event) => setExternalGroupName(event.target.value)} placeholder="群名称" />
            <input value={externalWebhookUrl} onChange={(event) => setExternalWebhookUrl(event.target.value)} placeholder="自定义机器人 Webhook（仅 Relay 模式需要）" />
            <input type="password" value={externalWebhookSecret} onChange={(event) => setExternalWebhookSecret(event.target.value)} placeholder="自定义机器人签名密钥（可选）" />
            <button className="primary-button" disabled={busy} onClick={() => void saveExternalGroup()}>登记外部群</button>
          </div>
          {externalGroups.length ? (
            <div className="group-context-admin__list">
              {externalGroups.map((item) => (
                <span key={item.group_id}>
                  <strong>{item.display_name}</strong>
                  <small>{item.enabled ? "公共模式已启用" : "已停用"} · {item.group_id} · {item.webhook_configured ? (item.webhook_secret_configured ? "自定义 Relay · 已验签" : "自定义 Relay · 未启用机器人签名") : "企业机器人直接入口 · 群成员免绑定"}</small>
                </span>
              ))}
            </div>
          ) : <small>尚未登记外部群。</small>}
        </section>
      ) : null}
      {!status ? <div className="connections-loading card">正在检查飞书连接状态…</div> : (
        <section className="connection-card card">
          <div className="connection-brand"><span>飞</span><div><strong>飞书</strong><small>日历、云文档、联系人和多维表格</small></div></div>
          <span className={`connection-state connection-state--${status.state}`}>
            {status.connected ? "已连接" : status.configured ? "未连接" : "等待管理员配置"}
          </span>
          <div className="connection-summary">
            {status.connected ? (
              <>
                <strong>{status.display_name || "已授权用户"}</strong>
                <p>UniHR 只会使用当前账户已批准的范围。访问凭证加密保存在后端，不会发送到浏览器或写入 Agent Trace。</p>
                <small>授权范围：{status.scopes.join(" · ") || "以飞书实际授权为准"}</small>
                {status.expires_at ? <small>访问凭证到期：{new Date(status.expires_at).toLocaleString("zh-CN")}</small> : null}
              </>
            ) : status.configured ? (
              <>
                <strong>连接你的飞书账户</strong>
                <p>授权页面会明确列出需要的权限。首期只申请日历忙闲读取和离线续期，不会申请删除或批量导出权限。</p>
              </>
            ) : (
              <>
                <strong>后端尚未完成飞书应用配置</strong>
                <p>这不是连接成功状态。管理员需要先在飞书开放平台创建企业自建应用，并配置回调地址与密钥。</p>
                <small>缺少：{status.missing_configuration.map((item) => missingLabels[item] || item).join("、")}</small>
              </>
            )}
          </div>
          <div className="connection-guardrails">
            <span><ShieldCheck size={16} />读取动作遵循本人权限</span>
            <span><CheckCircle2 size={16} />外部写入必须人工确认</span>
            <span><History size={16} />每次调用进入 Run Trace</span>
          </div>
          <div className="connection-capabilities">
            <div className="connection-capabilities__heading">
              <strong>真实适配器能力</strong>
              <small>{status.adapter_ready ? "后端已就绪；实际可用性以当前账户授权为准" : "等待管理员完成应用配置"}</small>
            </div>
            <div className="connection-capabilities__grid">
              {status.capabilities.map((item) => (
                <span key={item.capability} className={item.available ? "is-ready" : "is-waiting"}>
                  {item.mode === "read" ? "只读" : "需确认"} · {item.label}
                  <small>{item.available ? "可调用" : item.availability === "reauthorization_required" ? "需重新授权" : "尚未授权"}</small>
                </span>
              ))}
            </div>
          </div>
          <div className="connection-actions">
            {status.connected ? (
              <button className="secondary-button" disabled={busy} onClick={() => void disconnect()}>{busy ? "正在断开" : "断开连接"}</button>
            ) : (
              <button className="primary-button" disabled={busy || !status.configured} onClick={() => void connect()}><Link2 size={16} />{busy ? "正在跳转" : "连接飞书"}</button>
            )}
          </div>
        </section>
      )}
      <section className="connection-explainer card">
        <h2>连接后能做什么</h2>
        <div><article><CalendarDays size={19} /><strong>协调面试时间</strong><p>先读取忙闲，再向你展示候选时间；确认后才创建日程。</p></article><article><BookOpen size={19} /><strong>查找工作资料</strong><p>只读取当前用户有权访问的必要文档片段，不把整个云盘塞进上下文。</p></article><article><FileSpreadsheet size={19} /><strong>同步结果材料</strong><p>Word、Excel 通过本地 Harness 后，再由你选择是否上传飞书。</p></article></div>
      </section>
    </div>
  );
}

function PanelTitle({ icon, title, action }: { icon: ReactNode; title: string; action?: ReactNode }) {
  return <div className="panel-title"><div>{icon}<h2>{title}</h2></div>{action}</div>;
}

function FileChip({ icon, title, meta, tone }: { icon: ReactNode; title: string; meta: string; tone: string }) {
  return <button className="file-chip"><span className={`file-chip-icon file-chip-icon--${tone}`}>{icon}</span><span><strong>{title}</strong><small>{meta}</small></span><MoreHorizontal size={15} /></button>;
}

function KnowledgeCenter({ onBack, user }: { onBack: () => void; user: Principal }) {
  const [catalog, setCatalog] = useState<KnowledgeCatalog | null>(null);
  const [versions, setVersions] = useState<KnowledgeSourceVersion[]>([]);
  const [evaluation, setEvaluation] = useState<KnowledgeEvaluation | null>(null);
  const [evaluationHistory, setEvaluationHistory] = useState<KnowledgeEvaluation[]>([]);
  const [evaluationCandidates, setEvaluationCandidates] = useState<PolicyEvaluationCandidate[]>([]);
  const [candidateSources, setCandidateSources] = useState<Record<string, string[]>>({});
  const [candidateNoAnswer, setCandidateNoAnswer] = useState<Record<string, boolean>>({});
  const [candidateConflict, setCandidateConflict] = useState<Record<string, boolean>>({});
  const [candidateBusy, setCandidateBusy] = useState("");
  const [comparison, setComparison] = useState<KnowledgeVersionComparison | null>(null);
  const [compareSelection, setCompareSelection] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [indexing, setIndexing] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [evidence, setEvidence] = useState<KnowledgeEvidence | null>(null);
  const [evidencePage, setEvidencePage] = useState(1);
  const [visionEvaluation, setVisionEvaluation] = useState<VisionEvaluation | null>(null);
  const [visionEvaluating, setVisionEvaluating] = useState(false);
  const [ingestionJob, setIngestionJob] = useState<KnowledgeIngestionJob | null>(null);
  const [ingestionFailures, setIngestionFailures] = useState<KnowledgeIngestionFailure[]>([]);
  const [uploading, setUploading] = useState(false);
  const knowledgeUploadRef = useRef<HTMLInputElement>(null);
  const canManage = user.permissions.includes("knowledge.manage");
  const loadEvaluationGovernance = async () => {
    if (!canManage) return;
    const [history, candidates] = await Promise.all([
      unihrApi.listKnowledgeEvaluationHistory(),
      unihrApi.listPolicyEvaluationCandidates("draft")
    ]);
    setEvaluationHistory(history);
    setEvaluationCandidates(candidates);
    if (!evaluation && history[0]) setEvaluation(history[0]);
  };
  const loadCatalog = async () => {
    const [catalogResult, versionResult] = await Promise.all([
      unihrApi.getKnowledgeCatalog(),
      unihrApi.listKnowledgeVersions()
    ]);
    setCatalog(catalogResult);
    setVersions(versionResult.versions);
  };
  useEffect(() => {
    void loadCatalog().catch((reason: Error) => setError(reason.message));
    if (canManage) void unihrApi.getLatestVisionEvaluation().then(setVisionEvaluation).catch(() => undefined);
    if (canManage) void unihrApi.getLatestKnowledgeIngestionJob().then(setIngestionJob).catch(() => undefined);
    if (canManage) void unihrApi.listKnowledgeIngestionFailures().then((result) => setIngestionFailures(result.failures)).catch(() => undefined);
    if (canManage) void loadEvaluationGovernance().catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!ingestionJob || !["queued", "running"].includes(ingestionJob.status)) return;
    const timer = window.setInterval(() => {
      void unihrApi.getKnowledgeIngestionJob(ingestionJob.job_id).then((next) => {
        setIngestionJob(next);
        if (["completed", "failed"].includes(next.status)) {
          setIndexing(false);
          if (next.status === "completed") void loadCatalog();
          if (next.status === "completed") void unihrApi.listKnowledgeIngestionFailures().then((result) => setIngestionFailures(result.failures));
        }
      }).catch((reason: Error) => { setError(reason.message); setIndexing(false); });
    }, 1200);
    return () => window.clearInterval(timer);
  }, [ingestionJob?.job_id, ingestionJob?.status]);

  const names: Record<string, string> = {
    "calculate-annual-housing-fund": "住房公积金政策资料",
    "department-weekly-minutes": "部门周会与会议材料",
    "hr-audit-material-finder": "人事审计材料",
    "medical-faculty-contact-finder": "人才与院校联系资料",
    "official-document-skill": "公文规范与模板",
    "postdoc-eval-form-builder": "博士后考核表",
    "postdoc-management": "博士后政策与管理",
    "salary-letter-analysis": "薪酬函件与待遇材料",
    unassigned: "待归档资料"
  };
    const folders = Object.entries(catalog?.by_collection ?? {}).sort((left, right) => right[1] - left[1]);
  const disposition = catalog?.by_disposition ?? {};
  const failureLabels: Record<string, string> = {
    empty_or_scanned: "扫描件或空文件",
    unsupported_legacy_format: "旧版 Office 格式",
    PdfStreamError: "PDF 文件结构损坏",
    BadZipFile: "Word 文件结构损坏"
  };
  const sizeText = catalog ? `${(catalog.total_bytes / 1024 / 1024).toFixed(1)} MB` : "—";
  const uploadPolicies = (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    setIndexing(true);
    setError("");
    void unihrApi.uploadKnowledgeFiles(Array.from(files))
      .then((result) => setIngestionJob(result.job))
      .catch((reason: Error) => {
        setError(reason.message);
        setIndexing(false);
      })
      .finally(() => {
        setUploading(false);
        if (knowledgeUploadRef.current) knowledgeUploadRef.current.value = "";
      });
  };

  return (
    <div className="knowledge-center">
      <div className="knowledge-heading">
        <div><button className="back-link" onClick={onBack}><ArrowLeft size={16} />返回对话</button><span className="section-kicker">本地安全资料库</span><h1>政策知识库</h1><p>先完成分类与权限确认，再把政策条款写入检索库；个人与金额材料不会进入组织通用知识库。</p></div>
        <div className="knowledge-heading-actions">
          {canManage ? <>
            <input ref={knowledgeUploadRef} className="sr-only" type="file" accept=".pdf,.docx,.md,.txt" multiple onChange={(event) => uploadPolicies(event.target.files)} />
            <button className="secondary-button" disabled={uploading || indexing} onClick={() => knowledgeUploadRef.current?.click()}><Upload size={16} />{uploading ? "正在上传" : "上传政策"}</button>
          </> : null}
          {canManage ? <button className="secondary-button" disabled={indexing} onClick={() => {
            setIndexing(true);
            setError("");
            void unihrApi.reindexKnowledge()
              .then(setIngestionJob)
              .catch((reason: Error) => setError(reason.message))
              .finally(() => undefined);
          }}>{indexing ? <RotateCcw className="spin" size={16} /> : <WandSparkles size={16} />}{indexing ? "正在建立索引" : "建立版本草稿"}</button> : null}
          <button className="primary-button" onClick={onBack}><MessageSquareText size={16} />在对话中使用</button>
        </div>
      </div>
      {error ? <div className="conversation-error"><AlertTriangle size={16} />{error}</div> : null}
      {ingestionJob ? <div className={`knowledge-job-banner knowledge-job-banner--${ingestionJob.status}`}>
        <div><strong>{ingestionJob.status === "completed" ? "知识入库任务已完成" : ingestionJob.status === "failed" ? "知识入库任务失败" : "知识入库正在后台执行"}</strong><small>{ingestionJob.current_file || (ingestionJob.status === "queued" ? "等待后台开始" : "正在汇总结果")}</small></div>
        <div><span>{ingestionJob.processed_files}/{ingestionJob.total_files || "—"}</span><progress max={Math.max(1, ingestionJob.total_files)} value={ingestionJob.processed_files} /></div>
      </div> : null}
      {!catalog ? <div className="knowledge-loading card">正在检查本地资料目录…</div> : !catalog.configured ? (
        <section className="coming-soon card"><div className="coming-icon"><FolderClock /></div><h1>知识库还是空的</h1><p>管理员可直接点击“上传政策”，系统会保存原文件、生成不可变版本草稿，并建立带页码与章节引用的检索索引。</p></section>
      ) : (
        <>
          <section className="knowledge-metrics">
            <article className="card"><span>发现文件</span><strong>{catalog.file_count}</strong><small>{sizeText} · 原文件保持只读</small></article>
            <article className="card"><span>政策混合索引</span><strong>{catalog.indexed_sources}/{disposition.vector ?? 0}</strong><small>{catalog.indexed_chunks} 个条款片段 · {catalog.failed_sources} 份需处理</small></article>
            <article className="card"><span>结构化表格</span><strong>{disposition.structured_table ?? 0}</strong><small>按 Sheet、字段与行范围引用</small></article>
            <article className="card"><span>Case 隔离材料</span><strong>{disposition.case_only ?? 0}</strong><small>候选人、薪酬等敏感资料</small></article>
          </section>
          <section className="knowledge-layout">
            <div className="knowledge-folder-panel card">
              <div className="section-heading"><div><h2>资料文件夹</h2><p>按业务来源分组，当前显示安全统计而非敏感文件名</p></div><FolderClock size={19} /></div>
              <div className="knowledge-folder-grid">
                {folders.map(([key, count]) => (
                  <button key={key}><span><BookOpen size={20} /></span><strong>{names[key] ?? key}</strong><small>{count} 个文件 · 待确认</small><ChevronRight size={17} /></button>
                ))}
              </div>
            </div>
            <aside className="knowledge-governance card">
              <div className="section-heading"><div><h2>入库闸门</h2><p>这是正式 RAG 之前必须完成的检查</p></div><ShieldCheck size={19} /></div>
              <ol>
                <li><CheckCircle2 size={17} /><span><strong>来源盘点完成</strong><small>已识别格式、体量和业务集合</small></span></li>
                <li><AlertTriangle size={17} /><span><strong>等待权威性确认</strong><small>确认版本、生效日期和适用范围</small></span></li>
                <li><Circle size={17} /><span><strong>条款级切片与引用</strong><small>已索引 {catalog.indexed_sources}/{disposition.vector ?? 0} 份政策候选，保留页码、章节和原始版本</small></span></li>
                <li>{catalog.failed_sources ? <AlertTriangle size={17} /> : <CheckCircle2 size={17} />}<span><strong>入库异常治理</strong><small>{catalog.failed_sources ? Object.entries(catalog.failure_reasons).map(([reason, count]) => `${failureLabels[reason] ?? reason} ${count} 份`).join("；") : "没有待处理的入库异常"}</small></span></li>
                <li><Circle size={17} /><span><strong>检索评测后发布</strong><small>通过问题集、引用准确率和权限测试后才能启用</small></span></li>
              </ol>
              <div className="knowledge-warning"><ShieldCheck size={15} />仅通过入库闸门的政策材料进入向量库；候选人、薪酬等敏感资料继续按 Case 隔离。</div>
            </aside>
          </section>
          <section className="knowledge-version-panel card">
            <div className="section-heading"><div><h2>政策版本与发布</h2><p>{canManage ? "重新索引只创建不可变草稿；管理员确认版本和权限后才能发布" : "这里只显示当前账号有权检索的已发布政策"}</p></div>{canManage ? <button className="secondary-button" disabled={evaluating} onClick={() => {
              setEvaluating(true);
              setError("");
              void unihrApi.runKnowledgeEvaluation()
                .then((result) => { setEvaluation(result); void loadEvaluationGovernance(); })
                .catch((reason: Error) => setError(reason.message))
                .finally(() => setEvaluating(false));
            }}><Gauge size={15} />{evaluating ? "正在评测" : "运行固定评测集"}</button> : <ShieldCheck size={19} />}</div>
            <div className="knowledge-version-list">
              {versions.length ? versions.map((item) => (
                <article key={`${item.version_id}-${item.publication_id ?? "draft"}`}>
                  <div>
                    <strong>{item.title}</strong>
                    <span className={`knowledge-quality knowledge-quality--${item.ingestion_status}`}>{item.ingestion_status === "ready" ? "入库检查通过" : item.ingestion_status === "review_required" ? "OCR 证据待复核" : "入库失败"} · 质量分 {item.quality_score}</span>
                    <small>{names[item.collection] ?? item.collection} · {item.chunk_count} 个条款 · {item.source_version ?? "未识别版本号"}</small>
                    <span>{item.authority_level} · {item.policy_status} · {item.effective_date ?? "生效日期待确认"}</span>
                  </div>
                  <div className="knowledge-version-actions">
                    <span className={`status-badge status-badge--${item.lifecycle_status === "published" ? "green" : item.lifecycle_status === "withdrawn" ? "gray" : "amber"}`}>{item.lifecycle_status === "published" ? "已发布" : item.lifecycle_status === "withdrawn" ? "已撤回" : "待发布"}</span>
                    {canManage && item.ingestion_status === "review_required" ? <button className="secondary-button" onClick={() => {
                      setError("");
                      void unihrApi.getKnowledgeEvidence(item.version_id)
                        .then((result) => { setEvidence(result); setEvidencePage(result.blocks[0]?.page ?? 1); })
                        .catch((reason: Error) => setError(reason.message));
                    }}>查看 OCR 证据</button> : null}
                    {canManage && item.ingestion_status === "review_required" && item.quality_review_status !== "approved" ? <button className="secondary-button" onClick={() => {
                      void unihrApi.reviewKnowledgeQuality(item.version_id, true, "管理员已对照原图完成人工复核")
                        .then(loadCatalog).catch((reason: Error) => setError(reason.message));
                    }}>确认复核</button> : null}
                    {canManage && item.lifecycle_status !== "published" ? <button className="secondary-button" disabled={item.ingestion_status === "failed" || (item.ingestion_status === "review_required" && item.quality_review_status !== "approved")} onClick={() => {
                      setIndexing(true);
                      void unihrApi.publishKnowledgeVersion(item.version_id)
                        .then(loadCatalog)
                        .catch((reason: Error) => setError(reason.message))
                        .finally(() => setIndexing(false));
                    }}>发布给组织</button> : null}
                    {canManage ? <button className="secondary-button" onClick={() => {
                      const next = compareSelection.includes(item.version_id)
                        ? compareSelection.filter((id) => id !== item.version_id)
                        : [...compareSelection.slice(-1), item.version_id];
                      setCompareSelection(next);
                      setComparison(null);
                      if (next.length === 2) {
                        void unihrApi.compareKnowledgeVersions(next[0], next[1])
                          .then(setComparison)
                          .catch((reason: Error) => { setError(reason.message); setCompareSelection([]); });
                      }
                    }}>{compareSelection.includes(item.version_id) ? <Check size={13} /> : null}{compareSelection.includes(item.version_id) ? "已选" : "版本对比"}</button> : null}
                    {canManage && item.lifecycle_status === "published" && item.publication_id ? <button className="danger-button" onClick={() => {
                      setIndexing(true);
                      void unihrApi.withdrawKnowledgePublication(item.publication_id as string)
                        .then(loadCatalog)
                        .catch((reason: Error) => setError(reason.message))
                        .finally(() => setIndexing(false));
                    }}>撤回</button> : null}
                  </div>
                </article>
              )) : <div className="knowledge-empty">尚无可见的政策版本。</div>}
            </div>
            {canManage ? <div className="vision-eval-toolbar"><div><strong>扫描件质量评测</strong><small>使用固定人工标准答案衡量字段、坐标和风险拦截，不使用模型自评代替准确率。</small></div><button className="secondary-button" disabled={visionEvaluating} onClick={() => {
              setVisionEvaluating(true); setError("");
              void unihrApi.runVisionEvaluation().then(setVisionEvaluation)
                .catch((reason: Error) => setError(reason.message))
                .finally(() => setVisionEvaluating(false));
            }}><Gauge size={15} />{visionEvaluating ? "正在评测" : "运行视觉固定评测"}</button></div> : null}
            {visionEvaluation ? <div className="vision-eval-metrics">
              <div><span>字段准确率</span><strong>{Math.round(visionEvaluation.field_precision * 100)}%</strong></div>
              <div><span>字段召回率</span><strong>{Math.round(visionEvaluation.field_recall * 100)}%</strong></div>
              <div><span>坐标 IoU</span><strong>{visionEvaluation.mean_bbox_iou.toFixed(2)}</strong></div>
              <div><span>风险拦截率</span><strong>{Math.round(visionEvaluation.review_recall * 100)}%</strong></div>
              <div className={visionEvaluation.false_safe_rate > 0 ? "danger" : "safe"}><span>误放行率</span><strong>{Math.round(visionEvaluation.false_safe_rate * 100)}%</strong></div>
              <p><ShieldCheck size={14} />固定 {visionEvaluation.case_count} 个扫描样例 · {visionEvaluation.matched_field_count}/{visionEvaluation.expected_field_count} 个字段命中 · 报告 {visionEvaluation.evaluation_id}</p>
            </div> : null}
            {canManage && ingestionFailures.length ? <div className="knowledge-failure-panel">
              <header><div><strong>入库失败隔离区</strong><small>失败文件不会阻塞其他资料，也不会在普通重建中反复消耗 OCR。</small></div><span>{ingestionFailures.length} 份待处理</span></header>
              <div>{ingestionFailures.slice(0, 12).map((failure) => <article key={failure.source_id}>
                <div><strong>{failure.relative_path.split("/").pop()}</strong><small>{failure.collection} · {failure.reason} · 已重试 {failure.retry_count} 次</small></div>
                <button className="secondary-button" disabled={Boolean(ingestionJob && ["queued", "running"].includes(ingestionJob.status))} onClick={() => {
                  setIndexing(true); setError("");
                  void unihrApi.retryKnowledgeIngestionFailure(failure.source_id).then(setIngestionJob)
                    .catch((reason: Error) => { setError(reason.message); setIndexing(false); });
                }}><RotateCcw size={14} />只重试这一份</button>
              </article>)}</div>
            </div> : null}
            {evidence ? <div className="knowledge-evidence-review">
              <header><div><strong>{evidence.title}</strong><small>原图定位复核 · 第 {evidencePage} 页</small></div><button className="secondary-button" onClick={() => setEvidence(null)}>关闭</button></header>
              <div className="knowledge-evidence-stage">
                <img src={unihrApi.knowledgePagePreviewUrl(evidence.version_id, evidencePage)} alt={`${evidence.title} 第 ${evidencePage} 页`} />
                {evidence.blocks.filter((block) => block.page === evidencePage).map((block, index) => <span key={`${block.page}-${index}`} className={block.needs_review ? "needs-review" : ""} title={`${block.text} · ${Math.round((block.model_confidence ?? 0) * 100)}%`} style={{ left: `${block.bbox[0] / 10}%`, top: `${block.bbox[1] / 10}%`, width: `${(block.bbox[2] - block.bbox[0]) / 10}%`, height: `${(block.bbox[3] - block.bbox[1]) / 10}%` }} />)}
              </div>
              <div className="knowledge-evidence-pages">{[...new Set(evidence.blocks.map((block) => block.page))].map((page) => <button className={page === evidencePage ? "active" : ""} onClick={() => setEvidencePage(page)} key={page}>第 {page} 页</button>)}</div>
              <ul>{evidence.blocks.filter((block) => block.page === evidencePage).map((block, index) => <li key={`text-${index}`}><b>{block.needs_review ? "待复核" : "已定位"}</b><span>{block.text}</span><small>{Math.round((block.model_confidence ?? 0) * 100)}%</small></li>)}</ul>
            </div> : null}
            {evaluation ? <div className="knowledge-evaluation">
              <div><span>Hit Rate</span><strong>{Math.round(evaluation.hit_rate * 100)}%</strong></div>
              <div><span>Recall@{evaluation.top_k}</span><strong>{Math.round(evaluation.recall_at_k * 100)}%</strong></div>
              <div><span>MRR</span><strong>{evaluation.mean_reciprocal_rank.toFixed(2)}</strong></div>
              <div><span>NDCG@{evaluation.top_k}</span><strong>{evaluation.ndcg_at_k.toFixed(2)}</strong></div>
              <div><span>引用覆盖率</span><strong>{Math.round(evaluation.citation_coverage * 100)}%</strong></div>
              <div className={evaluation.effective_date_misuse_rate > 0 ? "danger" : "safe"}><span>有效期误用率</span><strong>{evaluation.temporal_guard_case_count ? `${Math.round(evaluation.effective_date_misuse_rate * 100)}%` : "—"}</strong></div>
              <div><span>冲突证据召回</span><strong>{evaluation.conflict_case_count ? `${Math.round(evaluation.conflict_evidence_recall * 100)}%` : "—"}</strong></div>
              <div><span>无答案准确率</span><strong>{Math.round(evaluation.no_answer_accuracy * 100)}%</strong></div>
              <p><ShieldCheck size={14} />固定 {evaluation.case_count} 题 · {evaluation.cases.filter((item) => item.hit).length} 题通过 · 时点专项 {evaluation.temporal_guard_case_count} 题 · 冲突专项 {evaluation.conflict_case_count} 题{evaluation.duration_ms !== undefined ? ` · ${evaluation.duration_ms} ms` : ""}</p>
            </div> : null}
            {canManage ? <div className="policy-eval-governance">
              <section>
                <header><div><strong>评测历史</strong><small>每次保存数据集、检索模型和回答提示版本，避免“分数变了但不知道为什么”。</small></div><span>{evaluationHistory.length} 次</span></header>
                {evaluationHistory.length ? <div className="policy-eval-history">{evaluationHistory.slice(0, 6).map((item) => <button key={item.evaluation_id} onClick={() => setEvaluation(item)}>
                  <span>{item.created_at ? new Date(item.created_at).toLocaleString("zh-CN") : "历史评测"}</span>
                  <strong>{Math.round(item.hit_rate * 100)}%</strong>
                  <small>{item.case_count} 题 · {item.rerank_model ?? "检索模型未记录"} · {item.prompt_version ?? "提示版本未记录"}</small>
                </button>)}</div> : <p className="knowledge-empty">运行一次固定评测后，这里会形成可追溯历史。</p>}
              </section>
              <section>
                <header><div><strong>用户反馈转回归用例</strong><small>“没帮助”只生成脱敏草稿；必须由管理员人工标注正确证据，才会进入固定评测。</small></div><span>{evaluationCandidates.length} 条待标注</span></header>
                {evaluationCandidates.length ? <div className="policy-eval-candidates">{evaluationCandidates.map((candidate) => {
                  const selected = candidateSources[candidate.candidate_id] ?? [];
                  const noAnswer = Boolean(candidateNoAnswer[candidate.candidate_id]);
                  return <article key={candidate.candidate_id}>
                    <strong>{candidate.question}</strong>
                    {candidate.comment ? <p>用户反馈：{candidate.comment}</p> : null}
                    <small>系统曾引用的资料仅作为标注候选，不会自动成为标准答案：</small>
                    <div>{candidate.suggested_sources.length ? candidate.suggested_sources.map((source) => <label key={source.source_id}>
                      <input type="checkbox" disabled={noAnswer} checked={selected.includes(source.source_id)} onChange={(event) => setCandidateSources((current) => ({ ...current, [candidate.candidate_id]: event.target.checked ? [...selected, source.source_id] : selected.filter((id) => id !== source.source_id) }))} />
                      <span>{source.title || source.source_id}<small>{source.version || source.status}</small></span>
                    </label>) : <em>本次回答没有可标注的证据来源</em>}</div>
                    <footer>
                      <label><input type="checkbox" checked={noAnswer} onChange={(event) => setCandidateNoAnswer((current) => ({ ...current, [candidate.candidate_id]: event.target.checked }))} />正确结果应为“证据不足”</label>
                      <label><input type="checkbox" checked={Boolean(candidateConflict[candidate.candidate_id])} onChange={(event) => setCandidateConflict((current) => ({ ...current, [candidate.candidate_id]: event.target.checked }))} />应识别版本冲突</label>
                      <button className="secondary-button" disabled={candidateBusy === candidate.candidate_id} onClick={() => {
                        setCandidateBusy(candidate.candidate_id);
                        void unihrApi.reviewPolicyEvaluationCandidate(candidate.candidate_id, { approved: false, notes: "管理员判定不适合作为固定评测" })
                          .then(loadEvaluationGovernance).catch((reason: Error) => setError(reason.message)).finally(() => setCandidateBusy(""));
                      }}>忽略</button>
                      <button className="primary-button" disabled={candidateBusy === candidate.candidate_id || (!noAnswer && !selected.length)} onClick={() => {
                        setCandidateBusy(candidate.candidate_id);
                        void unihrApi.reviewPolicyEvaluationCandidate(candidate.candidate_id, {
                          approved: true, expected_source_ids: noAnswer ? [] : selected,
                          expected_no_answer: noAnswer, expected_conflict: Boolean(candidateConflict[candidate.candidate_id]),
                          notes: "管理员已人工核对证据并纳入固定评测"
                        }).then(loadEvaluationGovernance).catch((reason: Error) => setError(reason.message)).finally(() => setCandidateBusy(""));
                      }}>标注并加入评测</button>
                    </footer>
                  </article>;
                })}</div> : <p className="knowledge-empty">暂无待标注反馈；普通用户对政策回答点击“没帮助”后，会在这里生成脱敏候选。</p>}
              </section>
            </div> : null}
            {comparison ? <div className="knowledge-comparison">
              <div><strong>版本差异：{comparison.title}</strong><span>文本相似度 {Math.round(comparison.similarity * 100)}% · 新增 {comparison.added_chunk_count} 段 · 删除 {comparison.removed_chunk_count} 段</span></div>
              {Object.keys(comparison.metadata_changes).length ? <p>元数据变化：{Object.keys(comparison.metadata_changes).join("、")}</p> : null}
              {comparison.added_samples.length ? <details><summary>查看新增条款样例</summary><ul>{comparison.added_samples.map((sample, index) => <li key={`added-${index}`}>{sample}</li>)}</ul></details> : null}
              {comparison.removed_samples.length ? <details><summary>查看删除条款样例</summary><ul>{comparison.removed_samples.map((sample, index) => <li key={`removed-${index}`}>{sample}</li>)}</ul></details> : null}
            </div> : null}
          </section>
        </>
      )}
    </div>
  );
}

function skillErrorMessage(reason: Error): string {
  const known: Record<string, string> = {
    system_skill_is_read_only: "系统 Skill 由平台统一维护，个人用户无需也不能停用。",
    system_skill_cannot_be_deleted: "系统 Skill 不能删除。",
    skill_generation_missing_instructions: "模型没有生成完整步骤，请补充任务目标后重试。",
    skill_generation_validation_failed: "Skill Builder Agent 两次修复后仍未通过质量门，请补充更具体的输入、步骤和输出要求。",
    skill_validation_failed: "该 Skill 尚未通过结构或安全校验，修正失败项后才能启用。",
    skill_publish_not_approved: "Skill 发布复核未通过，已保留为草稿。",
    skill_not_active: "所选 Skill 尚未启用，请先到 Skill 中心检查并启用。",
    skill_file_must_be_md_or_zip: "只支持上传 SKILL.md 或包含 SKILL.md 的 ZIP 包。",
    skill_manifest_missing: "ZIP 包中没有找到 SKILL.md。",
    skill_package_limit_5mb: "Skill 包不能超过 5 MB。",
    skill_package_expanded_limit: "Skill 包解压后文件过多或体积过大。",
    skill_package_encrypted: "暂不支持导入加密的 Skill 包。",
    skill_package_unsafe_path: "Skill 包包含不安全路径或符号链接，已拒绝导入。",
    skill_organization_approval_required: "只有组织管理员可以审批组织级 Skill。",
    organization_skill_admin_only: "组织共享 Skill 只能由管理员维护。",
    organization_skill_request_not_allowed: "系统或已共享的 Skill 不能重复申请组织发布。",
    skill_version_not_found: "目标 Skill 版本不存在。",
  };
  const matched = Object.entries(known).find(([code]) => reason.message.startsWith(code));
  return matched?.[1] ?? reason.message ?? "Skill 操作失败，请稍后重试。";
}

type SkillValidationSummary = {
  passed: boolean;
  publishable: boolean;
  score: number;
  checks: Record<string, boolean>;
  failed_checks: string[];
  warnings: string[];
  blocking_issues: string[];
};

type SkillCompletionSummary = {
  questions: string[];
  requiredResources: Array<{ kind: string; name: string; purpose: string }>;
};

function skillValidationFor(skill: SkillRecord): SkillValidationSummary | null {
  const value = skill.metadata.validation;
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<SkillValidationSummary>;
  return {
    passed: Boolean(candidate.passed),
    publishable: Boolean(candidate.publishable ?? candidate.passed),
    score: Number(candidate.score ?? 0),
    checks: candidate.checks ?? {},
    failed_checks: candidate.failed_checks ?? [],
    warnings: candidate.warnings ?? [],
    blocking_issues: candidate.blocking_issues ?? []
  };
}

function skillCompletionFor(skill: SkillRecord): SkillCompletionSummary {
  const questions = Array.isArray(skill.metadata.unresolved_questions)
    ? skill.metadata.unresolved_questions.map(String).filter(Boolean)
    : [];
  const resources = Array.isArray(skill.metadata.resource_plan) ? skill.metadata.resource_plan : [];
  const requiredResources = resources.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const candidate = item as { kind?: unknown; name?: unknown; purpose?: unknown; required?: unknown };
    if (!candidate.required) return [];
    return [{
      kind: String(candidate.kind ?? "resource"),
      name: String(candidate.name ?? "未命名资源"),
      purpose: String(candidate.purpose ?? "")
    }];
  });
  return { questions, requiredResources };
}

function skillEvalLabel(skill: SkillRecord): string {
  const value = skill.metadata.eval_suite;
  if (!value || typeof value !== "object") return "未生成触发评测集";
  const suite = value as { positive_triggers?: unknown[]; negative_triggers?: unknown[]; positive_results?: unknown[]; negative_results?: unknown[]; status?: string; harness_score?: number };
  const positive = suite.positive_results?.length ?? suite.positive_triggers?.length ?? 0;
  const negative = suite.negative_results?.length ?? suite.negative_triggers?.length ?? 0;
  const statusLabel: Record<string, string> = {
    generated_not_run: "待 Harness",
    stale: "内容已变更，需重跑",
    passed: `Harness 通过${Number.isFinite(suite.harness_score) ? ` ${suite.harness_score}/100` : ""}`,
    failed: `Harness 未通过${Number.isFinite(suite.harness_score) ? ` ${suite.harness_score}/100` : ""}`
  };
  return `正例 ${positive} 条 · 反例 ${negative} 条 · ${statusLabel[suite.status ?? ""] ?? suite.status ?? "待评测"}`;
}

function skillAuthoringLabel(skill: SkillRecord): string {
  const value = skill.metadata.authoring;
  if (!value || typeof value !== "object") return "";
  const authoring = value as { status?: string; graph_path?: unknown[]; attempt?: number };
  if (!authoring.status && !authoring.graph_path?.length) return "";
  const statusLabel: Record<string, string> = {
    running: "正在生成",
    needs_input: "等待补充",
    waiting_review: "等待你复核",
    validated: "已校验，等待复核",
    stale: "内容已修改",
    published: "已发布",
    rejected: "已退回",
    failed: "生成未通过"
  };
  const path = (authoring.graph_path ?? []).map(String).slice(-6).join(" → ");
  return `LangGraph · ${statusLabel[authoring.status ?? ""] ?? authoring.status ?? "草稿"}${authoring.attempt ? ` · ${authoring.attempt} 轮` : ""}${path ? ` · ${path}` : ""}`;
}

function SkillCenter({ onBack, onAction, user }: { onBack: () => void; onAction: (message: string) => void; user: Principal }) {
  const [skills, setSkills] = useState<SkillRecord[]>([]);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "active" | "mine" | "draft">("all");
  const [prompt, setPrompt] = useState("");
  const [preferredName, setPreferredName] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [generateElapsed, setGenerateElapsed] = useState(0);
  const [error, setError] = useState("");
  const [editingSkill, setEditingSkill] = useState<SkillRecord | null>(null);
  const [completingSkill, setCompletingSkill] = useState<SkillRecord | null>(null);
  const [completionText, setCompletionText] = useState("");
  const [deferMissingResources, setDeferMissingResources] = useState(true);
  const [versionSkill, setVersionSkill] = useState<SkillRecord | null>(null);
  const [skillVersions, setSkillVersions] = useState<SkillVersionRecord[]>([]);
  const [resourceSkill, setResourceSkill] = useState<SkillRecord | null>(null);
  const [skillResources, setSkillResources] = useState<SkillResourceRecord[]>([]);
  const [resourceKind, setResourceKind] = useState<SkillResourceRecord["kind"]>("references");
  const [dryRunSkill, setDryRunSkill] = useState<SkillRecord | null>(null);
  const [dryRunPrompt, setDryRunPrompt] = useState("");
  const [dryRunFiles, setDryRunFiles] = useState<File[]>([]);
  const [dryRunHistory, setDryRunHistory] = useState<SkillDryRunRecord[]>([]);
  const [organizationApprovals, setOrganizationApprovals] = useState<SkillRecord[]>([]);
  const [editDraft, setEditDraft] = useState({ displayName: "", description: "", instructions: "", triggers: "", capabilities: "" });
  const importRef = useRef<HTMLInputElement>(null);
  const resourceRef = useRef<HTMLInputElement>(null);
  const dryRunFileRef = useRef<HTMLInputElement>(null);

  const refresh = () => unihrApi.listSkills().then(setSkills);
  useEffect(() => {
    void refresh().catch((reason: Error) => setError(skillErrorMessage(reason))).finally(() => setLoading(false));
    if (user.role === "admin") {
      void unihrApi.listOrganizationSkillApprovals().then(setOrganizationApprovals).catch(() => setOrganizationApprovals([]));
    }
  }, []);
  useEffect(() => {
    if (busyId !== "generate") {
      setGenerateElapsed(0);
      return;
    }
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setGenerateElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1_000);
    return () => window.clearInterval(timer);
  }, [busyId]);

  const sourceLabel: Record<SkillRecord["source_type"], string> = {
    system: "系统内置",
    prompt: "描述生成",
    conversation: "对话总结",
    upload: "文件导入"
  };
  const shown = skills.filter((skill) => {
    const matchesSearch = `${skill.display_name} ${skill.description} ${skill.trigger_phrases.join(" ")}`.toLowerCase().includes(search.trim().toLowerCase());
    const matchesFilter = filter === "all"
      || (filter === "active" && skill.enabled)
      || (filter === "mine" && skill.source_type !== "system")
      || (filter === "draft" && skill.status === "draft");
    return matchesSearch && matchesFilter;
  });
  const activeCount = skills.filter((item) => item.enabled).length;
  const userCount = skills.filter((item) => item.source_type !== "system").length;
  const draftCount = skills.filter((item) => item.status === "draft").length;

  const toggleSkill = (skill: SkillRecord) => {
    setBusyId(skill.id);
    setError("");
    void unihrApi.updateSkill(skill.id, { enabled: !skill.enabled })
      .then((updated) => {
        setSkills((items) => items.map((item) => item.id === updated.id ? updated : item));
        if (updated.enabled && filter === "draft") setFilter("active");
        if (!updated.enabled && filter === "active") setFilter("mine");
        onAction(updated.enabled ? `已启用：${updated.display_name}` : `已停用：${updated.display_name}`);
      })
      .catch((reason: Error) => setError(skillErrorMessage(reason)))
      .finally(() => setBusyId(""));
  };

  const generate = (event: FormEvent) => {
    event.preventDefault();
    if (!prompt.trim()) return;
    setBusyId("generate");
    setError("");
    void unihrApi.generateSkill(prompt, preferredName)
      .then((created) => {
        setSkills((items) => [created, ...items]);
        setPrompt("");
        setPreferredName("");
        setFilter("draft");
        onAction(`已生成 Skill 草稿：${created.display_name}`);
      })
      .catch((reason: Error) => setError(skillErrorMessage(reason)))
      .finally(() => setBusyId(""));
  };

  const beginEdit = (skill: SkillRecord) => {
    setEditingSkill(skill);
    setEditDraft({
      displayName: skill.display_name,
      description: skill.description,
      instructions: skill.instructions,
      triggers: skill.trigger_phrases.join("、"),
      capabilities: skill.capabilities.join("、")
    });
  };

  const saveEdit = (event: FormEvent) => {
    event.preventDefault();
    if (!editingSkill) return;
    setBusyId(editingSkill.id);
    setError("");
    const splitList = (value: string) => value.split(/[、,，\n]/).map((item) => item.trim()).filter(Boolean);
    void unihrApi.updateSkill(editingSkill.id, {
      display_name: editDraft.displayName,
      description: editDraft.description,
      instructions: editDraft.instructions,
      trigger_phrases: splitList(editDraft.triggers),
      capabilities: splitList(editDraft.capabilities)
    }).then((updated) => {
      setSkills((items) => items.map((item) => item.id === updated.id ? updated : item));
      setEditingSkill(null);
      onAction(`已更新并重新校验：${updated.display_name}`);
    }).catch((reason: Error) => setError(skillErrorMessage(reason))).finally(() => setBusyId(""));
  };

  const validateSkill = (skill: SkillRecord) => {
    setBusyId(skill.id);
    setError("");
    void unihrApi.validateSkill(skill.id).then((updated) => {
      setSkills((items) => items.map((item) => item.id === updated.id ? updated : item));
      onAction(`已重新校验：${updated.display_name}`);
    }).catch((reason: Error) => setError(skillErrorMessage(reason))).finally(() => setBusyId(""));
  };

  const completeSkill = (event: FormEvent) => {
    event.preventDefault();
    if (!completingSkill) return;
    setBusyId(completingSkill.id);
    setError("");
    void unihrApi.completeSkill(completingSkill.id, completionText, deferMissingResources)
      .then((updated) => {
        setSkills((items) => items.map((item) => item.id === updated.id ? updated : item));
        setCompletingSkill(null);
        setCompletionText("");
        onAction(skillValidationFor(updated)?.publishable
          ? `已完善并通过 Harness：${updated.display_name}，请复核后启用`
          : `已保存补充信息：${updated.display_name}，仍有待处理项`);
      })
      .catch((reason: Error) => setError(skillErrorMessage(reason)))
      .finally(() => setBusyId(""));
  };

  const openVersions = (skill: SkillRecord) => {
    setBusyId(skill.id);
    setError("");
    void unihrApi.listSkillVersions(skill.id).then((versions) => {
      setVersionSkill(skill);
      setSkillVersions(versions);
    }).catch((reason: Error) => setError(skillErrorMessage(reason))).finally(() => setBusyId(""));
  };

  const openResources = (skill: SkillRecord) => {
    setBusyId(skill.id);
    setError("");
    void unihrApi.listSkillResources(skill.id).then((resources) => {
      setResourceSkill(skill);
      setSkillResources(resources);
    }).catch((reason: Error) => setError(skillErrorMessage(reason))).finally(() => setBusyId(""));
  };

  const openDryRun = (skill: SkillRecord) => {
    setBusyId(skill.id);
    setError("");
    void unihrApi.listSkillDryRuns(skill.id).then((runs) => {
      setDryRunSkill(skill);
      setDryRunHistory(runs);
      setDryRunPrompt(skill.trigger_phrases[0] ?? "请按这个 Skill 处理这项工作");
      setDryRunFiles([]);
    }).catch((reason: Error) => setError(skillErrorMessage(reason))).finally(() => setBusyId(""));
  };

  const runDryRun = (event: FormEvent) => {
    event.preventDefault();
    if (!dryRunSkill || !dryRunPrompt.trim()) return;
    setBusyId(`dry-run:${dryRunSkill.id}`);
    setError("");
    void unihrApi.runSkillDryRun(dryRunSkill.id, dryRunPrompt, dryRunFiles)
      .then((result) => {
        setDryRunHistory((items) => [result, ...items.filter((item) => item.id !== result.id)]);
        onAction(result.status === "passed"
          ? `隔离试运行通过：${dryRunSkill.display_name}`
          : result.status === "needs_input"
            ? `试运行需要补充真实输入：${dryRunSkill.display_name}`
            : `试运行未通过：${dryRunSkill.display_name}`);
      })
      .catch((reason: Error) => setError(skillErrorMessage(reason)))
      .finally(() => setBusyId(""));
  };

  const uploadResource = (file: File) => {
    if (!resourceSkill) return;
    setBusyId(resourceSkill.id);
    setError("");
    void unihrApi.addSkillResource(resourceSkill.id, resourceKind, file)
      .then(() => Promise.all([unihrApi.listSkillResources(resourceSkill.id), unihrApi.listSkills()]))
      .then(([resources, refreshedSkills]) => {
        setSkillResources(resources);
        setSkills(refreshedSkills);
        const refreshed = refreshedSkills.find((item) => item.id === resourceSkill.id);
        if (refreshed) setResourceSkill(refreshed);
        onAction(resourceKind === "scripts" ? `脚本已隔离保存（未执行）：${file.name}` : `已绑定 Skill 资源：${file.name}`);
      })
      .catch((reason: Error) => setError(skillErrorMessage(reason)))
      .finally(() => { setBusyId(""); if (resourceRef.current) resourceRef.current.value = ""; });
  };

  const deleteResource = (resource: SkillResourceRecord) => {
    if (!resourceSkill || !window.confirm(`移除资源“${resource.name}”？Skill 会自动重新校验。`)) return;
    setBusyId(resourceSkill.id);
    void unihrApi.deleteSkillResource(resourceSkill.id, resource.id)
      .then((updated) => {
        setSkills((items) => items.map((item) => item.id === updated.id ? updated : item));
        setResourceSkill(updated);
        return unihrApi.listSkillResources(updated.id);
      })
      .then(setSkillResources)
      .catch((reason: Error) => setError(skillErrorMessage(reason)))
      .finally(() => setBusyId(""));
  };

  const rollbackSkill = (skill: SkillRecord, version: number) => {
    if (!window.confirm(`以 v${version} 的内容创建一个新的回滚版本？历史版本不会被覆盖。`)) return;
    setBusyId(skill.id);
    setError("");
    void unihrApi.rollbackSkill(skill.id, version).then((updated) => {
      setSkills((items) => items.map((item) => item.id === updated.id ? updated : item));
      setVersionSkill(updated);
      return unihrApi.listSkillVersions(updated.id);
    }).then(setSkillVersions).then(() => onAction(`已创建不可变回滚版本：${skill.display_name}`))
      .catch((reason: Error) => setError(skillErrorMessage(reason))).finally(() => setBusyId(""));
  };

  const requestOrganizationApproval = (skill: SkillRecord) => {
    if (!window.confirm(`申请把“${skill.display_name}”发布为本组织共享 Skill？提交后需管理员审批。`)) return;
    setBusyId(skill.id);
    setError("");
    void unihrApi.requestOrganizationSkill(skill.id).then((updated) => {
      setSkills((items) => items.map((item) => item.id === updated.id ? updated : item));
      onAction(`已提交组织级审批：${updated.display_name}`);
    }).catch((reason: Error) => setError(skillErrorMessage(reason))).finally(() => setBusyId(""));
  };

  const decideOrganizationSkill = (skill: SkillRecord, approved: boolean) => {
    const comment = approved ? "" : window.prompt("请填写退回原因。", "");
    if (comment === null) return;
    setBusyId(skill.id);
    void unihrApi.decideOrganizationSkill(skill.id, approved, comment).then((updated) => {
      setOrganizationApprovals((items) => items.filter((item) => item.id !== skill.id));
      if (approved) setSkills((items) => items.some((item) => item.id === updated.id) ? items.map((item) => item.id === updated.id ? updated : item) : [updated, ...items]);
      onAction(approved ? `已批准组织 Skill：${updated.display_name}` : `已退回组织 Skill：${updated.display_name}`);
    }).catch((reason: Error) => setError(skillErrorMessage(reason))).finally(() => setBusyId(""));
  };

  return (
    <div className="skill-center">
      <div className="skill-heading">
        <div>
          <button className="back-link" onClick={onBack}><ArrowLeft size={16} />返回对话</button>
          <span className="section-kicker">可复用工作能力</span>
          <h1>Skill 中心</h1>
          <p>像插件广场一样查看、创建、上传和启停能力；草稿会依次经过生成、静态校验、Harness 和人工复核。</p>
        </div>
        <div className="skill-heading-actions">
          <input ref={importRef} className="sr-only" type="file" accept=".md,.zip" onChange={(event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            setBusyId("import");
            setError("");
            void unihrApi.importSkill(file)
              .then((created) => {
                setSkills((items) => [created, ...items]);
                setFilter("draft");
                onAction(`已导入 Skill 草稿：${created.display_name}`);
              })
              .catch((reason: Error) => setError(reason.message))
              .finally(() => { setBusyId(""); if (importRef.current) importRef.current.value = ""; });
          }} />
          <button className="secondary-button" disabled={Boolean(busyId)} onClick={() => importRef.current?.click()}><Upload size={16} />{busyId === "import" ? "正在导入" : "上传 Skill"}</button>
        </div>
      </div>

      {error ? <div className="conversation-error" role="alert"><AlertTriangle size={16} />{error}</div> : null}
      {user.role === "admin" && organizationApprovals.length ? (
        <section className="skill-organization-approvals card">
          <div className="section-heading"><div><h2>组织级 Skill 待审批</h2><p>只有通过规范、Harness 与管理员复核的版本才会对全组织生效。</p></div><ShieldCheck size={19} /></div>
          {organizationApprovals.map((skill) => (
            <article key={skill.id}><div><strong>{skill.display_name} · v{skill.version}</strong><span>{skill.description}</span></div><div><button className="secondary-button" disabled={busyId === skill.id} onClick={() => decideOrganizationSkill(skill, false)}>退回</button><button className="primary-button" disabled={busyId === skill.id} onClick={() => decideOrganizationSkill(skill, true)}><Check size={15} />批准组织发布</button></div></article>
          ))}
        </section>
      ) : null}
      <section className="skill-metrics">
        <article><span>全部 Skill</span><strong>{loading ? "—" : skills.length}</strong><small>系统能力与个人能力</small></article>
        <article><span>正在生效</span><strong>{loading ? "—" : activeCount}</strong><small>会参与本轮匹配</small></article>
        <article><span>我的 Skill</span><strong>{loading ? "—" : userCount}</strong><small>描述、对话或文件创建</small></article>
        <article><span>待检查草稿</span><strong>{loading ? "—" : draftCount}</strong><small>确认后手动启用</small></article>
      </section>

      <section className="skill-builder card">
        <div className="skill-builder-copy"><span><WandSparkles size={17} />用一句话制作 Skill</span><h2>把重复的人事工作沉淀成可复用流程</h2><p>系统会整理输入、步骤、证据校验、审批闸门和输出格式，并自动清除一次性敏感信息。</p></div>
        <form onSubmit={generate}>
          <input value={preferredName} onChange={(event) => setPreferredName(event.target.value)} placeholder="Skill 名称（可选）" />
          <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="例如：每次发 Offer 前，检查审批表、薪酬确认、候选人信息和附件是否齐全，输出缺失清单。" rows={3} />
          <button className="primary-button" disabled={!prompt.trim() || Boolean(busyId)}>{busyId === "generate" ? <RotateCcw className="spin" size={16} /> : <Sparkles size={16} />}{busyId === "generate" ? "正在生成" : "生成草稿"}</button>
        </form>
        <div className={`skill-generation-hint ${busyId === "generate" ? "skill-generation-hint--busy" : ""}`} role="status" aria-live="polite">
          {busyId === "generate" ? (
            <><RotateCcw className="spin" size={15} /><span><strong>AI 正在整理 Skill 草稿，已等待 {generateElapsed} 秒</strong><small>通常需要 20–40 秒；可以继续停留在本页，完成后会自动显示在“草稿”中。</small></span></>
          ) : (
            <><Sparkles size={15} /><span><strong>先生成草稿，再由你检查并启用</strong><small>未启用的草稿不会参与任务路由。</small></span></>
          )}
        </div>
      </section>

      <section className="skill-library card">
        <div className="skill-library-toolbar">
          <div className="skill-tabs" role="tablist">
            {([['all', '全部'], ['active', '已启用'], ['mine', '我的'], ['draft', '草稿']] as const).map(([key, label]) => <button key={key} className={filter === key ? "active" : ""} onClick={() => setFilter(key)}>{label}</button>)}
          </div>
          <label className="skill-search"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 Skill 或触发词" /></label>
        </div>
        {loading ? <div className="skill-empty">正在读取 Skill…</div> : !shown.length ? <div className="skill-empty">没有符合条件的 Skill</div> : (
          <div className="skill-grid">
            {shown.map((skill) => (
              <article className={`skill-card ${skill.enabled ? "skill-card--active" : ""}`} key={skill.id}>
                <div className="skill-card-head">
                  <span className="skill-card-icon"><Puzzle size={19} /></span>
                  <div><strong>{skill.display_name}</strong><span>{sourceLabel[skill.source_type]} · v{skill.version}{skill.visibility === "organization" ? " · 组织共享" : ""}</span></div>
                  {skill.source_type === "system" || (skill.visibility === "organization" && user.role !== "admin") ? (
                    <span className="skill-system-managed" aria-label={`${skill.display_name}为共享托管 Skill`} title="该 Skill 由平台或组织管理员统一维护"><ShieldCheck size={15} />{skill.source_type === "system" ? "系统启用" : "组织启用"}</span>
                  ) : (
                    <span className="skill-toggle-control">
                      <small>{busyId === skill.id ? "处理中" : skill.enabled ? "已启用" : (skill.metadata.authoring as { status?: string } | undefined)?.status === "waiting_review" ? "待你复核" : "未启用"}</small>
                      <button className={`skill-toggle ${skill.enabled ? "skill-toggle--on" : ""}`} disabled={Boolean(busyId) || (!skill.enabled && skillValidationFor(skill)?.publishable === false)} onClick={() => toggleSkill(skill)} aria-label={`${skill.enabled ? "停用" : "复核并启用"}${skill.display_name}`} title={skill.enabled ? "停用这个个人 Skill" : skillValidationFor(skill)?.publishable === false ? "仍有失败项或待确认问题，暂不能启用" : "复核后恢复 LangGraph checkpoint 并发布"}><span /></button>
                    </span>
                  )}
                </div>
                <p>{skill.description}</p>
                {skillValidationFor(skill) ? (
                  <div className={`skill-quality ${skillValidationFor(skill)?.publishable ? "skill-quality--passed" : "skill-quality--failed"}`}>
                    {skillValidationFor(skill)?.publishable ? <ShieldCheck size={15} /> : <AlertTriangle size={15} />}
                    <span><strong>{skillValidationFor(skill)?.publishable ? "可启用" : skillValidationFor(skill)?.passed ? "结构通过，待补齐" : "规范检查未通过"}</strong><small>质量分 {skillValidationFor(skill)?.score}/100 · {Object.values(skillValidationFor(skill)?.checks ?? {}).filter(Boolean).length}/{Object.keys(skillValidationFor(skill)?.checks ?? {}).length} 项</small></span>
                  </div>
                ) : <div className="skill-quality"><AlertTriangle size={15} /><span><strong>旧版 Skill</strong><small>建议重新校验后再启用</small></span></div>}
                {skillAuthoringLabel(skill) ? <div className="skill-authoring-state"><GitBranch size={14} /><span>{skillAuthoringLabel(skill)}</span></div> : null}
                {(skillCompletionFor(skill).questions.length || skillCompletionFor(skill).requiredResources.length) && !skill.enabled ? (
                  <div className="skill-completion-card">
                    <div><strong>还差 {skillCompletionFor(skill).questions.length + skillCompletionFor(skill).requiredResources.length} 项即可进入复核</strong><small>回答待确认问题，或决定暂不绑定固定模板与脚本。</small></div>
                    <button type="button" onClick={() => {
                      setCompletingSkill(skill);
                      setCompletionText("");
                      setDeferMissingResources(true);
                    }}>继续完善</button>
                  </div>
                ) : null}
                {skill.approval_status === "pending" ? <div className="skill-approval-state"><Clock3 size={14} />等待组织管理员审批</div> : skill.approval_status === "approved" && skill.visibility === "organization" ? <div className="skill-approval-state skill-approval-state--approved"><ShieldCheck size={14} />组织级版本已批准</div> : skill.approval_status === "rejected" ? <div className="skill-approval-state skill-approval-state--rejected"><AlertTriangle size={14} />组织发布被退回</div> : null}
                <div className="skill-tags">{skill.trigger_phrases.slice(0, 4).map((trigger) => <span key={trigger}>{trigger}</span>)}</div>
                <details><summary>查看 Skill 包与执行说明</summary><p>{skill.instructions}</p><small>能力：{skill.capabilities.join("、") || "仅使用对话能力"}</small><small className="skill-eval-line">{skillEvalLabel(skill)}</small>{skillValidationFor(skill)?.warnings.length ? <ul>{skillValidationFor(skill)?.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : null}</details>
                <div className="skill-card-footer">
                  <span className={`skill-status skill-status--${skill.status}`}>{skill.enabled ? "已启用" : skill.status === "pending_approval" ? "组织审批中" : skill.status === "draft" ? "草稿" : "已停用"}</span>
                  <button className="skill-dry-run-button" onClick={() => openDryRun(skill)}><Play size={15} />隔离试运行</button>
                  <div><a href={unihrApi.skillPackageExportUrl(skill.id)}><Download size={15} />ZIP 包</a>{skill.source_type !== "system" ? <><button onClick={() => openResources(skill)}><Paperclip size={15} />资源</button><button onClick={() => openVersions(skill)}><History size={15} />版本</button>{skill.visibility === "personal" && skill.approval_status !== "pending" ? <button onClick={() => requestOrganizationApproval(skill)}><ShieldCheck size={15} />组织发布</button> : null}{skill.visibility !== "organization" || user.role === "admin" ? <><button onClick={() => beginEdit(skill)}><Pencil size={15} />编辑</button><button onClick={() => validateSkill(skill)}><ShieldCheck size={15} />校验</button><button onClick={() => {
                    if (!window.confirm(`删除 Skill“${skill.display_name}”？`)) return;
                    setBusyId(skill.id);
                    void unihrApi.deleteSkill(skill.id).then(() => setSkills((items) => items.filter((item) => item.id !== skill.id))).catch((reason: Error) => setError(skillErrorMessage(reason))).finally(() => setBusyId(""));
                  }}><Trash2 size={15} />删除</button></> : null}</> : null}</div>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
      <p className="skill-safety-note"><ShieldCheck size={15} />上传包中的资源会先隔离；V1 不执行上传脚本。启用 Skill 也不会绕过数据权限、公平性规则或人工审批。</p>
      {dryRunSkill ? (
        <div className="dialog-scrim" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busyId) setDryRunSkill(null); }}>
          <form className="dialog skill-dry-run-dialog" role="dialog" aria-modal="true" aria-labelledby="skill-dry-run-title" onSubmit={runDryRun}>
            <div className="dialog-header">
              <div className="dialog-icon"><Play size={20} /></div>
              <div><h2 id="skill-dry-run-title">{dryRunSkill.display_name} · 隔离试运行</h2><p>用真实路由与受控工具验收当前版本；结果不写入正式对话、Case 或业务文件。</p></div>
              <button type="button" className="icon-button" disabled={Boolean(busyId)} onClick={() => setDryRunSkill(null)} aria-label="关闭隔离试运行"><X size={18} /></button>
            </div>
            <div className="skill-dry-run-safety"><ShieldCheck size={16} /><span><strong>可以测试未启用草稿</strong><small>临时工作区结束后自动清理；上传脚本仍保持隔离，绝不执行。</small></span></div>
            <label className="skill-dry-run-prompt"><span>像真实用户一样输入一句话</span><textarea rows={4} value={dryRunPrompt} onChange={(event) => setDryRunPrompt(event.target.value)} placeholder="例如：帮我看看未来30天谁的合同要到期" /></label>
            <div className="skill-dry-run-files">
              <input ref={dryRunFileRef} className="sr-only" type="file" multiple onChange={(event) => setDryRunFiles(Array.from(event.target.files ?? []))} />
              <button type="button" className="secondary-button" disabled={Boolean(busyId)} onClick={() => dryRunFileRef.current?.click()}><Paperclip size={15} />添加测试附件</button>
              <span>{dryRunFiles.length ? `已选择 ${dryRunFiles.length} 个附件：${dryRunFiles.map((file) => file.name).join("、")}` : "没有附件；需要数据的 Skill 会返回“需要输入”"}</span>
            </div>
            <div className="dialog-actions"><button type="button" className="secondary-button" disabled={Boolean(busyId)} onClick={() => setDryRunSkill(null)}>关闭</button><button type="submit" className="primary-button" disabled={!dryRunPrompt.trim() || Boolean(busyId)}>{busyId === `dry-run:${dryRunSkill.id}` ? <RotateCcw className="spin" size={15} /> : <Play size={15} />}{busyId === `dry-run:${dryRunSkill.id}` ? "正在隔离执行" : "开始试运行"}</button></div>
            <section className="skill-dry-run-history">
              <div className="section-heading"><div><h3>最近试运行</h3><p>只保存脱敏摘要、执行路径和校验结论。</p></div></div>
              {!dryRunHistory.length ? <div className="skill-empty">还没有试运行记录</div> : dryRunHistory.map((run) => {
                const stale = run.skill_version !== dryRunSkill.version;
                return <article className={`skill-dry-run-result skill-dry-run-result--${run.status}`} key={run.id}>
                  <div className="skill-dry-run-result-head"><strong>{run.status === "passed" ? "通过" : run.status === "needs_input" ? "需要输入" : "未通过"}</strong><span>v{run.skill_version}{stale ? " · 已过期，请重新测试" : " · 当前版本"} · {new Date(run.created_at).toLocaleString("zh-CN")}</span></div>
                  <p><b>测试问题：</b>{run.prompt}</p>
                  <p><b>可见回答：</b>{run.answer_excerpt || "本轮未生成最终回答"}</p>
                  <div className="skill-dry-run-path"><GitBranch size={14} />{run.graph_path.join(" → ")}</div>
                  <div className="skill-dry-run-traces">{run.trace_events.filter((event) => ["skill.selected", "skill.input.required", "document.compose", "spreadsheet.transform", "recruitment.evaluate", "policy.retrieve", "artifact.create"].includes(event)).map((event) => <span key={event}>{event}</span>)}</div>
                  {run.artifacts.map((artifact) => <div className="skill-dry-run-artifact" key={`${run.id}-${artifact.name}`}><FileCheck2 size={15} /><span>{artifact.name} · {Math.max(1, Math.ceil(artifact.byte_size / 1024))} KB</span><b>Harness {artifact.harness_status}</b></div>)}
                  {run.issues.length ? <div className="skill-dry-run-issues"><AlertTriangle size={14} />{run.issues.join("；")}</div> : null}
                </article>;
              })}
            </section>
          </form>
        </div>
      ) : null}
      {resourceSkill ? (
        <div className="dialog-scrim" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busyId) setResourceSkill(null); }}>
          <section className="dialog skill-resource-dialog" role="dialog" aria-modal="true" aria-labelledby="skill-resource-title">
            <div className="dialog-header">
              <div className="dialog-icon"><Paperclip size={20} /></div>
              <div><h2 id="skill-resource-title">{resourceSkill.display_name} · 资源工作区</h2><p>绑定模板、参考资料和脚本；资源变化会生成新版本并重新运行 Harness。</p></div>
              <button className="icon-button" disabled={Boolean(busyId)} onClick={() => setResourceSkill(null)} aria-label="关闭资源工作区"><X size={18} /></button>
            </div>
            <div className="skill-resource-upload">
              <select value={resourceKind} onChange={(event) => setResourceKind(event.target.value as SkillResourceRecord["kind"])}>
                <option value="references">参考资料 references</option>
                <option value="assets">输出模板 assets</option>
                <option value="scripts">脚本 scripts（隔离）</option>
              </select>
              <input ref={resourceRef} className="sr-only" type="file" onChange={(event) => { const file = event.target.files?.[0]; if (file) uploadResource(file); }} />
              <button className="primary-button" disabled={Boolean(busyId)} onClick={() => resourceRef.current?.click()}><Upload size={15} />选择并绑定文件</button>
            </div>
            {resourceKind === "scripts" ? <div className="dialog-note"><ShieldCheck size={15} />脚本只会隔离保存、静态登记和随包导出，本系统不会执行上传脚本。</div> : null}
            <div className="skill-resource-list">
              {!skillResources.length ? <div className="skill-empty">尚未绑定真实资源</div> : skillResources.map((resource) => (
                <article key={resource.id}>
                  <span className={`skill-resource-kind skill-resource-kind--${resource.kind}`}>{resource.kind}</span>
                  <div><strong>{resource.name}</strong><small>{Math.max(1, Math.ceil(resource.size_bytes / 1024))} KB · {resource.status === "ready" ? "可供工作流读取" : "已隔离，未执行"} · SHA {resource.sha256.slice(0, 10)}</small></div>
                  <a href={unihrApi.skillResourceDownloadUrl(resourceSkill.id, resource.id)}><Download size={14} />下载</a>
                  <button className="icon-button" disabled={Boolean(busyId)} onClick={() => deleteResource(resource)} aria-label={`移除 ${resource.name}`}><Trash2 size={15} /></button>
                </article>
              ))}
            </div>
            <div className="dialog-actions"><button className="secondary-button" onClick={() => setResourceSkill(null)}>完成</button></div>
          </section>
        </div>
      ) : null}
      {completingSkill ? (
        <div className="dialog-scrim" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busyId) setCompletingSkill(null); }}>
          <form className="dialog skill-completion-dialog" role="dialog" aria-modal="true" aria-labelledby="skill-completion-title" onSubmit={completeSkill}>
            <div className="dialog-header">
              <div className="dialog-icon"><WandSparkles size={20} /></div>
              <div><h2 id="skill-completion-title">继续完善「{completingSkill.display_name}」</h2><p>回答关键问题后，Builder Agent 会生成新版本并重新运行规范检查与 Harness。</p></div>
              <button type="button" className="icon-button" disabled={Boolean(busyId)} aria-label="关闭 Skill 完善" onClick={() => setCompletingSkill(null)}><X size={18} /></button>
            </div>
            {skillCompletionFor(completingSkill).questions.length ? (
              <section className="skill-completion-list"><strong>待确认问题</strong><ol>{skillCompletionFor(completingSkill).questions.map((question) => <li key={question}>{question}</li>)}</ol></section>
            ) : null}
            {skillCompletionFor(completingSkill).requiredResources.length ? (
              <section className="skill-completion-list"><strong>模型建议的必需资源</strong><ul>{skillCompletionFor(completingSkill).requiredResources.map((resource) => <li key={`${resource.kind}/${resource.name}`}><b>{resource.kind}/{resource.name}</b>{resource.purpose ? `：${resource.purpose}` : ""}</li>)}</ul></section>
            ) : null}
            <label><span>你的补充说明</span><textarea rows={5} value={completionText} onChange={(event) => setCompletionText(event.target.value)} placeholder="例如：固定模板不是必须；每次运行时由用户上传数据，缺少模板时使用系统标准版式。" /></label>
            <label className="skill-resource-defer"><input type="checkbox" checked={deferMissingResources} onChange={(event) => setDeferMissingResources(event.target.checked)} /><span><strong>未上传的模板、脚本和参考资料先改为可选</strong><small>Agent 将使用已注册工具或通用流程回退；不会虚构不存在的资源。</small></span></label>
            <div className="dialog-actions"><button type="button" className="secondary-button" disabled={Boolean(busyId)} onClick={() => setCompletingSkill(null)}>取消</button><button type="submit" className="primary-button" disabled={Boolean(busyId) || (!completionText.trim() && !deferMissingResources)}>{busyId === completingSkill.id ? <RotateCcw className="spin" size={15} /> : <WandSparkles size={15} />}{busyId === completingSkill.id ? "正在重新生成" : "生成完善版本"}</button></div>
          </form>
        </div>
      ) : null}
      {editingSkill ? (
        <div className="dialog-scrim" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditingSkill(null); }}>
          <form className="dialog skill-edit-dialog" role="dialog" aria-modal="true" aria-labelledby="skill-edit-title" onSubmit={saveEdit}>
            <div className="dialog-header">
              <div className="dialog-icon"><Puzzle size={20} /></div>
              <div><h2 id="skill-edit-title">编辑 Skill 草稿</h2><p>保存后会生成新版本，并重新执行结构与安全校验。</p></div>
              <button type="button" className="icon-button" aria-label="关闭 Skill 编辑" onClick={() => setEditingSkill(null)}><X size={18} /></button>
            </div>
            <div className="skill-edit-form">
              <label><span>显示名称</span><input value={editDraft.displayName} onChange={(event) => setEditDraft({ ...editDraft, displayName: event.target.value })} /></label>
              <label><span>触发词（用逗号或顿号分隔）</span><input value={editDraft.triggers} onChange={(event) => setEditDraft({ ...editDraft, triggers: event.target.value })} /></label>
              <label className="skill-edit-wide"><span>能力说明与触发场景</span><textarea rows={3} value={editDraft.description} onChange={(event) => setEditDraft({ ...editDraft, description: event.target.value })} /></label>
              <label className="skill-edit-wide"><span>SKILL.md 执行正文</span><textarea rows={14} value={editDraft.instructions} onChange={(event) => setEditDraft({ ...editDraft, instructions: event.target.value })} /></label>
              <label className="skill-edit-wide"><span>能力白名单</span><input value={editDraft.capabilities} onChange={(event) => setEditDraft({ ...editDraft, capabilities: event.target.value })} placeholder="例如：spreadsheet.inspect、artifact.create" /></label>
            </div>
            <div className="dialog-note"><ShieldCheck size={15} />脚本、MCP 和工具声明只代表权限申请；本页不会执行上传脚本。</div>
            <div className="dialog-actions"><button type="button" className="secondary-button" onClick={() => setEditingSkill(null)}>取消</button><button type="submit" className="primary-button" disabled={Boolean(busyId)}>保存新版本</button></div>
          </form>
        </div>
      ) : null}
      {versionSkill ? (
        <div className="dialog-scrim" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setVersionSkill(null); }}>
          <section className="dialog skill-version-dialog" role="dialog" aria-modal="true" aria-labelledby="skill-version-title">
            <div className="dialog-header"><div className="dialog-icon"><History size={20} /></div><div><h2 id="skill-version-title">{versionSkill.display_name} · 版本历史</h2><p>历史快照不可修改；回滚会创建一个新的草稿版本。</p></div><button className="icon-button" onClick={() => setVersionSkill(null)} aria-label="关闭版本历史"><X size={18} /></button></div>
            <div className="skill-version-list">
              {skillVersions.map((version) => (
                <article key={version.version}><div><strong>v{version.version}{version.version === versionSkill.version ? " · 当前" : ""}</strong><span>{new Date(version.created_at).toLocaleString("zh-CN")} · {version.change_type}</span><p>{version.snapshot.description}</p></div>{version.version !== versionSkill.version ? <button className="secondary-button" disabled={busyId === versionSkill.id} onClick={() => rollbackSkill(versionSkill, version.version)}><RotateCcw size={14} />回滚为新版本</button> : <span className="skill-version-current"><Check size={13} />当前版本</span>}</article>
              ))}
            </div>
            <div className="dialog-actions"><button className="secondary-button" onClick={() => setVersionSkill(null)}>关闭</button></div>
          </section>
        </div>
      ) : null}
    </div>
  );
}

function ApprovalCenter({ onBack, onAction }: { onBack: () => void; onAction: (message: string) => void }) {
  const [items, setItems] = useState<ApprovalQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setItems(await unihrApi.listApprovals());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "审批队列读取失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const decide = async (item: ApprovalQueueItem, approved: boolean) => {
    const comment = approved
      ? ""
      : window.prompt("请填写退回原因，申请人会在运行记录中看到这条意见。", "");
    if (comment === null) return;
    setBusyId(item.id);
    setError("");
    try {
      await unihrApi.decideRun(item.id, approved, comment);
      setItems((current) => current.filter((entry) => entry.id !== item.id));
      onAction(approved ? "复核已确认通过" : "已退回修改");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "审批决定提交失败");
    } finally {
      setBusyId("");
    }
  };

  const intentLabel = (intent: string) => ({
    recruitment_screening: "简历辅助筛选",
    document_generation: "公文材料",
    spreadsheet_processing: "表格处理",
    policy_question: "政策问答"
  }[intent] ?? intent);

  return (
    <div className="approval-center">
      <section className="approval-heading">
        <div>
          <button className="back-link" onClick={onBack}><ArrowLeft size={16} />返回对话</button>
          <span className="section-kicker">审批权限 · run.approve</span>
          <h1>待我审批</h1>
          <p>只展示本组织内已通过质量门、正在等待人工复核的结果。</p>
        </div>
        <button className="secondary-button" onClick={() => void load()} disabled={loading}><RotateCcw size={16} />刷新</button>
      </section>

      {error ? <div className="conversation-error" role="alert"><AlertTriangle size={16} />{error}</div> : null}
      <section className="approval-summary card">
        <div><span>待复核</span><strong>{items.length}</strong></div>
        <p><ShieldCheck size={16} />批准或退回都会写入运行审计记录；普通账户只能查看等待状态，不能执行审批。</p>
      </section>

      <section className="approval-list" aria-label="待审批结果">
        {items.map((item) => (
          <article className="approval-card card" key={item.id}>
            <div className="approval-card-main">
              <span className="approval-type">{intentLabel(item.business_intent)}</span>
              <h2>{item.conversation_title}</h2>
              <p>{item.owner_display_name}（{item.owner_username}）· {new Date(item.updated_at).toLocaleString("zh-CN")}</p>
              <div className="approval-graph"><GitBranch size={15} />{item.graph_path.join(" → ")}</div>
              {item.quality_issues.length ? <div className="approval-warning"><AlertTriangle size={15} />{item.quality_issues.join("；")}</div> : null}
            </div>
            <div className="approval-actions">
              <button className="secondary-button" disabled={busyId === item.id} onClick={() => void decide(item, false)}>退回修改</button>
              <button className="primary-button" disabled={busyId === item.id} onClick={() => void decide(item, true)}><Check size={16} />{busyId === item.id ? "正在提交" : "确认通过"}</button>
            </div>
          </article>
        ))}
        {!loading && !items.length ? <div className="approval-empty card"><CheckCircle2 size={28} /><h2>当前没有待审批结果</h2><p>新的高风险人事产物通过质量门后会出现在这里。</p></div> : null}
        {loading ? <div className="approval-empty card">正在读取审批队列…</div> : null}
      </section>
    </div>
  );
}

function AdminCenter({ onBack, onAction }: { onBack: () => void; onAction: (message: string) => void }) {
  const [stats, setStats] = useState<FeedbackStats | null>(null);
  const [feedback, setFeedback] = useState<AdminFeedbackRecord[]>([]);
  const [history, setHistory] = useState<AdminConversationSummary[]>([]);
  const [filter, setFilter] = useState<"all" | "helpful" | "not_helpful">("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedDetail, setSelectedDetail] = useState<ConversationDetail | null>(null);
  const [contextOps, setContextOps] = useState<ContextOperationsDashboard | null>(null);
  const [resourceEval, setResourceEval] = useState<ResourceQueryEvalReport | null>(null);
  const [runningResourceEval, setRunningResourceEval] = useState(false);
  const [retentionReport, setRetentionReport] = useState<ContextRetentionReport | null>(null);
  const [runningRetention, setRunningRetention] = useState(false);
  const [capabilityOps, setCapabilityOps] = useState<CapabilityOperationsSnapshot | null>(null);
  const [routingGoldenEval, setRoutingGoldenEval] = useState<RoutingGoldenReport | null>(null);
  const [runningRoutingGoldenEval, setRunningRoutingGoldenEval] = useState(false);
  const [productHelpEval, setProductHelpEval] = useState<ProductHelpRouteEvalReport | null>(null);
  const [runningProductHelpEval, setRunningProductHelpEval] = useState(false);
  const [rewriteEval, setRewriteEval] = useState<RewriteEvaluationReport | null>(null);
  const [runningRewriteEval, setRunningRewriteEval] = useState(false);
  const [savingBudget, setSavingBudget] = useState(false);
  const [budgetDraft, setBudgetDraft] = useState({
    monthly_budget_cny: 0,
    per_run_budget_cny: 0,
    daily_call_limit: 0,
    warning_ratio: 0.8
  });

  const load = async (search = query, ratingFilter = filter) => {
    setLoading(true);
    setError("");
    try {
      const helpful = ratingFilter === "all" ? undefined : ratingFilter === "helpful";
      const [nextStats, nextFeedback, nextHistory, nextContextOps, nextCapabilityOps] = await Promise.all([
        unihrApi.getAdminFeedbackStats(),
        unihrApi.listAdminFeedback(helpful),
        unihrApi.listAdminHistory(search),
        unihrApi.getAdminContextDashboard(),
        unihrApi.getCapabilityOperations()
      ]);
      setStats(nextStats);
      setFeedback(nextFeedback);
      setHistory(nextHistory);
      setContextOps(nextContextOps);
      setCapabilityOps(nextCapabilityOps);
      setBudgetDraft({
        monthly_budget_cny: nextContextOps.cost_control.policy.monthly_budget_cny,
        per_run_budget_cny: nextContextOps.cost_control.policy.per_run_budget_cny,
        daily_call_limit: nextContextOps.cost_control.policy.daily_call_limit,
        warning_ratio: nextContextOps.cost_control.policy.warning_ratio
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "管理员数据读取失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load("", "all"); }, []);

  const changeFilter = (next: "all" | "helpful" | "not_helpful") => {
    setFilter(next);
    void load(query, next);
  };

  const updateStatus = async (item: AdminFeedbackRecord, testStatus: AdminFeedbackRecord["test_status"]) => {
    try {
      const updated = await unihrApi.updateAdminFeedback(item.id, testStatus);
      setFeedback((current) => current.map((entry) => entry.id === item.id ? updated : entry));
      setStats(await unihrApi.getAdminFeedbackStats());
      onAction(testStatus === "added_to_test" ? "已加入反馈测试集" : "反馈状态已更新");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "状态更新失败");
    }
  };

  const deleteHistory = async (item: AdminConversationSummary) => {
    if (!window.confirm(`删除“${item.title}”及其回答、反馈和生成文件？此操作无法恢复。`)) return;
    try {
      await unihrApi.deleteAdminHistory(item.id);
      setHistory((current) => current.filter((entry) => entry.id !== item.id));
      setFeedback((current) => current.filter((entry) => entry.conversation_id !== item.id));
      setStats(await unihrApi.getAdminFeedbackStats());
      setSelectedDetail(null);
      onAction("回答历史已删除");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除失败");
    }
  };

  const rateText = stats?.helpful_rate == null ? "—" : `${Math.round(stats.helpful_rate * 100)}%`;

  const runResourceEval = async () => {
    setRunningResourceEval(true);
    setError("");
    try {
      const report = await unihrApi.runResourceQueryEvaluation();
      setResourceEval(report);
      onAction(`Resource Query 固定评测完成：${report.passed}/${report.total} 通过`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Resource Query 评测失败");
    } finally {
      setRunningResourceEval(false);
    }
  };

  const runProductHelpEval = async () => {
    setRunningProductHelpEval(true);
    setError("");
    try {
      const report = await unihrApi.runProductHelpRouteEvaluation();
      setProductHelpEval(report);
      onAction(`产品帮助路由固定评测完成：${report.passed}/${report.total} 通过`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "产品帮助路由评测失败");
    } finally {
      setRunningProductHelpEval(false);
    }
  };

  const runRoutingGoldenEval = async () => {
    setRunningRoutingGoldenEval(true);
    setError("");
    try {
      const report = await unihrApi.runCapabilityRoutingEvaluation();
      setRoutingGoldenEval(report);
      setCapabilityOps(await unihrApi.getCapabilityOperations());
      onAction(`普通路由黄金集完成：${report.passed}/${report.total} 通过，${report.fallback_cases} 条使用兜底`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "普通路由黄金集运行失败");
    } finally {
      setRunningRoutingGoldenEval(false);
    }
  };

  const runRewriteEval = async () => {
    setRunningRewriteEval(true);
    setError("");
    try {
      const report = await unihrApi.runQueryRewriteEvaluation();
      setRewriteEval(report);
      onAction(`简短 Prompt 通用化评测完成：${report.passed}/${report.total} 通过`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Query Rewrite 通用化评测失败");
    } finally {
      setRunningRewriteEval(false);
    }
  };

  const runRetention = async (execute: boolean) => {
    if (execute && !window.confirm("执行后会清理预演列出的过期运行数据，且不能恢复。继续吗？")) return;
    setRunningRetention(true);
    setError("");
    try {
      const report = await unihrApi.runContextRetention(execute);
      setRetentionReport(report);
      onAction(execute ? "上下文保留策略已执行并写入审计" : "上下文保留策略预演完成，尚未删除任何数据");
      if (execute) setContextOps(await unihrApi.getAdminContextDashboard());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "上下文保留策略运行失败");
    } finally {
      setRunningRetention(false);
    }
  };

  const saveBudget = async () => {
    setSavingBudget(true);
    setError("");
    try {
      const costControl = await unihrApi.updateAdminModelBudget(budgetDraft);
      setContextOps((current) => current ? { ...current, cost_control: costControl } : current);
      onAction("模型预算与调用阈值已保存");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "模型预算保存失败");
    } finally {
      setSavingBudget(false);
    }
  };

  return (
    <div className="admin-center">
      <section className="admin-heading">
        <div>
          <button className="back-link" onClick={onBack}><ArrowLeft size={16} />返回对话</button>
          <span className="section-kicker">管理员权限 · answer_history.manage</span>
          <h1>回答历史与反馈</h1>
          <p>查看组织内回答质量，收集用户反馈作为回归测试样本；普通用户没有此入口。</p>
        </div>
        <a className="secondary-button" href={unihrApi.adminFeedbackExportUrl()}><Download size={16} />导出测试数据 CSV</a>
      </section>

      {error ? <div className="conversation-error" role="alert"><AlertTriangle size={16} />{error}</div> : null}

      <section className="admin-metrics" aria-label="反馈统计">
        <article><span>收到反馈</span><strong>{stats?.total ?? 0}</strong><small>每条回答每位用户只计一次</small></article>
        <article><span>有帮助率</span><strong>{rateText}</strong><small>{stats?.helpful ?? 0} 条有帮助</small></article>
        <article><span>需改进</span><strong>{stats?.not_helpful ?? 0}</strong><small>优先进入人工复核</small></article>
        <article><span>测试样本</span><strong>{stats?.added_to_test ?? 0}</strong><small>{stats?.pending_test_review ?? 0} 条待判断</small></article>
      </section>

      <section className="card product-capability-governance" aria-label="产品能力治理">
        <div className="admin-section-header">
          <div><h2>Agent 能力运营中心</h2><p>把注册能力、真实运行、Trace 兜底和用户反馈放在同一张质量视图中；这里不填演示数字。</p></div>
          <div className="capability-ops-actions">
            <button className="secondary-button" onClick={() => void runProductHelpEval()} disabled={runningProductHelpEval}>
              <Gauge size={16} />{runningProductHelpEval ? "正在评测产品帮助" : "产品帮助边界测试"}
            </button>
            <button className="primary-button" onClick={() => void runRoutingGoldenEval()} disabled={runningRoutingGoldenEval}>
              <GitBranch size={16} />{runningRoutingGoldenEval ? "正在调用真实路由" : "运行普通路由黄金集"}
            </button>
          </div>
        </div>
        <div className="product-capability-summary">
          <article><strong>{capabilityOps?.summary.registered_capabilities ?? 0}</strong><span>已注册能力</span><small>{capabilityOps?.summary.observed_capabilities ?? 0} 项有真实运行</small></article>
          <article><strong>{capabilityOps?.summary.total_runs ?? 0}</strong><span>近 {capabilityOps?.window_days ?? 30} 天运行</span><small>{capabilityOps?.summary.completed_runs ?? 0} 次完成或待复核</small></article>
          <article><strong>{capabilityOps?.summary.completion_rate == null ? "—" : `${Math.round(capabilityOps.summary.completion_rate * 100)}%`}</strong><span>任务完成率</span><small>{capabilityOps?.summary.capabilities_needing_attention ?? 0} 项需关注</small></article>
          <article><strong>{capabilityOps?.summary.fallback_rate == null ? "—" : `${Math.round(capabilityOps.summary.fallback_rate * 100)}%`}</strong><span>规则兜底率</span><small>{capabilityOps?.summary.helpful_rate == null ? "暂无反馈" : `有帮助率 ${Math.round(capabilityOps.summary.helpful_rate * 100)}%`}</small></article>
        </div>
        <div className="capability-operations-list">
          {capabilityOps?.capabilities.map((item) => (
            <article className={`capability-operations-card capability-operations-card--${item.health}`} key={item.capability_id}>
              <header>
                <div><strong>{item.label}</strong><small>{item.capability_id} · v{item.version}</small></div>
                <span className={`capability-health capability-health--${item.health}`}>{item.health === "healthy" ? "健康" : item.health === "watch" ? "需关注" : "待观察"}</span>
              </header>
              <p>{item.description}</p>
              <dl>
                <div><dt>输入</dt><dd>{item.input_contract}</dd></div>
                <div><dt>输出</dt><dd>{item.output_contract}</dd></div>
              </dl>
              <div className="capability-operations-stats">
                <span><b>{item.run_count}</b> 次运行</span>
                <span><b>{item.completion_rate == null ? "—" : `${Math.round(item.completion_rate * 100)}%`}</b> 完成</span>
                <span><b>{item.fallback_runs}</b> 次兜底</span>
                <span><b>{item.helpful_rate == null ? "—" : `${Math.round(item.helpful_rate * 100)}%`}</b> 有帮助</span>
              </div>
              <footer><span>{item.owner}</span><span>{item.risk_level === "high" ? "高风险" : item.risk_level === "medium" ? "中风险" : "低风险"}</span><span>{item.retrieval_mode === "none" ? "无需检索" : `${item.retrieval_mode} 检索`}</span></footer>
            </article>
          ))}
        </div>
        {routingGoldenEval ? <div className={`routing-golden-eval routing-golden-eval--${routingGoldenEval.status}`}>
          <header><div><strong>TaskIntent + Capability Registry 黄金集</strong><span>短句、奇怪表述、上下文续问、组合任务、交付形态与 fallback</span></div><b>{routingGoldenEval.passed}/{routingGoldenEval.total}</b></header>
          <div className="routing-golden-cases">{routingGoldenEval.results.map((item) => <article className={item.passed ? "passed" : "failed"} key={item.case_id}>
            <span>{item.passed ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}</span>
            <div><strong>{item.title}</strong><p>{item.prompt}</p><small>{item.actual_capability_id} · {item.actual_action} · {item.actual_retrieval_mode} · {item.actual_delivery_mode}{item.fallback_used ? " · fallback" : " · model-first"}</small>{item.issues.length ? <em>{item.issues.join("；")}</em> : null}</div>
          </article>)}</div>
        </div> : null}
        {productHelpEval ? <div className={`product-help-eval product-help-eval--${productHelpEval.status}`}>
          <header><div><strong>产品帮助路由固定评测</strong><span>覆盖误判、fallback 与业务边界</span></div><b>{productHelpEval.passed}/{productHelpEval.total}</b></header>
          <div className="product-help-eval-cases">{productHelpEval.cases.map((item) => <article key={item.case_id}>
            <span>{item.passed ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}</span>
            <div><strong>{item.case_id}</strong><small>{item.actual_intent} → {item.actual_route}</small><p>{item.message}</p></div>
          </article>)}</div>
        </div> : null}
      </section>

      <section className="card rewrite-eval-governance" aria-label="简短 Prompt 通用化评测">
        <div className="admin-section-header">
          <div><h2>简短 Prompt / Query Rewrite Harness</h2><p>使用口语、省略、错别字、上下文续问和只读约束检验真实改写，不把固定题目的答案写进业务执行器。</p></div>
          <button className="secondary-button" onClick={() => void runRewriteEval()} disabled={runningRewriteEval}>
            <WandSparkles size={16} />{runningRewriteEval ? "正在调用路由模型评测" : "运行通用化评测"}
          </button>
        </div>
        {rewriteEval ? <>
          <div className="rewrite-eval-metrics">
            <article><span>整体通过</span><strong>{rewriteEval.passed}/{rewriteEval.total}</strong><small>{rewriteEval.model_routed_cases} 条使用模型 · {rewriteEval.fallback_cases} 条 fallback</small></article>
            <article><span>领域路由</span><strong>{Math.round(rewriteEval.route_accuracy * 100)}%</strong><small>识别用户在做哪类工作</small></article>
            <article><span>本轮动作</span><strong>{Math.round(rewriteEval.action_accuracy * 100)}%</strong><small>新建、续问、复用或澄清</small></article>
            <article><span>上下文召回</span><strong>{Math.round(rewriteEval.context_recall * 100)}%</strong><small>省略信息是否被正确补全</small></article>
            <article><span>事实保真</span><strong>{Math.round(rewriteEval.fidelity_rate * 100)}%</strong><small>未引入来源中不存在的硬值</small></article>
          </div>
          <div className="rewrite-eval-case-list">{rewriteEval.cases.map((item) => <article className={item.passed ? "passed" : "failed"} key={item.case_id}>
            <header><span>{item.passed ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}</span><div><strong>{item.title}</strong><small>{item.actual_intent} · {item.actual_action} · {item.actual_mode}</small></div></header>
            <dl><div><dt>用户原话</dt><dd>{item.original_message}</dd></div><div><dt>系统改写</dt><dd>{item.resolved_request}</dd></div></dl>
            {!item.passed ? <p>{item.missing_expected_terms.length ? `缺少上下文：${item.missing_expected_terms.join("、")}` : ""}{item.unexpected_hard_facts.length ? ` 引入未授权硬值：${item.unexpected_hard_facts.join("、")}` : ""}</p> : null}
          </article>)}</div>
        </> : <div className="rewrite-eval-empty"><WandSparkles size={24} /><strong>尚未运行</strong><span>评测会真实调用当前路由模型，并单独报告 fallback，结果可能暴露尚未解决的问题。</span></div>}
      </section>

      <section className="card admin-context-ops" aria-label="Context Operations 运营看板">
        <div className="admin-section-header">
          <div><h2>Context Operations</h2><p>直接读取持久 Snapshot、压缩检查点、水合计数和 Resource Query Trace。</p></div>
          <button className="secondary-button" onClick={() => void runResourceEval()} disabled={runningResourceEval}>
            <Gauge size={16} />{runningResourceEval ? "正在运行固定评测" : "运行 Resource Query Eval"}
          </button>
        </div>
        <div className="admin-context-metrics">
          <article><span>上下文快照</span><strong>{contextOps?.snapshot_count ?? 0}</strong><small>{contextOps?.compaction_count ?? 0} 次使用压缩检查点</small></article>
          <article><span>减少注入</span><strong>{Math.round((contextOps?.compression_ratio ?? 0) * 100)}%</strong><small>{(contextOps?.saved_chars ?? 0).toLocaleString()} 字符未进入模型</small></article>
          <article><span>估算 Token</span><strong>{(contextOps?.estimated_tokens ?? 0).toLocaleString()}</strong><small>当前持久快照合计</small></article>
          <article><span>状态水合</span><strong>{contextOps?.hydration_count ?? 0}</strong><small>{contextOps?.fidelity_failures ?? 0} 次 Fidelity 异常</small></article>
          <article><span>局部读取</span><strong>{contextOps?.resource_query.executed_calls ?? 0}</strong><small>{(contextOps?.resource_query.characters ?? 0).toLocaleString()} 字符</small></article>
        </div>
        <div className="context-cost-note">
          <strong>输入费用估算：{contextOps?.cost.configured ? `¥${contextOps.cost.estimated_input_cost_cny.toFixed(4)}` : "未配置模型单价"}</strong>
          <span>{contextOps?.cost.note}</span>
        </div>
        <div className="model-usage-panel">
          <header>
            <div><h3>真实模型调用台账</h3><p>最近 {contextOps?.model_usage.days ?? 30} 天，按调用用途、模型和能力类型拆分。</p></div>
            <div><strong>{contextOps?.model_usage.calls ?? 0}</strong><span>次调用</span></div>
            <div><strong>{(contextOps?.model_usage.input_units ?? 0).toLocaleString()}</strong><span>输入单位</span></div>
            <div><strong>{contextOps?.model_usage.pricing_configured ? `¥${contextOps.model_usage.estimated_cost_cny.toFixed(4)}` : "未配置"}</strong><span>配置成本</span></div>
          </header>
          {contextOps?.model_usage.breakdown.length ? <div className="model-usage-table">
            <div className="model-usage-row model-usage-row--head"><span>能力</span><span>用途</span><span>模型</span><span>调用</span><span>输入 / 输出</span><span>成本</span></div>
            {contextOps.model_usage.breakdown.slice(0, 12).map((item) => <div className="model-usage-row" key={`${item.operation_kind}-${item.purpose}-${item.model}-${item.status}`}>
              <span>{item.operation_kind}</span><span>{item.purpose}</span><span>{item.model}</span><span>{item.calls}</span><span>{item.input_units.toLocaleString()} / {item.output_units.toLocaleString()}</span><span>{item.pricing_configured ? `¥${item.estimated_cost_cny.toFixed(4)}` : "未定价"}</span>
            </div>)}
          </div> : <div className="model-usage-empty">尚无新版本模型调用记录；运行一次任务或固定评测后会自动出现。</div>}
        </div>
        <div className="model-cost-control">
          <header>
            <div>
              <h3>模型预算与调用护栏</h3>
              <p>0 表示暂不启用硬阈值；未配置模型单价时，系统只统计调用量并明确标注覆盖缺口。</p>
            </div>
            <div className="model-cost-summary">
              <span>今日 {contextOps?.cost_control.daily_calls ?? 0} 次</span>
              <span>本月 {contextOps?.cost_control.monthly_calls ?? 0} 次</span>
              <span>定价覆盖 {Math.round((contextOps?.cost_control.pricing_coverage ?? 1) * 100)}%</span>
            </div>
          </header>
          {contextOps?.cost_control.alerts.length ? <div className="model-cost-alerts">
            {contextOps.cost_control.alerts.map((alert) => <div key={alert.code} className={`model-cost-alert model-cost-alert--${alert.severity}`}><AlertTriangle size={15} />{alert.message}</div>)}
          </div> : <div className="model-cost-ok"><CheckCircle2 size={15} />当前没有预算或调用量告警</div>}
          <div className="model-budget-form">
            <label><span>组织月预算（元）</span><input type="number" min="0" step="0.01" value={budgetDraft.monthly_budget_cny} onChange={(event) => setBudgetDraft((current) => ({ ...current, monthly_budget_cny: Number(event.target.value) }))} /></label>
            <label><span>单任务预算（元）</span><input type="number" min="0" step="0.01" value={budgetDraft.per_run_budget_cny} onChange={(event) => setBudgetDraft((current) => ({ ...current, per_run_budget_cny: Number(event.target.value) }))} /></label>
            <label><span>每日调用量阈值</span><input type="number" min="0" step="1" value={budgetDraft.daily_call_limit} onChange={(event) => setBudgetDraft((current) => ({ ...current, daily_call_limit: Number(event.target.value) }))} /></label>
            <label><span>预算预警比例</span><input type="number" min="0.1" max="1" step="0.05" value={budgetDraft.warning_ratio} onChange={(event) => setBudgetDraft((current) => ({ ...current, warning_ratio: Number(event.target.value) }))} /></label>
            <button className="secondary-button" disabled={savingBudget} onClick={() => void saveBudget()}>{savingBudget ? "正在保存" : "保存预算护栏"}</button>
          </div>
          {contextOps?.model_usage.attribution.length ? <div className="model-attribution-list">
            <h4>调用归属（会话 / 招聘 Case）</h4>
            {contextOps.model_usage.attribution.slice(0, 8).map((item, index) => <div key={`${item.conversation_id}-${item.case_id}-${index}`}>
              <strong>{item.case_name || item.conversation_title}</strong>
              <span>{item.case_id ? "招聘 Case" : item.conversation_id ? "对话" : "待归属"}</span>
              <span>{item.calls} 次调用</span>
              <span>{contextOps.model_usage.pricing_configured ? `¥${item.estimated_cost_cny.toFixed(4)}` : "未完整定价"}</span>
            </div>)}
          </div> : null}
        </div>
        {contextOps?.daily.length ? (
          <div className="context-trend-panel">
            <div><h3>最近 30 天上下文趋势</h3><p>按持久化快照统计 Token、压缩和按需恢复。</p></div>
            <div className="context-trend-bars">
              {contextOps.daily.slice(-14).map((day) => {
                const maximum = Math.max(...contextOps.daily.slice(-14).map((item) => item.estimated_tokens), 1);
                return <article key={day.date} title={`${day.date} · ${day.estimated_tokens.toLocaleString()} tokens`}><span style={{ height: `${Math.max(8, day.estimated_tokens / maximum * 100)}%` }} /><small>{day.date.slice(5)}</small></article>;
              })}
            </div>
          </div>
        ) : null}
        {contextOps?.routes.length ? (
          <div className="admin-context-routes">
            <h3>按任务路由比较</h3>
            {contextOps.routes.slice(0, 8).map((route) => (
              <article key={route.route}>
                <strong>{route.route}</strong>
                <span>{route.snapshot_count} 快照</span>
                <span>{route.estimated_tokens.toLocaleString()} tokens</span>
                <span>减少 {Math.round(route.compression_ratio * 100)}%</span>
              </article>
            ))}
          </div>
        ) : null}
        {resourceEval ? (
          <div className={`resource-eval-report resource-eval-report--${resourceEval.status}`}>
            <header><div><strong>Resource Query 固定评测</strong><span>{resourceEval.model}</span></div><b>{resourceEval.passed}/{resourceEval.total}</b></header>
            <div className="resource-eval-metrics"><span>调用判断 {Math.round(resourceEval.decision_accuracy * 100)}%</span><span>资源准确 {Math.round(resourceEval.resource_precision * 100)}%</span><span>定位准确 {Math.round(resourceEval.locator_precision * 100)}%</span></div>
            <div className="resource-eval-cases">{resourceEval.cases.map((item) => {
              const passed = item.decision_passed && item.resource_passed && item.locator_passed;
              return <article className={passed ? "resource-eval-case--passed" : "resource-eval-case--failed"} key={item.case_id}><span>{passed ? <Check size={13} /> : <AlertTriangle size={13} />}</span><div><strong>{item.title}</strong><small>{item.expected_call ? "应调用局部读取" : "不应调用"} · 实际{item.actual_call ? "已调用" : "未调用"}</small></div></article>;
            })}</div>
          </div>
        ) : null}
        <div className="context-retention-panel">
          <div><h3>上下文保留策略</h3><p>默认保留每个会话最近 20 个快照；过期摘要仅在无快照引用时处理，等待审批和运行中的任务不会被清理。</p></div>
          <div className="context-retention-actions">
            <button className="secondary-button" disabled={runningRetention} onClick={() => void runRetention(false)}>预演保留策略</button>
            <button className="danger-button" disabled={runningRetention || !retentionReport?.dry_run} onClick={() => void runRetention(true)}>确认执行清理</button>
          </div>
          {retentionReport ? <div className="context-retention-results">
            {retentionReport.candidates.map((item) => <span key={item.kind}><b>{item.count}</b>{item.kind}</span>)}
            <small>{retentionReport.dry_run ? "预演完成：未删除任何数据" : `执行完成：回收 ${formatFileBytes(retentionReport.reclaimed_bytes)}`}</small>
          </div> : null}
        </div>
      </section>

      <section className="card admin-feedback-section">
        <div className="admin-section-header">
          <div><h2>用户反馈</h2><p>保留问题、回答、评价和改进说明，可直接沉淀成测试数据。</p></div>
          <div className="admin-filter-tabs">
            {(["all", "helpful", "not_helpful"] as const).map((item) => <button key={item} className={filter === item ? "active" : ""} onClick={() => changeFilter(item)}>{item === "all" ? "全部" : item === "helpful" ? "有帮助" : "没帮助"}</button>)}
          </div>
        </div>
        <div className="admin-feedback-list">
          {feedback.map((item) => (
            <article className="admin-feedback-card" key={item.id}>
              <div className="admin-feedback-meta">
                <span className={item.helpful ? "feedback-positive" : "feedback-negative"}>{item.helpful ? <ThumbsUp size={13} /> : <ThumbsDown size={13} />}{item.helpful ? "有帮助" : "没帮助"}</span>
                <strong>{item.owner_display_name}</strong><span>{item.conversation_title}</span><time>{new Date(item.updated_at).toLocaleString("zh-CN")}</time>
              </div>
              <div className="admin-feedback-content"><p><b>用户问题</b>{item.question || "—"}</p><p><b>AI 回答</b>{item.answer}</p>{item.comment ? <p className="admin-feedback-comment"><b>用户说明</b>{item.comment}</p> : null}</div>
              <label>测试处理<select value={item.test_status} onChange={(event) => void updateStatus(item, event.target.value as AdminFeedbackRecord["test_status"])}><option value="unreviewed">待判断</option><option value="added_to_test">加入测试集</option><option value="resolved">已处理</option></select></label>
            </article>
          ))}
          {!loading && !feedback.length ? <div className="admin-empty">当前筛选条件下还没有反馈</div> : null}
        </div>
      </section>

      <section className="card admin-history-section">
        <div className="admin-section-header">
          <div><h2>回答历史管理</h2><p>按账号查看组织内会话，不会混入管理员自己的普通对话列表。</p></div>
          <form className="admin-history-search" onSubmit={(event) => { event.preventDefault(); void load(query, filter); }}><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、回答或账号" /><button>搜索</button></form>
        </div>
        <div className="admin-history-table-wrap">
          <table className="admin-history-table"><thead><tr><th>会话</th><th>用户</th><th>消息</th><th>反馈</th><th>更新时间</th><th /></tr></thead><tbody>
            {history.map((item) => <tr key={item.id}><td><strong>{item.title}</strong><span>{item.preview || "暂无摘要"}</span></td><td>{item.owner_display_name}<small>{item.owner_username}</small></td><td>{item.message_count}</td><td>{item.feedback_count}</td><td>{new Date(item.updated_at).toLocaleString("zh-CN")}</td><td><button onClick={() => void unihrApi.getAdminHistory(item.id).then(setSelectedDetail).catch((reason: Error) => setError(reason.message))}>查看</button><button className="danger" onClick={() => void deleteHistory(item)}><Trash2 size={14} />删除</button></td></tr>)}
          </tbody></table>
          {!loading && !history.length ? <div className="admin-empty">没有匹配的回答历史</div> : null}
        </div>
      </section>

      {selectedDetail ? (
        <div className="dialog-scrim" onMouseDown={(event) => event.target === event.currentTarget && setSelectedDetail(null)}>
          <section className="admin-history-dialog" role="dialog" aria-modal="true" aria-label="回答历史详情">
            <header><div><span>只读历史</span><h2>{selectedDetail.conversation.title}</h2></div><button className="icon-button" onClick={() => setSelectedDetail(null)} aria-label="关闭"><X size={19} /></button></header>
            <div className="admin-history-dialog-body">{selectedDetail.messages.map((message) => <article key={message.id} className={`admin-history-message admin-history-message--${message.role}`}><strong>{message.role === "user" ? "用户" : "UniHR Assistant"}</strong><div className="conversation-markdown"><Suspense fallback={<LazyModuleFallback label="正在加载回答…" />}><MarkdownContent>{message.content}</MarkdownContent></Suspense></div></article>)}</div>
          </section>
        </div>
      ) : null}
    </div>
  );
}

function ComingSoon({ view, onBack }: { view: Exclude<View, "tasks" | "planner" | "case" | "recruitment" | "knowledge" | "skills" | "connections" | "approvals" | "quality" | "admin">; onBack: () => void }) {
  const content = {
    templates: { icon: <LayoutTemplate />, title: "模板与经验库正在建设", text: "将沉淀历史材料、规则、字段映射和人工修订经验。" }
  }[view];

  return (
    <section className="coming-soon card">
      <div className="coming-icon">{content.icon}</div>
      <span className="section-kicker">Phase 0</span>
      <h1>{content.title}</h1>
      <p>{content.text}</p>
      <button className="secondary-button" onClick={onBack}><ArrowLeft size={16} /> 返回任务中心</button>
    </section>
  );
}

function CreateTaskDialog({ onClose, onSubmit }: { onClose: () => void; onSubmit: (title: string) => void }) {
  const [title, setTitle] = useState("整理新入职人员信息并生成复核台账");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (title.trim()) onSubmit(title.trim());
  };

  return (
    <div className="dialog-scrim" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="create-task-title" onSubmit={submit}>
        <div className="dialog-header">
          <div className="dialog-icon"><Sparkles size={20} /></div>
          <div><h2 id="create-task-title">创建人事任务</h2><p>先描述目标，AI 会识别所需资料并生成执行计划。</p></div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭"><X size={19} /></button>
        </div>
        <label className="form-field">
          <span>你希望完成什么工作？</span>
          <input ref={inputRef} value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <button className="upload-zone" type="button">
          <span className="upload-icon"><Upload size={21} /></span>
          <span><strong>上传本年度资料和历史材料</strong><small>支持 Excel、Word、PDF、PPT、图片和压缩包</small></span>
        </button>
        <div className="dialog-note"><ShieldCheck size={16} /><span>文件将进行安全检查和敏感信息识别，原始文件不会被覆盖。</span></div>
        <div className="dialog-actions"><button type="button" className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" type="submit">创建任务 <ArrowRight size={16} /></button></div>
      </form>
    </div>
  );
}

export default App;
