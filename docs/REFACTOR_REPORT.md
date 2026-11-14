# 客户端与 Python 服务端迁移报告

> 本报告记录重构完成时的交付与测试基线。用户随后授权清理旧代码；当前文件树、依赖及测试计数见 LEGACY_CLEANUP.md，原 79 项旧实现测试不再参与当前验证。

日期：2026-11-13。范围为既有 Mock 功能迁移；无真实第三方连接器、模型调用、复杂 RAG 或多 Agent。

## 交付结果

系统已从 React → 业务 IPC → Electron Main / TypeScript Core 改为 React → REST/SSE → 独立 FastAPI / Worker。已实际完成从桌面添加账户、发起同步、后端落库、读取任务和事件、Python LangGraph 分析、退出桌面继续同步、再次启动读取历史的完整闭环。

本机无图形依赖的后端进程与 Linux arm64 Docker Compose 均已执行真实业务验证。Docker 镜像不包含 Node / Electron；API 和 Worker 为独立进程，不依赖桌面存活。

仓库初始没有 Git。先保存已验证源码基线 `898328d`，再建立 `refactor/client-server-python`。当前重构修改留在该分支工作区供审查，未推送或发布。用户 `.idea/` 保持原样，未纳入基线。

## 文件变化与设计理由

| 路径 | 实际变化与理由 |
| --- | --- |
| `backend/pyproject.toml` / `uv.lock` | Python 依赖独立管理，精确锁版本；服务端可不安装 TS 工程运行 |
| `backend/app/main.py`、`api/` | create_app/lifespan、Depends、Pydantic DTO、REST、受认证的本地 Swagger、统一固定错误 |
| `backend/app/services/` | 迁移账户状态、设置、任务入队、事件、Agent、旧库导入；业务与 HTTP 解耦 |
| `backend/app/repositories/`、`models/`、`migrations/` | SQLAlchemy/Alembic；约束、事务、原子提交和独立 schema 版本 |
| `backend/app/core/` | 私有外部 Fernet 密钥环、轮换、受限解密、bootstrap/短会话、Host/Origin/CORS/正文边界 |
| `backend/app/connectors/`、`workers/`、`agent/` | Protocol/Registry/Mock、持久队列/租约/fencing/恢复、Python 两节点 StateGraph |
| `apps/desktop/src/main/`、`preload/` | 移除活动业务依赖；仅窗口/托盘/通知/原生选择/配置/会话，6 项受限原生 API |
| `apps/desktop/src/renderer/api/`、`store.ts`、`src.tsx` | 独立 Fetch Client、生成 TS 类型、Query 缓存、SSE、离线提示/重连、Agent 进度，复用页面样式 |
| `apps/desktop/package.json`、构建配置 | 删除桌面旧业务包/SQLite 原生运行时依赖；本地 Electron 分发目录用于未签名测试包 |
| `packages/shared/` | 保留历史 DTO 兼容，补充 Agent 事件与客户端连接错误；后端不导入它 |
| `legacy/`、历史架构/学习文档 | 保留旧 Main/Preload 的学习注释和原实现，供对照与回滚 |
| `backend/tests/`、`tests/client-api.test.ts`、`tests/e2e/`、`scripts/` | Python/API/迁移/图/安全测试与真实 REST/SSE/开发/包内/Linux 验证 |
| `compose.yaml`、`backend/Dockerfile`、`.env.example` | API/Worker 无桌面开发部署，回环映射、非 root、私有数据/密钥卷，无硬编码秘密 |
| README、AGENTS、ARCHITECTURE、API、LEARNING、ADR、日志 | 更新当前职责、启动、迁移、风险、实际结果及 3 个自学练习 |

## 功能与数据迁移

保留：多个账户与连接器分离、连接/过期/错误/断开状态、手动/定时同步、增量 cursor/revision、同账户互斥、默认全局并发 2、幂等变化事件、持久任务/重试时间、最多 3 次尝试、临时/永久错误分类、取消与晚到响应保护、崩溃后租约恢复、旧历史和分析入口。

连续添加账户曾发现异步清空覆盖新输入，已改为提交期间禁止编辑并只清理本次提交值；重新打包验证。密钥初始化/轮换先取得数据库写锁再读取和发布密钥环，防止多个进程互相覆盖新密钥，并增加并发测试。

新增迁移必需的协议能力：客户端认证、普通请求超时、事件分页/过滤、SSE 重放、短会话到期与重连、Agent 持久 run/progress、OpenAPI 类型生成。没有将业务复制回 Renderer/Main。

实际旧库为 macOS `~/Library/Application Support/Tavo/tavo.sqlite`，检查结果：Phase 0，连接器 1、账户 0、事件 0、同步任务 0、迁移元记录 1。已使用 SQLite backup API 备份并导入独立 `backend/.local/tavo.sqlite`，验证外键与关键字段。目标连接 0、数据 0、任务 0、业务事件 0、旧密文 0、迁移归档 1。不能把此结果描述为迁移了真实账户数据。

源库未修改；备份位于私有 `.local/` 与 `.local/backups/`，不进入 Git。先前只由本轮冒烟测试生成的后端库另存 `pre-import-smoke.sqlite`，未覆盖来源数据。Phase 0/1 含模拟记录的迁移完整性另由测试夹具验证，保留 ID、余额、cursor、事件 sequence/correlationId、旧分析入口及密文字节；失败整体回滚，重复导入拒绝。

兼容差异：

- Electron safeStorage 密文不可直接供 Python/Linux 解密；保留 `legacy_secrets` 原字节，账户标记 disconnected / credentialConfigured=false，要求重新授权。
- 旧 pending/running 同步不直接续跑，因为旧授权不可用；转 cancelled，原始记录/状态保留归档，并报告取消数量。
- `cleanup_pending`、`delete_requested` 等旧宿主标记保存在原连接 JSON 归档，不作为新后端可用授权依据。
- `startMinimized` 属于每个桌面客户端的原生偏好，不自动把服务器旧配置应用到任意客户端；旧设置仍可归档/查询兼容字段。
- 新数据没有自动反向写回旧库。回滚需保留新数据库与密钥，使用原源码/原库；不得用删除新库或覆盖旧库充当回滚。

## 实际验证

所有自动化业务用 Mock 数据和隔离目录，未请求真实平台。运行机器为 macOS ARM64；Docker Linux arm64。

| 实际命令 | 结果与证据范围 |
| --- | --- |
| 重构前 `pnpm typecheck` / `pnpm lint` / `pnpm test` / `pnpm build` | 基线通过，旧 Vitest 79 项 |
| `pnpm api:generate` | 成功生成 OpenAPI 与 TS 类型，无后端启动或私密文件读取副作用 |
| `pnpm lint` | ESLint、Ruff 与 Python 格式检查通过 |
| `pnpm typecheck` | 严格 TS 检查通过 |
| `pnpm test` | Vitest 85 项通过：旧 79 + 新客户端/配置 6；最终 Pytest 33 项通过 |
| 后续 `pnpm test:backend` | 新增 Registry、非法旧库与并发密钥轮换用例后，最终 Pytest 33 项通过 |
| `node scripts/backend-smoke.mjs` | 真正独立 FastAPI：认证、REST、持久同步、SSE 回放/重连、Python Agent、进程重启历史通过 |
| `TAVO_BROWSER_CHANNEL=chrome NODE_OPTIONS='--require=/private/tmp/tavo-dns.cjs' pnpm exec playwright test --timeout 90000 --reporter=line` | 构建模式真实桌面 3 场景通过，含浏览器 /docs、客户端同步/分析/离线恢复/重启、旧库导入、桌面退出后同步 |
| `pnpm test:dev` | Vite 真窗口添加账户、HTTP 同步、Python 分析通过；测试使用随机开发端口及精确 CORS |
| `pnpm build` / `pnpm package:dir` | 三个桌面进程产物与 ARM64 `.app` 构建成功；未签名/公证/发布 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` | 本地真实 `.app` 3 场景通过；还检查 Renderer 无 Node/旧业务桥，生产 CSP 限定脚本与后端来源 |
| `docker compose config --quiet` | Compose 结构与依赖校验通过 |
| `docker compose build --build-arg HTTPS_PROXY=http://host.docker.internal:18591 --build-arg HTTP_PROXY=http://host.docker.internal:18591` | Linux 镜像实际构建成功，Python 3.12.16，锁定依赖安装；代理仅用于本次网络修复 |
| `node scripts/docker-smoke.mjs` | Linux arm64 / UID 10001 / 无 Node；认证 REST、独立 Worker 停止时任务持久 pending、重启后成功、加密/密钥权限、SSE、Python Agent、API 重启保留数据通过 |
| `git diff --check` | 通过，无空白错误；未自动提交本轮重构或推送 |

Python 测试覆盖 Health、CRUD、分页/能力声明、DTO/正文/来源校验、401 拒绝、会话失效与撤销、凭据不进入响应/日志/数据库明文字段、轮换和文件权限、并发轮换不丢密钥、同账户/全局并发、幂等游标、暂时/永久失败、超时上限、最终事件失败事务回滚、租约接管/fencing、Agent 进度与等价图、checkpoint 扩展、Phase 0/1 导入与回滚、非法 Mock/analysis/settings/JSON/版本拒绝、SSE 回放/到期、Registry 生命周期。

环境修复说明：本机默认 DNS 导致 Python/PyPI、镜像仓库和 Electron 重复下载失败。使用进程级 DNS 解析、只允许 PyPI 站点的临时 CONNECT 代理；官方镜像经 HTTPS 拉取并逐层校验 SHA256 后导入本地缓存，再完成真实构建。没有更改系统 DNS/Docker 配置，临时代理已停止，临时镜像下载文件已清理。Playwright 自带 Chromium 下载超时，最终使用已安装 Chrome 完成浏览器文档验证。以上失败没有被记作通过。

## 运行与回滚

日常两个终端：后端 `cd backend && uv sync --frozen --python 3.12 && uv run python -m app.cli init && uv run python -m app.cli serve`；客户端仓库根目录 `pnpm dev`。后端默认只绑定回环，桌面不负责启停后端。

无桌面运行：`serve --no-worker` + `worker` 共享本地目录；或 `docker compose up --build -d`。Compose 只绑定宿主回环，数据/密钥分别为卷；不要把端口直接映射公网。正常停止不删除卷。测试脚本使用唯一项目和临时卷，验证后仅清理自己的资源。

回滚：先保存当前未提交改动与新后端数据/密钥，停止新 API/Worker，再使用基线 `898328d` 和原 Electron 数据库。旧库未被改写；新业务数据不会自动出现在旧架构。禁止使用 reset/clean 删除用户文件。

## 残留风险与后续工作

- 单所有者身份模型已实现；生产 HTTPS 终止、完整身份/角色/租户隔离、认证限流与审计尚未实现，不能宣称安全公网 SaaS。
- Fernet 保护凭据而非全部业务库；外部密钥文件需独立保护和备份，没有 KMS、自动旧键裁剪或恢复向导。密钥与数据库一起泄漏时无法保持秘密。删除有效连接不等于擦除旧库、备份和历史 OS 密文，归档保留/清理策略尚未实现。
- 同步 SQLite 查询与写锁适合当前小规模；未做吞吐、长时间稳定性或大量 SSE 连接评测。多进程租约仅用于同机本地磁盘，不是跨机器分布式队列。
- SSE 依赖持久事件回放，但没有持久消费者确认、保留/清理策略、连接配额；结果需以 REST 查询为准。
- Graph 的 checkpoint 参数与 MemorySaver 测试存在；持久 checkpoint、真实工具权限、Memory、HITL、模型流式 token 尚未实现。
- Mock 的读操作、取消和故障语义已验证；真实 OAuth、厂商权限/限流/格式与凭据输入流程仍未开发。
- macOS ARM64 与 Linux arm64 容器已验证；Windows、Intel macOS、Linux amd64、不同宿主密钥/文件权限、干净机器安装未实际验证。
- `.app` 未签名、公证或发布；默认图标仍为 Electron，托盘/通知/文件选择基本能力已接入，原生弹窗的完整人工体验尚未逐项验收。
- 保留旧 TS 实现以便回归与学习，形成暂时维护成本；清理应单独评审，避免把历史包重新引入客户端业务。

后续建议一次完成一个可验证的小任务：先完成学习练习，再讨论一个只读官方连接器的授权和契约测试，或单独补强远程生产安全。

## 面试与学习线索

可据实际代码讨论：Controller/Service/Repository、依赖注入与生命周期、Pydantic/编译期类型、REST 状态码与异步资源、SQLAlchemy session/ACID/唯一约束、Schema migration 与 ETL、重试/幂等/租约/fencing、SSE 回放、StateGraph/checkpoint、客户端认证/授权/CORS/TLS、密钥轮换与最小权限。详细代码位置和三个练习见 `LEARNING_NOTES.md`，不要把未实现能力写成项目成绩。
