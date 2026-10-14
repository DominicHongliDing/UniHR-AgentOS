## 系统如何工作

UniHR 把执行分成三层：

- 子 Agent 负责一个专业领域，例如政策问答、公文生成、表格处理、招聘筛选或联网信息；
- Skill 保存已经验证过的业务方法、模板和约束；
- Task Plan 执行图负责复杂任务的先后依赖、并行节点、人工复核和失败返修。

Agent 负责判断当前要做什么，Skill 提供稳定做法，Task Plan 负责把多个能力组织成一次完整交付。

```mermaid
flowchart TD
    U[Web / 飞书 / 文件上传] --> C[会话、附件和任务状态]
    C --> R[TaskIntent + Query Rewrite]
    R --> CR[Capability Registry]
    CR --> P{任务规划}

    P -->|单项任务| A1[专业子 Agent]
    P -->|复杂任务| G[Task Plan 执行图]
    G --> S1[Skill 节点]
    G --> S2[子 Agent 节点]
    G --> T[工具 / MCP 节点]

    A1 --> PR[Policy RAG]
    A1 --> DOC[Document Agent]
    A1 --> XLS[Spreadsheet Agent]
    A1 --> REC[Recruitment Agent]
    A1 --> WEB[Public Info Agent]

    PR --> Q[Quality Gate]
    DOC --> Q
    XLS --> Q
    REC --> H[Human Review Gate]
    WEB --> Q
    S1 --> Q
    S2 --> Q
    T --> Q

    Q -->|需要修复| LOOP[定向返修 Loop]
    LOOP --> G
    Q -->|检查通过| ART[回答 / Word / Excel / 证据包]
    H -->|人工确认| ART

    ART --> M[状态外置与分层记忆]
    M --> C
```

### Policy RAG

政策检索不会把「找到相关文件」直接当成答案。系统会继续判断证据是否覆盖用户真正询问的内容，并检查版本、生效期、权限和冲突。

```mermaid
flowchart LR
    Q[用户原始问题] --> W[结合上下文改写]
    W --> D[动态 Top-K]
    D --> H1[向量召回]
    D --> H2[关键词 / BM25 召回]
    H1 --> M[融合与去重]
    H2 --> M
    M --> RR[语义 Rerank]
    RR --> V[版本、生效期、权限和冲突检查]
    V --> E[主张与证据覆盖校验]
    E -->|证据不足| RE[补偿检索]
    RE --> RR
    E -->|检查通过| A[带页码、章节和版本信息的回答]
```

### 记忆与长任务

UniHR 不会把全部聊天历史无限追加到模型上下文。不同信息保存在不同位置，并在任务需要时加载：

- Working Memory 保存当前目标、约束、最近结果和下一步；
- Thread State / Checkpoint 保存 LangGraph 节点状态，支持暂停和继续；
- Semantic Memory 保存稳定偏好、组织规则和可复用事实；
- Episodic Memory 保存过去任务的关键决策和结果；
- Procedural Memory 通过 Skill 保存可复用工作方法；
- External Memory 保存原文件、表格、报告和证据包，模型只读取当前需要的部分。

长对话达到阈值后，系统压缩低价值历史，保留目标、约束、决策、未完成事项和最近原始上下文。完整数据仍留在工作区，可以在需要核对时重新读取。


[返回项目介绍](../README.md) · [模块与状态设计](ARCHITECTURE.md)

[历史工程评测记录](EVALUATION-NOTES.md)
