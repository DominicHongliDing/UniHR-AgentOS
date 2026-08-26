const API_BASE = import.meta.env.VITE_UNIHR_API_BASE ?? "http://127.0.0.1:8088/api/v1";

export type Conversation = {
  id: string;
  tenant_id: string;
  owner_user_id: string;
  title: string;
  preview: string;
  pinned: boolean;
  created_at: string;
  updated_at: string;
};

export type Message = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  trace: TraceStep[];
  task_brief: TaskBrief | Record<string, never>;
};

export type TaskBrief = {
  version: "1.0";
  kind: "general" | "recruitment" | "policy" | "spreadsheet" | "document" | "image";
  objective: string;
  constraints: string[];
  deliverables: string[];
  selected_skill_ids: string[];
  approval_required: boolean;
  mode_spec: Record<string, unknown>;
  artifact_style: {
    document?: Record<string, unknown>;
    spreadsheet?: Record<string, unknown>;
  };
};

export type TraceStep = {
  event?: string;
  summary?: string;
  stage?: string;
  title?: string;
  detail?: string;
  status?: string;
  [key: string]: unknown;
};

export type FeishuConnectionStatus = {
  provider: "feishu";
  configured: boolean;
  connected: boolean;
  state: "not_configured" | "not_connected" | "reauthorization_required" | "connected";
  display_name: string | null;
  external_user_id: string | null;
  scopes: string[];
  expires_at: string | null;
  last_error: string | null;
  missing_configuration: string[];
  adapter_ready: boolean;
  capabilities: Array<{
    capability: string;
    label: string;
    mode: "read" | "write";
    implemented: boolean;
    available: boolean;
    availability: string;
  }>;
};

export type FeishuChannelStatus = {
  enabled: boolean;
  mode: "webhook" | "long_connection";
  credentials_configured: boolean;
  verification_token_configured: boolean;
  default_identity_enabled: boolean;
  external_relay_enabled: boolean;
  external_relay_secret_configured: boolean;
  external_group_count: number;
  queue: Record<string, number>;
  action_queue: Record<string, number>;
  pending_bindings: Array<{
    external_tenant_key: string;
    external_user_id: string;
    event_count: number;
    last_seen_at: string;
  }>;
};

export type FeishuExternalGroup = {
  group_id: string;
  display_name: string;
  owner_user_id: string;
  enabled: boolean;
  webhook_configured: boolean;
  webhook_secret_configured: boolean;
};

export type FeishuGroupContextPolicy = {
  provider: "feishu";
  external_tenant_key: string;
  external_conversation_id: string;
  enabled: boolean;
  max_messages: number;
  retention_hours: number;
};

export type ExternalEvidenceBundle = {
  provider: "feishu";
  capability: string;
  evidence: Array<{
    source_id: string;
    title: string;
    content: string;
    url: string | null;
    observed_at: string;
    metadata: Record<string, unknown>;
  }>;
  warnings: string[];
};

export type ExternalActionProposal = {
  id: string;
  capability: string;
  summary: string;
  normalized_arguments: Record<string, unknown>;
  parameter_hash: string;
  status: string;
  expires_at: string;
};

export type ExternalActionReceipt = {
  id: string;
  proposal_id: string;
  capability: string;
  status: "succeeded" | "failed";
  external_ids: string[];
  error_code: string | null;
};

export type FeishuAuthorizationStart = {
  authorization_url: string;
  expires_at: string;
};

export type StoredAttachment = {
  id: string;
  message_id: string;
  display_name: string;
  mime_type: string;
  byte_size: number;
  summary: Record<string, unknown>;
};

export type StoredArtifact = {
  id: string;
  message_id: string;
  kind: string;
  display_name: string;
  mime_type: string;
  byte_size: number;
  metadata: Record<string, unknown>;
};

export type ResourceItem = {
  id: string;
  resource_type: "source" | "artifact";
  message_id: string;
  display_name: string;
  mime_type: string;
  byte_size: number;
  created_at: string;
  status: string;
  download_url: string;
  parent_resource_ids: string[];
  lineage_key: string;
  version_number: number;
  is_current: boolean;
  archived_at: string;
  preview_url: string;
  metadata: Record<string, unknown>;
};

export type ResourceWorkspaceManifest = {
  conversation_id: string;
  source_count: number;
  artifact_count: number;
  total_bytes: number;
  active_count: number;
  archived_count: number;
  items: ResourceItem[];
};

export type ContextSnapshotMetric = {
  id: string;
  conversation_id: string;
  conversation_title: string;
  route: string;
  raw_chars: number;
  injected_chars: number;
  saved_chars: number;
  compression_ratio: number;
  estimated_tokens: number;
  compaction_applied: boolean;
  hydration_count: number;
  strategy: string;
  fidelity_status: string;
  created_at: string;
};

export type ResourceQueryMetrics = {
  attempted_calls: number;
  executed_calls: number;
  rejected_calls: number;
  truncated_calls: number;
  characters: number;
  average_characters: number;
  locator_precision: number | null;
  invalid_call_rate: number | null;
  truncation_rate: number | null;
};

export type ContextRouteMetric = {
  route: string;
  snapshot_count: number;
  raw_chars: number;
  injected_chars: number;
  saved_chars: number;
  compression_ratio: number;
  estimated_tokens: number;
  hydration_count: number;
};

export type ContextDailyMetric = {
  date: string;
  snapshot_count: number;
  estimated_tokens: number;
  compaction_count: number;
  hydration_count: number;
  estimated_cost_cny: number;
};

export type ContextCostEstimate = {
  configured: boolean;
  input_cost_cny_per_million: number;
  estimated_input_cost_cny: number;
  note: string;
};

export type ModelUsageSummary = {
  days: number;
  calls: number;
  input_units: number;
  output_units: number;
  estimated_cost_cny: number;
  pricing_configured: boolean;
  priced_calls: number;
  unpriced_calls: number;
  pricing_coverage: number;
  breakdown: Array<{
    operation_kind: string;
    purpose: string;
    model: string;
    status: string;
    calls: number;
    input_units: number;
    output_units: number;
    estimated_cost_cny: number;
    pricing_configured: boolean;
  }>;
  daily: Array<{
    day: string;
    calls: number;
    input_units: number;
    output_units: number;
    estimated_cost_cny: number;
  }>;
  attribution: Array<{
    conversation_id: string;
    conversation_title: string;
    case_id: string;
    case_name: string;
    calls: number;
    estimated_cost_cny: number;
  }>;
};

export type ModelCostControl = {
  policy: {
    tenant_id: string;
    monthly_budget_cny: number;
    per_run_budget_cny: number;
    daily_call_limit: number;
    warning_ratio: number;
    updated_by: string;
    updated_at: string;
  };
  monthly_calls: number;
  priced_calls: number;
  unpriced_calls: number;
  pricing_coverage: number;
  monthly_cost_cny: number;
  daily_calls: number;
  max_run_cost_cny: number;
  max_run_id: string;
  alerts: Array<{ code: string; severity: string; message: string }>;
};

export type ContextRetentionPolicy = {
  snapshot_days: number;
  snapshot_keep_latest: number;
  summary_days: number;
  run_payload_days: number;
  turn_execution_days: number;
  model_usage_days: number;
};

export type ContextRetentionReport = {
  audit_id: string;
  tenant_id: string;
  dry_run: boolean;
  status: "previewed" | "executed";
  policy: ContextRetentionPolicy;
  candidates: Array<{ kind: string; count: number; byte_size: number; oldest_created_at: string }>;
  deleted_counts: Record<string, number>;
  reclaimed_bytes: number;
  created_at: string;
};

export type SemanticFact = {
  id: string;
  conversation_id: string;
  slot_key: string;
  domain: string;
  label: string;
  value: unknown;
  display_value: string;
  version: number;
  status: "active" | "superseded" | "tombstoned" | "conflicted" | "pending_confirmation" | "rejected";
  source_message_id: string;
  supersedes_id: string;
  extraction_method: string;
  confidence: number;
  created_at: string;
};

export type ConversationCleanupReport = {
  audit_id: string;
  conversation_id: string;
  conversation_title: string;
  tenant_id: string;
  owner_user_id: string;
  deleted_counts: Record<string, number>;
  graph_threads_deleted: number;
  turn_executions_deleted: number;
  files_deleted: number;
  files_skipped: number;
  skills_detached: number;
  verification_status: "passed" | "residual_state_detected";
  created_at: string;
};

export type ContextOperationsDashboard = {
  scope: "conversation" | "tenant";
  conversation_id: string | null;
  snapshot_count: number;
  compaction_count: number;
  raw_chars: number;
  injected_chars: number;
  saved_chars: number;
  compression_ratio: number;
  estimated_tokens: number;
  hydration_count: number;
  fidelity_failures: number;
  resource_query: ResourceQueryMetrics;
  routes: ContextRouteMetric[];
  daily: ContextDailyMetric[];
  cost: ContextCostEstimate;
  model_usage: ModelUsageSummary;
  cost_control: ModelCostControl;
  snapshots: ContextSnapshotMetric[];
  semantic_facts: SemanticFact[];
  pending_semantic_facts: SemanticFact[];
};

export type ResourceQueryEvalCaseResult = {
  case_id: string;
  title: string;
  expected_call: boolean;
  actual_call: boolean;
  decision_passed: boolean;
  resource_passed: boolean;
  locator_passed: boolean;
  called_resource_ids: string[];
  notes: string[];
};

export type ResourceQueryEvalReport = {
  status: "passed" | "needs_attention";
  model: string;
  total: number;
  passed: number;
  decision_accuracy: number;
  resource_precision: number;
  locator_precision: number;
  cases: ResourceQueryEvalCaseResult[];
};

export type ConversationDetail = {
  conversation: Conversation;
  messages: Message[];
  attachments: StoredAttachment[];
  artifacts: StoredArtifact[];
  feedback: AnswerFeedback[];
  runs: RunRecord[];
  workflow_cases: WorkflowCase[];
};

export type WorkflowAgent = "planner" | "recruitment" | "human_review" | "document" | "spreadsheet" | "public_info" | "policy";

export type WorkflowStep = {
  id: string;
  sequence: number;
  title: string;
  agent: WorkflowAgent;
  instruction: string;
  depends_on: string[];
  requires_human_review: boolean;
  optional: boolean;
  required_artifact_kinds: string[];
  status: "pending" | "running" | "waiting_human" | "succeeded" | "skipped" | "failed";
  run_ids: string[];
  artifact_ids: string[];
  summary: string;
  started_at: string;
  completed_at: string;
  error_code: string;
};

export type WorkflowHandoff = {
  contract_version: "unihr.handoff/v1";
  id: string;
  source_kind: "workflow_step" | "skill" | "agent";
  source_id: string;
  target_kind: "workflow_step" | "skill" | "agent";
  target_id: string;
  run_refs: string[];
  output_refs: string[];
  artifact_refs: string[];
  provided_capabilities: string[];
  business_refs: Record<string, string>;
  from_step_id: string;
  to_step_id: string;
  summary: string;
  run_ids: string[];
  artifact_ids: string[];
  recruitment_case_id: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type WorkflowCase = {
  id: string;
  tenant_id: string;
  owner_user_id: string;
  conversation_id: string;
  title: string;
  objective: string;
  rationale: string;
  planner_source: string;
  status: "running" | "waiting_human" | "succeeded" | "failed" | "cancelled";
  current_step_id: string;
  recruitment_case_id: string;
  steps: WorkflowStep[];
  handoffs: WorkflowHandoff[];
  artifact_ids: string[];
  decision_log: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
};

export type Principal = {
  user_id: string;
  username: string;
  display_name: string;
  role: "admin" | "user";
  tenant_id: string;
  permissions: string[];
};

export type LoginResult = {
  access_token: string;
  token_type: "bearer";
  expires_at: string;
  user: Principal;
};

export type AnswerFeedback = {
  id: string;
  conversation_id: string;
  message_id: string;
  user_id: string;
  helpful: boolean;
  comment: string;
  test_status: "unreviewed" | "added_to_test" | "resolved";
  created_at: string;
  updated_at: string;
};

export type FeedbackStats = {
  total: number;
  helpful: number;
  not_helpful: number;
  helpful_rate: number | null;
  pending_test_review: number;
  added_to_test: number;
};

export type ProductCapability = {
  capability_id: string;
  status: "implemented" | "implemented_manual" | "not_implemented" | string;
  user_scope: "admin" | "authorized_user" | "none" | string;
  fact: string;
};

export type CapabilityOperationsSummary = {
  registered_capabilities: number;
  active_capabilities: number;
  observed_capabilities: number;
  total_runs: number;
  completed_runs: number;
  completion_rate: number | null;
  fallback_runs: number;
  fallback_rate: number | null;
  feedback_count: number;
  helpful_rate: number | null;
  capabilities_needing_attention: number;
};

export type CapabilityOperationsCard = {
  capability_id: string;
  label: string;
  intent: string;
  route: string;
  description: string;
  version: string;
  lifecycle: string;
  risk_level: "low" | "medium" | "high" | string;
  owner: string;
  input_contract: string;
  output_contract: string;
  human_review_policy: string;
  tools: string[];
  retrieval_mode: string;
  requires_freshness: boolean;
  run_count: number;
  completed_runs: number;
  completion_rate: number | null;
  fallback_runs: number;
  feedback_count: number;
  helpful_rate: number | null;
  quality_issue_count: number;
  health: "healthy" | "watch" | "no_data";
};

export type CapabilityOperationsSnapshot = {
  generated_at: string;
  window_days: number;
  summary: CapabilityOperationsSummary;
  capabilities: CapabilityOperationsCard[];
};

export type RoutingGoldenCaseResult = {
  case_id: string;
  title: string;
  prompt: string;
  expected_capability_id: string;
  actual_capability_id: string;
  expected_action: string | null;
  actual_action: string;
  expected_retrieval_mode: string | null;
  actual_retrieval_mode: string;
  expected_execution_mode: string | null;
  execution_mode: string;
  expected_delivery_mode: string | null;
  actual_delivery_mode: string;
  matched_layer: string;
  fallback_used: boolean;
  passed: boolean;
  issues: string[];
};

export type RoutingGoldenReport = {
  dataset_version: string;
  status: "passed" | "needs_attention";
  total: number;
  passed: number;
  accuracy: number;
  model_routed_cases: number;
  fallback_cases: number;
  results: RoutingGoldenCaseResult[];
};

export type QualityEvalSuite = {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  domain: string;
  version: number;
  frozen: boolean;
  case_ids: string[];
  created_at: string;
};

export type QualityEvalRun = {
  id: string;
  tenant_id: string;
  suite_id: string;
  suite_version: number;
  candidate_label: string;
  baseline_run_id: string | null;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  snapshot: {
    build_version: string;
    router_policy: string;
    model_chain: string[];
    capability_versions: Record<string, string>;
    skill_versions: Record<string, string>;
    knowledge_snapshot: string;
    budget_policy: {
      max_p95_duration_ms: number;
      max_average_cost_cny: number;
      max_total_cost_cny: number;
      min_pricing_coverage: number;
      enforcement: "warning" | "blocking";
    };
  };
  case_count: number;
  passed_count: number;
  started_at: string;
  completed_at: string | null;
  error: string;
};

export type QualityAssertion = {
  dimension: string;
  code: string;
  passed: boolean;
  expected: unknown;
  actual: unknown;
  critical: boolean;
};

export type QualityCaseResult = {
  id: string;
  run_id: string;
  case_id: string;
  passed: boolean;
  criticality: "critical" | "standard" | "exploratory";
  assertions: QualityAssertion[];
  failures: { layer: string; code: string; summary: string; assertion_index: number }[];
  root_failure: { layer: string; code: string; summary: string } | null;
  observation: {
    capability_id: string;
    business_intent: string;
    turn_action: string;
    retrieval_mode: string;
    execution_mode: string;
    delivery_mode: string;
    routing_fallback_used: boolean;
    matched_layer: string;
    graph_path: string[];
    trace_events: string[];
    artifact_names: string[];
    artifact_harness_passed: boolean | null;
    answer_excerpt: string;
    duration_ms: number;
    model_used: string;
    estimated_cost_cny: number | null;
    cost_pricing_configured: boolean | null;
    model_call_count: number;
    runtime_error: string;
  };
};

export type QualityReport = {
  run: QualityEvalRun;
  results: QualityCaseResult[];
  metrics: {
    key: string;
    label: string;
    numerator: number;
    denominator: number;
    value: number | null;
    baseline_value: number | null;
    delta: number | null;
  }[];
  root_failure_counts: Record<string, number>;
  regressions: string[];
  p50_duration_ms: number;
  p95_duration_ms: number;
  total_estimated_cost_cny: number;
  average_estimated_cost_cny: number;
  priced_case_count: number;
  cost_observed_case_count: number;
  pricing_coverage: number;
};

export type QualityReleaseDecision = {
  run_id: string;
  status: "passed" | "blocked" | "waiver_required";
  blocking_failures: string[];
  warnings: string[];
  passed_critical: number;
  total_critical: number;
  waiver_reason: string;
  budget_checks: {
    code: string;
    label: string;
    actual: number;
    limit: number;
    unit: string;
    passed: boolean;
    blocking: boolean;
  }[];
  decided_at: string;
};

export type QualityFeedbackCandidate = {
  id: string;
  feedback_id: string;
  source_message_id: string;
  scrubbed_prompt: string;
  expected_correction: string;
  status: "pending" | "accepted" | "rejected";
  created_at: string;
};

export type QualityOverview = {
  suites: QualityEvalSuite[];
  runs: QualityEvalRun[];
  latest_root_failures: Record<string, number>;
  feedback_candidates: QualityFeedbackCandidate[];
  storage: {
    backend: "postgresql" | "sqlite";
    production_ready: boolean;
    fallback_reason: string;
  };
  queue: {
    queued: number;
    running: number;
    retryable: number;
    failed: number;
  };
  skill_nodes: SkillNodeOperations;
};

export type ProductHelpRouteEvalCase = {
  case_id: string;
  message: string;
  expected_intent: string;
  actual_intent: string;
  expected_route: string;
  actual_route: string;
  passed: boolean;
};

export type ProductHelpRouteEvalReport = {
  status: "passed" | "needs_attention";
  total: number;
  passed: number;
  accuracy: number;
  cases: ProductHelpRouteEvalCase[];
};

export type RewriteEvaluationCaseResult = {
  case_id: string;
  title: string;
  original_message: string;
  resolved_request: string;
  matched_layer: string;
  expected_intent: string;
  actual_intent: string;
  expected_action: string;
  actual_action: string;
  expected_mode: string;
  actual_mode: string;
  expected_terms: string[];
  missing_expected_terms: string[];
  unexpected_hard_facts: string[];
  route_passed: boolean;
  action_passed: boolean;
  mode_passed: boolean;
  context_passed: boolean;
  fidelity_passed: boolean;
  passed: boolean;
};

export type RewriteEvaluationReport = {
  status: "passed" | "needs_attention";
  total: number;
  passed: number;
  route_accuracy: number;
  action_accuracy: number;
  mode_accuracy: number;
  context_recall: number;
  fidelity_rate: number;
  model_routed_cases: number;
  fallback_cases: number;
  cases: RewriteEvaluationCaseResult[];
};

export type AdminConversationSummary = Conversation & {
  owner_username: string;
  owner_display_name: string;
  message_count: number;
  feedback_count: number;
};

export type AdminFeedbackRecord = AnswerFeedback & {
  owner_username: string;
  owner_display_name: string;
  conversation_title: string;
  question: string;
  answer: string;
};

export type TurnAttachment = {
  id: string;
  name: string;
  mime_type: string;
  byte_size: number;
  kind: string;
  summary: Record<string, unknown>;
};

export type TurnArtifact = {
  id: string;
  kind: string;
  name: string;
  mime_type: string;
  byte_size: number;
  download_url: string;
  metadata: Record<string, unknown>;
};

export type RunSummary = {
  id: string;
  status: "running" | "succeeded" | "waiting_input" | "waiting_review" | "exhausted" | "failed" | "rejected";
  iteration: number;
  max_iterations: number;
  graph_path: string[];
  quality_issues: string[];
  requires_human_review: boolean;
};

export type RunRecord = RunSummary & {
  tenant_id: string;
  owner_user_id: string;
  conversation_id: string;
  message_id: string | null;
  business_intent: string;
  turn_action: string;
  execution_mode: string;
  checkpoint_thread_id: string;
  error_code: string | null;
  approval_decision: boolean | null;
  approval_comment: string;
  created_at: string;
  updated_at: string;
};

export type ApprovalQueueItem = RunRecord & {
  conversation_title: string;
  owner_username: string;
  owner_display_name: string;
};

export type RunEvent = {
  id: string;
  run_id: string;
  sequence: number;
  event: string;
  status: string;
  summary: string;
  details: Record<string, unknown>;
  created_at: string;
};

export type SkillNodeOperations = {
  run_count: number;
  node_started: number;
  node_completed: number;
  node_failed: number;
  node_waiting_input: number;
  node_reused: number;
  recovery_started: number;
  recovery_completed: number;
  retry_success_rate: number | null;
  reuse_rate: number | null;
  average_duration_ms: number | null;
  total_estimated_cost_cny: number;
};

export type TurnExecutionStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export type TurnExecutionEvent = {
  id: string;
  execution_id: string;
  sequence: number;
  event: string;
  status: string;
  summary: string;
  details: Record<string, unknown>;
  created_at: string;
};

export type TurnExecutionRecord = {
  id: string;
  tenant_id: string;
  owner_user_id: string;
  status: TurnExecutionStatus;
  cancel_requested: boolean;
  conversation_id: string | null;
  run_id: string | null;
  result: TurnResponse | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
};

export type RunDetail = { run: RunRecord; events: RunEvent[] };

export type RecruitmentInterviewNote = {
  id: string;
  note: string;
  outcome: "not_scheduled" | "scheduled" | "passed" | "hold" | "not_advanced";
  recorded_by: string;
  recorded_at: string;
};

export type RecruitmentInterviewPanelMember = {
  user_id: string;
  display_name: string;
};

export type RecruitmentInterviewFeedback = {
  id: string;
  session_id: string;
  candidate_id: string;
  interviewer_user_id: string;
  interviewer_name: string;
  recommendation: "strong_yes" | "yes" | "hold" | "no" | "strong_no";
  dimension_scores: Record<string, number>;
  evidence_note: string;
  risk_note: string;
  submitted_at: string;
  updated_at: string;
};

export type RecruitmentInterviewSession = {
  id: string;
  candidate_id: string;
  title: string;
  start_at: string;
  end_at: string;
  timezone: string;
  mode: "onsite" | "online" | "phone";
  location: string;
  meeting_url: string;
  interviewers: RecruitmentInterviewPanelMember[];
  focus_dimensions: string[];
  status: "scheduled" | "completed" | "cancelled";
  feedback: RecruitmentInterviewFeedback[];
  created_by: string;
  created_at: string;
  updated_at: string;
  cancelled_by: string;
  cancelled_at: string;
};

export type RecruitmentInterviewerRecord = {
  user_id: string;
  display_name: string;
  role: string;
};

export type RecruitmentCandidateReview = {
  candidate_id: string;
  candidate_name: string;
  source_name: string;
  rank: number;
  ai_score: number;
  ai_recommendation: string;
  evidence_coverage: number;
  strengths: string[];
  gaps: string[];
  verification_questions: string[];
  evidence_locations: Array<{
    criterion_label?: string;
    evidence?: string;
    locator?: Record<string, unknown>;
  }>;
  review_status: "pending" | "shortlisted" | "hold" | "not_advanced";
  reviewer_note: string;
  reviewed_by: string;
  reviewed_at: string;
  interview_outcome: "not_scheduled" | "scheduled" | "passed" | "hold" | "not_advanced";
  interview_notes: RecruitmentInterviewNote[];
};

export type RecruitmentCaseRecord = {
  id: string;
  tenant_id: string;
  owner_user_id: string;
  conversation_id: string;
  run_id: string;
  artifact_id: string;
  job_title: string;
  case_name: string;
  folder_id: string;
  archived_at: string;
  rubric_hash: string;
  selection_count: number;
  status: "reviewing" | "interviewing" | "finalized";
  candidates: RecruitmentCandidateReview[];
  collaborator_user_ids: string[];
  interview_sessions: RecruitmentInterviewSession[];
  final_candidate_ids: string[];
  final_rationale: string;
  finalized_by: string;
  finalized_at: string;
  created_at: string;
  updated_at: string;
};

export type RecruitmentCaseAction = {
  action: "review_candidate" | "record_interview" | "finalize" | "reopen" | "rename_case" | "move_case" | "archive_case" | "restore_case" | "schedule_interview" | "cancel_interview" | "submit_interview_feedback";
  candidate_id?: string;
  review_status?: RecruitmentCandidateReview["review_status"];
  reviewer_note?: string;
  interview_outcome?: RecruitmentCandidateReview["interview_outcome"];
  interview_note?: string;
  final_candidate_ids?: string[];
  final_rationale?: string;
  case_name?: string;
  folder_id?: string;
  interview_session_id?: string;
  interview_title?: string;
  interview_start_at?: string;
  interview_end_at?: string;
  interview_timezone?: string;
  interview_mode?: RecruitmentInterviewSession["mode"];
  interview_location?: string;
  interview_meeting_url?: string;
  interviewer_ids?: string[];
  focus_dimensions?: string[];
  recommendation?: RecruitmentInterviewFeedback["recommendation"];
  dimension_scores?: Record<string, number>;
  evidence_note?: string;
  risk_note?: string;
};

export type RecruitmentFolderRecord = {
  id: string;
  tenant_id: string;
  owner_user_id: string;
  name: string;
  created_at: string;
  updated_at: string;
};

export type RecruitmentPackageType = "interview_evaluation" | "final_decision";

export type RecruitmentPackagePreview = {
  case_id: string;
  package_type: RecruitmentPackageType;
  title: string;
  can_generate: boolean;
  blockers: string[];
  sections: string[];
  candidate_count: number;
  interview_session_count: number;
  feedback_count: number;
  final_candidate_count: number;
};

export type RecruitmentPackageRecord = {
  id: string;
  case_id: string;
  tenant_id: string;
  owner_user_id: string;
  package_type: RecruitmentPackageType;
  title: string;
  files: Array<{
    format: "docx" | "xlsx";
    display_name: string;
    byte_size: number;
    artifact_id: string;
  }>;
  harness: {
    passed?: boolean;
    docx?: { status?: string; issues?: string[] };
    xlsx?: { status?: string; issues?: string[] };
  };
  created_by: string;
  created_at: string;
};

export type TurnResponse = {
  conversation: Conversation;
  user_message: Message;
  assistant_message: Message;
  interpretation: {
    business_intent: string;
    execution_route: string;
    confidence: number;
    risk_level: string;
    matched_layer: string;
    risk_flags: string[];
    suggested_capabilities: string[];
    reason_summary: string;
    turn_action: string;
    execution_mode: string;
    retrieval_mode: "none" | "attachments" | "internal" | "web" | "hybrid";
  };
  attachments: TurnAttachment[];
  artifacts: TurnArtifact[];
  run: RunSummary;
  context_uses: Array<Record<string, string>>;
  trace: TraceStep[];
  model_status: string;
  workflow_case: WorkflowCase | null;
};

export type KnowledgeCatalog = {
  configured: boolean;
  file_count: number;
  total_bytes: number;
  by_disposition: Record<string, number>;
  by_collection: Record<string, number>;
  indexed_sources: number;
  indexed_chunks: number;
  failed_sources: number;
  failure_reasons: Record<string, number>;
  embedding_model: string;
  source_versions: number;
  pending_versions: number;
};

export type KnowledgeSourceVersion = {
  version_id: string;
  source_id: string;
  title: string;
  collection: string;
  checksum: string;
  source_version: string | null;
  publication_date: string | null;
  effective_date: string | null;
  expiry_date: string | null;
  policy_status: string;
  authority_level: string;
  chunk_count: number;
  lifecycle_status: "draft" | "published" | "withdrawn";
  publication_id: string | null;
  allowed_roles: string[];
  created_at: string;
  published_at: string | null;
  ingestion_status: "ready" | "review_required" | "failed";
  quality_score: number;
  quality_review_status: "not_required" | "pending" | "approved" | "rejected";
  quality_report: Record<string, unknown>;
};

export type KnowledgeEvidence = {
  version_id: string;
  title: string;
  relative_path: string;
  quality_report: Record<string, unknown>;
  blocks: Array<{ page: number; text: string; bbox: number[]; model_confidence: number | null; needs_review: boolean; kind: string }>;
};

export type KnowledgeEvaluation = {
  evaluation_id?: string;
  created_at?: string;
  dataset_version?: string;
  rerank_model?: string;
  prompt_version?: string;
  duration_ms?: number;
  case_count: number;
  top_k: number;
  hit_rate: number;
  recall_at_k: number;
  mean_reciprocal_rank: number;
  no_answer_accuracy: number;
  ndcg_at_k: number;
  citation_coverage: number;
  effective_date_misuse_rate: number;
  conflict_evidence_recall: number;
  temporal_guard_case_count: number;
  conflict_case_count: number;
  cases: Array<{
    case_id: string;
    question: string;
    hit: boolean;
    expected_no_answer: boolean;
    matched_sources: string[];
    retrieved_sources: string[];
    reciprocal_rank: number;
    ndcg_at_k: number;
    citation_coverage: number;
    retrieved_forbidden_sources: string[];
    expected_conflict: boolean;
    conflict_detected: boolean;
    expected_source_ids: string[];
    retrieved_source_ids: string[];
    matched_source_ids: string[];
    retrieved_forbidden_source_ids: string[];
  }>;
};

export type PolicyEvaluationCandidate = {
  candidate_id: string;
  feedback_id: string;
  conversation_id: string;
  message_id: string;
  question: string;
  answer_excerpt: string;
  comment: string;
  suggested_sources: Array<{ source_id: string; title: string; version: string; status: string }>;
  status: "draft" | "approved" | "rejected";
  approved_case: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type PolicyEvaluationCandidateReview = {
  approved: boolean;
  expected_source_ids?: string[];
  expected_no_answer?: boolean;
  expected_conflict?: boolean;
  forbidden_source_ids?: string[];
  as_of_date?: string | null;
  notes?: string;
};

export type VisionEvaluation = {
  evaluation_id: string;
  created_at?: string;
  case_count: number;
  expected_field_count: number;
  predicted_field_count: number;
  matched_field_count: number;
  exact_match_count: number;
  review_expected_count: number;
  review_detected_count: number;
  false_safe_count: number;
  field_precision: number;
  field_recall: number;
  exact_match_rate: number;
  mean_bbox_iou: number;
  review_recall: number;
  false_safe_rate: number;
  cases: Array<{ case_id: string; source_title: string; passed: boolean; fields: Array<Record<string, unknown>> }>;
};

export type KnowledgeIngestionJob = {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed";
  total_files: number;
  processed_files: number;
  current_file: string;
  current_stage: string;
  result: Record<string, unknown>;
  error: string;
  created_at: string;
  updated_at: string;
};

export type KnowledgeIngestionFailure = {
  relative_path: string;
  source_id: string;
  collection: string;
  reason: string;
  last_attempted_at: string;
  retry_count: number;
  last_error: string;
};

export type KnowledgeVersionComparison = {
  source_id: string;
  title: string;
  left_version_id: string;
  right_version_id: string;
  similarity: number;
  left_chunk_count: number;
  right_chunk_count: number;
  added_chunk_count: number;
  removed_chunk_count: number;
  added_samples: string[];
  removed_samples: string[];
  metadata_changes: Record<string, { before: string | null; after: string | null }>;
};

export type SkillRecord = {
  id: string;
  name: string;
  display_name: string;
  description: string;
  instructions: string;
  trigger_phrases: string[];
  capabilities: string[];
  source_type: "system" | "prompt" | "conversation" | "upload";
  source_conversation_id: string | null;
  enabled: boolean;
  status: "draft" | "active" | "disabled" | "pending_approval";
  version: number;
  visibility: "personal" | "organization" | "system";
  approval_status: "not_required" | "pending" | "approved" | "rejected";
  approval_requested_by: string;
  approved_by: string;
  approved_at: string;
  approval_comment: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type SkillVersionRecord = {
  skill_id: string;
  version: number;
  snapshot: {
    display_name: string;
    description: string;
    instructions: string;
    trigger_phrases: string[];
    capabilities: string[];
    status: SkillRecord["status"];
    enabled: boolean;
  };
  change_type: string;
  content_sha256: string;
  created_by: string;
  created_at: string;
};

export type SkillResourceRecord = {
  id: string;
  skill_id: string;
  kind: "scripts" | "references" | "assets";
  name: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  status: "ready" | "quarantined";
  scripts_executed: boolean;
  created_at: string;
};

export type SkillDryRunRecord = {
  id: string;
  skill_id: string;
  skill_version: number;
  skill_content_sha256: string;
  status: "passed" | "failed" | "needs_input";
  prompt: string;
  answer_excerpt: string;
  business_intent: string;
  graph_path: string[];
  trace_events: string[];
  artifacts: Array<{
    name: string;
    kind: string;
    mime_type: string;
    byte_size: number;
    harness_status: string;
    harness_issues: string[];
  }>;
  issues: string[];
  created_at: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { credentials: "include", ...init });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json() as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // Keep the status-based message when the server did not return JSON.
    }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function buildTurnForm({
  message,
  conversationId,
  taskMode,
  files,
  documentFont,
  documentFontSize,
  taskBrief
}: {
  message: string;
  conversationId: string | null;
  taskMode: string;
  files: File[];
  documentFont: string;
  documentFontSize: number;
  taskBrief: TaskBrief;
}) {
  const form = new FormData();
  form.set("message", message);
  form.set("task_mode", taskMode);
  form.set("document_font", documentFont);
  form.set("document_font_size", String(documentFontSize));
  form.set("task_brief_json", JSON.stringify(taskBrief));
  if (conversationId) form.set("conversation_id", conversationId);
  files.forEach((file) => form.append("files", file, file.name));
  return form;
}

export const unihrApi = {
  login: (username: string, password: string) => request<LoginResult>("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  }),
  getMe: () => request<Principal>("/auth/me"),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  getFeishuConnection: () => request<FeishuConnectionStatus>("/connections/feishu"),
  getFeishuChannelStatus: () => request<FeishuChannelStatus>("/channels/feishu/status"),
  listFeishuGroupContext: () => request<FeishuGroupContextPolicy[]>("/channels/feishu/group-context"),
  saveFeishuGroupContext: (policy: Omit<FeishuGroupContextPolicy, "provider">) => request<FeishuGroupContextPolicy>("/channels/feishu/group-context", {
    method: "PUT",
    body: JSON.stringify(policy)
  }),
  listFeishuExternalGroups: () => request<FeishuExternalGroup[]>("/channels/feishu/external/groups"),
  saveFeishuExternalGroup: (group: {
    group_id: string;
    display_name: string;
    owner_user_id: string;
    webhook_url: string;
    webhook_secret: string;
    enabled: boolean;
  }) => request<FeishuExternalGroup>("/channels/feishu/external/groups", {
    method: "POST",
    body: JSON.stringify(group)
  }),
  startFeishuAuthorization: () => request<FeishuAuthorizationStart>("/connections/feishu/authorize", { method: "POST" }),
  disconnectFeishu: () => request<FeishuConnectionStatus>("/connections/feishu", { method: "DELETE" }),
  readFeishu: (capability: string, args: Record<string, unknown>) => request<ExternalEvidenceBundle>("/connections/feishu/read", {
    method: "POST",
    body: JSON.stringify({ capability, arguments: args })
  }),
  proposeFeishuAction: (payload: { capability: string; arguments: Record<string, unknown>; summary: string; risk_level?: string }) => request<ExternalActionProposal>("/connections/feishu/actions/propose", {
    method: "POST",
    body: JSON.stringify(payload)
  }),
  commitFeishuAction: (proposalId: string, expectedParameterHash: string) => request<ExternalActionReceipt>(`/connections/feishu/actions/${proposalId}/commit`, {
    method: "POST",
    body: JSON.stringify({ expected_parameter_hash: expectedParameterHash })
  }),
  rejectFeishuAction: (proposalId: string) => request<ExternalActionProposal>(`/connections/feishu/actions/${proposalId}/reject`, { method: "POST" }),
  listConversations: () => request<Conversation[]>("/conversations"),
  getConversation: (id: string) => request<ConversationDetail>(`/conversations/${id}`),
  getResourceWorkspace: (id: string, includeArchived = false) => request<ResourceWorkspaceManifest>(`/resources/conversations/${id}${includeArchived ? "?include_archived=true" : ""}`),
  archiveResource: (id: string) => request<ResourceItem>(`/resources/${id}/archive`, { method: "POST" }),
  restoreResource: (id: string) => request<ResourceItem>(`/resources/${id}/restore`, { method: "POST" }),
  getConversationContextDashboard: (id: string) => request<ContextOperationsDashboard>(`/context-operations/conversations/${id}`),
  resolveSemanticFact: (id: string, approved: boolean) => request<SemanticFact>(`/context-operations/semantic-facts/${id}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved })
  }),
  getDeletionAudits: () => request<ConversationCleanupReport[]>("/context-operations/deletion-audits"),
  getAdminContextDashboard: () => request<ContextOperationsDashboard>("/context-operations/admin/dashboard"),
  updateAdminModelBudget: (policy: { monthly_budget_cny: number; per_run_budget_cny: number; daily_call_limit: number; warning_ratio: number }) => request<ModelCostControl>("/context-operations/admin/model-budget", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(policy)
  }),
  getAdminDeletionAudits: () => request<ConversationCleanupReport[]>("/context-operations/admin/deletion-audits"),
  runResourceQueryEvaluation: () => request<ResourceQueryEvalReport>("/context-operations/admin/resource-query-evaluations", { method: "POST" }),
  runContextRetention: (execute = false, policy?: Partial<ContextRetentionPolicy>) => request<ContextRetentionReport>("/context-operations/admin/retention-runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ execute, policy: {
      snapshot_days: 90,
      snapshot_keep_latest: 20,
      summary_days: 180,
      run_payload_days: 30,
      turn_execution_days: 30,
      model_usage_days: 180,
      ...policy
    } })
  }),
  createConversation: (title = "新对话") => request<Conversation>("/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title })
  }),
  updateConversation: (id: string, patch: { title?: string; pinned?: boolean }) => request<Conversation>(`/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch)
  }),
  deleteConversation: (id: string) => request<void>(`/conversations/${id}`, { method: "DELETE" }),
  listConversationRuns: (id: string) => request<RunRecord[]>(`/conversations/${id}/runs`),
  getRun: (id: string) => request<RunDetail>(`/runs/${id}`),
  recoverSkillRun: (id: string, options: { node_id?: string; supplied_inputs?: Record<string, unknown>; comment?: string } = {}) => request<RunDetail>(`/runs/${id}/skill-recovery`, {
    method: "POST",
    body: JSON.stringify({
      node_id: options.node_id ?? "",
      supplied_inputs: options.supplied_inputs ?? {},
      comment: options.comment ?? ""
    })
  }),
  getRecruitmentCase: (id: string) => request<RecruitmentCaseRecord>(`/recruitment/cases/${id}`),
  listRecruitmentInterviewers: () => request<RecruitmentInterviewerRecord[]>("/recruitment/interviewers"),
  listRecruitmentCases: ({ status = "", folderId = "", query = "", archived = false }: { status?: string; folderId?: string; query?: string; archived?: boolean } = {}) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (folderId) params.set("folder_id", folderId);
    if (query) params.set("query", query);
    if (archived) params.set("archived", "true");
    return request<RecruitmentCaseRecord[]>(`/recruitment/cases${params.size ? `?${params.toString()}` : ""}`);
  },
  applyRecruitmentCaseAction: (id: string, action: RecruitmentCaseAction) => request<RecruitmentCaseRecord>(`/recruitment/cases/${id}/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(action)
  }),
  recruitmentInterviewCalendarUrl: (caseId: string, sessionId: string) => `${API_BASE}/recruitment/cases/${caseId}/interviews/${sessionId}.ics`,
  previewRecruitmentPackage: (caseId: string, packageType: RecruitmentPackageType) => request<RecruitmentPackagePreview>(`/recruitment/cases/${caseId}/packages/preview?package_type=${packageType}`),
  listRecruitmentPackages: (caseId: string) => request<RecruitmentPackageRecord[]>(`/recruitment/cases/${caseId}/packages`),
  generateRecruitmentPackage: (caseId: string, packageType: RecruitmentPackageType) => request<RecruitmentPackageRecord>(`/recruitment/cases/${caseId}/packages`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ package_type: packageType })
  }),
  recruitmentPackageDownloadUrl: (caseId: string, packageId: string, format: "docx" | "xlsx") => `${API_BASE}/recruitment/cases/${caseId}/packages/${packageId}/download?format=${format}`,
  listRecruitmentFolders: () => request<RecruitmentFolderRecord[]>("/recruitment/folders"),
  createRecruitmentFolder: (name: string) => request<RecruitmentFolderRecord>("/recruitment/folders", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name })
  }),
  renameRecruitmentFolder: (id: string, name: string) => request<RecruitmentFolderRecord>(`/recruitment/folders/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name })
  }),
  deleteRecruitmentFolder: (id: string) => request<void>(`/recruitment/folders/${id}`, { method: "DELETE" }),
  decideRun: (id: string, approved: boolean, comment = "") => request<RunDetail>(`/runs/${id}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved, comment })
  }),
  listApprovals: () => request<ApprovalQueueItem[]>("/approvals"),
  rateAnswer: (messageId: string, helpful: boolean, comment = "") => request<AnswerFeedback>(`/feedback/messages/${messageId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ helpful, comment })
  }),
  getAdminFeedbackStats: () => request<FeedbackStats>("/admin/feedback/stats"),
  listProductCapabilities: () => request<ProductCapability[]>("/admin/product-capabilities"),
  getCapabilityOperations: (days = 30) => request<CapabilityOperationsSnapshot>(`/admin/capability-operations?days=${days}`),
  runCapabilityRoutingEvaluation: () => request<RoutingGoldenReport>("/admin/evaluations/capability-routing", { method: "POST" }),
  runProductHelpRouteEvaluation: () => request<ProductHelpRouteEvalReport>("/admin/evaluations/product-help-routing", { method: "POST" }),
  runQueryRewriteEvaluation: () => request<RewriteEvaluationReport>("/admin/evaluations/query-rewrite", { method: "POST" }),
  getQualityOverview: () => request<QualityOverview>("/admin/quality"),
  listQualitySuites: () => request<QualityEvalSuite[]>("/admin/quality/suites"),
  listQualitySuiteCases: (suiteId: string) => request<unknown[]>(`/admin/quality/suites/${encodeURIComponent(suiteId)}/cases`),
  startQualityRun: (suiteId: string, baselineRunId?: string) => request<QualityEvalRun>("/admin/quality/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ suite_id: suiteId, baseline_run_id: baselineRunId || null })
  }),
  getQualityRun: (runId: string) => request<QualityReport>(`/admin/quality/runs/${runId}`),
  cancelQualityRun: (runId: string) => request<QualityEvalRun>(`/admin/quality/runs/${runId}/cancel`, { method: "POST" }),
  assessQualityRun: (runId: string) => request<QualityReleaseDecision>(`/admin/quality/runs/${runId}/release-decision`, { method: "POST" }),
  reviewQualityFeedback: (candidateId: string, accepted: boolean) => request<QualityFeedbackCandidate>(`/admin/quality/feedback-candidates/${candidateId}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accepted, title: "真实用户反馈回归", expectation: { answer_required: true } })
  }),
  listAdminFeedback: (helpful?: boolean) => request<AdminFeedbackRecord[]>(`/admin/feedback${helpful === undefined ? "" : `?helpful=${helpful}`}`),
  updateAdminFeedback: (id: string, testStatus: AnswerFeedback["test_status"]) => request<AdminFeedbackRecord>(`/admin/feedback/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ test_status: testStatus })
  }),
  listAdminHistory: (query = "") => request<AdminConversationSummary[]>(`/admin/history${query ? `?q=${encodeURIComponent(query)}` : ""}`),
  getAdminHistory: (id: string) => request<ConversationDetail>(`/admin/history/${id}`),
  deleteAdminHistory: (id: string) => request<void>(`/admin/history/${id}`, { method: "DELETE" }),
  adminFeedbackExportUrl: () => `${API_BASE}/admin/feedback-export.csv`,
  getKnowledgeCatalog: () => request<KnowledgeCatalog>("/knowledge/catalog"),
  uploadKnowledgeFiles: (files: File[]) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file, file.name));
    return request<{ uploaded: string[]; job: KnowledgeIngestionJob }>("/knowledge/upload", {
      method: "POST",
      body: form
    });
  },
  reindexKnowledge: () => request<KnowledgeIngestionJob>("/knowledge/reindex", { method: "POST" }),
  getKnowledgeIngestionJob: (id: string) => request<KnowledgeIngestionJob>(`/knowledge/ingestion-jobs/${id}`),
  getLatestKnowledgeIngestionJob: () => request<KnowledgeIngestionJob>("/knowledge/ingestion-jobs/latest"),
  listKnowledgeIngestionFailures: () => request<{ failures: KnowledgeIngestionFailure[] }>("/knowledge/ingestion-failures"),
  retryKnowledgeIngestionFailure: (sourceId: string) => request<KnowledgeIngestionJob>(`/knowledge/ingestion-failures/${encodeURIComponent(sourceId)}/retry`, { method: "POST" }),
  listKnowledgeVersions: () => request<{ versions: KnowledgeSourceVersion[] }>("/knowledge/versions"),
  publishKnowledgeVersion: (id: string, allowedRoles = ["admin", "user"]) => request<KnowledgeSourceVersion>(`/knowledge/versions/${id}/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ allowed_roles: allowedRoles })
  }),
  reviewKnowledgeQuality: (id: string, approved: boolean, comment = "") => request<KnowledgeSourceVersion>(`/knowledge/versions/${id}/quality-review`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved, comment })
  }),
  getKnowledgeEvidence: (id: string) => request<KnowledgeEvidence>(`/knowledge/versions/${id}/evidence`),
  knowledgePagePreviewUrl: (id: string, page: number) => `${API_BASE}/knowledge/versions/${id}/pages/${page}/preview`,
  withdrawKnowledgePublication: (id: string) => request<{ status: string }>(`/knowledge/publications/${id}/withdraw`, { method: "POST" }),
  runKnowledgeEvaluation: () => request<KnowledgeEvaluation>("/knowledge/evaluations/run", { method: "POST" }),
  listKnowledgeEvaluationHistory: () => request<KnowledgeEvaluation[]>("/knowledge/evaluations/history"),
  listPolicyEvaluationCandidates: (status = "draft") => request<PolicyEvaluationCandidate[]>(`/knowledge/evaluations/candidates?status=${encodeURIComponent(status)}`),
  reviewPolicyEvaluationCandidate: (id: string, review: PolicyEvaluationCandidateReview) => request<PolicyEvaluationCandidate>(`/knowledge/evaluations/candidates/${id}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(review)
  }),
  runVisionEvaluation: () => request<VisionEvaluation>("/knowledge/evaluations/vision/run", { method: "POST" }),
  getLatestVisionEvaluation: () => request<VisionEvaluation>("/knowledge/evaluations/vision/latest"),
  compareKnowledgeVersions: (left: string, right: string) => request<KnowledgeVersionComparison>(`/knowledge/versions/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`),
  listSkills: () => request<SkillRecord[]>("/skills"),
  generateSkill: (prompt: string, preferredName = "") => request<SkillRecord>("/skills/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, preferred_name: preferredName })
  }),
  createSkillFromConversation: (conversationId: string, preferredName = "") => request<SkillRecord>("/skills/from-conversation", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: conversationId, preferred_name: preferredName })
  }),
  importSkill: (file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<SkillRecord>("/skills/import", { method: "POST", body: form });
  },
  updateSkill: (id: string, patch: Partial<Pick<SkillRecord, "display_name" | "description" | "instructions" | "trigger_phrases" | "capabilities" | "enabled">>) => request<SkillRecord>(`/skills/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch)
  }),
  validateSkill: (id: string) => request<SkillRecord>(`/skills/${id}/validate`, { method: "POST" }),
  completeSkill: (id: string, supplement: string, deferMissingResources = true) => request<SkillRecord>(`/skills/${id}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ supplement, defer_missing_resources: deferMissingResources })
  }),
  listSkillResources: (id: string) => request<SkillResourceRecord[]>(`/skills/${id}/resources`),
  addSkillResource: (id: string, kind: SkillResourceRecord["kind"], file: File) => {
    const form = new FormData();
    form.set("kind", kind);
    form.set("file", file, file.name);
    return request<SkillResourceRecord>(`/skills/${id}/resources`, { method: "POST", body: form });
  },
  deleteSkillResource: (id: string, resourceId: string) => request<SkillRecord>(`/skills/${id}/resources/${resourceId}`, { method: "DELETE" }),
  skillResourceDownloadUrl: (id: string, resourceId: string) => `${API_BASE}/skills/${id}/resources/${resourceId}`,
  listSkillVersions: (id: string) => request<SkillVersionRecord[]>(`/skills/${id}/versions`),
  listSkillDryRuns: (id: string) => request<SkillDryRunRecord[]>(`/skills/${id}/dry-runs`),
  runSkillDryRun: (id: string, prompt: string, files: File[]) => {
    const form = new FormData();
    form.set("prompt", prompt);
    files.forEach((file) => form.append("files", file, file.name));
    return request<SkillDryRunRecord>(`/skills/${id}/dry-runs`, { method: "POST", body: form });
  },
  rollbackSkill: (id: string, version: number) => request<SkillRecord>(`/skills/${id}/rollback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version })
  }),
  requestOrganizationSkill: (id: string) => request<SkillRecord>(`/skills/${id}/organization-request`, { method: "POST" }),
  listOrganizationSkillApprovals: () => request<SkillRecord[]>("/skills/organization-approvals/pending"),
  decideOrganizationSkill: (id: string, approved: boolean, comment = "") => request<SkillRecord>(`/skills/${id}/organization-decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved, comment })
  }),
  deleteSkill: (id: string) => request<void>(`/skills/${id}`, { method: "DELETE" }),
  skillExportUrl: (id: string) => `${API_BASE}/skills/${id}/export`,
  skillPackageExportUrl: (id: string) => `${API_BASE}/skills/${id}/export-package`,
  sendTurn: ({
    message,
    conversationId,
    taskMode,
    files,
    documentFont,
    documentFontSize,
    taskBrief
  }: {
    message: string;
    conversationId: string | null;
    taskMode: string;
    files: File[];
    documentFont: string;
    documentFontSize: number;
    taskBrief: TaskBrief;
  }) => {
    const form = buildTurnForm({ message, conversationId, taskMode, files, documentFont, documentFontSize, taskBrief });
    return request<TurnResponse>("/assistant/turns", { method: "POST", body: form });
  },
  startTurn: (input: {
    message: string;
    conversationId: string | null;
    taskMode: string;
    files: File[];
    documentFont: string;
    documentFontSize: number;
    taskBrief: TaskBrief;
  }) => request<TurnExecutionRecord>("/assistant/turns:start", {
    method: "POST",
    body: buildTurnForm(input)
  }),
  steerTurn: (id: string, input: {
    message: string;
    conversationId: string | null;
    taskMode: string;
    files: File[];
    documentFont: string;
    documentFontSize: number;
    taskBrief: TaskBrief;
  }) => request<TurnExecutionRecord>(`/assistant/turns/${id}/steer`, {
    method: "POST",
    body: buildTurnForm(input)
  }),
  getTurnExecution: (id: string) => request<TurnExecutionRecord>(`/assistant/turns/${id}`),
  cancelTurnExecution: (id: string) => request<TurnExecutionRecord>(`/assistant/turns/${id}/cancel`, { method: "POST" }),
  getWorkflowCase: (id: string) => request<WorkflowCase>(`/workflow-cases/${id}`),
  applyWorkflowCaseAction: (
    id: string,
    action: "approve" | "revise" | "retry" | "skip" | "cancel",
    comment = "",
    selectedCandidateIds: string[] = []
  ) => request<WorkflowCase>(`/workflow-cases/${id}/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, comment, selected_candidate_ids: selectedCandidateIds })
  }),
  watchTurnExecution: async (
    id: string,
    after: number,
    onEvent: (event: TurnExecutionEvent) => void,
    signal?: AbortSignal
  ) => {
    const response = await fetch(`${API_BASE}/assistant/turns/${id}/events?after=${Math.max(0, after)}`, {
      credentials: "include",
      headers: { Accept: "text/event-stream" },
      signal
    });
    if (!response.ok || !response.body) throw new Error(`事件流连接失败（${response.status}）`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const data = block.split("\n").find((line) => line.startsWith("data: "))?.slice(6);
        if (data) onEvent(JSON.parse(data) as TurnExecutionEvent);
      }
    }
  },
  artifactDownloadUrl: (id: string) => `${API_BASE}/artifacts/${id}/download`,
  artifactPreviewUrl: (id: string, index = 0) => `${API_BASE}/artifacts/${id}/previews/${index}`,
  attachmentDownloadUrl: (id: string) => `${API_BASE}/resources/attachments/${id}/download`,
  resourceFileUrl: (path: string) => new URL(path, API_BASE).toString()
};
