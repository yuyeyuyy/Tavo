# Tavo

Local-first Personal Agent：Electron / React 客户端 + 独立 Python / FastAPI 服务端。当前核心是**持久对话、受限 Context、真实模型工具循环与可审计审核**。账户/余额/同步功能已移除，旧数据库记录保留历史；不含 RAG、自动长期记忆或多 Agent。

客户端使用左右聊天气泡，支持安全 Markdown 与跟随系统／浅色／深色（默认跟随系统），底部仅保留长条输入和发送／停止。模型与命令权限放在右上角设置。支持会话列表、重启恢复、停止/重试、时间与计算工具记录，以及完全访问权限/帮我审批/请求审批三个命令权限档位（共同控制沙箱与宿主机命令；旧会话保留原策略）。设置中直接选择厂商、填写模型 ID、添加配置，再在密码框输入 Key 加密上传；Key 在服务端加密保存，CLI 隐藏录入仍可用。保存 Key 不调用厂商。

[模型重试指南](docs/MODEL_RETRY_GUIDE.md) · [客户端界面验收](docs/CHAT_APPEARANCE_MARKDOWN_REPORT.md) · [模型与 Key 指南](docs/MODEL_SETUP.md) · [架构](docs/ARCHITECTURE.md) · [API](docs/API.md) · [双执行工具验收](docs/HOST_SANDBOX_TOOLS_REPORT.md) · [学习记录](docs/LEARNING_LOG.md)

新会话默认同时提供沙箱与真实宿主机 Bash，由 Agent 按任务选择；三档权限共同控制两种命令。宿主是 Python 后端当前用户所在机器，无容器隔离。升级到0008后新建对话使用，旧会话不自动扩大权限，见[操作指南](docs/HOST_BASH_GUIDE.md)。

## 本机启动

需要 Python3.12 / uv / Node.js24 LTS / pnpm11，并先打开 Docker Desktop / Docker Engine。在仓库根目录安装前端依赖：

```bash
pnpm install --frozen-lockfile
```

后端终端：

```bash
cd backend
uv sync --frozen --python 3.12
uv run python -m app.cli init
uv run python -m app.cli serve
```

另一个终端，仓库根目录：

```bash
pnpm dev
```

已有安装升级前停止旧后端/客户端，备份 `backend/.local` 和独立密钥文件，再执行 `pnpm backend:init` 增量升级至 **0007_sandbox_execution_policy**，不要删库。模型配置、Key 和旧表保留；本轮没有自动修改你的正常数据目录。

后端启动前检查 Docker，缺少默认 Bash 镜像会自动构建，再运行并清理预热容器，成功后才监听。Docker 未启动会提示并退出；首次构建可能需要下载基础镜像，默认最多等待300秒。`init` 不依赖 Docker，启动行为见 [启动报告](docs/BASH_STARTUP_REPORT.md)。

终端显示四阶段进度与每5秒等待提示。已有镜像会明确提示复用，预热结束显示沙箱准备完成；随后`Uvicorn running on http://…`表示HTTP监听就绪。预热容器完成后自动删除，实际Bash调用时再创建独立容器；不需要在Docker Desktop中一直看到一个运行中的预热容器。

后端默认回环 `127.0.0.1:8000`，开发界面5173。初始化不打印私密认证文件。桌面 Main 读取 `backend/.local/client.token` 换短会话，Renderer 不接触认证原文。更换开发端口须同时配置后端精确 CORS。

[认证 API 文档](http://127.0.0.1:8000/docs)：浏览器用户名 `owner`，密码为私有 client.token 文件值；请在私有编辑器读取，不上传、不提交或打印到共享日志。后端与桌面独立，窗口退出后后台仍可执行已入队 Run；后端停止时不会继续运行。

## 体验闭环

1. 保持 Mock，发送“计算 12 / 3”，查看最终结果4和工具记录；“现在的 UTC 时间”使用时钟工具。
2. 新对话选择“工具逐次确认”，发送“计算 8 * 7”，批准后得到56；拒绝会记录 denied 结果，模型不能声称已执行。
3. 等待确认时关闭客户端和后端，再重启，恢复原会话并继续审核。停止按钮持久取消，重新生成不重复插入用户消息。
4. 设置中选厂商、填写控制台支持的模型 ID，添加后输入 Key。设置 → 对话 → 模型中选择该模型，关闭设置并发送消息才调用厂商；工具协议支持取决于实际模型。
5. 右上角会话列表可切回历史。新对话或切换模型/命令权限创建新会话，不自动取消旧运行；使用停止按钮取消。
6. 后端不可用时，新建 Mock 对话可使用明确标注的离线演示。离线历史不会自动上传/合并。

九家预设对应四类模型协议，自动化只使用合成 Key、本地 HTTP 和协议替身，不表示九家真实模型/权限均实测。当前非 token 流式；Context 只选本轮窗口，完整历史仍存盘。

## 验证命令

| 命令 | 作用 |
| --- | --- |
| `pnpm dev` / `pnpm dev:backend` | 桌面 / 已初始化后端 |
| `pnpm backend:init` | 同步锁定依赖、增量迁移、初始化私有文件 |
| `pnpm api:generate` | Pydantic → OpenAPI → TS，Zod 另行运行时验证 |
| `pnpm lint` / `pnpm typecheck` / `pnpm test` | ESLint/Ruff、严格TS、Vitest/Pytest |
| `node scripts/backend-smoke.mjs` | 独立进程认证、Session、幂等、审核/工具/重启 |
| `node scripts/model-smoke.mjs` | 合成 Key、本地 HTTP 文本与两轮 Agent 工具协议 |
| `pnpm build` / `pnpm test:dev` / `pnpm test:e2e` | 构建、真实开发窗口、桌面集成 |
| `pnpm package:dir` / `pnpm test:packaged` | 本机 ARM64 未签名测试包及包内回归 |
| `docker compose config --quiet` / `docker compose build` / `pnpm test:docker` | 配置检查 / 镜像构建 / 隔离容器业务验证，三者含义不同 |

E2E 可设 `TAVO_BROWSER_CHANNEL=chrome` 使用已安装 Chrome。测试均用隔离临时数据，不调用真实厂商。Docker 正常启动可用 `docker compose up --build -d`，不需要 Electron；见 [后端说明](backend/README.md)。

## 目录和边界

```text
apps/desktop/src/main/       原生能力、连接配置与客户端认证
apps/desktop/src/preload/    六个受限方法，无业务 IPC
apps/desktop/src/renderer/   会话 UI、公开 DTO 校验、设置与专用 Key 提交
backend/app/api/             FastAPI Router / Pydantic
backend/app/services/        会话、模型/凭据服务与依赖装配
backend/app/repositories/    SQLite 事务、幂等、分页、审核和结果提交
backend/app/agent/           Assistant / Context / Tool / Reviewer / LangGraph
backend/app/llm/             四类模型协议、配置与受限 Key 访问
backend/app/workers/         持久 Run 队列、租约与 fence
backend/app/credentials/     通用 JWE 挑战/用途处理/版本CAS
backend/app/models/          活动 ORM 与只读历史元数据 legacy.py
backend/migrations/          增量 Alembic，旧迁移不改
backend/tests/fixtures/legacy/ 原样旧 SQL 与哈希
```

旧账户 API 返回410，旧导入 CLI 退役，不会删除旧数据或执行旧账户队列。原 TS 实现在 Git基线898328d；本轮移除源代码另做不含私有数据的临时备份。历史报告记录旧行为，以当前架构/本轮报告为准。

数据业务字段（含聊天）未整体加密；凭据和模型私有续接使用 Fernet，私有密钥环须单独保护和备份。当前单所有者、本机SQLite，不宣称公网生产/多租户隔离或外部Exactly-once。未签名 `.app` 是测试产物。

分支为 `feat/agent-foundations`，本次未自动提交、推送或合并。协作约定见 [AGENTS.md](AGENTS.md) 与 [Git工作流](docs/GIT_WORKFLOW.md)。


AgentPool/独立模型自审查/隔离Bash已加入。Bash和新对话默认启用，客户端没有启用开关。宿主Python后端启动时准备默认镜像和预热容器；Docker不可用会阻止后端启动。原Compose显式关闭Bash，不挂宿主socket。操作、模式、额外模型费用、工作卷备份与限制见 [Bash指南](docs/BASH_TOOL.md)；最新启动验收见 [启动报告](docs/BASH_STARTUP_REPORT.md)。

Bash执行策略4沿用长脚本分块、Python基础环境、Run容器复用和版本化Session工作区。新会话默认完全访问；三档仅控制Bash审批，其他工具合法调用自动执行。默认经专用出口访问任意公网HTTPS，POST/PUT/PATCH/DELETE无需独立审批；已保存Token仍只注入绑定目标，额外依赖保持哈希锁定wheel。配置见 [执行指南](docs/BASH_EXECUTION_POLICY_GUIDE.md)，本轮验证见 [权限与网络报告](docs/PERMISSION_NETWORK_REPORT.md)。
