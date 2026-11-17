# AGENTS.md — Tavo 开发协作规则

> 当前架构已由用户在 2026-11-13 明确授权重构；以下末尾“客户端 + Python 服务端”补充优先于旧 Phase 0/1 的实现约定。

## 项目目标

Tavo 是一个开源、Local-first 的桌面 Personal Agent：接入授权的第三方账户，可靠同步并统一展示账户数据，用规则和受控 LLM 生成主动提醒。第一版 macOS 优先，未来复用核心模块支持无界面部署。

## 角色

你（Codex）既是编码助手，也是技术导师。优先帮助开发者理解系统的输入、输出、状态、风险和测试，不以增加代码量为目标。

## 工作方式

1. 修改前先阅读 `docs/DEVELOPMENT_PLAN.md`、已有架构文档和受影响的代码。
2. 对非简单任务，先提出设计、接口、影响范围、测试计划；等待确认后再开始编码。
3. 一次只实现一个可验证的最小任务，禁止无关重构或擅自扩大需求。
4. 客户端优先 TypeScript、后端使用 Python 3.12，采用明确的数据模型、可测试的纯函数和依赖注入；不要为了赶进度堆装饰性架构。
5. 每次修改都要列出文件变化、设计理由、已执行的验证命令及结果、残留风险。
6. 遇到外部 API 权限或格式不确定，先查官方文档或使用 Mock，不要猜测请求路径/权限。
7. 不得把“编译成功”误称“实际业务验证通过”；没有执行的验证必须明确标注。
8. 遇到复杂技术点，先解释 2 种可行方案、取舍及相关基础知识，并提出一个让我自己完成的小练习。

## 安全要求（P0）

- 绝不输出或提交真实 API Key、OAuth Token、Cookie、会话、个人隐私数据。
- 不将原始凭据传入 Renderer、LLM 上下文、普通日志、崩溃报告或测试快照。
- Electron Renderer 保持 `contextIsolation: true`、禁用 `nodeIntegration`，使用受限 preload API、CSP 和严格 IPC 校验。
- 连接器初期全部只读，使用最小授权权限，不绕过反爬、验证码或平台限制。
- 使用 SQLite 事务、唯一约束保证增量同步的幂等；外部失败不能伪装成余额 0。
- 所有外部网页/API/用户输入都视作不可信数据，客户端通过 Zod、服务端通过 Pydantic 校验，不允许 Prompt Injection 升级工具权限。
- 不自动安装非必要依赖、不自动发布/推送、不修改远程数据或执行破坏性操作，除非用户明确授权。

## 质量门禁

每个可交付任务至少通过：

- lint
- TypeScript typecheck
- 相关单元/集成测试
- 对应构建或烟雾测试

记录实际执行命令；无法运行则解释原因，并提供人工验证步骤。

## 文档维护

- `docs/ARCHITECTURE.md`：模块与数据流
- `docs/ADR/`：重要设计决策及理由
- `docs/LEARNING_LOG.md`：开发者今天学会了什么
- `docs/EVALUATION.md`：测试指标、方法和结果

## 本阶段任务约束

以 `docs/DEVELOPMENT_PLAN.md` 的阶段划分为准。优先完成 Phase 0；没有用户同意，不得直接开始 Phase 1 或大规模 Agent 实现。

## Phase 0 工程补充

- 本轮用户明确授权的最小闭环包含 Mock Connector、SQLite、最小 LangGraph 与 UI；后续仍不得据此推定已授权真实平台连接器或 Phase 1。
- 根目录原始规划保留，约定入口为 `docs/DEVELOPMENT_PLAN.md`；最新实际实现见 `docs/ARCHITECTURE.md`，验证结果见 `docs/EVALUATION.md`。
- 使用 Node.js 24 LTS、pnpm 11；依赖版本与 `pnpm-lock.yaml` 一起维护，必要脚本由 `pnpm-workspace.yaml` 的 `allowBuilds` 白名单控制。
- 验证基本命令：`pnpm typecheck`、`pnpm lint`、`pnpm test`、`pnpm build`；桌面改动还需 `pnpm test:dev` / `pnpm test:e2e`，原生与分发改动还需 `pnpm package:dir` 和 `pnpm test:packaged`。
- 已执行 SQL 迁移不可修改；当前使用 Alembic 新 revision、Pydantic / OpenAPI / Zod 契约与升级测试，旧 Drizzle SQL 仅为迁移夹具。
- 本地 `.app` 是未签名的测试产物；不得把 dir 构建测试称为正式分发、签名或跨平台安装验证。

## Phase 1 工程补充

- 2026-11-13 用户已明确授权 Phase 1 的 Mock 多连接、安全凭据、可靠同步和业务事件；未授权接入真实账户、写第三方数据或自动进入下一阶段。
- 新业务入口为 WorkspaceRuntime，账户与任务状态由 Core/Repository 维护；旧表和旧运行时保留历史兼容。不得为简化代码删除旧数据或修改版本 1 迁移。
- 凭据由 Main 生成/加密，Renderer 无凭据输入或读取 API。Linux 的 basic_text / unknown 后端必须拒绝；升级 Electron 前重新核对系统后端适配和实际测试。
- 同步游标、数据、成功任务和变化事件必须同事务；等待解密后重新检查授权，超时不得启动晚到请求；OS 轮换重加密使用 CAS 防止覆盖更新或复活删除。
- 自动同步属于 Main/Core 生命周期；界面轮询仅用于展示。接入新连接器必须明确 AbortSignal、错误分类、增量和重试语义，不声称外部 Exactly-once。
- 当前验收和边界见 `docs/PHASE_1_REPORT.md`；未签名测试包、模拟故障、平台替身测试不能称为正式分发或跨平台业务验证。


## 客户端 + Python 服务端工程补充（当前生效）

- 本次已授权迁移既有 Mock 功能，后续仍不自动接入真实平台、模型、复杂 RAG 或多 Agent。非简单后续任务先设计和确认；明确授权的重构步骤无需逐项再次确认。
- 业务权威在 `backend/`：FastAPI Router → Service → Repository → SQLAlchemy / SQLite，连接器和 Python LangGraph 由服务层/Worker 调用。Electron 不导入旧 TS Core/Storage/Agent，不用业务 IPC 代理 HTTP。
- Main 只提供窗口/托盘/通知/文件选择、后端配置和客户端会话能力。Preload 保持 6 个固定原生方法，严格 sender/参数校验；Renderer 不接触 bootstrap、短会话或第三方凭据原文。
- Python 使用 `pyproject.toml` 与 `uv.lock`；前端使用 pnpm workspace 与锁文件。修改 Pydantic 契约后运行 `pnpm api:generate`，同时维护客户端 Zod 运行时验证；不得手改生成类型来掩盖协议漂移。
- 结构使用 Alembic 新 revision；旧 Drizzle 迁移保持只读历史。导入旧库必须先一致性备份、写独立目标、核验数量/关键字段/外键，不删除源库。safeStorage 密文保留归档但需要重新授权。
- 服务端 SecretStore 使用外部私有文件密钥环，Fernet/MultiFernet；不得硬编码密钥或使用明文降级。保护并备份数据与密钥；当前是单所有者，不宣称多用户隔离或公网生产可用。
- 同步任务、重试时间、Agent 进度与业务事件持久化；Worker 租约与 fence 阻止过期写入，数据/cursor/成功状态/变化事件同事务。不可用 BackgroundTasks 取代可恢复队列，不将 SSE 当唯一任务状态。
- 用户已授权清理旧 TS 业务包、业务 IPC/safeStorage 和重复归档；原实现与学习注释在 Git 基线 `898328d`。工作区不再运行旧 79 项实现测试，以当前客户端/Python/E2E 验证为准。旧 SQL 在 `backend/tests/fixtures/legacy/` 原样保留并校验哈希，导入器、旧历史 DTO 和真实数据不可随旧代码一起删除。
- 质量命令：`pnpm lint`（含 Ruff）、`pnpm typecheck`、`pnpm test`（含 Pytest）、`pnpm build`；网络/任务改动运行 `node scripts/backend-smoke.mjs`；桌面改动验证开发/E2E/测试包。Docker 必须区分配置检查、镜像构建和实际容器业务验证。
- 新约定见 `docs/ARCHITECTURE.md`、`docs/REFACTOR_PLAN.md`、`docs/REFACTOR_REPORT.md`；旧 Phase 报告是历史结果，不等于当前版本已验证。

## Agent 首轮基础设施补充（2026-11-14）

- 用户已确认 `docs/AGENT_INFRA_PLAN.md` 的推荐范围：结构化账户偏好、受限上下文、两个本地只读工具的 Mock 闭环。实现与验证见 `docs/AGENT_INFRA_REPORT.md`；不自动推进真实模型、持久 checkpoint、复杂 RAG、多 Agent 或写工具。
- 记忆使用账户范围与 revision CAS；首次执行冻结上下文，恢复沿用已捕获版本。删除偏好只影响未来运行，不宣称清除历史审计和备份。
- RunScope 由 Worker 构造，工具参数不能指定账户或扩大权限；上下文不得包含凭据、SecretStore 或全量 Services。每次提交验证租约/fence，超时的线程读取保留资源名额至结束，晚到结果丢弃。
- Alembic head 为 `0002_agent_foundations`，既有数据库通过 init 增量升级，不删库、不改 0001；当前新增表和步骤恢复不等于 LangGraph 持久 checkpoint。

## 单页对话客户端补充（2026-11-14）

- 用户已确认 `docs/CHAT_CLIENT_PLAN.md` 方案 A，移除五个业务页面，客户端只保留单页对话与小型连接设置。Python 业务、历史、Agent DTO 和数据保留，当前界面不调用业务分析或工具。
- 回复为明确标注的本地 Mock，ChatTransport 可替换；不接入真实模型、会话持久化或自动长期记忆。窗口重启清空聊天，停止/新对话使用 abort + requestId 拒绝晚到结果。
- 对话使用纯文本，输入法组词不能误发送；保留六方法桥接和原安全策略。当前实际测试范围见 `docs/CHAT_CLIENT_REPORT.md`，不得用旧业务页面 E2E 描述当前 UI。

## 多厂商模型聊天补充（2026-11-16）

- 用户已确认 `docs/MODEL_PROVIDERS_PLAN.md`：九家文本协议预设、服务端隐藏 Key CLI/加密与客户端选择，当前实现/操作见 `docs/MODEL_PROVIDERS_REPORT.md` / `docs/MODEL_SETUP.md`。优先于上一节仅 Mock 的限制；不自动扩展流式、持久会话、模型工具、真实账户或多 Agent。
- Key 仅由 Python 模型作用域解密，复用 SecretStore/Fernet；模型 JSON 与目录不能包含 Key/密文/秘密引用，Renderer 无 Key 输入或读取。改厂商/目标地址必须重新录入授权，禁止前端传任意 URL/header/system。
- 真实聊天失败不得伪装 Mock，默认不自动重试/换厂商。非流式请求受预算/取消/并发约束，不冒充持久 Agent 队列；取消不保证厂商不计费，协议/本地替身测试不等于九家真实账户已实测。

## 客户端凭据加密提交补充（2026-11-16，当前生效）

- 用户已确认 `docs/SECRET_SUBMISSION_PLAN.md`，允许在专用 Renderer 密码表单手动输入模型 Key 并加密上传，优先于此前“Renderer 无 Key 输入”的限制。例外只限输入/加密的短暂生命周期；不得读取/回填服务端已有 Key，或进入聊天、Agent、全局状态、Query、存储、IPC、日志。bootstrap 和短会话仍只由 Main 管理。
- 复用边界是 `SecretInput / secret-submission.ts / SecretSubmissionService / 静态 purpose handler`，当前仅注册 `model.api_key`、已有启用 profile；禁止任意秘密引用/路径/URL/用途。新用途需要明确契约、授权和测试，不以通用模块推定全部凭据权限。
- 传输固定 JWE RSA-OAEP-256/A256GCM，远程仍要求 HTTPS；Web Crypto 不可用禁止明文降级，CLI 保留。专用表单提交/关闭立即清空，缓冲尽力清理，不宣称 JS 内存绝对擦除或能抵御恶意前端。
- 挑战/传输私钥仅单 API 进程内存，120 秒、4/会话、64/进程、两个解密任务，原子消费且不自动重试；取消后保留名额直到工作结束。不能据此宣称多实例/公网生产安全或网络 Exactly-once。
- Alembic head 为 `0003_secret_submission_state`，持久内容 revision/删除墓碑，HTTP/CLI 更新删除与 SecretStore 同事务并 CAS，拿到写锁后重新检查会话/过期；Fernet 轮换不改内容版本。0001/0002 与旧 SQL 不动，升级已有数据库先备份再 init，旧版本进程先停。
- 文件范围、实际命令与边界见 `docs/SECRET_SUBMISSION_REPORT.md` / ADR 0007，操作见 `docs/MODEL_SETUP.md`。当前仍无已保存凭据读取 API、客户端任意模型配置、真实账户连接器或自动模型工具。

## 客户端自助模型配置补充（2026-11-16，当前生效）

- 用户已确认 `docs/CLIENT_MODEL_SETUP_PLAN.md`：客户端选择九家厂商、填写模型 ID/可选名称，添加后录入 Key，全程无需手改 JSON。优先于此前仅服务端配置 profile 的限制；不扩大为任意端点/headers/system 或秘密引用。
- Alembic head 为 `0004_client_model_profiles`。SQLite 模型目录是权威数据，旧 JSON 仅首次事务导入（缺失文件也记录为空目录已完成），保留 ID/目标/预算/默认值/旧 Key，原文件不改不删，后续不能用编辑 JSON/重启覆盖客户端配置。
- Chat/凭据 handler/CLI 统一读取持久目录，创建后即时可用；普通配置接口只收 provider/model/label，Key 仍独立 JWE 提交与服务端加密。创建配置和保存 Key 是两步，不能宣称跨请求原子或网络 Exactly-once。
- 新建使用固定厂商端点和 2048 默认预算，最多 32 个配置含禁用项；首次不提供配置编辑/删除、任意 URL、实时厂商模型发现或自动付费测试。模型 ID 由用户填写，不猜测最新模型名。
- 本次升级需停旧进程、备份并 init 后重启一次；以后通过客户端添加不需重启。完整范围/命令/限制见 `docs/CLIENT_MODEL_SETUP_REPORT.md` / ADR 0008，操作见 MODEL_SETUP.md。正常用户数据升级不应混入自动测试；不自动提交/推送。


## Session Agent 核心补充（2026-11-16，当前优先）

- 用户明确确认 `docs/SESSION_AGENT_REFACTOR_PLAN.md` 全部范围，已移除账户/余额/同步/事件/旧偏好/旧分析运行代码和 API 能力；旧表、0001–0004、原始 SQL、已有数据与模型 Key 保留。历史 Phase/首轮 Agent/窗口清空约定不覆盖本轮授权。
- 当前 head `0005_session_agent_core`。活动核心为持久 ConversationSession/类型化 Message/AssistantRun/Context/Tool/Reviewer/Decision，复用 LangGraph 和 lease/fence Worker；认证 client_sessions 独立。
- 客户端连接后 Mock 与真实模型均走持久 Session；重启恢复、新对话/切换只切换展示，显式停止才取消。离线演示不自动上传/合并。模型/Key 配置保留原协议。
- 仅允许时间与有界 Decimal 计算工具；auto_readonly/ask/disabled，人工不能越过白名单或改参数。审核 revision/摘要/到期与唯一 ToolResult 均须验证；所有结果写回检查当前 scope/lease/fence。
- 真实模型四协议续接包含必要私有推理/签名，仅在服务端加密并绑定 Session/Run/round/profile；不进入公开 DTO/普通日志。未知 in-flight 请求中断而不自动重发付费调用。当前为持久步骤恢复，不是完整 LangGraph checkpoint。
- 代码/测试与运行边界见 `docs/SESSION_AGENT_REFACTOR_REPORT.md`、ADR0009；后续不自动扩展真实账户、RAG、长期记忆、多 Agent、MCP 或写工具。


## AgentPool、自审查与隔离Bash补充（2026-11-16，当前优先）

- 用户已明确确认 `docs/AGENT_POOL_BASH_PLAN.md`：有界Run执行池、模型工具自审查和隔离Docker Bash。优先于之前仅两个只读工具的范围，未授权宿主shell/任意挂载/网络或多角色Agent。
- head0006，0001–0005保持。Session的bashEnabled默认false；auto_sandbox须显式开启，auto_readonly不自动放行Bash，ask人工，disabled无工具。旧冻结策略1仍只读，新策略2复核。
- AgentPool管理资源生命周期，队列/lease/fence仍持久；未确认清理的执行保留名额。Bash每调用独立非root/无外网容器，Session卷保留文件，不传Key/认证/宿主挂载，禁止宿主回落。
- 自审查为独立无工具模型请求、严格有界JSON；Decision区分policy/model/mock/human，规则或模型deny不能由人工覆盖。主模型/审核续接purpose分离并加密，不公开隐藏推理。
- Bash写作用不采用只读自动恢复；持久意图后create/校验/start，未知结果中断不重播，未知工作区下次人工确认。旧Worker关闭只清理自己的执行，不能清理新Worker或其他应用资源。
- 首轮支持宿主Python Worker + 本机Docker；原Compose不自动挂Docker socket。无通用卷总容量硬配额，卷需独立备份/迁移；不宣称恶意多租户代码安全或副作用回滚。
- 质量另执行 `TAVO_TEST_DOCKER=1 backend/.venv/bin/python -m pytest -q backend/tests/test_bash_sandbox.py`、`node scripts/bash-smoke.mjs`，桌面/E2E/包内Bash用TAVO_TEST_DOCKER=1。默认测试跳过真实Docker组，不能据此宣称容器已测。操作见BASH_TOOL.md，文件/实际结果见AGENT_POOL_BASH_REPORT.md。


## Bash默认启用调整（2026-11-16，当前优先）

- 用户明确要求默认启用Bash并去掉客户端选择开关。新Session省略bashEnabled时默认true，纯文本省略时false；后端TAVO_BASH_ENABLED默认1，管理员显式0仍可关闭。
- 会话创建与Docker就绪分离：Docker不可用不阻断普通聊天，实际工具调用仍失败并明确说明，禁止宿主回落。历史Session/冻结Context不自动改变权限，无新增迁移。
- 原有规则、自审查、人工确认和未知执行限制保留；本次默认可用不等于默认auto_sandbox。操作见BASH_TOOL.md，本次文件/验证见BASH_DEFAULT_REPORT.md。

## Bash启动准备（2026-11-16，当前优先）

- 用户已确认`docs/BASH_STARTUP_PLAN.md`：Python后端启动前检查Docker，默认镜像缺失自动可信构建、运行并清理短命预热容器，成功才监听/领取任务。覆盖前文Docker不可用不阻断启动的规则；Docker不可达应中文提示并非零退出，不自动启动Docker服务。
- `prepare()`用于启动，`probe()`仍只读探测；复用同一容器隔离参数，预热无Session/宿主挂载，实际工具每调用独立容器。自定义镜像缺失不替换，失败/取消也要清理资源。无新迁移，历史权限/审核/数据不变。
- `init`、模型Key管理和密钥轮换不依赖Docker。管理员显式`TAVO_BASH_ENABLED=0`跳过准备；当前Compose明确关闭Bash，不加宿主socket。真实测试使用独立镜像标签和无效socket，不删除默认镜像或停止用户Docker。
- 本次实际文件、命令、结果和限制见`docs/BASH_STARTUP_REPORT.md`；普通测试外，真实组执行`TAVO_TEST_DOCKER=1 backend/.venv/bin/python -m pytest -q backend/tests/test_bash_startup_docker.py backend/tests/test_bash_sandbox.py`。
- 用户随后要求启动进度与完成提醒：固定四阶段、长步骤每5秒等待提示，不转发Docker原始日志或伪造百分比；区分镜像复用、沙箱完成、初始化完成和实际HTTP监听。最新验证见`docs/BASH_STARTUP_PROGRESS_REPORT.md`。

## Bash执行策略3（2026-11-17，当前优先）

- 用户已完整确认`docs/BASH_EXECUTION_POLICY_PLAN.md`，暂停后于11月17日明确要求继续；增量实现统一配置、分级审核、SandboxManager、脚本/版本化工作区、UDS受控出口及API凭据和受控Python依赖。优先于旧禁网不可配置/3000字节/每调用容器/强制模型审核约定，不推定任意宿主执行或无限联网。
- head0007，0001–0006和历史数据保留。新策略3冻结配置，当前更严格限制仍生效；旧Context/审批不重新解释。默认网络none，目标/操作/依赖白名单由服务端明确配置，不自动授权真实付费API。
- 执行容器始终network=none、非root、无宿主路径/socket/凭据；仅可信代理出网，通过只读UDS进行应用级请求。不能用普通bridge或HTTP_PROXY声称受控联网；Fake-IP/内网/元数据拒绝，不允许脚本选择DNS或CONNECT隧道。
- Session文件跨Run保留，Run容器/环境/授权隔离；工作副本大范围diff批准后才提交版本，不重跑脚本。取消/超时回收完整容器与子进程，未知文件/外部结果不自动重放，清理未知保留容量。
- API凭据使用专用sandbox.api_key用途绑定目标和版本，不复用模型Key为任意测试授权，不进入脚本/LLM/日志。敏感HTTP审批展示具体请求，批准只执行该请求；未知请求意图不自动重发。网络配额预留/核销，无法核验时保留额度。
- 默认32768字节、60/300秒、CPU1/最高2、内存512/最高1024MiB、PID128、单文件64MiB、每数据库1执行Sandbox。基础Python依赖仅装专用镜像，额外依赖仅批准的哈希wheel；不修改宿主venv。工作区软监测不称为卷硬配额。
- 配置/操作见`docs/BASH_EXECUTION_POLICY_GUIDE.md`，文件和实际验证见`docs/BASH_EXECUTION_POLICY_REPORT.md`。后续不自动扩展透明SDK联网、任意包、宿主文件操作、RAG或多Agent。

## 命令权限与默认公网补充（2026-11-17）

- 用户明确确认full_access/auto_review/request_approval三个档位仅控制Bash，新会话默认full_access；其他工具免额外审批，仍保留认证、scope/fence、校验、配额和隔离。自动执行不伪造human授权。
- 用户明确确认任意公网HTTPS经UDS出口自动访问，外部POST/PUT/PATCH/DELETE免独立请求审批。默认proxy/allow_public_https=true/require_write_approval=false；宿主、内网、元数据、直连和隧道隔离保留。带保存Token仍须单独绑定别名，不把模型Key或已有凭据授予任意URL。
- 新Context策略4；旧模式、策略1/2/3与等待中的批准不重新解释，无结构变更不新增迁移。当前指南为docs/BASH_EXECUTION_POLICY_GUIDE.md，设计/报告为docs/PERMISSION_NETWORK_PLAN.md和docs/PERMISSION_NETWORK_REPORT.md。
- 正文不主动罗列内部工具/函数/框架，保留真实失败和独立审计；不用正文正则过滤掩盖问题，不声称提示词能绝对阻止模型透露实现。

## 沙箱与宿主命令补充（2026-11-17，用户已确认）

- 用户已确认HOST_SANDBOX_TOOLS_PLAN.md：新会话默认sandbox与host_bash双入口，由Agent选择，共享三档审批，无用户启用/位置开关；旧bash始终Docker语义。0008迁移旧Session为sandbox_only，策略1–4/待批准不获取宿主权限，策略5新会话host_and_sandbox。
- 宿主是Python后端当前OS用户/机器；原生Mac才是Mac，普通容器不等于Mac。没有容器级秘密隔离、不自动sudo、不注入Tavo凭据，不据此执行未授权真实项目部署/远程修改。输出防护不声称能对任意恶意脚本强隔离。
- 原生宿主进程监督与恢复不凭旧PID误杀，不自动重放未知写入；未知清理保留容量，主动逃离组不作强回收承诺。宿主不冒充具备Docker资源配额或文件回滚。
- 配置见[host]与TAVO_HOST_BASH_ENABLED；指南HOST_BASH_GUIDE.md、决策ADR/0014-host-and-sandbox-execution.md、验收HOST_SANDBOX_TOOLS_REPORT.md。真实宿主测试TAVO_TEST_HOST=1只使用临时项目和合成凭据；Linux/Windows验证不得用macOS或Docker替代。

## 客户端主题与 Markdown 补充（2026-11-17，用户已确认）

- 用户已确认 CHAT_APPEARANCE_MARKDOWN_PLAN.md：默认跟随系统、浅/深主题、左右聊天气泡；模型/三档命令权限移入设置，底部仅长条输入和发送/停止，保留历史、Key与审核。覆盖旧“纯文本/仅深色”客户端约定，后端与head0008不变。
- 仅助手正常回复通过 react-markdown/remark-gfm 转 React 节点；skipHtml，不执行代码、不自动加载远程图片。用户输入和工具原始内容仍是文本；不得为排版放宽生产CSP、原生六方法或Renderer隔离。
- 主题公开偏好独立保存在客户端，切换不重置会话/草稿/权限。外链只在用户点击后经Renderer/Main验证HTTP/HTTPS并交系统浏览器，仍拒绝Electron子窗口、页面导航和危险协议。
- 设计决策ADR/0015-chat-appearance-safe-markdown.md，实际文件/命令/结果见CHAT_APPEARANCE_MARKDOWN_REPORT.md；桌面改动必须区分开发、桌面E2E与未签名包内测试。

## 模型有界重试补充（2026-11-17，用户已确认）

- 用户确认MODEL_RETRY_OUTPUT_PLAN.md，覆盖旧“默认不自动重试”：每逻辑模型请求初次＋最多3次重试，文本/Agent/自审查共用，受原60/30秒及Run总预算约束；不重放用户消息、已执行工具或Worker未知在途请求。
- DeepSeek当前支持模型显式保留思考，空正文且length时逐级增额，最大16384；不改存储初始预算/8192约束，不允许客户端提交覆盖值，不偷偷关闭思考或换厂商。其他厂商参数不扩大。
- ModelRetryPolicy由Settings注入，MODEL_OUTPUT_LIMIT/MODEL_EMPTY_RESPONSE与公共TS错误同步。只日志白名单阶段/次数/状态/用量，不能输出Key、正文、思考、会话ID或原始异常。
- 用户明确拒绝原对话外发，已授权的实际验收仅限固定合成内容；本轮两次真实逻辑调用及60秒超时限制见MODEL_RETRY_OUTPUT_REPORT.md。未知请求恢复、工具权限、数据/迁移不变，head0008。
