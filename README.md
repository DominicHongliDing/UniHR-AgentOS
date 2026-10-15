![UniHR AgentOS：把琐碎的人事任务做成可交付的工作](assets/cover.svg)

# UniHR · 人事工作台

UniHR 是面向高校人事和行政经办人员的 AI 工作台。你可以用日常语言交代任务，让它查政策、整理材料，生成可以继续编辑的 Word 和 Excel 文件。

[看人事任务示例](https://dominicspace-agent-showcase.peterzhukejie2020.chatgpt.site/apps/unihr/index.html) · [典型任务](#经办人可以用它做什么) · [设计取舍](#几个关键的产品选择) · [本地启动](#本地启动) · [技术细节](docs/RUNTIME-DETAILS.md) · [关于作者](https://github.com/DominicHongliDing)

## 第一次看，建议这样体验

1. [先看三步示例](https://dominicspace-agent-showcase.peterzhukejie2020.chatgpt.site/apps/unihr/index.html#example)：以培训完成情况表为例，了解怎样确认要求、整理资料、核对文件。
2. [浏览工作台](https://dominicspace-agent-showcase.peterzhukejie2020.chatgpt.site/apps/unihr/index.html?mode=browse)：无需填写 API Key，可以先看任务入口、材料和文件放在哪里。
3. 想实际生成回答或文件时，再点击「连接模型」，使用自己的模型账户。

示例页展示处理思路，未调用模型生成结果。在线访客工作台与下面的本地版本分别运行，本地安装与模型配置见[本地启动](#本地启动)。

## 我为什么做这个项目

在国际医学院做人事、行政和国际交流相关工作时，我经常要查政策、核对表格、准备通知和会议材料。查到一条规定以后，还得确认版本；表格内容整理好了，还要检查字段和公式；通知写完了，格式、附件、落款也要逐一核对。

这些工作，也是我参与 AI 业务流程优化时和经办同事讨论的内容。以准备通知为例，查到相关政策后，还要写正文、做附件、核对格式。我希望 UniHR 能帮经办人接着完成这些步骤，最后拿到一份可以修改和使用的文件。

## 经办人可以用它做什么

| 想完成的工作 | 工作台怎样帮助 | 用户拿到什么 |
|---|---|---|
| 查一项人事政策 | 根据问题检索原文，检查版本与适用范围 | 附出处的回答，可以回到原文件核对 |
| 整理岗位与候选人材料 | 按岗位要求比较材料，标出原文依据和待核实项 | 比较结果与 Excel 报告，供招聘人员复核 |
| 制作通知、会议材料或业务表格 | 确认内容与格式，调用文件工具生成并检查 | 可以编辑的 Word / Excel 及检查结果 |
| 完成有多个步骤的任务 | 安排处理顺序，保存中间结果，提示需要确认的地方 | 可以查看进度、继续修改的任务记录 |

## 本地产品界面

![UniHR 主工作台：对话入口与任务工作区](docs/images/unihr-home.png)

在对话区说明要求、上传附件，在工作区查看文件、出处和处理进度。想改内容时，可以接着说，不必另开一次任务。

<details>
<summary>查看常用任务如何复用（本地界面称为 Skill 中心）</summary>

![UniHR Skill 中心](docs/images/skills-center.png)

常做的工作可以保存为可复用的任务流程（Skill），把步骤、模板和注意事项一起记下来。试跑并确认后，下次遇到同类任务就能沿用。

</details>

## 一个任务如何完成

以“根据材料整理一份通知，并附上对应表格”为例。用户先提供材料和要求，系统确认文件类型与限制，按需要查资料、准备内容、生成文件，再检查结果。经办人可以核对依据、字段和格式，提出修改，最后决定是否正式使用或发送。

```mermaid
flowchart LR
    A[说明需求与提供材料] --> B[确认目标与处理步骤]
    B --> C[查资料并处理文件]
    C --> D[检查依据与交付物]
    D --> E[人工核对与确认]
    E --> F[下载或继续修改]
```

## 几个关键的产品选择

不同任务完成得好不好，要看不同的地方。通知要能继续编辑，表格要核对字段和公式，政策回答则要能找到适用的原文。因此，我为这些任务分别设置了检查要求。

简单的任务可以直接处理；涉及多份文件或多种工具时，先列出步骤，并保存每一步的结果。这样，某个环节出了问题可以回去修改，经办人也能知道还有哪些事情没完成。

可以复用的方法、模板和注意事项保存在常用任务流程中，每次任务的具体材料单独管理。AI 帮忙整理和比较材料、处理文件；是否录用、如何定薪，以及是否正式发送，仍由经办人员确认。

## AI 技术在产品里起什么作用

| 能力 | 用在哪里 |
|---|---|
| 大模型与任务识别 | 理解经办人的日常表达、追问和修改要求 |
| RAG 政策检索 | 找到相关条文、核对版本，把出处附在回答中 |
| Agent 与任务编排 | 按任务需要组合政策、文档和表格等处理步骤 |
| 工具调用 | 读取材料，实际生成 Word / Excel 文件 |
| 上下文与任务状态 | 记住目标、限制、中间结果和未完成事项，供后续使用 |
| 文件检查与返修 | 检查字段、结构、公式等要求，将问题交回对应步骤修改 |

模型之外，还需要软件把文件处理、结果检查和人工确认组织起来，这部分就是项目中的 Agent Harness。具体实现见[运行机制](docs/RUNTIME-DETAILS.md)和[系统设计](docs/ARCHITECTURE.md)。同一套能力也可以接入飞书机器人，方便经办人在日常协作中发起任务。

## 当前阶段与我的工作

UniHR 是一个可运行的个人产品 Demo。我负责需求整理、产品规划、流程与验收设计，并借助 AI 编程工具实现原型。场景来自日常工作与业务沟通，公开仓库使用空知识库和虚构示例。

接下来，我想请经办人员实际试用：能否独立完成一项工作，检查结果是否方便，保存的流程下次还能不能用。开发中遇到的文件遗漏和多轮对话问题，也促使我开始做 [Agent EVO 评测平台](https://github.com/DominicHongliDing/DominicHongliDing/blob/main/docs/agent-evo.md)。

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
