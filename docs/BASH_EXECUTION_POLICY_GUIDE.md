# Bash与Python开发沙箱指南

2026-11-17，当前新会话为策略5双入口，升级与宿主边界见[HOST_BASH_GUIDE.md](HOST_BASH_GUIDE.md)。下文策略4沙箱行为保持兼容，旧冻结策略1–4不升级宿主权限。设计见[BASH_EXECUTION_POLICY_PLAN.md](BASH_EXECUTION_POLICY_PLAN.md)，策略3历史验收见[BASH_EXECUTION_POLICY_REPORT.md](BASH_EXECUTION_POLICY_REPORT.md)，本轮见[PERMISSION_NETWORK_REPORT.md](PERMISSION_NETWORK_REPORT.md)。

## 升级与启动

停止旧后端，备份数据库、独立密钥及Docker会话工作卷，再在仓库根目录执行：

```bash
pnpm backend:init
pnpm dev:backend
```

另一终端`pnpm dev`。当前新增0008，0001–0007和旧数据保留。没有自动升级正常用户库。先打开Docker Desktop/Engine；后端自动构建/更新`tavo-bash:dev-v3`、预热并确认清理，终端显示阶段进度。Docker不可达即退出，不自动打开系统Docker服务。

新策略用于新捕获的Bash Run，旧冻结策略/批准不重解释。`init`、模型/API Key管理及轮换不依赖Docker。`TAVO_BASH_ENABLED=0`仍是管理员开关；原Compose无Docker CLI/socket，明确关闭Bash，不扩大宿主权限。

## 执行与资源

新会话默认“完全访问权限”（full_access），在隔离工作区内自动执行Bash，不经过模型或人工审批；“帮我审批”（auto_review）对每条Bash进行独立模型复核，必要时询问用户；“请求审批”（request_approval）每条Bash先询问用户，大范围版本提交还会显示具体diff。只有Bash受这三档控制：计算、时间、受控文件写入等合法调用免额外审批，但仍有参数、归属、资源和网络边界。全访问/代审可自动提交大范围工作区版本。模型代审可能产生额外模型请求与费用。

切换档位开始新会话。旧auto_readonly/ask/disabled/auto_sandbox会话及已冻结Run沿用原行为，可在历史会话中看到兼容档位，不会因默认值变化获得新权限。

Mock可测试真实执行：

```text
bash: python -c 'from pathlib import Path; Path("note.txt").write_text("hello"); print(Path("note.txt").read_text())'
```

模型是Mock，文件操作是真实的。镜像预装Python3.12、pip、pytest、requests、httpx、pydantic；属于沙箱，不共享FastAPI宿主venv。独立Linux环境不等于Mac终端，Git等非基础程序不承诺预装。

| 配置项 | 默认/上限 |
| --- | --- |
| command_max_bytes | 32768 UTF-8字节，超长明确拒绝，不截断 |
| default_timeout_seconds / max_timeout_seconds | 60 / 300秒 |
| cpu / max_cpu | 1 / 2核 |
| memory_mib / max_memory_mib | 512 / 1024MiB |
| pids_limit / max_file_bytes | 128 / 67108864字节 |
| max_sandboxes | 每后端数据库1个执行Sandbox，持久claim防止双Worker重复占位 |
| max_rounds / max_tool_calls / max_active_seconds | 新Bash Run为12 / 24 / 1200，人工等待不计 |
| workspace_max_bytes / workspace_max_files | 监测512MiB / 10000项 |
| retained_versions | 3个已确认版本及必要的Run基线 |
| capture_bytes / public_output_bytes | 捕获64KiB，公开stdout/stderr各约2KiB，标注truncated |

统一默认入口`backend/sandbox/defaults.toml`。复制[示例配置](../backend/sandbox.example.toml)到管理员本地文件，选择绝对路径：

```bash
TAVO_SANDBOX_CONFIG=/absolute/path/sandbox.toml pnpm dev:backend
```

`.env.example`不自动加载，TOML中不放Key。非法配置拒绝启动。模型只能选择允许的resourceProfile（default/expanded）和上限内timeoutSeconds，不能指定镜像、挂载、Docker参数、任意环境或更高上限。恢复时同时受冻结权限及当前更严格配置约束。排队与执行窗口分开，工具预算不会因复用容器重置。

## 文件与任务生命周期

每Run可复用容器，Run结束、等待确认、空闲、失租、超时或取消后回收。非root、只读根目录、cap-drop ALL、no-new-privileges、默认seccomp；不挂宿主目录、Docker socket、密钥或宿主网络。容器内残留子进程会触发整容器回收，不仅kill一个shell进程。

Session文件跨Run保留，不同Session隔离；新独立任务使用“新对话”。每Run使用工作副本，执行停止后校验并提交到版本化会话工作区；大量删除/覆盖绑定diff摘要等待批准，批准只提交这个版本，不重跑脚本，拒绝保留原已确认文件。执行容器看不到历史备份卷。

`workspace_write`用于长脚本：相对path、content、offset、expectedSha256，每块默认16KiB。写新文件可省略摘要，覆盖/追加已有文件必须匹配摘要；offset=0替换，非零必须等于当前字节数。返回新sha256和offset。绝对路径、..、symlink/hardlink和特殊文件被受控写入拒绝。超长脚本分块保存，再`python script.py`；每块写入也计入预算。

取消不保证撤销已发生的外部作用。结果未知不自动重放，未知工作副本保留待核验，暂不提供客户端导出/恢复/删除入口；不要用通配符清理用户卷。未确认容器清理时保留执行容量。

Docker命名卷没有跨平台统一总容量硬配额。依赖临时区有容量和解压总量限制；工作区是周期监测及提交前检查，存在监测间隔。单文件64MiB和软监测不能冒充卷硬配额，仍需管理员管理Docker数据盘。本实现不是恶意多租户托管环境。

## 受控联网

新会话默认`network.mode="proxy"`、`allow_public_https=true`、`require_write_approval=false`，可访问任意公网HTTPS443，不要求逐域名建别名；POST/PUT/PATCH/DELETE免独立请求审批。管理员可设`mode="none"`禁网，或`allow_public_https=false`仅允许已配置目标；收紧配置立即约束后续调用，扩大配置不会突破已冻结Run权限。旧会话保持原目标/写方法审批。仅固定代码的出口代理出网；执行容器**始终network=none**，经只读Unix socket卷请求代理。不提供普通bridge直连、自定义DNS、CONNECT/WebSocket/TCP隧道或任意HTTP代理。原始requests.get/curl/pip直连仍不通。

使用镜像内的客户端，直接传公网URL：

```python
import tavo_http
reply = tavo_http.request("https://pypi.org/pypi/six/1.17.0/json")
reply.raise_for_status()
print(reply.json()["info"]["version"])
```

公网URL不携带已保存Token；需要Token时仍使用管理员按精确域名、HTTPS443、路径、方法和api_test能力绑定的别名。额外wheel来源按package_download能力绑定。解析全部A/AAAA，拒绝内网、loopback、元数据、保留/映射地址和保护网段；连接核验后的IP并保留域名TLS证书/SNI。重定向逐跳重查，跨源不带认证；不接受脚本自定Host/认证/代理头。默认30秒/请求、2MiB响应、30次/分钟、100次/Run。关闭代理前封闭请求并核销计数，无法证明最终用量保留预留额度，回收容器不能重置额度。

Tavo默认`dns_mode="cloudflare-doh"`：可信代理通过固定公网IP并验证Cloudflare域名TLS获得公网记录，避免依赖系统Fake-IP解析；脚本不能指定端点，不修改macOS或代理软件的DNS设置。关闭代理全局模式不一定关闭Fake-IP DNS，容器DNS结果也可能与宿主不同。管理员可显式改为`dns_mode="system"`使用自管DNS；若返回198.18.x.x、2001:2::等非公网地址仍会拒绝，绝不放行这些网段。DoH不可达时请求失败，不静默切换解析方式。修改解析配置后须重启后端并发起新Run，已冻结Run不重解释。

## API Token与审批

`credential=true`的api_test目标在客户端“沙箱 API 凭据”选择并加密提交；或在后端终端隐藏输入：

```bash
cd backend
uv run python -m app.cli api-key --target my-api
```

专用`sandbox.api_key`绑定用途、目标与版本；聊天模型Key不自动授予API权限。Token只由可信代理向授权目标注入，不进入脚本环境/文件、LLM或普通日志，无已有Token读取API。修改目标能力后需重新录入绑定凭据。

新会话所有允许的HTTP方法直接执行，不建立虚假的human批准记录。旧策略或管理员显式启用require_write_approval时，写请求仍返回APPROVAL_REQUIRED，界面显示具体目标/方法/路径/请求体摘要；批准仅执行这个请求，不重跑父脚本，使用`tavo_http.result(request_id)`读结果。普通自动HTTP请求不提供Exactly-once保证：脚本应自行处理幂等键与业务确认，未知外部效果不可假定失败或自动重发。开启公网也不会注入任意已保存凭据，亦不会开放内网或宿主权限。

## 受控安装依赖

额外依赖必须配置精确wheel URL、版本、SHA256和全部传递依赖，来源属于package_download目标：

```bash
python -m tavo_pip install six==1.17.0
```

只安装批准wheel，不运行sdist构建，不接收git/editable/extra-index-url。验证哈希和归档路径/大小/数量，在有界临时区使用pip `--no-index --no-deps`安装，再写任务`.deps`，跨调用/Run复用。宿主venv、系统site-packages和其他Session不修改。锁定依赖仍是供应链信任边界。

## 错误和审计

结果包含success、exitCode、stdout、stderr、errorCode、errorMessage、durationMs、timedOut、sandboxId、truncated。执行前拒绝exitCode=null，非零退出保留真实值；Agent可读取异常修复后重试，相同失败摘要重复达上限才停止。

| 类型 | 含义 |
| --- | --- |
| COMMAND_TOO_LONG / INVALID_INPUT | 长度 / 参数校验 |
| POLICY_DENIED / MODEL_REVIEW_DENIED | 规则越界 / 必要模型复核拒绝 |
| APPROVAL_REQUIRED | 具体外部请求或文件版本尚待批准 |
| NETWORK_DENIED | 目标、方法、地址、解析或通道不允许 |
| TIMEOUT / RESOURCE_LIMIT | 超时 / 内存、PID、文件、工作区限制 |
| SANDBOX_ERROR / EXECUTION_ERROR | 容器状态、启动、清理异常 / 命令本身失败 |
| CANCELLED / TOOL_EXECUTION_UNKNOWN | 已取消 / 结果未知，不自动重放 |

普通审计只记录阶段、代码和ToolCall ID。命令与受限输出有意持久化到会话业务历史，业务内容未整体加密；不要把凭据放入命令/脚本。只读模式和完整权限边界以本指南及本轮验收报告为准。

## 对话表达

共享系统提示要求正文直接描述任务进展、结果及必要失败原因，不主动罗列内部工具、函数名和执行框架；Mock同样遵循。审计卡片仍保留真实调用。提示约束不等于模型绝对不会透露实现细节，也不会通过删除正文字符串破坏用户代码或伪装成功。
