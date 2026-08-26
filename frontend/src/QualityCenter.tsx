import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, CheckCircle2, GitBranch, Play, RotateCcw, ShieldCheck, Square, XCircle } from "lucide-react";
import { unihrApi, type QualityEvalRun, type QualityOverview, type QualityReleaseDecision, type QualityReport } from "./api";

const terminal = new Set(["completed", "failed", "cancelled"]);

function budgetValue(value: number, unit: string) {
  if (unit === "ms") return `${Math.round(value)}ms`;
  if (unit === "比例") return `${Math.round(value * 100)}%`;
  return `¥${value.toFixed(4)}`;
}

export default function QualityCenter({ onBack }: { onBack: () => void }) {
  const [overview, setOverview] = useState<QualityOverview | null>(null);
  const [selectedSuite, setSelectedSuite] = useState("routing-core");
  const [selectedReport, setSelectedReport] = useState<QualityReport | null>(null);
  const [running, setRunning] = useState<QualityEvalRun | null>(null);
  const [releaseDecision, setReleaseDecision] = useState<QualityReleaseDecision | null>(null);
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const value = await unihrApi.getQualityOverview();
      setOverview(value);
      if (!value.suites.some((item) => item.id === selectedSuite) && value.suites[0]) {
        setSelectedSuite(value.suites[0].id);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "质量运营数据加载失败");
    }
  };

  useEffect(() => { void load(); }, []);

  useEffect(() => {
    if (!running || terminal.has(running.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const report = await unihrApi.getQualityRun(running.id);
        setSelectedReport(report);
        setRunning(report.run);
        void load();
        if (terminal.has(report.run.status)) {
          if (report.run.status === "completed") setReleaseDecision(await unihrApi.assessQualityRun(report.run.id));
          void load();
        }
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "运行状态读取失败");
      }
    }, 1200);
    return () => window.clearInterval(timer);
  }, [running?.id, running?.status]);

  const latestBySuite = useMemo(() => {
    const result = new Map<string, QualityEvalRun>();
    for (const run of overview?.runs ?? []) if (!result.has(run.suite_id)) result.set(run.suite_id, run);
    return result;
  }, [overview]);

  const start = async () => {
    setError("");
    setSelectedReport(null);
    setReleaseDecision(null);
    try {
      const baseline = latestBySuite.get(selectedSuite);
      const run = await unihrApi.startQualityRun(selectedSuite, baseline?.status === "completed" ? baseline.id : undefined);
      setRunning(run);
      void load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "评测启动失败");
    }
  };

  const openRun = async (run: QualityEvalRun) => {
    setError("");
    setReleaseDecision(null);
    try {
      setSelectedReport(await unihrApi.getQualityRun(run.id));
      if (run.status === "completed") setReleaseDecision(await unihrApi.assessQualityRun(run.id));
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "评测详情读取失败"); }
  };

  const cancel = async () => {
    if (!running || terminal.has(running.status)) return;
    setError("");
    try { setRunning(await unihrApi.cancelQualityRun(running.id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "取消评测失败"); }
  };

  const reviewFeedback = async (id: string, accepted: boolean) => {
    setError("");
    try { await unihrApi.reviewQualityFeedback(id, accepted); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "反馈处理失败"); }
  };

  const totalCases = overview?.suites.reduce((sum, item) => sum + item.case_ids.length, 0) ?? 0;
  const latest = overview?.runs[0];
  const latestRate = latest?.case_count ? latest.passed_count / latest.case_count : null;

  return <div className="quality-center">
    <section className="quality-heading">
      <button className="back-button" onClick={onBack}><ArrowLeft size={17} />返回</button>
      <div><span className="eyebrow">Agent Quality Operations</span><h1>质量与能力运营中心</h1><p>用真实 Workbench 回放证明每次升级没有让路由、Skill、Graph 或文件交付退步。</p></div>
      {running && !terminal.has(running.status)
        ? <button className="secondary-button" onClick={() => void cancel()}><Square size={15} />安全停止</button>
        : <button className="primary-button" onClick={() => void start()}><Play size={16} />运行所选黄金集</button>}
    </section>

    {error ? <div className="quality-error"><AlertTriangle size={17} />{error}</div> : null}
    {overview ? <section className={`quality-infrastructure ${overview.storage.production_ready ? "ready" : "degraded"}`}>
      <div><strong>{overview.storage.production_ready ? "PostgreSQL 质量证据已启用" : "SQLite 降级模式"}</strong><span>{overview.storage.production_ready ? "历史评测、发布决定与任务租约可跨进程恢复。" : `当前仅适合单机演示；${overview.storage.fallback_reason || "尚未配置 PostgreSQL"}`}</span></div>
      <div><span>队列 {overview.queue.queued}</span><span>执行中 {overview.queue.running}</span><span>可恢复 {overview.queue.retryable}</span><span>失败 {overview.queue.failed}</span></div>
    </section> : null}
    <section className="quality-metrics">
      <article><span>黄金测试集</span><strong>{overview?.suites.length ?? 0}</strong><small>{totalCases} 个版本化案例</small></article>
      <article><span>最近通过率</span><strong>{latestRate == null ? "—" : `${Math.round(latestRate * 100)}%`}</strong><small>{latest ? `${latest.passed_count}/${latest.case_count}` : "等待首次运行"}</small></article>
      <article><span>回归数量</span><strong>{selectedReport?.regressions.length ?? 0}</strong><small>基线通过、候选失败</small></article>
      <article><span>反馈候选</span><strong>{overview?.feedback_candidates.length ?? 0}</strong><small>等待管理员沉淀</small></article>
      <article><span>P95 响应时延</span><strong>{selectedReport ? `${(selectedReport.p95_duration_ms / 1000).toFixed(1)}s` : "—"}</strong><small>发布门禁读取真实回放</small></article>
      <article><span>本轮模型成本</span><strong>{selectedReport ? selectedReport.pricing_coverage < 1 ? "待定价" : `¥${selectedReport.total_estimated_cost_cny.toFixed(4)}` : "—"}</strong><small>{selectedReport ? `定价覆盖 ${Math.round(selectedReport.pricing_coverage * 100)}% · ${selectedReport.cost_observed_case_count} 个模型案例` : "只使用已配置单价"}</small></article>
    </section>

    <section className="card quality-run-history">
      <header><div><h2>Skill 节点运行与恢复</h2><p>来自真实 Agent Run 轨迹；失败、等待补充、复用和人工恢复均可下钻。</p></div></header>
      <div className="quality-metrics">
        <article><span>Skill 子图</span><strong>{overview?.skill_nodes.run_count ?? 0}</strong><small>真实运行次数</small></article>
        <article><span>节点完成</span><strong>{overview?.skill_nodes.node_completed ?? 0}</strong><small>失败 {overview?.skill_nodes.node_failed ?? 0} · 等资料 {overview?.skill_nodes.node_waiting_input ?? 0}</small></article>
        <article><span>恢复成功率</span><strong>{overview?.skill_nodes.retry_success_rate == null ? "—" : `${Math.round(overview.skill_nodes.retry_success_rate * 100)}%`}</strong><small>{overview?.skill_nodes.recovery_completed ?? 0}/{overview?.skill_nodes.recovery_started ?? 0} 次</small></article>
        <article><span>节点复用率</span><strong>{overview?.skill_nodes.reuse_rate == null ? "—" : `${Math.round(overview.skill_nodes.reuse_rate * 100)}%`}</strong><small>复用 {overview?.skill_nodes.node_reused ?? 0} 个节点</small></article>
        <article><span>平均节点耗时</span><strong>{overview?.skill_nodes.average_duration_ms == null ? "—" : `${Math.round(overview.skill_nodes.average_duration_ms)}ms`}</strong><small>按完成节点计算</small></article>
        <article><span>节点估算成本</span><strong>¥{(overview?.skill_nodes.total_estimated_cost_cny ?? 0).toFixed(4)}</strong><small>只累计有计价依据的节点</small></article>
      </div>
    </section>

    <div className="quality-layout">
      <section className="card quality-suites">
        <header><div><h2>评测套件</h2><p>选择一组真实用户任务，不使用演示成功率。</p></div></header>
        {(overview?.suites ?? []).map((suite) => {
          const run = latestBySuite.get(suite.id);
          return <button key={suite.id} className={selectedSuite === suite.id ? "selected" : ""} onClick={() => setSelectedSuite(suite.id)}>
            <div><strong>{suite.name}</strong><span>v{suite.version} · {suite.case_ids.length} 案例</span></div>
            <p>{suite.description}</p>
            <small>{run ? `最近 ${run.passed_count}/${run.case_count} · ${run.status}` : "尚未运行"}</small>
          </button>;
        })}
      </section>

      <section className="card quality-run-detail">
        <header><div><h2>{running && !terminal.has(running.status) ? "正在真实回放" : selectedReport ? "评测详情" : "发布证据"}</h2><p>{running && !terminal.has(running.status) ? "Router → Skill Plan → Graph → Tool → Harness" : "点击历史运行查看失败发生在哪一层。"}</p></div>{running && !terminal.has(running.status) ? <RotateCcw className="spin" size={20} /> : <ShieldCheck size={20} />}</header>
        {selectedReport ? <>
          {!terminal.has(selectedReport.run.status) ? <div className="quality-progress"><span>已完成 {selectedReport.results.length}/{selectedReport.run.case_count} 个真实案例</span><progress value={selectedReport.results.length} max={selectedReport.run.case_count || 1} /></div> : null}
          <div className="quality-report-metrics">{selectedReport.metrics.map((metric) => <article key={metric.key}><span>{metric.label}{!terminal.has(selectedReport.run.status) ? "（阶段值）" : ""}</span><strong>{metric.value == null ? "—" : `${Math.round(metric.value * 100)}%`}</strong><small>{metric.numerator}/{metric.denominator}{metric.delta == null ? "" : ` · ${metric.delta >= 0 ? "+" : ""}${Math.round(metric.delta * 100)}pt`}</small></article>)}</div>
          {releaseDecision ? <article className={`quality-release ${releaseDecision.status}`}>
            <ShieldCheck size={20} /><div><strong>{releaseDecision.status === "passed" ? "允许进入下一版本" : releaseDecision.status === "blocked" ? "发布门禁已阻断" : "需要人工豁免后发布"}</strong><p>关键案例 {releaseDecision.passed_critical}/{releaseDecision.total_critical} 通过</p>{releaseDecision.budget_checks.map((item) => <small key={item.code} className={item.passed ? "budget-pass" : "budget-fail"}>{item.passed ? "✓" : "✕"} {item.label}：{budgetValue(item.actual, item.unit)} / 限值 {budgetValue(item.limit, item.unit)}</small>)}{[...releaseDecision.blocking_failures, ...releaseDecision.warnings].map((item) => <small key={item}>{item}</small>)}</div>
          </article> : null}
          <details className="quality-snapshot"><summary>本次运行使用了哪些模型与能力版本</summary><p>模型链：{selectedReport.run.snapshot.model_chain.join(" → ") || "未记录"}</p><p>路由策略：{selectedReport.run.snapshot.router_policy || "未记录"}</p><p>能力版本：{Object.entries(selectedReport.run.snapshot.capability_versions).map(([key, value]) => `${key}@${value}`).join(" · ") || "未记录"}</p><p>Skill 版本：{Object.entries(selectedReport.run.snapshot.skill_versions).map(([key, value]) => `${key}@${value}`).join(" · ") || "本轮未使用"}</p></details>
          <div className="quality-case-results">{selectedReport.results.map((result) => <article key={result.id} className={result.passed ? "passed" : "failed"}>
            {result.passed ? <CheckCircle2 size={17} /> : <XCircle size={17} />}
            <div><strong>{result.case_id}</strong><span>{result.observation.capability_id || result.observation.business_intent || "未识别"} · {result.observation.delivery_mode || "未声明交付形态"} · {result.observation.duration_ms}ms{result.observation.estimated_cost_cny == null ? "" : ` · ¥${result.observation.estimated_cost_cny.toFixed(4)}`}</span>{result.root_failure ? <p>最早根因：{result.root_failure.layer} / {result.root_failure.code}</p> : <p>业务路由、交付形态和关键合同全部通过</p>}</div>
          </article>)}</div>
        </> : <div className="quality-empty"><GitBranch size={34} /><strong>选择并运行一个黄金集</strong><span>系统会保留实际能力版本、模型链、Graph 路径、文件 Harness 和失败根因。</span></div>}
      </section>
    </div>

    <section className="card quality-run-history"><header><div><h2>真实运行记录</h2><p>每个数字都能下钻到 Eval Case Result。</p></div></header><div className="quality-run-table">
      {(overview?.runs ?? []).map((run) => <button key={run.id} onClick={() => void openRun(run)}><span>{run.suite_id}</span><strong>{run.passed_count}/{run.case_count}</strong><em>{run.status}</em><small>{new Date(run.started_at).toLocaleString("zh-CN")}</small></button>)}
      {!overview?.runs.length ? <div className="quality-empty compact">还没有真实评测运行</div> : null}
    </div></section>
    <section className="card quality-feedback-candidates"><header><div><h2>真实用户反馈候选</h2><p>没帮助、人工退回和改选先脱敏，再由管理员决定是否进入黄金集。</p></div></header>
      <div>{(overview?.feedback_candidates ?? []).map((item) => <article key={item.id}>
        <div><strong>{item.scrubbed_prompt || "未保留问题文本"}</strong><p>{item.expected_correction || "用户未填写修改说明"}</p><small>{item.status} · {new Date(item.created_at).toLocaleString("zh-CN")}</small></div>
        {item.status === "pending" ? <aside><button onClick={() => void reviewFeedback(item.id, false)}>忽略</button><button className="primary-button" onClick={() => void reviewFeedback(item.id, true)}>加入回归集</button></aside> : null}
      </article>)}</div>
      {!overview?.feedback_candidates.length ? <div className="quality-empty compact">暂时没有待处理反馈</div> : null}
    </section>
  </div>;
}
