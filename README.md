![UniHR · 人事工作台](assets/cover.svg)

# UniHR · 人事工作台

UniHR 是面向高校人事和行政经办人员的 AI 工作台。用日常语言交代任务，它可以查政策、整理材料，并调用工具生成可编辑的 Word 和 Excel 文件。

当前是可本地运行的个人 Demo，公开仓库使用空知识库和虚构示例。

[在线示例与工作台](https://dominicspace-agent-showcase.peterzhukejie2020.chatgpt.site/apps/unihr/index.html) · [产品设计](docs/product-design.md) · [本地启动](#本地启动)

## 主要用途

| 经办人的任务 | 工作台提供什么 | 需要人工核对什么 |
| --- | --- | --- |
| 查询人事政策 | 检索相关条文，附原文出处与版本 | 适用范围和正式口径 |
| 对照岗位与候选人材料 | 整理比较结果、原文依据与待核实项 | 材料完整性和招聘判断 |
| 制作通知、会议材料或业务表格 | 可编辑的 Word / Excel 及文件检查结果 | 内容、字段、公式与格式 |
| 继续修改多步骤任务 | 保留材料、进度和中间结果，沿用已确认要求 | 本次修改范围和最终文件 |

## 界面与任务流程

![UniHR 主工作台：对话入口与任务工作区](docs/images/unihr-home.png)

在对话区说明要求、上传附件，在工作区查看文件、出处和进度；需要修改时，继续在当前任务中提出要求。

```mermaid
flowchart LR
    A[说明需求与提供材料] --> B[确认目标与处理步骤]
    B --> C[查资料并处理文件]
    C --> D[检查依据与交付物]
    D --> E[人工核对与确认]
    E --> F[下载或继续修改]
```

<details>
<summary>查看常用任务流程（Skill 中心）</summary>

![UniHR Skill 中心](docs/images/skills-center.png)

常做的工作可以保存为可复用流程，把步骤、模板和注意事项一起记下来。试跑并确认后，下次遇到同类任务就能沿用。

</details>

## 核心案例：培训完成情况统计与提醒通知

根据一份名单，整理完成情况、计算完成率，再起草提醒通知。设计重点是重复记录怎样合并、空白状态怎样处理，以及两份文件怎样保持一致。

[阅读核心案例](docs/cases/training-follow-up.md) · [下载虚构名单](examples/training-follow-up.csv) · [完整 PRD](docs/PRD.md)

案例提供可复算的预期结果与验收要求，尚无这份名单的完整模型运行和文件交付记录。PRD 为 14 节立项评审稿，包含待验证目标。

## 在线体验

1. [查看三步示例](https://dominicspace-agent-showcase.peterzhukejie2020.chatgpt.site/apps/unihr/index.html#example)：了解确认要求、整理材料和核对文件的流程。
2. [浏览工作台](https://dominicspace-agent-showcase.peterzhukejie2020.chatgpt.site/apps/unihr/index.html?mode=browse)：无需 API Key，可以先看任务、材料与文件入口。
3. 实际生成回答或文件时，点击「连接模型」，使用自己的模型账户。

三步示例用于展示设计，未现场调用模型。在线访客工作台与本地版本分别运行，本地安装与配置见下文。

## 关键设计

| 需要解决的问题 | 产品选择 |
| --- | --- |
| 查到答案后仍需制作材料 | 将政策查询、内容整理和文件生成放在同一任务里，保留来源与要求 |
| 一段回答无法说明文件是否可用 | 政策检查出处，表格检查字段与公式，文稿检查内容和结构 |
| 复杂任务失败后需要重做 | 保存中间结果和未完成事项，便于定位、修改和继续处理 |
| 常用模板与临时材料混在一起 | 流程、模板和注意事项可复用，每次任务的具体材料单独管理 |

AI 理解需求、组织步骤和起草内容；政策检索（RAG）提供原文依据；工具负责计算和文件读写；上下文与任务状态支持后续修改。软件将这些能力与检查、返修、人工确认组织起来，构成项目的 Agent Harness。简单文本任务直接处理，涉及多份材料和工具时再组织步骤。

经办人确认统计口径和正式发送；录用、定薪等决策由具备权限的人员作出。详细输入输出、异常路径与验证计划见[产品设计](docs/product-design.md)；实现见[运行机制](docs/RUNTIME-DETAILS.md)和[系统设计](docs/ARCHITECTURE.md)。同一套能力也可接入飞书机器人。

## 需求来源与我的工作

项目来自我在国际医学院的人事、行政与国际交流工作，以及与经办同事讨论 AI 业务流程优化的经历。我负责需求整理、范围与流程设计、验收要求，并借助 AI 编程工具实现原型。

下一步请经办人完整试用任务，记录准备、核对与返工的总用时，确认哪些结果可直接采用、哪些仍需重写。当前尚无组织试用后的效率数据。

---

## 本地启动

### 准备环境

- Windows 10/11，目前的一键脚本面向 Windows
- Python 3.11+
- Node.js 20+
- Docker Desktop
- 可用的阿里云百炼 / DashScope API Key

### 1. 下载并安装依赖

```powershell
git clone https://github.com/DominicHongliDing/UniHR-AgentOS.git
cd UniHR-AgentOS
.\setup.ps1
```

### 2. 配置模型

打开根目录的 `.env`：

```dotenv
UNIHR_BAILIAN_API_KEY=你的百炼APIKey
```

模型路由、Embedding、Rerank、本地数据库目录与 PostgreSQL Checkpointer 已提供默认配置。如果你的百炼账号不能使用某个默认模型，可以在同一文件中替换模型名称。

### 3. 启动

```powershell
.\start.ps1
```

浏览器会打开 [http://127.0.0.1:5180](http://127.0.0.1:5180)。

本地演示账号：

| 角色 | 用户名 | 密码 |
|---|---|---|
| 管理员 | `admin` | `UniHR@2026` |
| 普通 HR | `hr-user` | `UniHR@2026` |

这些账号仅用于本机演示。共享部署前，请在 `.env` 中修改账号密码和 `APP_JWT_SECRET_KEY`。

## 上传自己的政策

1. 使用管理员账号登录；
2. 打开「政策知识库」；
3. 点击「上传政策」，选择 PDF、DOCX、Markdown 或 TXT；
4. 等待系统解析文件并建立版本草稿；
5. 检查条款、页码和版本信息后发布；
6. 返回对话页，用自己的说法提问。

也可以先上传 [`examples/虚构政策示例.md`](examples/虚构政策示例.md)，体验从入库到引用回答的完整过程。

## 技术栈

- Frontend：React 19、TypeScript、Vite、React Flow
- Backend：FastAPI、Pydantic、Python 3.11+
- Agent Runtime：LangGraph、PostgreSQL Saver、Capability Registry、自研任务规划与 Harness
- RAG：百炼 Embedding、混合检索、动态 Top-K、Rerank、Query Rewrite、版本与证据治理
- Artifacts：python-docx、openpyxl、PyMuPDF、Pillow
- Channels：Web、飞书机器人（可选）

更完整的模块和状态流转说明见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 数据与使用边界

仓库以空知识库发布，不包含真实政策、简历、历史会话、运行数据库或服务密钥。使用者上传的资料保存在自己的运行环境中。详细说明见 [`docs/PRIVACY.md`](docs/PRIVACY.md) 和 [`SECURITY.md`](SECURITY.md)。

UniHR 当前是一套可运行的产品 Demo 和 AI 产品工程样例。招聘结果用于辅助人工复核，正式政策口径仍由具备权限的人员确认。

## 贡献

欢迎提交 Issue、能力适配器、Skill、评测集和改进建议。提交前请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## License

[MIT](LICENSE)
