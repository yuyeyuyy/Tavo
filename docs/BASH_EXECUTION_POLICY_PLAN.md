# Bash执行策略增量优化方案

日期：2026-11-16，2026-11-17完成。状态：用户已确认完整方案，暂停后按“继续”恢复并完成实施/验收。实际结果见 [BASH_EXECUTION_POLICY_REPORT.md](BASH_EXECUTION_POLICY_REPORT.md)，操作见[指南](BASH_EXECUTION_POLICY_GUIDE.md)。暂停记录保留历史，不再代表未完成。

## 1. 当前架构与实际问题

现有调用链为Session API → ConversationService/Repository → 持久Run队列 → Scheduler/AgentPool → LangGraph的model/review/tools节点 → BashRunner → Docker CLI。SQLite lease/fence阻止失租提交；执行意图先持久化，未知写作用不自动重播。SecretStore/JWE和四种模型协议保持独立。

| 现状 | 问题/需要联动修改 |
| --- | --- |
| BashRunner每调用创建/删除容器，Session命名卷保留文件，asyncio.Lock串行 | 已有文件持久化和串行基础，但没有任务容器复用、完整SandboxManager和跨进程沙箱容量记录 |
| BashArguments命令3000字节/字符、超时10–30秒 | 长脚本/依赖安装容易失败，参数写死 |
| 容器1CPU/256MiB/PID64/单文件16MiB、无网络、只读根目录 | 宿主隔离应保留，资源改为配置，网络缺少真正受控出口 |
| llm/turns.py只解析4KiB工具参数，self_review输入上限24KiB | 只改BashArguments仍会丢弃长命令，或在审核前失败 |
| 单Run4模型轮次/8工具/180秒，Context64KiB | 单命令300秒无法真正生效，多次修复很快耗尽预算，需要仅对新策略Bash Run联动配置 |
| 所有合法调用每轮模型复核；提示词禁止联网；规则只检查模式/工具名/参数 | 普通脚本也会被模型误拒；没有可执行的网络授权边界或风险分级 |
| policy/model/human多种拒绝最终统一FORBIDDEN | 参数超限和安全拒绝混淆，Agent拿不到准确修复方向 |
| Python3.12-slim基础镜像 | pip存在，但pytest/requests/httpx/pydantic未预装；宿主依赖不应共享进去 |
| 只有model.api_key凭据用途 | 不能把现有模型Key交给脚本，也不能把模型凭据授权推定为任意API授权 |
| 客户端严格Zod限制工具数量、Context大小和runtime网络状态 | 需要同步公开契约与展示相关字段，不能只改后端导致客户端拒绝响应 |

没有现成的受控出口代理；现有HTTP中间件只保护入站来源/请求大小，不提供出站SSRF防护。现有HTTP请求体32KiB继续保护普通API，不为长脚本全局放宽；脚本写入采用分块工具。

## 2. 两种出口方式与选择

1. 隔离Docker内部网络连接独立出口代理：可用，但需要额外验证宿主网桥、DNS和路由隔离。普通bridge或单独设置HTTP_PROXY都不能作为安全边界。
2. **执行容器继续network=none，只通过只读命名卷中的Unix socket连接专用出口代理（推荐）**：脚本没有公网/内网TCP出口，不能通过自选DNS、IP直连、NO_PROXY或隧道绕过。只有受控代理具备出网连接和目的地校验。

已做真实Docker原型验证：两个临时容器均非root、network=none、只读根目录、无宿主挂载；服务端在命名卷创建AF_UNIX socket，客户端只读挂载同卷仍可通信。验证通过，临时容器/卷已清理。该实验只证明通道可行，**不等于网络策略、DNS防护或完整代理已经实现**。

## 3. 统一配置与执行预算

新增服务端私有数据之外的普通TOML配置入口，例如`backend/sandbox.example.toml`，由`TAVO_SANDBOX_CONFIG`选择；未指定使用代码随包携带的同一默认配置。使用Python3.12 tomllib和现有Pydantic校验，无dotenv隐式加载。配置只能由服务端管理员控制，不收模型传入的镜像/挂载/Docker参数/任意资源上限。不在配置放API Key。

| 参数 | 默认/边界 |
| --- | --- |
| command_max_bytes | 32768 UTF-8字节，先检查编码与NUL，不截断 |
| command_timeout_seconds / max_timeout_seconds | 60 / 300秒 |
| cpu / max_cpu | 1 / 2核 |
| memory_mib / max_memory_mib | 512 / 1024MiB |
| pids_limit / max_file_bytes | 128 / 67108864字节 |
| max_sandboxes | 1，启动预热也纳入，不靠每个进程各自的锁声称全局约束 |
| Run主模型轮数/工具预算/活动秒数 | 新策略Bash Run默认12 / 24 / 1200；等待人工不计入。普通只读/冻结旧Run保持旧规则 |
| Bash Run Context预算 | 有界、默认256KiB；工具参数传输预算独立于命令内容字节数，考虑JSON转义。普通工具继续4KiB参数预算 |
| 输出/磁盘/回收 | 捕获、公开输出、依赖下载/解压、工作区监测、空闲回收、启动构建均集中配置；不让模型自定 |

可定义default/expanded两个受限资源profile；Agent只能在管理员预先允许的profile中选择，不直接注入Docker参数。重用容器资源升级仍由Manager验证上限和审计。命令等待沙箱容量的排队时间单独记录，不提前消耗300秒执行窗口。

`ToolRegistry`基于注入配置生成准确JSON Schema；`llm/turns.py`对Bash及受控文件工具使用匹配的参数封装上限，保留无效原因，不再将过长参数简单丢为None。审核、Context和客户端Zod同步，避免隐藏4KiB/24KiB/64KiB上限继续拦截。

## 4. 执行与文件生命周期

抽取`SandboxManager`，保留BashRunner为兼容执行入口与现有意图记录。Manager负责持久容量claim、创建/复用/空闲回收/关闭/失租恢复；Agent不获得Docker CLI或socket。

- 容器以Run为归属，同Run的多次Bash调用可复用；Run结束、取消、超时、失租、OOM、后台进程残留或空闲TTL到期时删除容器。清理未确认保留容量，不启动第二个执行沙箱。
- 保留现有**Session工作区跨Run文件持久化**；不同Session隔离。每Run拥有独立执行状态和网络授权；新的独立任务使用新Session。不把一个容器的环境变量、进程、授权自动带给下个Run。
- 执行容器非root、只读根目录、cap-drop ALL、no-new-privileges、默认seccomp、无端口发布、无宿主PID/IPC/network模式、无宿主路径/Docker socket/凭据挂载。只有任务工作区及有界临时区可写；出口socket卷只读。
- 固定可信supervisor作为容器主进程，Docker init负责孤儿回收。每次exec记录意图、参数摘要、scope/fence后再运行。完成后检查残留子进程；有残留就回收整个容器，不把进程组kill误称可覆盖setsid/双fork。超时/取消必须终止整个容器cgroup并确认删除。
- 容器复用是优化，不以保留后台服务为目标；进程清理后文件保留。中断的文件/外部副作用不宣称回滚，不自动重放整个脚本。

任意Python的内部行为无法仅通过字符串规则证明安全。因此Workspace使用每Run工作副本/快照，保留上一次已确认版本：普通新增/修改在调用结束、无残留进程后受控提交；大量删除/大范围覆盖等风险变更绑定具体diff摘要和版本进入人工确认，**批准前不影响已有持久工作区**。拒绝保留原版本，不自动提交；用户批准也不能提交到Workspace外。若不能稳定获取diff或确认执行停止，失败关闭。Run结束后保留文件版本而清理执行容器；仅自动清理有明确归属和保留策略的临时副本，不删除原有卷/历史数据。

新增`workspace_write`工具：只接受工作区相对路径、有限UTF-8分块、偏移/版本/内容摘要；拒绝绝对路径、..、symlink/hardlink跳转和特殊文件，使用受控打开/原子替换处理竞态。长脚本先分块写入再`python script.py`/`bash script.sh`，不截断或擅自把被拒命令改为另一条执行。写入同样审核、审计、计入工具预算，不能变成绕过长度/授权的隐蔽通道。

## 5. 审核策略3与用户授权

冻结新Run的策略版本、资源配置摘要、可用能力；旧Context策略1/2、历史审批和数据不重新解释。

| 风险 | 规则和行为 |
| --- | --- |
| LOW | 可确定的小型本地只读命令/普通环境查询，通过完整参数、模式、Workspace和资源校验后直接执行，不再强制模型复核。仅识别严格语法子集；find -exec、管道/展开等不能因为首词在白名单就判LOW |
| MEDIUM | 一般Bash/Python、生成脚本、Workspace写入/测试、已授权的代理API或依赖安装。靠实际容器、文件提交和代理能力边界限制；auto_sandbox可自动执行。模型只在意图/副作用不明确时辅助复核，不能因出现Python/写文件/已授权网络就一概拒绝 |
| HIGH | 宿主凭据/系统、特权、Docker Engine、内部网络/元数据/绕过出口等越界操作规则拒绝且不可被人工覆盖。范围内敏感变更/外部副作用则绑定具体操作、目标、摘要、scope、有效期和revision，执行/提交前明确批准 |

`auto_readonly`保持兼容语义，不自动放行任意Bash；`ask`继续逐次确认；`disabled`不声明工具。常规开发使用现有auto_sandbox。风险分类可用语法信息作为提示，但不是防护边界：编码/脚本/子进程也不能获得未授予的网络、宿主路径或持久变更提交权限。

模型复核失败/格式异常不得放行需要复核的调用；LOW不受模型误判影响。模型deny作为`MODEL_REVIEW_DENIED`明确返回；可授权的意图不明确应转ask，不让模型创造新权限。人工拒绝、缺少授权和规则拒绝各有独立阶段与错误。

## 6. 受控网络出口

执行路径：`脚本/tavo_http → 只读AF_UNIX通道 → 独立EgressProxy → 已验证公网目标`。代理容器不是运行用户代码的Sandbox，不挂任务文件、宿主目录、Docker socket或整个服务端密钥环。代理控制通道不挂给脚本，配置/短期能力由可信Manager发送；代理和执行容器按Run创建授权、关闭后清理。

- 默认network.mode=none。只有受控出口全部实现、健康检查通过并存在管理员配置的目标/能力时才允许proxy模式；配置错误/代理失效不退回普通bridge直连。
- 目标配置按精确域名、HTTPS端口、方法、规范化路径和能力（public_read、api_test、package_download）授权。默认允许列表为空；PyPI规则作为显式示例，不自动授权任意域名或真实付费API。
- 严格解析URL，拒绝userinfo、IP字面量/编码IP、非常规端口/协议和不匹配的域名。解析全部A/AAAA后拒绝任何非公网、loopback、link-local、RFC1918、CGNAT、保留/组播、IPv4-mapped IPv6、元数据及管理员保护网段。
- **连接已验证的IP而非再次按域名解析**，保留原域名TLS SNI/证书验证和Host；禁用客户端环境代理与自动重定向。每个重定向重新做域名/方法/地址/能力检查，限制跳数，跨源不传认证；禁止CONNECT、Upgrade、WebSocket、任意TCP隧道和用户覆盖Host/认证/代理头。
- 只传允许的响应字段，按解压后流量限制请求/响应大小、总时限、并发、请求频率和Run配额，防止压缩炸弹。日志记录请求ID/目标别名/方法/脱敏路径、阶段/耗时/结果，不记录认证、敏感query/body或原始响应。
- 脚本使用轻量`tavo_http`/HTTPX受控Transport；不承诺未适配库的任意requests.get或curl能透明联网。直连仍被network=none阻断。必要的SDK可注入受控Transport/base_url，不实现任意CONNECT代理。
- 复用SecretStore与通用JWE框架增加**专用API凭据用途**，绑定目标/操作和revision；模型Key不自动作为API测试授权。只有可信代理可按已批准目标注入认证；脚本、工具结果、LLM、普通日志不读取Token。阻断响应中原始凭据回显，清理敏感响应头。管理界面仅复用现有专用密码表单增加相关目标用途，不新增凭据读取API。
- 外部写入/付费等敏感操作不能仅靠域名允许。代理按具体HTTP操作请求授权；未批准返回APPROVAL_REQUIRED并持久记录精确请求摘要。批准后只执行这个请求，**不重跑整段Bash脚本**；相同请求ID返回已保存结果，未知外部结果中断且不自动重发。

## 7. Python环境与受控安装

沙箱专用镜像固定Python3.12，构建时安装带锁定版本的pip/pytest/requests/httpx/pydantic与轻量受控客户端；不修改FastAPI宿主依赖。使用镜像版本/清单校验，旧`tavo-bash:local`即便存在也不能误报为新开发环境，必要时构建新版本镜像并保留旧镜像。

额外依赖只能从受控包代理安装：批准的包名、精确版本/哈希、来源和已解析的传递依赖；首轮仅wheel，不运行不受控sdist构建脚本，不使用extra-index-url/git/editable/任意URL。支持受控安装helper或绑定本地镜像入口的pip；修改PIP_INDEX_URL、代理/DNS也不能获得直连能力。

下载/解压分别有文件数、单文件、总字节和时限；依赖安装在有容量上限的临时区完成，验证包与体积后提交到任务的独立依赖目录，禁止写系统site-packages/宿主venv。基础依赖只构建一次，任务依赖在相同工作区保留。

Docker普通命名卷没有跨平台通用的总容量硬配额；不能把单文件64MiB称为磁盘配额。依赖下载/解压使用硬容量临时区与累计上限；工作区/快照另做有界文件数与总量监测并在超限时终止、拒绝提交。若部署要求整个卷严格硬配额，必须验证支持配额的存储后端，否则明确不可承诺，不静默降级。

## 8. 结构化结果、状态与兼容

每次Bash/文件/网络操作都记录阶段。BashResult保留已有exitCode/stdout/stderr/timedOut/truncated并增加：

```text
success, errorCode, errorMessage, durationMs, sandboxId
```

Python字段使用snake_case，公开JSON沿用现有camelCase约定。新策略所有终态含统一结果，包括执行前拒绝（exitCode=null、durationMs=0、sandboxId可空），不能把未执行解释为exitCode=0。`ToolResult.status/errorCode`继续兼容。

错误区分POLICY_DENIED、MODEL_REVIEW_DENIED、APPROVAL_REQUIRED、COMMAND_TOO_LONG、TIMEOUT、RESOURCE_LIMIT、NETWORK_DENIED、SANDBOX_ERROR、EXECUTION_ERROR、CANCELLED；保留旧码供历史读取。OOM/PID等使用cgroup/Docker状态与计数核验，不靠exitCode=137一律猜OOM；保留真实命令退出码。未知结果保留TOOL_EXECUTION_UNKNOWN，不误报可安全重试。

日志与审计明确parameter/policy/model/approval/sandbox/execution/network/cleanup阶段。工具结果和可恢复错误回到Agent Loop，修改后的脚本可继续执行；连续相同参数/脚本摘要的失败设置次数上限，不禁止修复后重试。每次Bash、受控写入和敏感网络操作计入预算，预算不因容器复用而重置。

新增Alembic0007，保留0001–0006：持久sandbox_instances容量/生命周期、网络请求/具体授权状态、必要的review风险/阶段/摘要字段，继续使用已有ToolCall/ToolResult/Decision而不另造通用Agent框架。迁移保留模型/Key/旧执行/Session卷，旧等待审批不升级权限。

## 9. 改动范围与分段验收

以下是同一授权任务内按依赖推进的可验证步骤，不代表把网络或生命周期留作未来功能：

1. 统一配置、长度/参数链路、错误结果和新策略冻结；新增契约后生成OpenAPI/TS并同步Zod。
2. SandboxManager、任务工作副本/变更提交、受控写文件、Python镜像、Run预算/取消/恢复；离线实际容器验证。
3. UDS出口代理、请求级授权/凭据、受控包安装；未验收前默认禁网。
4. 相关客户端工具卡片/确认摘要/凭据用途的最小同步，完成全量回归和文档。保留六个原生方法，不重构聊天/模型配置/账户历史。

主要影响`agent/bash.py/tools.py/self_review.py/assistant.py/context.py/contracts.py`、新sandbox管理/文件/网络模块、`core/config.py/errors.py`、必要的`llm/turns.py`参数解析、Repository/ORM/Alembic、Sandbox Dockerfile/配置、仅相关REST/Zod/工具展示。AgentPool/Scheduler只调整资源占用与清理协作，不重做持久队列或多Agent架构。

## 10. 验证计划与部署边界

- 覆盖用户17项用例：Bash成功、Python写入/运行/修复、3000–32768字节通过及UTF-8精确边界、超长明确拒绝、超时/OOM/PID、Workspace持久化/任务隔离、LOW不经无关模型拒绝、禁网/已授权出口、SSRF/DNS重绑定/重定向/环境代理/IP直连/隧道、Docker socket和宿主密钥隔离、真实退出码、单Sandbox、取消完整清理。
- 追加风险diff审批/摘要防篡改/拒绝不提交、请求授权不重放脚本、凭据不进脚本/输出、依赖wheel/哈希/来源/磁盘界限、孙进程/setsid、回收失败占位、双Worker和启动预热竞争、旧策略/迁移/已有Key兼容。
- 先用依赖注入传输/解析器/时钟做确定性测试；DNS/重定向的替身测试与实际容器隔离/公网HTTPS出口验证分别报告。真实联网测试不调用用户真实厂商Key或执行付费/写操作，使用合成凭据和受控测试服务；无公网测试条件时不能将Mock称为公网访问已实测。
- 执行lint/typecheck/test/build、api:generate、backend/model/Bash smoke；涉及工具卡片/批准/凭据用途则运行开发/E2E/未签名包相关回归。Docker分别记录配置、镜像构建和实际执行，网络出口单独验收。
- 部署需备份后init升级0007、构建新版沙箱/出口镜像，并显式配置授权目标/能力；普通Compose没有Docker控制环境，继续关闭Bash，不自动挂宿主socket。默认空允许列表，不因测试通过而开放用户真实第三方请求。

小练习：解释为什么设置HTTP_PROXY不足以阻止Python自己创建socket，以及为什么300秒命令需要同时修改180秒Run预算。

## 官方依据

- Docker bridge默认提供外网伪装路由；internal网络也需注意宿主网桥地址：[Bridge driver](https://docs.docker.com/engine/network/drivers/bridge/)、[Gateway modes](https://docs.docker.com/engine/network/port-publishing/)。
- HTTPX支持Unix socket传输，HTTPCore公开SNI扩展可用于固定验证后的IP且校验证书域名：[Transports](https://www.python-httpx.org/advanced/transports/)、[Extensions](https://www.encode.io/httpcore/extensions/)。
- Docker存储配额依赖驱动/宿主文件系统，pip支持精确锁定和哈希校验：[docker run](https://docs.docker.com/reference/cli/docker/container/run/)、[Repeatable installs](https://pip.pypa.io/en/stable/topics/repeatable-installs/)。
