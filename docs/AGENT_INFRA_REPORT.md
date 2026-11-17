# Agent 首轮基础设施验收报告

> 本报告保留首轮实现时的验证。随后用户确认单页对话客户端重构：原账户偏好/Agent 页面已移除，Python 能力与 DTO/数据保留；当前 UI 与验证见 CHAT_CLIENT_REPORT.md，不沿用本报告的旧页面场景描述。

日期：2026-11-14（Asia/Taipei）。用户确认 `AGENT_INFRA_PLAN.md` 推荐范围后实施。基线为 Python 重构分支 `c9e7f68`，当前本地功能分支 `feat/agent-foundations`；本轮未提交、推送、合并或发布，没有新增依赖。

## 可验证的闭环

账户卡片设置监控偏好 → SQLite 保存版本 → 对一个持久余额事件创建 AgentRun → Worker 冻结本次上下文 → 两个受限只读工具收集证据 → LangGraph 分类/解释 → 保存结果和执行概览 → 桌面与后端重启后仍可查询。

例子：同一个 8.50 USD 事件，默认阈值 10.00 会得到 warning；保存阈值 5.00 后的新分析得到 info，运行记录显示采用偏好版本 1 和两个成功工具步骤。桌面场景另用 3.50 USD / 阈值 2.00 验证同一行为。

长期记忆是可跨运行使用的业务数据；ContextBundle 是这一次实际选择的输入；图状态是步骤间传递的 JSON；运行结果和工具记录用于查询/解释。它们没有实现 LangGraph thread 的持久 checkpoint。

## 实现与设计理由

| 文件范围 | 变化与理由 |
| --- | --- |
| `backend/app/agent/contracts.py`、`api/base.py` | 明确偏好、来源、证据、工具和上下文 DTO；共享 DTO 基类避免 API/Agent 循环导入，金额统一为 ASCII 数字字符串 |
| `models/entities.py`、`migrations/versions/0002_agent_foundations.py` | 新增三张表，沿用旧 AgentRun；不修改已执行的 0001 或旧 Drizzle SQL |
| `repositories/agent.py` | 账户/任务/租约检查、偏好读取、公开状态及有界历史查询；没有凭据读取接口 |
| `agent/context.py` | 纯函数选择有效偏好、默认阈值、UTF-8 字节预算、来源和概览；便于独立验证 |
| `agent/tools.py` | 静态 Registry 与执行器分工，校验参数/结果/范围、限制资源、保存逻辑步骤；工具不直接获得整个 Services |
| `services/agent.py`、`agent/graph.py` | Service 管理偏好、冻结快照、每个运行的依赖装配；图增加 collect_evidence，沿用确定性 classify/explain |
| `workers/scheduler.py`、`services/application.py` | Worker 构造可信 RunScope，保持租约/fencing；读取超时后保留执行名额，关闭时等待线程资源释放 |
| `api/routes.py`、`schemas.py`、`core/errors.py`、`main.py` | 新增偏好 REST、旧运行 DTO 的兼容字段、固定错误码、精确来源的 PUT CORS；不新增业务 IPC |
| `backend/openapi.json`、客户端 `api/generated.ts` | 由 `pnpm api:generate` 生成，没有手改生成类型 |
| 客户端 `api/agent.ts`、`api/client.ts`、`renderer/agent.tsx`、`src.tsx`、`styles.css`、共享错误码 | Zod 校验、带版本的偏好编辑、运行来源/阈值/工具步骤展示；原生桥接仍为 6 方法 |
| `backend/tests/test_agent_foundations.py`、`tests/contracts.test.ts`、桌面 E2E、两个后端 smoke 脚本 | 从权限/时序/迁移/失败到真实进程和 UI 验证，保留旧分析/导入入口 |
| README、后端 README、API/架构/规划/ADR/学习/评测/开发记录、AGENTS/Git 工作流 | 记录当前边界、升级方式、验证证据与后续独立任务 |

结构化关系表与向量记忆是两种可行方案：当前只有监控阈值，需要精确账户隔离、版本和删除，关系表更直接；向量检索适合未来自然语言资料，但需要另外评估 embedding、召回、来源和删除。图流程也可以自写循环，当前复用已有 LangGraph，授权与事务仍由项目服务层负责。

## 输入、状态与边界

**偏好**：仅 `monitoring.preference`，值为 USD/CNY 和两位小数低余额阈值，可设置带时区的未来有效期。首次写 `expectedRevision=0`，后续在 `BEGIN IMMEDIATE` 中比较版本；冲突 409。删除将内容置空并保留递增的版本墓碑，避免旧客户端用旧版本覆盖。没有偏好、过期或币种不匹配时用 USD 10.00 / CNY 50.00。未选中偏好的值不进入图上下文。

**上下文**：首次执行时冻结来源事件及偏好版本，不是在点击入队瞬间冻结。恢复已有运行仍使用已捕获版本。来源事件余额是分类事实；账户当前缓存指标另带采集时间，不能用新余额覆盖旧事件。历史按同账户、截至来源的时间/sequence 查询，只取同步/数据变化摘要，排除 Agent 输出。旧导入事件没有 sequence 时使用时间边界。

上下文不含账户名称、外部账户标识、credentialRef、SecretStore、客户端会话、原始请求或 ORM/Services 实例。Tool handler 是可信内置 Python 代码，这不是执行第三方插件的沙箱；Mock 测试只能证明当前执行边界，不能证明真实模型能识别所有 Prompt Injection。

**工具**：`get_account_snapshot({})` 返回公开状态和缓存指标；`get_recent_events({limit})` 返回 1–10 条有界历史摘要，Mock 固定请求 5 条。参数不允许 accountId、SQL、URL、文件路径或任意函数名，没有任意工具执行 HTTP 入口。Worker 从持久任务构造 RunScope，调用前和提交前检查账户、任务状态、owner、lease、fence。

| 资源 | 当前限制 |
| --- | --- |
| 完整 ContextBundle | 12 KiB UTF-8 JSON；先裁去最旧辅助历史，主事实无法容纳时失败 |
| 单工具结果 | 6 KiB，Pydantic 输出校验和账户/时间范围复查 |
| 逻辑工具调用 | 最多 2 步，`(run_id, step)` 唯一 |
| 等待预算 | 单次 2 秒、一次执行总预算 8 秒，用单调时钟检查 |
| 阻塞读取资源 | 共享线程池最多 2 个；超时后未退出的读取继续占用 Worker 名额 |

字节数不等于模型 token 数；数据库锁等待、调度和无法硬取消的线程可能延长清理，8 秒不构成硬实时保证。晚到结果不提交。删除账户后排队任务明确失败；执行中删除账户拒绝保存工具结果，并尽可能在有效 fence 下记录失败元数据。失去租约的旧 Worker 连失败审计也不能覆盖新执行者的记录。

**执行记录**：`agent_run_contexts` 保存受限白名单快照，`agent_tool_calls` 保存步骤、版本、已校验的小参数、状态、attempts、耗时、错误码和证据引用。保存的是被选择的证据字段，不是任意完整工具响应或模型提示词。合法恢复可复用成功步骤，未成功步骤可增加 attempts；这不是外部 Exactly-once 或任意图节点恢复。

偏好删除/修改影响以后创建上下文的运行，历史运行保留当时快照用于解释。没有实现审计自动过期或级联擦除所有备份；普通业务数据库也未整体加密。查询仍受现有单所有者认证保护。

## 实际验证

环境：macOS arm64、Node 24.15.0、pnpm 11.1.3、Python 3.12.14、Electron 44.11.1；容器为 Linux arm64。全部使用隔离 Mock 数据，未接入真实账户或模型。

| 命令 | 实际结果与证据范围 |
| --- | --- |
| `pnpm api:generate` | 成功生成 OpenAPI 与 TS 类型 |
| `pnpm lint` | ESLint、Ruff、Python 格式检查通过 |
| `pnpm typecheck` | TypeScript 严格检查通过 |
| `pnpm test` | 客户端 15 项、Python 62 项通过；基线分别 12 / 34 |
| `pnpm build` | 通过，最终 `pnpm package:dir` 也重新执行了 build |
| `node scripts/backend-smoke.mjs` | 独立后端进程的认证、REST、持久同步、SSE、偏好、上下文、两工具和重启查询通过 |
| `pnpm test:dev` | 实际启动 Vite / Electron，添加、同步和分析通过 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm exec playwright test` | 构建模式 3 场景通过：多账户/偏好/分析/重启，旧库导入分析，客户端退出后 Worker 继续运行 |
| `pnpm package:dir` | macOS ARM64 未签名 `.app` 成功生成，不发布 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` | 测试包 3 场景通过（42.5 秒），包含偏好版本与两步工具记录、重启恢复和原隔离断言 |
| `docker compose build` | 本次 Linux 镜像实际构建成功，依赖层复用缓存 |
| `node scripts/docker-smoke.mjs` | 实际隔离 Compose API/Worker 业务通过：非 root、无 Node、队列/Worker 重启、加密存储、SSE、偏好改变结果、上下文/两步工具及 API 重启持久化 |
| `git diff --check` | 通过；0001 与本轮基线的 diff 为空 |

新增 Python 测试覆盖偏好 CRUD/CAS/墓碑/过期/重开、鉴权和多余参数、币种选择、账户/历史边界、冻结版本、无关/过期偏好值排除、上下文裁剪、未知/越权工具、预算、输出超大/非法/跨账户、异常脱敏、超时保留资源、失租晚到写入、0001→0002 升级与 Alembic 元数据一致、失败不生成成功分析、精确 PUT CORS，以及账户删除的排队/读取时序。

桌面使用本机已安装 Chrome 检查文档浏览器；无依赖下载。`test-results/tavo-packaged.png` 已目视检查：偏好版本 1 / 阈值 2.00、4518 字节上下文和两个成功步骤可读。截图、测试数据库和构建产物不纳入源码。

构建仍有第三方 PURE 注释提示、默认应用图标与跳过签名提示；Playwright 有颜色环境变量提示。均未导致本轮验证失败。容器脚本仅清理自己创建的独立项目和卷。

## 本地升级与复现

本轮没有对开发者现有正常数据目录执行迁移；测试只使用独立数据库。已有服务应停止后备份数据库与独立密钥环，再运行：

```bash
pnpm backend:init
pnpm dev:backend
# 另一个终端
pnpm dev
```

`init` 执行增量 Alembic 至 `0002_agent_foundations`，不删旧数据、不轮换既有密钥。不要通过删库升级。CLI 不支持破坏性降级，回滚需使用配套的已验证备份与原代码版本。

学习练习：同一个 8.50 USD 事件先设置阈值 5.00，再设置 10.00 分别运行分析；先预测 severity，再核对 memoryRevision、threshold、toolCalls。删除偏好后再运行，解释为什么新分析用默认值，而旧运行仍显示旧版本。进一步追踪 `Router → AgentService → ContextBuilder → ToolExecutor → Graph → AgentRun`，说明工具参数为什么不能自行指定账户。

## 剩余范围

当前是确定性 Mock 决策经过真实校验/存储/调度基础设施，没有真实 LLM Function Calling、自主规划、会话连续性、持久 LangGraph checkpoint、向量检索、自动记忆抽取、写工具或 HITL。下一项仍应单独设计确认。

未验证真实平台/模型的权限、费用、数据传输、回答质量与注入防护；未做规模性能、完整渗透/秘密审计、Windows/Intel macOS/Linux amd64 或签名公证分发验证。现有单所有者服务不能据此称为公网多用户生产可用。
