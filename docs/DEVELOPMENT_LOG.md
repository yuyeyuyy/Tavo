# Phase 0 实际开发记录

日期：2026-11-12。范围按本次用户请求，仅实现本地 Mock 最小闭环。

## 初始检查与保护

- 初始目录只有 `AGENTS.md`、`CODEX_START_PROMPT.md`、`TAVO_DEVELOPMENT_PLAN.md`，没有源码、依赖或 `.git`。
- `git status --short` 实际返回 `fatal: not a git repository`。未初始化 Git、未改历史、未提交或推送。
- 已阅读三份原文件。根目录原始开发计划完整复制到 `docs/DEVELOPMENT_PLAN.md`，`cmp` 验证一致。原启动提示保留；AGENTS 只追加本轮工程说明。
- 本次请求明确要求解释后直接实施，并授权 Mock、SQLite 和 LangGraph 闭环；未执行真实连接器或下一阶段业务。

## 小步实施与设计理由

| 步骤 | 文件范围 | 作用与理由 | 验证 |
| --- | --- | --- | --- |
| 工程初始化 | 根 package/lock/workspace、TS、ESLint、Vitest、Playwright 配置 | 锁定稳定版本，严格类型，统一门禁；pnpm 11 显式脚本白名单 | frozen-lockfile 安装、typecheck、lint |
| Electron 基础 | desktop 配置、main/preload | 三方法桥接、窗口与生命周期；业务不依赖 Electron | 真实开发命令、桌面 E2E |
| 协议、Core、Connector | shared/core/connectors | unknown 入边界、能力声明、初始化清理、注入端口、单飞锁、类型事件 | 注册/结构/事件/并发/故障测试 |
| SQLite | storage/schema、migrations、index | 事务、外键、上游唯一键；迁移嵌入构建，重启保留数据 | CRUD、回滚、幂等、迁移损坏、中断状态测试 |
| LangGraph | agent/index | State + classify/explain 两节点，无 LLM；保留 checkpointer 注入 | 图执行、金额阈值、MemorySaver 状态测试 |
| UI 集成 | renderer/store、tsx、css、html | 黑白灰界面、空/忙/错状态、余额币种、事件分析与 JSON、同步记录 | 开发与构建模式点击验证、截图检查 |
| 分发验证 | builder、native 检查脚本、E2E | 原生库 external + ASAR unpack，未签名 dir 包 | `.app` 启动、同步、分析、关闭后重启 |
| 文档 | README、架构、ADR、学习、评测 | 说明输入输出、状态、限制与可复现证据 | 与最终文件和命令核对 |

每个模块的 TypeScript / Node / Electron 技术、扩展注意事项和面试追问见 `LEARNING_NOTES.md`，重要取舍见 ADR 0001。

## 实际遇到的问题

1. **网络**：普通 npm 请求和放宽沙箱后的请求均遇到系统 DNS `ENOTFOUND`；HTTPS 本身可用。通过 `/private/tmp/tavo-dns.cjs` 为本次安装子进程设置临时解析回退，完成官方注册表和 Electron 下载；未修改系统 DNS、Hosts 或提交该辅助文件。以后在干净机器上安装仍需网络正常。
2. **pnpm 11 脚本审批**：早期使用旧式配置导致安装拒绝未批准构建脚本，改为 `allowBuilds`，仅允许必要依赖、拒绝 Windows installer 脚本后安装成功。
3. **SQLite 版本事实**：开始按传统 ABI 模块规划重编译；检查已选包与官方 v13 发布说明后确认其采用 Node-API。删除不必要的直接 rebuild 依赖，改为双运行时实际读写检查；electron-builder 保留自己的原生依赖处理。
4. **测试 API 和构建临时文件**：移除不存在于当前公开 Electron 类型中的测试调用，改验证 Renderer 能力和生产 CSP；ESLint 忽略 electron-vite 构建期间生成并立即删除的配置临时文件，避免并行读取竞态。
5. **开发烟雾清理**：CDP 断开不能保证 macOS Electron 退出。脚本现在在独立进程组中启动测试子进程，先 TERM、再 KILL 清理，关闭连接并删除隔离目录；普通退出和重启由 Playwright Electron E2E 另外验证。最终脚本已正常返回退出码 0。清理代码曾被 `no-unsafe-finally` 拦截，修正后重新运行 lint。
6. **非失败警告**：Zod 的 PURE 注释触发 Rollup 提示，注释被移除，构建成功；builder 提示默认图标和未签名，符合本轮产物设置。没有为消除依赖提示而扩展需求。

## 实际验证与残留风险

完整命令、数量和结果见 `EVALUATION.md`。没有把类型检查或构建成功当作业务通过；开发模式、生产构建模式、打包模式均另行执行了真实操作。

残留：主线程同步 SQLite 仅验证演示规模；无真实凭据库、自动队列、持久 checkpoint、真实 API；未签名、公证或做跨平台安装。没有发布、上传账户数据或使用真实密钥。

# 2026-11-13：Phase 1 增量开发

## 范围与审查

本次用户明确授权 Phase 1 五个模块，并要求分析后直接实施。已重读 AGENTS、README、开发计划、架构、开发记录、受影响源码与原有测试；工作目录仍无 `.git`，`git status --short` 返回非仓库。没有初始化 Git、提交、推送或发布。

Phase 0 已具备隔离 IPC、独立 Core、Registry、SQLite、Mock 与图执行，但没有多连接实例、安全凭据库、可靠队列、定时调度和持久业务事件。保留 Main 中用户添加的学习注释，以及原始规划和启动提示。修改前将源码和文档备份到本地临时目录，仅用于本轮回看。

## 文件变化与设计理由

| 文件范围 | 修改与理由 |
| --- | --- |
| shared/contracts、phase1、index | 保持原导入路径；新增连接、任务、事件、设置和端口，严格校验状态组合；统一固定错误码 |
| core/accounts、credentials、sync-engine、workspace-runtime | Account Manager、安全存储抽象、限定范围的凭据使用、独立持久同步、业务入口；无 Electron 依赖 |
| connectors/index | 保留原 Mock 接口，新增每账户增量协议、AbortSignal、模拟故障和标准错误分类 |
| storage/phase1-migration、workspace-schema、workspace | 新增版本 2 和六张表；独立 EventRepository、密文仓库、游标与事件的原子提交 |
| storage/index、migrations | 接入新仓库和迁移；版本 1 SQL hash 对比确认未变 |
| desktop main/security、ipc、preload | OS 加密适配器、业务白名单、安全来源/参数验证、独立调度生命周期；移除 Renderer 的旧全局同步入口 |
| renderer | 四个区域、账户表单、同步/断开/删除/凭据更新、Mock 配置、活动关联、设置；旧历史与分析继续可用 |
| tests/phase1、tests/e2e、scripts/dev-smoke | 新增故障、安全、恢复测试；真实开发、构建和打包场景验证 |
| README、架构、学习、报告、ADR、评测 | 记录实际行为、原理、两项练习、验证和未解决边界 |

本轮没有新增第三方依赖，也没有大规模改写原 Event Bus、Graph 或 SQLite 基础实现。旧同步表作为历史兼容保留，新链路只写新任务/连接表。

## 分模块验证与发现

1. 账户与安全：多实例、重复拒绝、状态转换、密文更新/删除、不可用失败、Linux 后端策略和凭据作用域；真实 macOS safeStorage 的加密与重启解密在 E2E 单独验证，单元测试加密替身不被当成 OS 证据。
2. 同步：增量空页、数据变更、401/403/429/网络/服务器/超时、有限重试、并发限制、取消、文件重开恢复，以及 SQL 最终事件写入失败后的整批回滚。
3. 事件：顺序、去重、correlationId、通知与持久记录一致、观察者错误、A→B→A 变化，以及原 Graph 分析保存。
4. 同毫秒任务原先按随机 ID 排序，测试发现队列顺序不可靠，已改为 SQLite 入队顺序。
5. Promise.race 不取消底层请求：新增请求名额保留，晚到结果丢弃；超时发生在解密等待阶段时，也不启动连接器请求。
6. 安全复查发现旧值的 OS 轮换重加密可能覆盖并发更新；改用密文 CAS，并在解密后重新校验账户授权，补充可复现竞争测试。
7. 首次打包因系统 DNS 无法解析 github.com 失败。使用已有临时 `/private/tmp/tavo-dns.cjs` 仅为打包子进程提供解析回退后成功；未改系统设置，未把该辅助文件放进仓库。
8. ESLint 捕获开发脚本中浏览器回调的 window 环境声明问题，已明确浏览器上下文并通过复查。第三方 PURE 注释、默认图标、未签名和颜色环境提示仍是非失败提示。

最终实际命令、测试数量和边界见 `PHASE_1_REPORT.md` 与 `EVALUATION.md`。没有把 401/429 模拟测试当作真实 HTTP 平台验证，也没有把构建成功当作实际业务成功。

## 2026-11-13：Architecture Refactor

用户明确授权将 Electron 单体重构为 Electron/React 客户端与独立 Python/FastAPI 服务端。先阅读旧规划/架构/实现，验证 79 项旧 TS 测试、lint/typecheck/build；仓库起初无 Git，建立源码基线 `898328d` 和 `refactor/client-server-python` 分支，不推送。

新增后端 Pydantic、SQLAlchemy/Alembic、SecretStore、客户端会话认证、账户/连接器、持久 Worker 租约与 fencing、增量事务/重试/恢复、事件 REST/SSE、Python LangGraph。客户端改用 API Client + TanStack Query；原生 IPC 仅 6 项。保留旧业务包和学习注释，旧数据库先备份再导入独立目标。

逐步验证 Python/API/Worker/图与迁移、API Client、独立进程 REST/SSE/重启，以及真实开发窗口和构建/打包 Electron。详细命令、计数、部署边界及未验证事项以 REFACTOR_REPORT.md / EVALUATION.md 为准，旧 Phase 报告保持历史记录。

## 2026-11-13：清理迁移前的 TS 实现

按用户要求删除不再使用的 Core/Storage/Connector/JS Agent、旧业务 IPC/safeStorage、重复归档及对应测试/原生检查脚本。删除前验证受影响旧文件与 Git 基线一致；当前未提交的客户端/Python 改动、IDE 文件和私有数据不动。

原 SQL 先逐字节提取到 backend/tests/fixtures/legacy，Python 测试不再读取 TS 源码；当前共享 DTO 保留，移除旧业务端口。依赖与文档同步更新。当前验证结果和测试计数以 LEGACY_CLEANUP.md 为准，原 79 项旧运行时测试作为 Git 历史保留。


## 2026-11-14：上传 GitHub 与分支整理

用户授权上传至 yuyeyuyy/Tavo。保持仓库私有，main/dev 使用已验证 Phase 1 基线加私有文件忽略规则，完整 Python 重构与清理成果放到 feat/client-server-python。上传前重新执行 lint/typecheck/客户端 12 项/Python 34 项/build，检查当前文件及 Git 历史的常见密钥格式，不上传 .idea、数据库、密钥、令牌或本机产物。

使用 GitHub noreply 提交身份，不修改全局身份；通过临时回环 GitHub TLS 隧道解决现有 DNS 故障，不修改系统 DNS。具体分支职责见 GIT_WORKFLOW.md，不自动合并或发布 Release。

## 2026-11-14：Agent 记忆、上下文与只读工具首轮

先阅读开发计划、当前架构和 Agent/Worker/API/客户端实现，提出自写循环与复用 LangGraph、结构化表与向量记忆的取舍。将接口、范围和测试写入 AGENT_INFRA_PLAN.md，用户回答“按推荐范围开始实现”后，在 Python 分支基线创建本地 feat/agent-foundations；不自动提交/推送。

实现一种账户监控偏好、CAS/版本墓碑/有效期、首次执行冻结的 ContextBundle、两个本地只读工具、参数与结果范围校验、UTF-8 大小/步骤/时间预算，以及白名单上下文和逻辑步骤记录。复用已有持久队列/租约/fencing，工具超时后仍保留底层读取名额，拒绝晚到写入。补齐排队或读取期间删除账户的终态处理，防止永久 pending 和伪成功。新增 Alembic 0002，0001 与旧 SQL 不动。

客户端增加偏好编辑和 Agent 执行概览；Pydantic/OpenAPI/生成 TS/Zod 同步，Main/Preload 仍为原 6 个原生方法。没有新增依赖、真实模型、凭据上下文或第三方写入。

最终检查通过 lint/typecheck、客户端 15 / Python 62 项、构建、独立后端 smoke、开发窗口、构建及未签名测试包各 3 个桌面场景；Linux 镜像构建和隔离 Compose 实际业务也通过。截图已检查。详细文件范围、实际命令、学习练习与限制见 AGENT_INFRA_REPORT.md / EVALUATION.md；历史记录与 checkpoint 的边界已写入架构和 ADR 0004。

## 2026-11-14：客户端收敛为单页对话

按 AGENTS 先阅读现状、写 CHAT_CLIENT_PLAN.md，解释“先做界面 + Mock”和“同时建设真实聊天后端”两种范围，用户确认方案 A 后实施。先临时备份旧 UI 源码，重写 Renderer、移除旧页面及仪表盘状态，新增可注入 ChatTransport、Zustand 内存消息、Mock 与连接弹窗；Main 仅调整窗口外观/尺寸，不放宽安全边界。上一轮未提交的 Python 基础设施保留，本轮不改数据/迁移，不新增依赖或提交推送。

状态测试重点覆盖取消无效、晚到请求、新对话与重试，不添加只镜像 UI 的测试。真实 E2E 找到弹窗关闭后焦点未返回，已用实际打开入口恢复并复验。开发 smoke 的初始导航就绪/回环地址等待已修正；最终验证命令、计数、截图和限制以 CHAT_CLIENT_REPORT.md 为准。旧后端业务继续通过独立进程/兼容夹具验证，当前客户端仅 Mock 对话，不宣称模型或工具已接入。

## 2026-11-16：多厂商文本聊天

先核对各家官方接口，设计九家 profile/四类协议、服务端 Key 与真实聊天最小链路。用户确认 MODEL_PROVIDERS_PLAN.md 后实施，复用 SecretStore 和锁定 httpx，新增 ModelKeys、薄适配器、ChatService/API、隐藏 CLI 与模板，客户端显式选模型、不自动 fallback。不扩展事件 Agent 工具或持久会话。开发者新加学习注释保留；基线 Ruff 报告的空行/导入问题仅做必要格式整理。

实际通过客户端 27 / Python 107、lint/typecheck/build、独立后端与真实回环 HTTP 模型 smoke、开发窗口、构建及最终未签名测试包各 4 场景。真实 PTY 检查 Key 不回显；模型目录/响应与日志用合成秘密标记断言。离线依赖更新只移动 httpx 到运行组，没有厂商 SDK 或联网下载。真实 Key/厂商没有执行，Docker 未重建，正常数据库和已执行迁移不改，无提交推送。文件/命令/风险见 MODEL_PROVIDERS_REPORT.md，配置教程见 MODEL_SETUP.md。

## 2026-11-16：客户端加密 Key 提交

用户明确改为客户端手动录入，先比较 HTTPS 与额外加密信封并写 SECRET_SUBMISSION_PLAN.md，回答“按推荐方案实现”后编码。专用 SecretInput / Web Crypto 提交器与模型目录包装分离；后端挑战/解密服务与静态用途 handler 分离，沿用既有 SecretStore 和 CLI。新增 0003 版本/删除墓碑，HTTP/CLI 同事务 CAS，保留旧密文和旧迁移。取得写锁后重新验证会话/过期，避免排队期间失效的提交落盘；普通请求和模型请求的超时错误也分别映射。

没有新增依赖、读取已有 Key、任意 URL/secretRef、模型工具或账户连接器。跨语言测试直接用生产 TS Web Crypto 加密并交给生产 Python 解密；全部凭据为合成值。最终文件、命令、计数、开发/打包桌面与迁移证据见 SECRET_SUBMISSION_REPORT.md / EVALUATION.md。工作区原有改动保留，正常用户数据未初始化/迁移，无提交/推送。

## 2026-11-16：客户端自助配置模型

用户在截图中指出空配置没有 Key 输入入口，并明确要求用户在客户端选厂商/模型。先写 CLIENT_MODEL_SETUP_PLAN.md 比较 JSON 热加载和持久目录，确认后新增 0004/Repository/Service/认证 API，原 JSON 首次事务导入保留 ID/目标/预算/Key。Chat/handler/CLI 统一读目录，客户端添加表单/Query 刷新/定位密码框，不再要求编辑服务端文件或每次重启。普通配置请求拒绝秘密/任意 URL。

新增客户端三项与 Python 21 项，旧迁移夹具只 seed 原 schema 再升级；CLI 测试使用持久目录，harness 在初始化前准备旧 JSON。空目录桌面测试在测试专用 Python 入口注入回环预设，生产无该参数，真实加密/数据库/认证/聊天仍走原实现。实际命令、桌面迭代失败及最终结果见 CLIENT_MODEL_SETUP_REPORT.md，不以编译或本地替身宣称真实厂商成功。无依赖增加、正常用户数据写入、提交/推送。


## 2026-11-16：Session Agent 核心

按已确认完整方案退出账户/余额运行功能，旧数据/迁移保留；新增0005和持久Session/消息/Run/审核/模型轮次。复用LangGraph、lease/fence、四协议网关、SecretStore/JWE，客户端接入会话恢复、工具记录与人工确认。详细文件、验证、已知限制见 `SESSION_AGENT_REFACTOR_REPORT.md`；未提交或推送。


## 2026-11-16：AgentPool、自审查与隔离Bash

用户确认完整方案后实施0006；新增有界池、独立模型复核、显式Bash模式与真实Docker执行，保持账户历史/模型/Key和原会话功能。旧迁移5/5不变，实际验证/文件变化与操作边界见AGENT_POOL_BASH_REPORT.md。未提交或推送。

## 2026-11-16：Bash启动准备

用户确认BASH_STARTUP_PLAN后，在Python后端初始化前检查Docker、自动构建缺失默认镜像、运行并清理短命预热容器；Docker不可达提示并退出。复用执行隔离配置，补齐初始化失败和清理中取消的资源释放，Compose显式关闭Bash。无新迁移或依赖。最终客户端34、普通后端178、显式Docker9通过，构建和独立smoke/Compose实际业务通过。文件与命令见BASH_STARTUP_REPORT.md；未提交或推送。

## 2026-11-16：启动进度反馈

按用户反馈补齐四阶段、每5秒等待和完成提示；明确缓存镜像复用、预热后清理，不强制拉取或保留常驻容器。启动输出立即flush，CLI保留实际监听日志。客户端34、普通后端180、显式Docker11通过，lint/typecheck/build及独立backend/Bash smoke通过。文件与本轮未重复的验证见BASH_STARTUP_PROGRESS_REPORT.md；无迁移/依赖或提交推送。

## 2026-11-17：执行策略3完成

按已确认方案，16日暂停后17日恢复并完成统一资源/分级审核、SandboxManager、长脚本/工作副本版本提交、UDS出口、专用API Token与受控Python依赖。默认禁网，0007增量升级，旧迁移六项不变，无宿主依赖增加或正常数据升级。客户端34、普通后端223、真实Docker21通过；独立smoke/Compose/开发窗口通过，桌面/未签名包内各8/8。新增恢复、配额、取消、精确审批和清理失败禁止提交回归。文件、实际命令、真实与替身边界及部署步骤见BASH_EXECUTION_POLICY_REPORT.md；未提交/推送。

## 2026-11-17：三档命令权限与默认公网

按已确认方案完成策略4。新会话full_access默认，auto_review/request_approval仅控制Bash；受控公网URL及写方法免独立审批，旧策略冻结与凭据隔离保留。共享正文提示和两端Mock不主动介绍内部工具，审计继续显示。修复默认网关下中断清理后重复删除socket卷的生命周期问题。无新依赖、迁移或推送；完整变更和验证见[报告](PERMISSION_NETWORK_REPORT.md)。

## 2026-11-17：双命令执行入口

按已确认方案完成sandbox与host_bash、共享三档、策略5/0008能力升级，旧会话和旧bash继续Docker语义。宿主标准库监督、取消/恢复、名额、最小环境及凭据输出防护接入原Agent循环；前端明确目标并修复取消刷新期的重试可点击问题。临时项目真实宿主、原Docker、桌面与未签名包通过，文件清单/命令/平台与信任边界见[报告](HOST_SANDBOX_TOOLS_REPORT.md)。没有升级正常数据、提交或推送。
