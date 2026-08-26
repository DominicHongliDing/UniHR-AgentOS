# UniHR AgentOS Architecture

## 1. 请求如何进入系统

每个请求先进入会话与附件层。系统装配当前消息、最近对话、任务状态、附件摘要和长期记忆索引，再交给 `TaskIntent` 解释层。解释层判断这是新任务、已有结果上的追问、结果修改、闲聊或无需执行的确认，并通过 Query Rewrite 把口语化表达改写成后续链路可执行的需求。

路由器不直接选择函数名。它只输出稳定的语义意图、所需能力和缺失信息；`Capability Registry` 再把能力映射到当前可用的 Agent、Skill、Adapter 或 MCP。新增能力主要通过注册完成，关键词规则只保留为模型不可用时的最后兜底。

## 2. 专业 Agent

### Policy Agent

负责政策问答、版本比较、有效期判断和证据化回答。检索链包括上下文改写、Dense + Sparse 混合召回、动态 Top-K、Rerank、一次补偿检索和主张—证据校验。

### Document Agent

把用户目标编译为文档计划，生成段落、表格、签署区和样式，然后通过结构、占位符和渲染 Harness 检查。用户只要求润色时返回文本，不擅自升级为 Word 任务。

### Spreadsheet Agent

把自然语言要求编译为结构化 Spreadsheet Plan，再由受控 Python 工具执行。Harness 对比源文件与结果文件的数据、公式和样式，并在可用时执行公式重算与渲染验证。

### Recruitment Agent

解析多份简历，以岗位要求为评分依据，保留每个分数对应的原文证据和待核实问题。受保护属性不进入评分，最终结果进入人工复核门。

### Public Info Agent

只访问公开、只读能力。实时事实必须调用可追踪的数据源；工具不可用时明确降级，不把模型常识伪装成实时查询。

## 3. Task Plan 执行图

单一 Agent 能完成的任务走短路径。跨领域任务由 Planner 生成有向执行图：节点可以是 Agent、Skill、工具或人工确认，边表示数据依赖和执行顺序。运行时根据依赖并行或串行执行，并把中间产物写入工作区。

`Quality Gate` 判断结果是否满足任务卡、证据、格式和文件要求。可修复问题会生成定向反馈并回到对应节点，而不是把整个任务从头再跑。达到轮次上限、涉及敏感决策或需要发布动作时进入 Human Review Gate。

## 4. Skill 系统

Skill 是版本化的程序性记忆。它可以来自一句话描述、一次完整对话或上传的 Skill 包，经过生成、结构校验、Dry Run、版本保存和启用流程后进入个人能力库。

Skill Graph 并不是另一个“大 Agent”。它负责把一个 Skill 内部的多个步骤、工具和检查节点组织为可恢复的小型执行图；上层 Task Plan 可以调用一个或多个 Skill Graph，也可以同时调用专业 Agent。

## 5. 状态与记忆

- PostgreSQL Saver 持久化 LangGraph Checkpoint；
- SQLite 保存本地演示账号、会话、反馈和产物元数据；
- 大文件与大结果外置到 Artifact Workspace，Checkpoint 只保存引用；
- 上下文压缩保留目标、约束、决策、进度、未完成事项和最近原始消息；
- Semantic、Episodic 与 Procedural Memory 分开存储和按需召回。

## 6. 质量与可观测性

每次 Run 都保留安全 Trace：显示经过的节点、工具与质量门，但不暴露模型私有推理。管理员可以查看用户反馈、把失败样本加入评测集、运行回归测试并对比版本质量。

