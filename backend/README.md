# Tavo Python Backend

Python 3.12 / FastAPI / SQLAlchemy / SQLite / LangGraph。当前提供持久会话、模型与 Key、时间/计算、沙箱与真实宿主 Bash、共享三档审核和独立 Worker；账户/余额/同步运行功能已移除，旧表保留。

## 本机运行

先打开 Docker Desktop / Docker Engine。`serve`、独立 `worker` 和直接 ASGI 启动默认都先检查 Docker、自动构建缺失的默认镜像、运行并清理预热容器；成功后才接受请求/领取任务。Docker不可达会提示并非零退出，`--no-worker`不跳过检查。`init`、Key管理和轮换不依赖Docker；不自动启动系统Docker服务。

启动会立即输出四阶段进度，耗时步骤每5秒显示等待秒数；缓存镜像明确提示复用。沙箱完成、后端初始化完成分别提示，CLI保留`Uvicorn running on http://…`作为实际监听提示；独立worker输出启动完成。进度不受warning日志级别过滤，不转发Docker原始输出；见 [进度报告](../docs/BASH_STARTUP_PROGRESS_REPORT.md)。

```bash
uv sync --frozen --python 3.12
uv run python -m app.cli init
uv run python -m app.cli serve
```

`init` 增量升级到 `0008_host_bash`，保留0001–0007、模型和凭据。升级已有数据前停止原后端、备份数据目录与私有密钥文件；不要删库。默认数据 `.local/tavo.sqlite`，密钥环 `.local/master.keys` 与认证 `.local/client.token` 不输出内容。已有密文但密钥丢失时拒绝生成替代密钥。旧 models.json 仍仅首次事务导入，新增模型请用客户端设置。

`serve` 监听 `127.0.0.1:8000` 并内嵌 Worker；也可分开：

```bash
uv run python -m app.cli serve --no-worker
```

另一终端（相同目录/密钥）：

```bash
uv run python -m app.cli worker
```

Worker 单领导租约/fence，失租拒绝写回。审核等待持久化并释放名额；模型请求未知时 interrupted，不自动重复付费。后台不依赖桌面窗口。SQLite 不支持跨主机 Worker 集群。

## 认证与凭据

所有业务与文档需认证。Main 使用 bootstrap 换15分钟认证会话；Renderer 不读取认证原文。`/docs` 浏览器 Basic 用户名 `owner`，密码为私有 client.token 文件值，随后 HttpOnly/SameSite Cookie；不要把内容打印、上传或提交。

`.env.example` 是配置清单，不自动加载 dotenv。CORS/Host 精确限制，单请求32 KiB。本机回环开发允许 HTTP，远程需 HTTPS；当前无多用户隔离/生产 TLS 服务，不应直接暴露公网。

客户端可选九家厂商、自填模型 ID、添加配置、在密码框输入 Key 后 JWE 加密提交。Key 最终仅服务端 Fernet 保存；CLI 隐藏录入和 `rotate-keys` 保留。密钥轮换也重加密私有模型续接，保留旧密钥用于备份恢复，无明文降级。详见 [模型指南](../docs/MODEL_SETUP.md)。

`import-legacy` 已退役，会明确 FEATURE_REMOVED；旧表和原始 SQL 仍保留，当前运行不再导入或执行账户任务。

## 验证和部署

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python -m app.export_openapi
```

根目录 `node scripts/backend-smoke.mjs` 验证独立进程 Session/审核/工具/重启，`node scripts/model-smoke.mjs` 验证合成 Key + 本地 HTTP 模型协议与两轮工具闭环。不会自动调用真实厂商。接口见 [API.md](../docs/API.md)，架构与预算/恢复限制见 [ARCHITECTURE.md](../docs/ARCHITECTURE.md)。

根目录 `docker compose up --build -d` 启动 init/API/独立Worker，共享私有数据和密钥卷，宿主端口仅回环。镜像非root，无Node；`pnpm test:docker` 使用独立测试项目，验证队列暂停、Worker/API重启、审核、工具与加密续接后清理自己的卷。正式数据卷不得照搬测试清理命令。当前容器业务验证仍不等于公网生产部署。


AgentPool/独立模型自审查/隔离Bash已加入。Bash和新对话默认启用，客户端没有启用开关。管理员可设`TAVO_BASH_ENABLED=0`关闭Bash及预热；原Compose明确使用此配置，不挂宿主Docker socket。默认镜像自动构建限300秒，自定义镜像缺失则提示并退出。操作与限制见 [Bash指南](../docs/BASH_TOOL.md)，最新验证见 [启动报告](../docs/BASH_STARTUP_REPORT.md)。

执行策略3的统一TOML、32768字节命令、60/300秒超时、Run容器与工作区版本、UDS受控出口、专用API凭据和wheel安装见 [指南](../docs/BASH_EXECUTION_POLICY_GUIDE.md)。新会话默认full_access和受控公网HTTPS（proxy、allow_public_https=true、require_write_approval=false），HTTP写方法免独立审批。另两档auto_review/request_approval只控制Bash；旧会话/冻结Run继续旧策略。管理员可配置none禁网或allow_public_https=false仅允许别名。凭据仍须单独绑定目标，普通requests/curl无直连网络。详见[本轮报告](../docs/PERMISSION_NETWORK_REPORT.md)。0007需停止旧后端、备份并init后启动；本轮没有自动升级正常用户库。

默认网络解析为`cloudflare-doh`，仅用于可信出口，避免系统/代理Fake-IP导致公网域名被误挡；不会更改系统DNS。已有TOML显式配置`dns_mode="system"`仍生效；需要调整自管配置并重启后端、新建对话。不要靠放行198.18/2001:2等非公网网段解决。详见[DNS排障报告](../docs/DNS_DEFAULT_REPORT.md)。

策略5双入口默认在新会话启用，宿主以本后端当前OS用户运行，主配置[host]与TAVO_HOST_BASH_ENABLED独立于Docker开关。原生Mac与容器内后端的执行范围不同；Host没有Docker隔离/配额或文件回滚。升级0008后新建对话，详见[指南](../docs/HOST_BASH_GUIDE.md)及[报告](../docs/HOST_SANDBOX_TOOLS_REPORT.md)。
