# Tavo

Local-first Personal Agent，现为 **Electron / React 客户端 + 独立 Python / FastAPI 服务端**。目前只有只读 Mock 连接器和确定性 LangGraph 分析，无真实账户、模型请求或 RAG。

账户、凭据、同步、事件、Agent 与数据库全部由后端管理。桌面退出后，单独运行的后端继续执行任务。现有页面和旧历史记录保留。

## 本机启动

环境：Python 3.12、uv、Node.js 24 LTS、pnpm 11。两个终端分别启动：

```bash
# 仓库根目录，首次安装
pnpm install --frozen-lockfile
cd backend
uv sync --frozen --python 3.12
uv run python -m app.cli init
uv run python -m app.cli serve
```

```bash
# 另一个终端，仓库根目录
pnpm dev
```

后端默认只监听 `127.0.0.1:8000`，数据位于 `backend/.local/tavo.sqlite`。初始化自动生成私有密钥环与客户端凭据文件，不输出内容。开发端口默认 5173 且不自动换端口；若已被占用，请停止自己的旧开发实例，或显式设置 TAVO_DEV_PORT 并同步配置后端 CORS。开发客户端自动使用该凭据文件，认证材料只留在 Main；Renderer 发 REST 请求并订阅 SSE。

[API 文档](http://127.0.0.1:8000/docs) 也要求认证。浏览器用户名 `owner`，密码使用本地 `backend/.local/client.token` 文件中的值；请在自己的私有编辑器读取，不上传、不提交、不打印到共享终端。Swagger 静态资源本地托管。

已打包客户端可在顶部「配置后端地址」填写地址，再通过原生对话框选择此服务专用的私有凭据文件。切换地址不会自动复用旧凭据。远程地址必须为 HTTPS；完整生产 HTTPS、多用户授权和租户隔离尚未实现。

## 演示

1. 在「账户连接」添加两个 Mock 账户，点击同步。
2. 修改模拟余额后再次同步；重复相同数据不会产生重复变化事件。
3. 选择首次网络错误或限流，查看持久化重试与最终结果。失败保留上次成功数据。
4. 对 `data.changed` 运行模拟分析，在 Agent 页查看 Python LangGraph 的进度和结构化结果。
5. 启用定时同步，退出整个 Electron，再启动桌面读取历史。后端必须单独保持运行。
6. 停止后端，桌面显示连接失败及最近缓存；启动后点击「重新连接后端」。

## 命令

| 命令 | 作用 |
| --- | --- |
| `pnpm dev` / `pnpm dev:backend` | 分别启动桌面 / 已初始化的后端 |
| `pnpm backend:init` | 安装 Python 依赖、执行 Alembic、初始化密钥 |
| `pnpm api:generate` | Pydantic → OpenAPI → TypeScript 类型 |
| `pnpm typecheck` | 严格 TypeScript 检查 |
| `pnpm lint` | ESLint、Ruff 检查与格式检查 |
| `pnpm test` | 客户端 API/契约测试、Python 测试 |
| `node scripts/backend-smoke.mjs` | 实际独立进程 REST / SSE / Agent / 重启验证 |
| `pnpm build` | 构建桌面 Main、Preload、Renderer |
| `pnpm test:dev` | 开发窗口完整业务烟雾测试 |
| `pnpm test:e2e` | 真实桌面、浏览器文档、迁移与退出后同步 |
| `pnpm package:dir` / `pnpm test:packaged` | 本机 ARM64 未签名测试包 / 包内测试 |
| `pnpm test:docker` | 已构建镜像的隔离 Linux REST/Worker/SSE/Agent 验证 |
| `docker compose up --build -d` | 无 Electron 的 API + 独立 Worker，端口仅绑定本机 |

E2E 默认使用 Playwright Chromium（`pnpm exec playwright install chromium`）；也可设置 `TAVO_BROWSER_CHANNEL=chrome` 使用已安装的 Chrome。测试使用临时目录和模拟数据。旧 TS 业务实现与原生 SQLite 检查脚本已清理；当前桌面不加载 SQLite 原生模块。

## 目录与边界

```text
apps/desktop/src/main/       窗口、托盘、通知、连接配置、客户端会话
apps/desktop/src/preload/    6 个原生能力；没有业务 IPC
apps/desktop/src/renderer/   React、API Client、Query 缓存、SSE
backend/app/api/             REST Router、Pydantic DTO、认证依赖
backend/app/services/        Account / Event / Agent / Migration / 应用装配
backend/app/repositories/    数据访问、事务、原子同步提交
backend/app/models/          SQLAlchemy ORM
backend/app/connectors/      Protocol、Registry、Mock
backend/app/workers/         持久化调度、租约、fencing、重试恢复
backend/app/agent/           Python StateGraph
backend/app/core/            配置、错误、HTTP 安全、SecretStore 与客户端认证
backend/migrations/         Alembic 版本化迁移
packages/shared/            客户端运行时 Zod 契约与历史兼容类型
backend/tests/fixtures/legacy/  原样保留的旧 SQL 与哈希，用于迁移兼容测试
```

旧 Core/Storage/Connector/JS Agent 已从工作区删除，原实现及学习注释可在 Git 基线 `898328d` 查看。清理边界和验证见 [旧代码清理记录](docs/LEGACY_CLEANUP.md)。

后端可独立安装运行，无 pnpm、Electron 或 TS 包运行时依赖。桌面业务请求不经过 Main IPC；Main 仅为可信窗口、精确后端来源注入短期客户端会话。

## 迁移与安全

旧库仍位于 macOS `~/Library/Application Support/Tavo/tavo.sqlite`，不会自动覆盖或删除。显式导入到已初始化且没有账户的后端：

```bash
cd backend
uv run python -m app.cli import-legacy --source '/完整路径/旧版/tavo.sqlite'
```

脚本先使用 SQLite backup API 建立 0600 一致性备份，再在事务中导入并验证数量、关联和关键字段。旧 OS 密文保留归档，账户需要重新连接；未完成旧同步任务取消并记录原状态。详细兼容差异及回滚见 [迁移报告](docs/REFACTOR_REPORT.md)。

后端使用 cryptography Fernet/MultiFernet，数据库只保存第三方凭据密文。密钥环独立于数据库，文件 0600、目录 0700；必须单独保护和备份。密钥轮换命令在 [后端说明](backend/README.md)。数据库业务字段未整体加密，本阶段仍是单所有者模型，不能作为多用户公网服务部署。

同步数据、游标、成功状态和变化事件同事务提交；唯一约束、账户互斥和 Worker 租约降低重复执行风险。保证本地提交幂等，不承诺外部请求 Exactly-once。

阅读：[架构](docs/ARCHITECTURE.md) · [学习与 3 个练习](docs/LEARNING_NOTES.md) · [API](docs/API.md) · [迁移报告与实际验收](docs/REFACTOR_REPORT.md) · [协作规则](AGENTS.md)。

## 分支协作

`main` 保存稳定基线，`dev` 用于集成，当前 Python 重构与旧代码清理位于 `feat/client-server-python`。请查看功能分支获取本轮最新代码；尚未自动合并到 dev/main。详见 [Git 工作流](docs/GIT_WORKFLOW.md)。
