# Tavo Python Backend

独立 FastAPI API 与持久 Worker。Python 3.12，依赖精确锁定在 `pyproject.toml` / `uv.lock`。无需 Electron、Node 或真实服务凭据。

## 初始化及运行

在本目录执行：

```bash
uv sync --frozen --python 3.12
uv run python -m app.cli init
uv run python -m app.cli serve
```

`init` 执行 Alembic 至 head，并创建私有密钥环、客户端认证文件。已有文件保持不变，数据库已有密文但密钥丢失时拒绝生成新密钥冒充可用。默认数据目录 `.local`，忽略 Git；使用 `--data-dir /绝对路径` 或 `TAVO_DATA_DIR` 指定。

`serve` 默认 `127.0.0.1:8000`，内含同进程 Scheduler，生命周期不依赖桌面。开发调试可直接 `uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`，需先 init。

分离 API 和 Worker：

```bash
uv run python -m app.cli serve --no-worker
# 另一终端，相同数据目录和密钥配置
uv run python -m app.cli worker
```

租约决定唯一活动调度者，多启动的 Worker 会待命；不支持用 SQLite 在多台主机之间组集群。正常退出释放租约；异常退出后租约到期，由新领导者恢复 running 任务。任务成功提交必须携带当前 owner / fence，过期执行者不能落库。

## 认证与环境变量

所有业务接口，包括 health、SSE 和文档，都要求认证。客户端读取 `client.token` 并 POST `/api/v1/auth/session`，Bearer 携带 bootstrap 值，返回 15 分钟会话。数据库只存会话摘要；后续业务请求 Bearer 携带会话。旋转 bootstrap 文件会撤销已有会话。

`/docs` 可使用浏览器 Basic 登录，用户名 `owner`、密码为 bootstrap 文件内容；随后使用 HttpOnly / SameSite=Strict Cookie。不要把令牌写进 URL、源码、命令参数、普通日志或截图。只在可信本机回环使用 HTTP。

`.env.example` 只作为变量清单，应用不自动加载 dotenv。默认 CORS 精确允许 `tavo://app`、localhost / 127.0.0.1 的 5173 开发源；Host 也有白名单。开发端口变化必须显式配置，不能使用 `*` 规避限制。单请求正文限制 32 KiB。

远程客户端支持 HTTPS URL，但本服务未实现生产 TLS 终止、账户体系、权限角色和租户隔离。`--container-local` 仅允许开发容器内部绑定 `0.0.0.0`；Compose 映射到宿主 `127.0.0.1`。不得直接暴露公网。

## 凭据与迁移

```bash
uv run python -m app.cli rotate-keys
uv run python -m app.cli import-legacy --source '/绝对路径/tavo.sqlite'
```

密钥环独立文件而非数据库字段；第三方凭据使用 Fernet 认证加密。初始化/轮换由数据库写锁串行化，轮换先原子发布含新旧密钥的环，再在事务内重加密，保留旧密钥支持恢复。当前没有自动裁剪旧密钥，不能手动删除仍可能用于备份的密钥。

SecretStore 只对活动、已授权且 connectorId 匹配的账户提供解密能力。凭据以 SecretStr 限定在连接器调用上下文中，Agent 仅收到允许的账户事件字段。Mock 不发网络请求，LangSmith tracing 显式关闭。

导入不会原地修改源库；先备份，再校验数量、外键、ID 和事件关键字段。仅支持既有 Phase 0/1。保留旧密文但无法跨 OS 解密，需要重新连接。导入不允许覆盖非空账户库或再次导入。

## 测试与接口

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python -m app.export_openapi
```

在仓库根目录还可运行 `node scripts/backend-smoke.mjs`，启动真正的独立监听进程，验证 REST、SSE 回放、Agent 和重启。该脚本不需要桌面。API 见 [API.md](../docs/API.md)，架构见 [ARCHITECTURE.md](../docs/ARCHITECTURE.md)。

## Docker Compose

仓库根目录：

```bash
docker compose up --build -d
# 正常停止，不删除数据或密钥卷
docker compose down
```

可通过 TAVO_HTTP_PORT 改变宿主映射端口，默认 8000。init 一次性迁移 → API / Worker 共享数据库卷和独立密钥卷。进程 UID 10001，根文件系统只读、无额外 Linux capabilities；临时目录单独 tmpfs。API 不带内嵌 Worker，另有 worker 服务。healthcheck 只检查监听；业务健康接口仍需认证。

桌面连接需要从 `backend-keys` 卷安全复制 `client.token` 到当前用户私有文件（0600），通过原生文件选择器配置；不要 `cat` 到共享终端。卷备份需同时保护数据和密钥，`down -v` 会删卷，正常流程不要使用。

本轮已实际通过 Linux arm64 Compose 业务烟雾测试：仓库根目录 `pnpm test:docker` 使用唯一项目、随机回环端口和临时卷，验证后仅清理自己的资源。首次镜像拉取遇到本机 DNS 故障，通过进程级解析与临时受限 PyPI 代理完成构建；未修改系统或 Docker 配置。详细证据及未验证平台见迁移报告。
