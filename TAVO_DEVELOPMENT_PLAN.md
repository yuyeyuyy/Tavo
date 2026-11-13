# Tavo V1 开发方案（Codex 边开发边学习版）

> 版本：V1 规划稿｜目标：macOS 桌面端优先，后续 Windows；Local-first；可开源；预留 Headless Runtime 能力。
>
> 定位：开源、可扩展、主动式个人数字账户 Agent（Proactive Personal Agent）。

## 0. 核心原则

1. **先交付可用产品，再扩展 Agent。** 用户能够连接账户、同步数据、查看变化、收到有效通知，即为最小闭环。
2. **官方授权优先。** 只使用有权限的官方 API / OAuth / 用户授权连接器；V1 不做浏览器自动登录及反爬绕过。
3. **确定性逻辑优先。** 同步、数据校验、幂等、异常阈值由普通代码完成；LLM 主要负责解释、总结、建议及有限的工具调用。
4. **Local-first。** 数据默认留在用户设备，桌面端退出或电脑休眠时不保证继续监控。
5. **学习优先于代码数量。** 每个里程碑都有原理说明、测试、验收和复盘，Codex 不得无限制自主扩展范围。
6. **真实成绩才写简历。** 先收集数据再填写「支持 N 个平台」「成功率 X%」等指标。

## 1. 第一版的功能范围

### V1 必做

- 添加/删除账户连接；检测是否有权限；显示连接状态与最近同步时间。
- DeepSeek 余额查询；OpenRouter Credits 查询（需管理密钥）；GitHub 仓库 Issue/PR/Actions 只读状态查询。
- 手动同步 + 应用运行时周期同步；失败重试、限流、过期授权提示。
- 统一的余额快照、用量聚合、资源状态和事件模型。
- Dashboard、账户详情、事件时间线、Agent Insights、同步日志、设置。
- 基于确定性规则检测异常；使用 BYOK LLM 分析事件并生成可解释的主动提醒。
- 桌面托盘、通知；一键暂停同步；断开账户并删除凭据。
- Mock 数据模式、自动化测试、开发/打包文档、第一版开源许可证。

### V1 明确不做

- 手机端、用户云账号、多设备同步、自建 SaaS 后台、云浏览器登录、代理绕过风控。
- 自动充值/付款/修改第三方账号配置；未经审批的写操作。
- 多 Agent 群体协作、大型向量数据库/复杂 RAG、完整个人记忆图谱。
- 支持所有平台/每次 API 调用明细。各平台数据可见性按官方权限和接口真实能力展示。

### 首批连接器

| 平台 | V1 可验证能力 | 重要限制 |
|---|---|---|
| DeepSeek | `/user/balance` 查询余额 | 此接口不等于历史消费明细接口 |
| OpenRouter | Credits/Usage 汇总 | Credits API 需要 Management Key；需区分可用额度与累计消费 |
| GitHub | 只读 Issue、PR、Actions 状态 | 先用公开仓库/测试 token 验证；正式授权应最小权限 |

## 2. 推荐技术方案

- Electron + React + TypeScript + electron-vite（或等价成熟脚手架）
- Node.js + TypeScript：core、connector、sync runtime
- LangGraph.js：状态图、checkpointer、暂停/继续、受控工具调用
- SQLite + Drizzle ORM：事件、快照、同步任务、结构化记录
- Electron safeStorage / OS key store：加密敏感凭据；优先评估异步 API
- Zod：API 响应、连接器输出与 IPC 参数验证
- TanStack Query + Zustand：异步 UI 查询与短期 UI 状态分离
- Vitest、API Mock、Playwright Electron E2E、GitHub Actions
- pnpm workspaces，遵守单向依赖与接口契约

> 注意：`better-sqlite3` 等原生数据库驱动的 Electron ABI 与打包需在首个技术验证阶段测试，避免最后才发现无法分发。可以按实际兼容情况替换驱动。

### 建议目录（目标结构，按里程碑渐进创建）

```text
tavo/
├── AGENTS.md
├── package.json
├── pnpm-workspace.yaml
├── apps/
│   └── desktop/
│       ├── src/main/                # Electron main / IPC / 托盘
│       ├── src/preload/             # 白名单 API
│       └── src/renderer/            # React 前端
├── packages/
│   ├── domain/                      # 类型、Schema、权限/能力定义
│   ├── core/                        # 应用业务逻辑、事件处理
│   ├── connectors/                  # DeepSeek/OpenRouter/GitHub
│   ├── sync/                        # 调度、任务生命周期、重试
│   ├── agent/                       # LangGraph 工作流、工具注册
│   ├── storage/                     # SQLite、迁移、Repository
│   └── testing/                     # Mock 和契约测试共享工具
├── docs/
│   ├── PRODUCT.md
│   ├── ARCHITECTURE.md
│   ├── ADR/                            # 设计决策
│   ├── LEARNING_LOG.md
│   └── EVALUATION.md
├── tests/
├── .github/workflows/
├── README.md
├── SECURITY.md
├── CONTRIBUTING.md
└── LICENSE
```

### 关键边界

```
React Renderer（只显示数据，不直接拿凭据）
       ↕ Type-safe IPC + Zod + Sender 校验
Electron Main（凭据库、授权、通知、窗口生命周期）
       ↕ 显式受控接口
Node Core/Worker（Connector + Sync + Agent）
       ↕ Repository
SQLite（账户元数据、快照、任务、事件、Insights）
```

核心逻辑尽可能不用 Electron API，便于后续在 Node 服务器上复用。不能为了所谓的“复用”过早抽象复杂微服务。

## 3. 统一数据结构（先设计，再编码）

- `Connection`：平台、显示名称、授权状态、能力、上次同步时间、凭据引用 ID。
- `Capability`：`balance`、`usage`、`transaction`、`resource`、`activity` 等，可选字段。
- `MetricSnapshot`：数据源、采集时间、指标名称、原始字符串数值、单位、币种、范围、可信度/状态。
- `UsageBucket`：聚合时间窗口、维度（平台/模型/Key，如果上游支持）、token、requests、cost。
- `ActivityEvent`：外部事件 ID / fingerprint、类型、时间、资源引用、原始来源。
- `SyncJob`：`queued/running/succeeded/failed/needs_auth`、尝试次数、下次重试时间、游标和错误码。
- `Insight`：事件来源、触发规则、分析摘要、证据、优先级、已读状态。
- `AgentRun`：输入事件、图状态引用、输出、模型、Token 成本、执行结果。

**关键规则：** 金额用十进制字符串/安全 decimal 处理，不用浮点数做财务合计；不同币种未经明确汇率不能直接相加；区分余额、累计支出与预估成本；不支持的字段显示“不可获取”，不能伪造为 0。日期原始记录采用 UTC 时间并在 UI 按用户时区呈现。

## 4. 增量同步与容错策略

- 连接器遵守能力声明，按平台限流政策处理分页、cursor / since 等同步方式。
- 用 `(connectionId, providerRecordId)` 或可证明稳定的 fingerprint 唯一约束实现幂等；需要处理上游记录修订。
- `SyncJob` 落盘，启动时恢复被中断任务；超时、指数退避加 jitter；401/403 进入 `needs_auth`；429 优先尊重 Retry-After。
- 严格区分「数据源暂不可用」和「余额为 0」；Dashboard 展示数据更新时间与同步健康状态。
- 外部动作不会因为重试而产生重复副作用。V1 所有第三方工具只读。
- 桌面电脑休眠时任务暂停，重新唤醒后补做一次适量同步，避免瞬时请求风暴。

## 5. Agent 设计（V1 单图，不做多 Agent）

**输入**：同步后可信的结构化事件/指标，而非完整网页、原始密钥。

**工作流**：

```
Sync Completed
   ↓
Deterministic Rules（异常筛选 + 去重 + 限流）
   ↓
Event Context Builder（证据 + 时间窗口 + 用户偏好）
   ↓
LangGraph Agent（解释原因、提出建议、引用来源）
   ↓
Evidence Validator（不得杜撰数据；置信不足时说明）
   ↓
Insight Store → Desktop Notification
```

**工具权限**：`list_connections`、`get_metrics`、`get_recent_events` 等经过授权的只读工具。原始 API Key 不提供给 LLM；模型不能任意执行 shell 命令、联网访问未授权域名或修改远程服务。

**记忆最小集**：用户可编辑的监控偏好和关注账号，不将所有聊天记录直接当成可靠长期记忆。Checkpointer 保存工作流状态，长期记忆另行建模，不混为一谈。

**是否需要 AI**：用户未配置 LLM 时，仍支持全部账户查询、同步和规则通知；AI 分析属于增强功能。

## 6. 开发里程碑与学习任务（建议 6–8 周）

### Phase 0（2–3 天）技术 Spike 与设计

- 学：Electron main/preload/renderer 的安全边界；Node Worker；Electron 打包与 SQLite 原生依赖；LangGraph.js 最小状态图。
- 做：创建 pnpm Monorepo；跑通桌面窗口；完成 SQLite 写入/迁移/打包 Spike；测试 LangGraph.js Checkpoint Demo。
- 验收：桌面程序启动；测试通过；能读写持久化 SQLite；打包后依然可以读取数据；形成 `ARCHITECTURE.md` 与一份 ADR。
- 产物：项目骨架 + 设计图 + 风险清单。

### Phase 1（第 1 周）安全的桌面应用基础

- 学：Electron IPC 安全、Zod、SQLite 事务、OS 凭据保护。
- 做：账户中心空状态、连接器注册表、设置、凭据 vault、数据库 migrations；实现 Mock Connector。
- 验收：Renderer 无法读取原始凭据；错误参数被拦截；重启后 mock 连接可恢复；密钥不会出现在日志和普通数据库记录中。
- 产物：可交互壳层、Mock 数据端到端链路、安全单测。

### Phase 2（第 2 周）插件化连接器 SDK

- 学：TypeScript 接口和泛型、适配器模式、HTTP API、REST、认证与限流。
- 做：`Connection/Capability/Connector/SyncResult` 契约；DeepSeek Balance、OpenRouter Credits、GitHub Read-only 适配器；Mock HTTP 测试。
- 验收：每个连接器成功、401、403、429、超时、坏 JSON 都有单测；支持断开与删除本地凭据。
- 产物：3 个可演示连接器、SDK 文档。

### Phase 3（第 3 周）可靠的数据同步系统

- 学：增量游标、事务、幂等、状态机、任务队列、重试退避。
- 做：手动/定时同步、历史快照、事件去重、失败恢复、限流与同步日志。
- 验收：对同一批数据重复同步不增加重复行；中途强制结束进程再重启可恢复；401 会暂停并提示重新授权；断网不会显示错误的 0 余额。
- 产物：故障注入测试集与同步生命周期图。

### Phase 4（第 4 周）Dashboard 与可解释事件

- 学：React 数据流、TanStack Query、图表、可访问性、数据质量。
- 做：首页、账户详情、趋势图、统一事件时间线、同步状态和原始来源入口。
- 验收：未授权/加载中/空数据/错误/过期数据状态完整；币种不误加；可按平台和时间筛选。
- 产物：产品截图、交互演示、基础性能数据。

### Phase 5（第 5 周）主动式 Agent

- 学：LangGraph StateGraph、checkpoint、Tool Calling、人工介入、评测。
- 做：先使用 3 条明确规则（低余额、消费突增、CI 失败）触发 insight；按需调用 LLM 做解释；证据引用；本地提醒；无 LLM 时的降级逻辑。
- 验收：异常数据集可复现；模型建议能回溯到来源；拒绝未经授权的操作；用户能关闭主动提醒。
- 产物：Agent 状态图、评测集、成功率/误报率报告。

### Phase 6（第 6 周）测试、开源发布

- 学：CI/CD、安全威胁建模、桌面发布、开源贡献流程。
- 做：Vitest / Electron E2E、密钥扫描、开源许可证、README、SECURITY、CONTRIBUTING；macOS 构建与真实安装验证。
- 验收：lint/typecheck/tests/build 全部通过；新机器可按文档启动；演示账户不需要真实密钥；应用安装、授权、同步、分析、删除数据流程可运行。
- 产物：GitHub Release、Demo 视频、可测量的简历指标。

### Phase 7（可选第 7–8 周）打磨与 Windows

- Windows 打包、原生依赖验证、性能压测、数据备份导出、连接器 SDK 贡献样例；不要阻碍首个 V1 发布。

## 7. 每一阶段的 Codex 学习流程

1. **自己先写理解（10–20 分钟）**：描述输入、输出、状态、异常和预期测试。
2. **让 Codex 只做方案设计**：比较两种实现，解释重要技术、列出风险；未经允许不修改文件。
3. **自己审查计划**：决定类型、接口、数据结构和边界，记录 ADR。
4. **小步实现**：每次让 Codex 只改一个子模块，给出最小可验证 PR/commit。
5. **运行测试**：单元测试 + 失败注入；阅读 Diff，问 Codex 为什么这样写以及其他做法。
6. **自己复述和改一小段代码**：不看 AI 解释逻辑，至少独立实现一个错误路径或一个测试。
7. **记录成果**：写 `LEARNING_LOG.md`，填「今天学到什么、遇到什么 bug、为什么这样设计、证据」。

### 统一提问模板（每个任务都套用）

> 背景：Tavo 是 Electron + React + TS 的本地优先 Personal Agent。当前阶段：{阶段}。
> 目标：{用户可观察到的行为}。
> 限制：只修改 {指定目录}；不得访问真实密钥；不引入未经讨论的依赖；先给出方案，不要立刻实现。
> 请依次输出：① 需要理解的 3 个知识点；② 接口/数据流设计；③ 边界条件与安全风险；④ 测试计划；⑤ 最小任务拆分。等我确认后再写代码。

### 代码完成后的复盘模板

> 请作为严谨的技术导师审查本次 Diff：指出潜在 bug、安全隐患和耦合问题；解释关键实现原理；提出 3 道面试追问题；让我独立修复 1 个小问题；最终列出实际执行的命令和测试结果。禁止把未运行的测试称作通过。

## 8. 自动化测试与简历指标

| 项目 | 如何测量 | 预期证据 |
|---|---|---|
| Connector 契约测试 | 200/401/403/429/超时/坏响应/权限不足 | 测试报告 |
| 幂等同步 | 重放同一页及同一游标 | DB 行数与唯一键断言 |
| 断点恢复 | 在同步任意阶段注入异常/退出 | SyncJob 状态和恢复日志 |
| 安全隔离 | Renderer 调用非法 IPC / Token 脱敏检查 | 安全测试用例 |
| Agent 事件召回率和误报率 | 固定评测集（不少于 30 个初始案例） | 混淆矩阵和评测报告 |
| 工具调用正确率 | 只读/越权/无权限场景 | 测试记录 |
| 桌面性能 | 空闲内存、启动耗时、同步延迟 | 测试设备和指标说明 |

目标值只能在完成基线后设定，不要预写成功率。性能数据必须说明设备、构建模式、数据量和测试次数。

## 9. 开源和安全规范

- 仓库根目录维护 `LICENSE`（建议评估 Apache-2.0）、`README.md`、`SECURITY.md`、`CONTRIBUTING.md`、`.env.example`。
- 开启依赖更新、秘密扫描、静态检查与 CI。禁止提交真实账号数据、密钥、Cookie、浏览器登录态。
- 默认最小权限；每条 Insight 都能查看来源；提供连接器停用/删除和本地数据删除。
- 插件执行权限受控；第一版优先内置且可审查的连接器，不运行社区上传的任意代码。
- OAuth 公共客户端不内置 client secret；按服务要求选用 PKCE 或其他适合的合法认证方式；先用只读测试账户和 Mock 开发。
- LLM 不接触 API Key 原文，不调用任意 URL；外部来源文本按不可信输入处理，防提示词注入。

## 10. 第一周马上开始的任务清单

- [ ] 新建 `tavo` 私有仓库，创建 `main` 与 `dev` 分支（仓库公开前清理密钥与样例数据）。
- [ ] 复制本方案到 `docs/DEVELOPMENT_PLAN.md`，将 `AGENTS.md` 放仓库根目录。
- [ ] 让 Codex 只输出 Phase 0 详细执行计划并解释技术取舍。
- [ ] 初始化 Electron + React + TypeScript + pnpm workspaces，能打开空窗口。
- [ ] 实现类型安全 IPC 的 `getAppInfo` 测试链路。
- [ ] 验证 SQLite 写入、schema migration、重启持久化和打包后的 native module 兼容。
- [ ] 验证 LangGraph.js 的最小 StateGraph / Checkpoint 独立 Demo（不要先接正式 Agent）。
- [ ] 增加 lint、typecheck、unit test、build 四个统一命令。
- [ ] 写出第一份 ADR：为何选择 TS-only + Local-first + Electron。

## 11. 简历写法（待完成后填写，禁止虚构）

**Tavo — 开源 Local-first 主动式 Personal Agent（Electron / React / TypeScript / LangGraph.js / SQLite）**

- 设计基于 Capability 的插件化 Connector SDK，封装鉴权、异常处理和跨平台数据映射，支持 **[N]** 类第三方服务及 **[N]** 种数据能力。
- 基于 SQLite 实现增量同步、幂等去重、状态持久化与故障恢复，通过 **[N]** 组异常测试验证数据一致性，同步成功率为 **[X]%**（定义及测试环境见评测报告）。
- 基于 LangGraph.js 构建事件驱动的主动式 Agent，结合规则筛选、上下文构建、证据校验和只读 Tool Calling，实现 **[N]** 种异常感知场景，评测集召回率 **[X]%**，误报率 **[Y]%**。
- 实现本地凭据保护、安全 IPC 和桌面自动化测试，完成 macOS **[版本]** 发布，提供开源部署/开发文档。

## 12. 技术参考（开发过程中以官方最新文档为准）

- Electron Security: https://www.electronjs.org/docs/latest/tutorial/security
- Electron safeStorage: https://www.electronjs.org/docs/latest/api/safe-storage
- LangGraph JS: https://docs.langchain.com/oss/javascript/langgraph/overview
- GitHub REST permissions: https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens
- DeepSeek balance: https://api-docs.deepseek.com/api/get-user-balance/
- OpenRouter credits: https://openrouter.ai/docs/api/api-reference/credits/get-credits
- Codex practices: https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex
