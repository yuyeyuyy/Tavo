> 当前模型请求：2026-11-17 已实施有界自动重试、DeepSeek空输出增额及脱敏诊断；最新证据见[MODEL_RETRY_OUTPUT_REPORT.md](MODEL_RETRY_OUTPUT_REPORT.md)，head0008不变。

> 当前客户端：2026-11-17 主题、Markdown与聊天布局，最新验证见[CHAT_APPEARANCE_MARKDOWN_REPORT.md](CHAT_APPEARANCE_MARKDOWN_REPORT.md)。后端策略5/head0008保持，旧报告保留历史。

> 后端：2026-11-17 策略5沙箱与真实宿主Bash，专项验证见[HOST_SANDBOX_TOOLS_REPORT.md](HOST_SANDBOX_TOOLS_REPORT.md)。以下策略4及更早结果保留历史，不用于替代本轮宿主验收。

> 当前本轮：2026-11-17 策略4命令权限与默认公网，最新验证见 [PERMISSION_NETWORK_REPORT.md](PERMISSION_NETWORK_REPORT.md)。以下策略3与更早报告均保留历史结果，不代表新默认值。

> 历史结果：Bash执行策略3，见 [BASH_EXECUTION_POLICY_REPORT.md](BASH_EXECUTION_POLICY_REPORT.md)。此前启动准备/进度/AgentPool/默认值/Session报告保留为历史。
>
> 2026-11-17本轮：api:generate、lint/typecheck/test/build通过；客户端34、普通后端223通过/21项真实Docker默认跳过。显式真实Docker21/21通过（126.97秒），相关开发/出口10/10复验；真实公网PyPI HTTPS及哈希wheel安装通过，认证POST上游仅合成传输。独立backend/model/Bash smoke、Compose配置/构建/实际业务、开发窗口通过，桌面8/8与最终未签名ARM64包内8/8通过。新head0007，0001–0006六项字节一致；未升级正常用户库，默认网络关闭。无真实付费模型/第三方写操作、其他桌面平台或完整安全/负载验收，普通卷监测不称为硬配额。初次失败及修复、所有文件/命令和部署限制见本轮报告。

> 当前有效结果：2026-11-16 Session Agent 核心重构，见文末及 [SESSION_AGENT_REFACTOR_REPORT.md](SESSION_AGENT_REFACTOR_REPORT.md)。此前账户/SSE/窗口清空等结果是历史版本，不能用于当前实现验收。

> 当前结果为 2026-11-16 多厂商文本聊天，见文末与 MODEL_PROVIDERS_REPORT.md。单页客户端、Agent、Python 重构和清理的历史结果分别保留于各报告，不等于当前真实厂商验收。

# Phase 0 验证记录

验证日期：2026-11-12（Asia/Taipei）。环境：macOS 27.0.1 / arm64，Node.js 24.15.0，pnpm 11.1.3；Electron 44.11.1 内嵌 Node.js 24.22.2，SQLite 3.53.4。数据均为本地模拟数据。

## 执行命令与真实结果

| 命令 | 结果 | 证据范围 |
| --- | --- | --- |
| `pnpm install --frozen-lockfile` | 退出码 0 | 最终锁文件安装成功；本机安装时加临时 DNS 辅助，见下文 |
| `pnpm typecheck` | 退出码 0 | 应用、业务包、测试和配置的 TypeScript 严格检查 |
| `pnpm lint` | 退出码 0 | ESLint、TypeScript 与 Hooks 规则；已修正中途发现的问题 |
| `pnpm test` | 退出码 0，5 个文件 / 36 个测试通过 | Node 原生加载、单元与 SQLite 集成 |
| `pnpm native:node` | 读写通过 | Node 24.15.0 / Node-API 10 / SQLite 3.53.4 |
| `pnpm native:electron` | 读写通过 | Electron 44.11.1 / Node 24.22.2 / Node-API 10 |
| `pnpm test:dev` | 退出码 0 | 实际启动 `pnpm dev`，通过 React 渲染、IPC 同步和 Graph 分析后清理测试进程 |
| `pnpm build` | 退出码 0 | Main CJS、sandbox Preload CJS、Renderer 静态资源 |
| `pnpm test:e2e` | 退出码 0，1 个桌面场景通过 | 构建模式完整操作与重启持久化 |
| `pnpm package:dir` | 退出码 0 | `release/mac-arm64/Tavo.app`，明确未签名，不发布 |
| `pnpm test:packaged` | 退出码 0，1 个桌面场景通过 | 直接启动 `.app`，重复验证同步、分析、隔离、重启 |
| `cmp TAVO_DEVELOPMENT_PLAN.md docs/DEVELOPMENT_PLAN.md` | 退出码 0 | 原规划内容保留一致 |

安装和首次下载时，实际前缀为 `NODE_OPTIONS='--require=/private/tmp/tavo-dns.cjs'`，用于绕开本机失效的系统 DNS 解析；该临时辅助不属于项目依赖。未修改系统网络设置。最终 lock 安装实际为 `NODE_OPTIONS='--require=/private/tmp/tavo-dns.cjs' pnpm install --frozen-lockfile`。

构建有第三方 PURE 注释提示；electron-builder 提示默认图标、跳过签名；Playwright 有颜色环境变量提示。以上没有导致退出失败。中途的失败及修正记录在 `DEVELOPMENT_LOG.md`，此表只记录修正后的成功执行。

## 自动化测试覆盖

| 测试文件 | 用例数 | 验证内容 |
| --- | ---: | --- |
| connectors.test.ts | 3 | 注册、重复拒绝、能力、初始化失败、清理、数据结构、稳定事件 ID |
| core.test.ts | 8 | 事件分发/退订/观察者错误、单飞锁、完整业务链、失败保留旧数据、跨连接器拒绝、未知事件、凭据不可用 |
| storage.test.ts | 6 | 真实临时文件 CRUD/重开、事务回滚、幂等、同步时间、中断恢复、迁移校验、缺失记录拒绝 |
| agent.test.ts | 5 | USD/CNY 阈值与边界、结构化证据、MemorySaver checkpoint |
| ipc.test.ts | 14 | 非法参数/额外键/不可信 URL/子框架/其他窗口拒绝、错误脱敏、合法调用 |

桌面 E2E 在两个目标上分别执行同一场景：第一次启动空状态 → 同步 → 两账户可见 → Graph 分析保存 → 再同步 → 两次成功任务、仍只有两条事件 → 非法 IPC 参数拒绝 → 完全退出 → 重开 → 账户、事件、任务、分析仍存在。

同时断言 Renderer 没有 `require`、`process`、`ipcRenderer`，桥接对象只有三方法，生产 CSP 禁止内联脚本及网络连接；没有观察到 pageerror。这些断言不等同于完整渗透测试或安全审计。

原生文件实际存在于 `.app/Contents/Resources/app.asar.unpacked/node_modules/better-sqlite3/prebuilds/darwin-arm64.node`。打包后实际 SQLite 写入和重开证明本目标平台加载可用，并非只确认文件存在。

## 截图与人工复核

打包后的界面截图已检查：`docs/images/tavo-phase0.png`。Mock 标签、余额币种、账户/事件数量、分析证据与同步记录均可见。`test-results/` 是可覆盖的本地测试产物，不纳入源码。

人工复现：

1. `pnpm dev`，点击同步、分析并展开结构化 JSON。
2. `⌘Q` 后重开，确认数据；正常数据目录为 `appData/Tavo`。
3. `pnpm package:dir` 后打开 `release/mac-arm64/Tavo.app`，重复操作。
4. 在独立本地数据目录运行故障实验，不向程序输入真实密钥；单元测试已有连接器故障注入及 DB 回滚断言。

## 未验证与未实现

- 未验证 Windows、Intel macOS、Linux、干净机器安装、签名、公证、Gatekeeper 分发、升级安装和大规模性能。
- 未访问真实账户 API，没有对真实连接器成功率作任何声明。
- 没有持久化 Graph checkpoint；MemorySaver 只在内存测试，分析结果另存 SQLite。
- 没有实现 safeStorage、OS Keychain、实际凭据持久化、Worker、定时任务、自动重试、工具调用、RAG、多 Agent、托盘或系统通知。
- 没有运行依赖安全审计、完整秘密扫描或渗透测试；没有设定虚构的性能/召回率指标。

# Phase 1 验证（2026-11-13）

最终结果以 `PHASE_1_REPORT.md` 为准；Phase 0 记录保留为历史证据。

本轮 `pnpm typecheck`、`pnpm lint`、`pnpm test` 均退出 0。Vitest 共 10 文件、79 用例：既有 36 + 新增 43（账户 7、凭据 8、同步 17、事件/存储 5、IPC 6）。真实临时 SQLite 验证事务、文件重开与迁移；纯时钟/随机源注入验证退避、定时计划与恢复。单元加密替身仅验证接口与故障控制，不代表 OS 加密。

真实 Electron 场景使用系统 safeStorage，没有明文降级：多账户完整流程、旧数据升级、关闭 Renderer 后定时同步。`pnpm test:dev` 退出 0；`pnpm test:e2e` 的 3 个场景通过；`pnpm package:dir` 退出 0；`pnpm test:packaged` 的同样 3 个场景通过。最终安全竞争修复均包含在这些产物中。桌面 E2E 检查密文表、重启后同步解密、凭据清理、Renderer 能力白名单、CSP 和 pageerror；这些检查不等同于全面安全审计。

本机打包首次因 `ENOTFOUND github.com` 失败；随后仅对打包进程使用临时 DNS 辅助。该辅助不在仓库，未更改系统设置。版本 1 迁移 SQL 与本轮开始前源码比较，hash 一致。

未验证：真实第三方 HTTP、Windows/Linux/Intel 系统安全后端、正式签名/公证/安装升级、大规模性能、跨 Worker 同步、完整渗透测试。所有重试和 401/429 均为 Mock 场景，不用于声明真实平台成功率。


# 客户端 + Python 服务端验证（2026-11-13）

实际最终结果：ESLint / Ruff / Python 格式检查、TS typecheck、build 均通过；Vitest 11 文件 / 85 用例（保留旧 79 + 新 6）通过，Pytest 最终 33 用例通过。迁移、Worker、凭据、图与 SSE 的覆盖范围及逐条命令见 REFACTOR_REPORT.md。

真实进程验证：`node scripts/backend-smoke.mjs` 通过 REST / 认证 / 持久同步 / SSE 回放 / Python Agent / 重启；`pnpm test:dev` 通过 Vite 窗口流程；构建模式 Playwright 3 场景通过；`pnpm package:dir` 产出未签名 ARM64 测试包，`TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` 3 场景通过（最终 39.2 秒），包含生产 CSP、Renderer 隔离、桌面退出后后端继续同步和迁移历史入口。实际浏览器 /docs 使用已安装 Chrome，Swagger 资源不依赖 CDN。

Linux 验证：Docker 镜像实际构建完成，`node scripts/docker-smoke.mjs` 退出 0。隔离 Compose 项目中以 Linux arm64 / UID 10001 运行，无 Node/Electron；API 与独立 Worker 共享数据库/密钥卷，验证 pending 队列、Worker 停止/重启、同步、加密与 0600 密钥、SSE、Python Agent、API 重启历史。仅清理测试脚本自己创建的容器/卷，常规数据与旧库不删。

真实旧库为 1 连接器、0 账户、0 任务、0 事件，已备份并导入独立后端；有数据 Phase 0/1 用例使用合成夹具做关系/字段/数量及回滚验证，不能声称迁移了真实账户。截图 `test-results/tavo-desktop.png` 已目视检查；产物不纳入源码。

未验证：Windows、Linux amd64、Intel macOS、干净机器安装、签名/公证、规模性能、认证暴力尝试、完整秘密扫描与渗透测试。未实现：完整生产 HTTPS/多用户授权与租户隔离、专用 KMS、持久 Graph checkpoint/真实工具/HITL、真实连接器/模型。通过编译、Mock 或容器测试不等于这些能力已通过。


# 清理旧代码后的验证（2026-11-13，历史基线）

原 79 项旧 TS 运行时测试随旧实现移除，先前 85 项是历史基线。当前 `pnpm test` 实际通过客户端 12 项（API/连接配置 6 + DTO/状态/历史兼容 6）、Python 34 项（含新增旧 SQL 哈希检查）。`pnpm lint`、`pnpm typecheck`、`pnpm build`、`git diff --check` 均通过。

真实 Electron `TAVO_BROWSER_CHANNEL=chrome NODE_OPTIONS='--require=/private/tmp/tavo-dns.cjs' pnpm exec playwright test`：3 场景通过（37.1 秒），验证当前同步/Agent/认证/CSP/重连、独立 SQL 夹具生成旧库并导入显示和分析、退出桌面后继续同步。首次测试因事件列表刷新时序误选旧事件失败，改为等待指定账户/余额的变化事件再分析后通过，未修改业务逻辑。

当前仅 root / desktop / shared 三个 pnpm 项目，锁文件没有旧业务包、better-sqlite3、Drizzle 或 JS LangChain。旧 SQL 与基线字节一致，迁移能力及用户数据库/密钥/备份保留。详见 LEGACY_CLEANUP.md。本轮未重跑测试包、Docker 或性能测试，不沿用历史结果冒充当前执行。

# Agent 首轮基础设施（2026-11-14，客户端重构前）

用户确认设计后实现结构化账户偏好、受限上下文、两个本地只读工具和 Mock 图执行记录。`pnpm api:generate`、`pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm build` 通过，当前为客户端 15 / Python 62 项。新增测试覆盖 CAS/过期/账户范围/历史时间、上下文预算与冻结、工具白名单/参数/输出/错误脱敏、超时资源保留和失租晚到写入、迁移及账户删除竞争。

真实进程 `node scripts/backend-smoke.mjs` 通过偏好、上下文、两步工具及重启持久化；`pnpm test:dev` 通过。构建模式 `TAVO_BROWSER_CHANNEL=chrome pnpm exec playwright test` 3 场景通过；`pnpm package:dir` 重新构建成功，`TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` 3 场景通过（42.5 秒）。未签名 macOS ARM64 测试包不等于正式分发；截图 `test-results/tavo-packaged.png` 已目视复核。

本次 `docker compose build` 实际构建成功，`node scripts/docker-smoke.mjs` 在隔离 Linux arm64 API/Worker 容器验证偏好阈值改变结果、上下文、两个工具记录、API 重启保留，以及原认证/队列/SSE/加密场景。没有仅凭 Compose 配置或镜像构建宣称业务通过。

0001 未修改，新增 0002；升级测试验证已有账户保留及 Alembic 元数据一致。没有迁移正常用户数据目录、提交或推送。本轮不增加依赖。完整文件变化、预算、复现与未验证项见 AGENT_INFRA_REPORT.md。仍无真实模型/连接器、持久 graph checkpoint、向量检索、写工具或 HITL；没有性能/安全全面验收或跨平台正式分发。

# 单页对话客户端（2026-11-14，历史基线）

用户确认方案 A 后移除五个旧页面，重构为深色单页 Chat、内存消息状态、MockTransport 与小型连接弹窗。Python API/迁移/数据保留，聊天没有接入真实模型或 Agent 工具。`pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm build` 通过：客户端 23 / Python 62 项；新增 8 项验证取消/晚到响应/新对话/重试与输出校验。

`node scripts/backend-smoke.mjs` 与 `pnpm test:dev` 通过。`TAVO_BROWSER_CHANNEL=chrome pnpm test:e2e` 当前 3 个场景通过（41.0 秒），覆盖聊天/键盘/composition、停止/新对话、连接/离线/重连、焦点、窄窗口及原隔离/CSP；后端旧历史/Agent 与退出后同步用 API 夹具验证，不再沿用旧页面流程描述。`pnpm package:dir` 成功，`TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` 直接启动未签名 `.app`，同样 3 个场景通过（46.0 秒）；详细记录见 CHAT_CLIENT_REPORT.md。

实际发现的 dialog 焦点恢复问题已修复并复验；开发 smoke 按实际 Vite 回环地址/端口等待 DOM 就绪。截图已目视检查。没有重跑 Docker，没有新增依赖、数据库迁移、用户数据修改、提交/推送。无真实聊天质量、持久会话、完整无障碍/性能或跨平台正式分发验收。

# 多厂商模型聊天（2026-11-16，凭据表单前的基线）

用户确认设计后实现九家 profile / 四类协议、服务端隐藏 Key CLI、既有 SecretStore 加密、认证目录/聊天 API 和客户端模型选择/HTTPTransport。`uv lock --offline` / `uv sync --frozen --offline` 成功，仅提升原锁定 httpx 为运行依赖，不升级版本。`pnpm api:generate`、lint/typecheck/test/build 通过，当前客户端 27 / Python 107 项；45 个 Python 专项验证协议、凭据/错误/隐藏推理隔离、预算、取消清理、轮换与真实 PTY 无回显。

真实本地 HTTP 模型替身 + 独立 Python 进程的 `node scripts/model-smoke.mjs` 通过；原 `node scripts/backend-smoke.mjs` 通过。`pnpm test:dev` 通过。构建模式 `TAVO_BROWSER_CHANNEL=chrome pnpm test:e2e` 4 场景通过（47.7 秒）；`pnpm package:dir` 成功，最终 `TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` 4 场景通过（47.9 秒）。模型选择/HTTP 回复/错误重试/停止/切换清空及原隔离/CSP 已验证，截图已目视复核。

全部自动模型请求使用合成 Key，不访问真实厂商；九家真实账户、费用、延迟和质量未验证。无流式 token、工具聊天、会话持久化/长期记忆或新 Linux/Docker 验收；未签名测试包不构成正式分发。正常用户数据和已执行迁移不改，无提交/推送。详见 MODEL_PROVIDERS_REPORT.md，本机操作见 MODEL_SETUP.md。

# 客户端加密凭据提交（2026-11-16，自助模型配置前基线）

用户确认方案后增加专用密码表单、通用 Web Crypto/JWE 提交模块、认证挑战/用途 handler，以及 0003 凭据内容 revision/删除墓碑。保留 CLI，已保存 Key 无读取/回填 API。实际通过 `pnpm api:generate`、`pnpm lint`、`pnpm typecheck`、`pnpm test`：客户端 34 / Python 135 项；新增 7 / 28 项含生产 TS→Python 加密兼容、篡改/重放/授权/有效期、挑战/解密资源、取消晚到、CLI/CAS/删除 ABA、轮换、数据库锁后过期和 0002→0003 密文不变升级。

`node scripts/backend-smoke.mjs`、`node scripts/model-smoke.mjs` 与 `node scripts/dev-smoke.mjs` 均通过。构建模式 `TAVO_BROWSER_CHANNEL=chrome pnpm test:e2e` 5 场景通过（59.0 秒），新场景实际 Electron Web Crypto 提交密文/清空、失败重输、关闭重开、目录刷新、随后本地 HTTP 模型和重启持久性；原四场景继续回归。`pnpm package:dir` 内构建并生成未签名 ARM64 测试包，`TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` 直接启动 .app，同样 5 场景通过（53.9 秒）。设置截图已目视检查，测试无真实 Key/厂商请求。

旧 0001/0002 与历史 SQL 不修改，已有模型密文只补版本。没有新增依赖、正常用户数据迁移、提交或推送；没有本轮 Docker/Linux/其他平台/签名公证/负载/完整安全审计结果。HTTPS 仍必需，JS 内存清理为尽力而为；挑战仅单 API 进程，不等于多实例或公网生产能力。文件和全部实际命令/限制见 SECRET_SUBMISSION_REPORT.md。

# 客户端自助模型配置（2026-11-16，当前）

用户确认后增加厂商/模型信息表单与认证创建 API、SQLite 权威目录、0004 一次性旧 JSON 事务导入。空客户端直接添加后聚焦 Key，普通信息与秘密接口分离，无需改 JSON 或每次重启。实际 `pnpm api:generate`、lint/typecheck、`pnpm test` 通过，客户端 37 / Python 156 项，新增 3 / 21；覆盖 API 白名单/权限/容量并发/写锁后认证、即时加密提交聊天、旧配置/Key 保留与 0003→0004/Alembic 元数据。

独立 `node scripts/backend-smoke.mjs`、`node scripts/model-smoke.mjs` 和实际 `node scripts/dev-smoke.mjs` 均通过。最终 `TAVO_BROWSER_CHANNEL=chrome pnpm test:e2e` 构建版 6 场景通过；`pnpm package:dir` 成功产出未签名 ARM64 .app；去掉临时诊断后 `TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` 同样 6 场景通过（约 1.1 分钟）。新场景仅在界面从空目录选厂商/填模型/录入合成 Key/聊天并重启保留，保存时无厂商请求。打包设置截图已目视复核，git diff --check 通过。

迭代有两次 Key 提交 422 和一次旧 Mock 输入预期不一致；补输入/响应断言后，24 次 Node 后端加密提交、8 次直接包内操作、单独 6 次与连续顺序 3 轮诊断均通过，最终全量也通过，原因未稳定复现，未声称已找到根因。临时诊断仅记录合成输入状态且已移除；实际失败/复验和残留风险完整见 CLIENT_MODEL_SETUP_REPORT.md。

无新依赖、已执行旧迁移修改、正常用户数据升级或提交推送。未验证真实厂商/费用/账户、Docker/Linux/其他平台/签名公证/规模性能/完整安全；无配置编辑/删除、地区网关/任意 URL、实时模型发现或自动付费测试。操作见 MODEL_SETUP.md。


# 持久 Session Agent 核心（2026-11-16，当前）

按完整批准方案退出账户/余额/旧事件运行功能并保留数据；新增0005、持久Session/消息/Run/Context、四协议真实模型工具循环、规则/人工审核、加密私有续接和桌面恢复。没有新增依赖或迁移正常用户库。

实际 `pnpm api:generate`、`pnpm lint`、`pnpm typecheck`、`pnpm test` 均通过：客户端 **33** / Python **138**。九家预设四协议HTTPX测试覆盖工具关联和隐藏思考/签名加密隔离；独立backend smoke和真实本地HTTP model smoke均通过。恢复测试覆盖未知付费请求不重发、已存提议恢复、取消迟到、失租写回拒绝、超时名额保留与完整轮次裁剪。0004→0005逐旧表行/Key/外键保留、SQL哈希、Alembic元数据检查均通过；实施前后0001–0004文件4/4完全相同。

实际开发窗口通过；构建版桌面E2E **6/6**、ARM64未签名包内 **6/6**。已目视复核截图并修正审核操作被滚动挤压、动态轮询重复占位。实际Compose配置检查、镜像构建、独立Linux API/Worker队列/重启/审核/工具/加密续接业务均通过，三种验证分别执行，测试专用卷已清理。

没有真实厂商付费请求、Windows/签名公证/生产多租户/负载或完整安全审计。旧Key偶发422本轮未复现，原始原因仍未确定；不宣称已修复。全部命令、文件变化、操作与限制见 [本轮报告](SESSION_AGENT_REFACTOR_REPORT.md)。


# AgentPool/独立自审查/隔离Bash（2026-11-16，当前）

按完整批准方案实现0006，保留0001–0005/旧数据/Key。有界Pool、每轮独立模型工具复核、规则/人工约束、显式沙箱能力与未知写操作不重播。`pnpm api:generate`、lint/typecheck、`pnpm test`均通过：客户端34，Python163通过/6项真实Docker默认跳过；显式TAVO_TEST_DOCKER组6/6通过，后端合计169项验证。

独立backend/model/Bash smoke、真实开发窗口通过；真实桌面7/7与未签名ARM64包内7/7（含Docker命令/文件跨重启/审核）通过。Compose分别做配置检查、镜像构建和实际独立Worker业务；不扩大其Docker权限。真实容器测非root/只读根目录/无外网路由、内存OOM/超时/输出与会话隔离/cwd符号链接。旧迁移5/5哈希一致、0005→0006旧审核/密文可读、外键和元数据通过。

工作卷总容量无统一硬配额、数据目录迁移需显式迁卷、单所有者/无真实厂商付费/无Windows或正式签名审计。测试资源规范路径清理问题已定位修正并核验回收；未操作正常用户库。完整文件/命令/限制见本轮报告。


# Bash默认启用（2026-11-16，当前）

用户要求默认启用并移除开关。服务端/新Session默认true，纯文本除外，Docker就绪不再阻断会话创建。历史Session/冻结Context保留，无新迁移或正常库修改。lint/typecheck通过，客户端34/后端164普通测试通过；6项Docker显式运行通过，独立Bash smoke/开发窗口/Compose配置-构建-实际业务均通过。桌面和未签名包内完整回归各7/7通过，结果见 [BASH_DEFAULT_REPORT.md](BASH_DEFAULT_REPORT.md)。审核/容器隔离保持，未调用真实厂商。

# Bash启动准备（2026-11-16，当前优先）

用户确认后端启动前准备Docker，覆盖上一轮环境缺失仍可启动的规则。默认镜像缺失自动构建，真实短命预热和清理成功后才就绪；Docker不可达中文提示、非零退出、无监听/任务领取。启动失败/取消均清理资源，不自动启动Docker服务。无新迁移，当前Compose显式关闭Bash且不扩大权限。

api:generate、lint/typecheck、客户端34、最终后端178通过/9项真实Docker默认跳过；显式Docker组9/9通过，后端共187项分别验证。build、独立backend/model/Bash smoke、Compose配置检查/镜像构建/实际业务均通过。未遗留预热容器或测试镜像标签。没有客户端UI/原生改动，本轮未重复桌面E2E/包内验证或真实付费模型请求；首构建使用本机基础层缓存，非冷网络性能测试。全部命令、文件和限制见 [BASH_STARTUP_REPORT.md](BASH_STARTUP_REPORT.md)。

# Bash启动进度与完成提示（2026-11-16，当前）

定位缓存镜像/短命预热容器/成功日志静默的反馈问题，补齐四阶段和每5秒等待耗时；失败无成功提示，心跳退出不遗留。CLI的实际监听日志与沙箱完成分开，独立Worker提示就绪。lint/typecheck通过，客户端34、后端180通过/11真实Docker默认跳过；显式Docker11/11通过，含serve/worker真实进程输出和镜像构建。build及独立backend/Bash smoke通过。本轮未重跑Compose/model smoke/桌面E2E/包内或真实付费模型，无新迁移/依赖。详见 [进度报告](BASH_STARTUP_PROGRESS_REPORT.md)。

## 2026-11-17：策略4命令权限与默认公网

本轮最终通过api:generate、lint、typecheck、test、build。客户端34/34，后端265通过/23真实Docker默认跳过；显式Docker23/23，最终出口专项5/5复验。独立backend/model/Bash smoke、开发窗口、桌面8/8与未签名ARM64包内8/8通过。旧0001–0007七项源文件与开始前备份字节一致，无新迁移或正常用户库升级。

公网实连为PyPI只读TLS/固定wheel，显式DoH配置；所有写方法上游合成，不代表真实第三方业务或真实模型质量验证。全部命令、失败原因/修复及范围见[PERMISSION_NETWORK_REPORT.md](PERMISSION_NETWORK_REPORT.md)。

### 同日DNS默认值修复验收

系统DNS在关闭代理全局模式后仍返回DeepSeek Fake-IP；Tavo默认改为已有cloudflare-doh。lint/typecheck/test/build通过，客户端34、后端266通过/24 Docker默认跳过；显式真实出口6/6（61.92秒），包含默认配置真实沙箱读取DeepSeek官方文档HTTP200。独立backend/Bash smoke及重启通过，无真实付费模型/第三方写操作，未重跑桌面或测试包。文件清单、原因和范围见[DNS_DEFAULT_REPORT.md](DNS_DEFAULT_REPORT.md)。

## 2026-11-17：策略5沙箱与真实宿主命令

当前通过api:generate、lint/typecheck/test/build。客户端34/34、后端280通过/38默认跳过；显式宿主组合28/28（14真实临时项目＋14契约/审核/恢复替身/迁移），真实Docker24/24。独立backend/model/Bash/host烟雾通过，宿主另验证系统/当前进程/临时日志/Docker版本只读查询。桌面9/9、最终未签名ARM64包内9/9、开发窗口通过；初次取消重试与输入法就绪失败及修正见[报告](HOST_SANDBOX_TOOLS_REPORT.md)。

head0008，原七项迁移与备份字节一致，正常用户库未自动升级。不使用真实付费模型，不执行真实项目部署/远程写入。没有Linux宿主、Windows业务、恶意多租户强隔离或正式签名分发验收。

## 2026-11-17：客户端主题、Markdown 与聊天布局

通过 lint（ESLint/Ruff/格式）、TypeScript、Vitest 52/52、Pytest 280 通过/38 默认跳过、构建、开发窗口、桌面完整11/11及未签名ARM64包内完整11/11。最后补齐脚注往返/多消息ID和隐藏标题定位，桌面定向两项2/2（12.8秒）；最终代码重新构建测试包并执行完整包内回归。

覆盖：默认跟随系统、显式浅/深、完整应用重开、非法/拒绝存储、草稿/会话/权限不变；Markdown格式、长代码/表格、嵌套列表、只读任务项、脚注、未闭合围栏；HTML/事件/危险协议拒绝、无自动图片请求、合法外链仅真实点击交系统浏览器替身、无Electron子窗口/页面跳转；原Key/历史/重试/IME和宿主/沙箱审批。截图检查浅/深、普通/长对话、设置、审批和620×540窄窗口。

这次未修改Python业务/契约/迁移，也未升级正常用户数据。初次焦点、脚注和布局失败及根因修复见[报告](CHAT_APPEARANCE_MARKDOWN_REPORT.md)。后端38项真实专项测试本轮普通命令未运行；显式桌面测试包含实际临时Docker/宿主操作，不能替代整个后端真实测试集。未调用真实付费模型，未启动系统浏览器访问测试域名，不声称正式分发或跨平台验收。

## 2026-11-17：DeepSeek 空正文错误诊断

真实官方合成输入两次对照：当前2048上限全部用于思考、length且无正文，被Tavo误判MODEL_REJECTED；同预算关闭思考有正文并被接受。用户拒绝原对话重发并要求自动重试后，再以合成输入通过实际ModelGateway/trust_env=false复验一次，得到同样结果。共三次实际请求，无会话外发或工具执行，用户DB只读。69项现有模型/Agent协议测试通过（10.52秒），尚未修复运行代码，不能把通过说成该缺陷已解决。诊断、审批边界与拟议修复见DEEPSEEK_RESPONSE_DIAGNOSIS.md及MODEL_RETRY_OUTPUT_PLAN.md。

## 2026-11-17：模型请求有界重试实施

用户确认完整方案后实现。api:generate（结果字节不变）、typecheck、lint通过；前端54/54、后端326通过/38默认跳过（50.72秒），新增模型重试46/46（1.45秒）。构建、独立backend/model烟雾、开发窗口、完整桌面11/11及未签名ARM64包内11/11通过。

真实DeepSeek仅固定合成内容、SQLite只读、无工具/会话外发：两次逻辑调用共5次HTTP。复杂执行池任务在2048/4096耗尽后，8192第三次被60秒总时限终止，此项未取得生成成功；有限CSV任务则从2048自动重试4096后stop、完整正文且未截断。模型/错误修复已验收，但不宣称所有复杂推理任务均能完成。详见MODEL_RETRY_OUTPUT_REPORT.md的逐次安全指标、文件/命令和费用/超时边界。
