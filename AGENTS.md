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
