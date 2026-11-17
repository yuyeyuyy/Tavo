# AgentPool、内部循环、自审查与隔离 BashTool 方案

日期：2026-11-16。状态：用户已确认完整方案并实施；实际文件/验证/限制见 AGENT_POOL_BASH_REPORT.md。正常用户数据没有自动升级。

用户已确认两点：Bash 在隔离容器专用工作目录执行；AgentPool 指多个会话 Run 的有界执行池，工具使用模型复核再经过后端规则约束，不是多个角色 Agent 协作。本文件将接口、执行权限、恢复和验收具体化。当前旧功能/模型/凭据/Session 的未提交工作全部保留，不自动提交或推送。

## 1. 当前实现与差距

- 没有名为 AgentPool 的模块。Scheduler 内的 active 字典管理 Run task；Assistant.pool 实际是两个线程的只读工具线程池，readers 负责超时后仍占用资源。
- LangGraph 已有 model → review → tools → model，默认4轮/8工具/180秒活动预算。Reviewer 目前只做确定性规则，没有独立模型自审查请求。
- ToolSpec.read_only 固定为 True；ToolContext 只有时钟。当前“失去结果可以有限重试”的工具恢复仅适用于时间/计算，不能直接用于有写作用的 Bash。
- 私有模型续接加密、Session/Run/ToolCall/Decision、lease/fence、JWE、模型配置、Zod和单页工具展示均可复用。

## 2. 两种实现方式与取舍

| 方式 | 优点 | 代价与边界 |
| --- | --- | --- |
| 在现有线程池中执行宿主 Bash，仅用提示词自审查 | 代码少、可操作宿主项目 | 命令具有后端进程权限，路径校验不能限制 shell；线程超时不能停止命令，模型判断不能形成权限边界 |
| 提取有界AgentPool、复用现有图、独立Docker沙箱执行与规则+模型审核（选用） | 权限、生命周期、写操作恢复有明确边界，可复用现有持久审计 | 需要Docker和预构建执行镜像；增加执行意图/审核轮次及真实容器测试 |

自审查比较“把前一回复当安全结论”和“独立、无工具权限的审核请求”：选后者。仍用当前会话所选模型/已有网关，不引入第二个Agent框架或新的厂商SDK。审核只返回结构化结论和简短摘要，不要求或公开完整思维链。

## 3. AgentPool 与内部循环

- 新增 `agent/pool.py`，统一 Run task、取消、异常回收、工具/容器清理名额、排空关闭与公开统计。复用 asyncio，不另建内存任务队列。
- Scheduler 保留数据库队列、领导租约、fence、领取/恢复职责；有槽位才领取有限数量的pending Run，避免一次加载全部队列。Pool不是持久状态权威。
- Assistant 保留一次Run的执行；将线程池职责明确为只读ToolExecutor，Bash走异步子进程执行器。默认2个Run、Bash并发1；同Session仍仅1个活动Run。等待人工审核释放Run名额，未清理的外部执行仍保留对应名额。
- 统一模型/审核/工具步骤的活动时间与取消检查。保留现有4轮主模型/8个工具/180秒默认预算；增加至多每轮1次批量自审查，即最多4次审核模型请求。审核也计入活动/上下文/费用预算，不以自审查名义无限循环。
- 每次工具提议批次走：参数和能力预检 → 无工具模型自审查 → 后端合并决定 → 必要时人工确认 → 执行前再校验 → ToolResult → 下一轮模型。
- 非零退出、拒绝、超时分别返回真实失败结果。连续重复同一被拒绝/失败调用且没有进展时终止；不能因字符串相同就禁止读取修改后的文件，正常命令仍受总预算限制。
- 取消后不启动后续工具；模型/审核响应在提交时重新验证scope/lease/fence。关闭/失租处理跟踪的资源，不能只删active字典而遗留容器。

## 4. 自审查和执行权限

审核输入：本轮用户目标、已声明能力、相关的有限历史/最近结果摘要、待执行的toolCallId/name/version/规范参数及摘要；工具结果/命令内容是数据，不能覆盖审核系统指令。不会传SecretStore、Key、全量Services、后台环境或其他Session历史。

审核输出严格校验：每个已提议调用恰好一个 `allow | deny | ask`，固定reasonCode和有界简短summary，禁止改参数/新增调用/漏项/重复ID/额外字段。tools=[]，审核模型提出工具、格式不合法、超时或网络失败时停止该轮工具执行，不自动当作通过或切Mock。

最终决策由后端收紧合并：规则deny覆盖全部；模型deny不能由人工覆盖；模型ask进入人工确认；会话ask要求人工；允许也只获得已启用能力。执行前重新检查参数摘要、策略版本和权限。保留policy/model/human分别记录的Decision，界面区分来源；Mock使用明确标记的确定性审核替身，不冒充真实模型审查。

为兼容现有会话：

- 新增Session字段 `bashEnabled`，默认false；旧Session迁移为false。现有auto_readonly/ask/disabled语义保留。
- 新增明确可选模式 `auto_sandbox`：用户启用Bash且选择此模式后，规则和模型均allow的命令可在沙箱内自动执行；ask仍进入人工确认。
- auto_readonly即便启用了Bash，也不会自动放行Bash写能力，Bash须人工确认；ask对所有合法工具逐次确认；disabled不声明工具，不能与Bash启用组合。
- UI新增“沙箱Bash”开关和自动沙箱模式，切换会开始新Session。Docker不可用时说明固定原因，不伪装可执行或回落宿主。旧客户端不发送新字段仍只使用原有工具。
- 当前冻结Context/审核策略版本1可完成原有只读运行；新Run捕获版本2。旧已批准记录不能被解释为Bash权限。

自审查会增加模型请求和延迟；自动化仅用合成Key/协议替身。真实模型由用户手动测试，不自动发付费请求。

## 5. BashTool 的具体契约

工具名 `bash`。输入 `command`（最多约3000 UTF-8字节，与现有4KiB参数预算兼容）、`cwd`（默认`.`，只收工作区内相对路径）、`timeoutSeconds`（1–30秒）。模型不能指定宿主路径、容器/卷名、镜像、网络、挂载、用户、环境变量或Docker参数。

输出 `exitCode`、`stdout`、`stderr`、`truncated`、`timedOut` 与固定错误码。stdout/stderr并发读取，保留合计至多约4KiB可见文本，JSON结果仍不超过现有8KiB；总输出超过64KiB时停止命令并记录输出上限，不能把管道缓冲大小当输出总量限制。非零退出保留有界诊断，结果为failed，模型可据此修正；不会伪装成成功。

使用 `asyncio.create_subprocess_exec` 启动可信Docker CLI，宿主不拼接shell命令。命令脚本仅作为独立参数传给容器内 `/bin/bash --noprofile --norc -c`；不做交互式终端、stdin输入、后台服务或宿主登录shell。一次调用一个临时容器，文件放Session专用Docker命名卷，下一次调用可继续读取；卷不共享给其他Session。

执行镜像由服务端固定配置并解析为镜像ID，不接受Renderer/模型传入，不在运行请求中自动拉取/安装工具。提供最小Bash执行镜像和准备命令，使用既有Python标准库/镜像基础工具，不新增Python/npm依赖。

## 6. 容器边界和部署

- 独立非root UID；只读根文件系统；只写Session工作卷及有大小限制的/tmp；不挂载仓库、home、数据库、私有密钥、Docker socket、SSH agent或宿主设备。
- 无外部网络、drop ALL capabilities、no-new-privileges、Docker默认seccomp；设置CPU、内存/交换、进程数和单文件大小限制。建议1CPU/256MiB/64进程、单文件16MiB，实施时验证实际平台支持，不能忽略不受支持选项后假称已限制。
- 容器环境只有固定PATH/HOME/LANG等；不继承后端模型Key、bootstrap、代理/云凭据或宿主其他环境。不向模型返回Docker CLI原始错误或后台路径。
- Docker命名卷不等于硬磁盘配额。通用macOS/Linux Docker部署没有本方案能统一承诺的卷总容量上限；命令时限/单文件限制也不能等同总容量限制。持久卷需要管理员管理Docker数据盘和备份，不能宣称可安全承载恶意多租户无限写入。
- 首轮运行方式是宿主Python Worker调用本机已安装Docker。现有Compose API/Worker不自动获取宿主Docker socket；Compose默认仍可用原有工具，Bash标为不可用。独立远程Runner服务和容器内Docker控制不在本轮实现，避免扩大部署权限。
- Docker只是本轮隔离边界，不宣称绝对沙箱逃逸防护；同宿主单所有者/管理员信任假设不变。

## 7. 持久执行、取消与未知结果

新增0006，0001–0005不修改、不删库：

1. Session新增bashEnabled默认false。
2. 模型轮次新增purpose（assistant/self_review，旧行为默认assistant），相应唯一约束区分用途。Context只重建assistant续接，审核请求/隐藏推理不混入主对话；新审核私有状态复用Fernet绑定并参与轮换，兼容旧密文绑定格式。
3. 为Bash新增执行意图/attempt/container身份/归属/生命周期记录；复用既有ToolCall/ToolResult/Decision，来源扩展但旧值保留。Bash最多尝试1次，不继承只读工具最多2次的恢复策略。

在启动前持久记录稳定执行名和owner/fence → Docker create（不执行脚本）→ 重新检查租约/取消/摘要并保存容器身份 → start。超时/取消/失租会终止并移除对应容器全部进程，确认清理后释放名额。只处理本实例、已记录且标签匹配的资源，不做全局docker prune，不删除其他应用或用户数据卷。

crash恢复先核查旧执行并清理；Bash running且结果未知时标为interrupted/TOOL_EXECUTION_UNKNOWN，不自动重跑。命令可能已修改工作卷，不能声称取消、fence或回滚撤销了文件副作用。工作区在旧容器清理确认前不可再次执行；显式重试/使用该未知工作区后的下一次Bash强制人工确认，避免自动再次写入。Docker不可用导致无法确认停止时保留未知状态/名额，公开说明未确认清理。

领导租约校验保护数据库提交；外部命令启动/文件写入并不是SQLite事务的一部分，不能承诺失租瞬间停止或外部Exactly-once。

## 8. 接口、文件影响与兼容

- 新 `agent/pool.py`、执行器/沙箱模块和最小执行镜像；修改Assistant、Context、Reviewer、Registry、Scheduler、应用装配/配置。
- SessionCreate/Session新增默认兼容字段；ReviewMode增加auto_sandbox；Decision增加模型/Mock来源与可选短摘要；ToolSpec.readOnly从True literal泛化为bool，保留旧只读工具默认值。
- 新认证 `GET /api/v1/agent-runtime` 返回公开Pool统计、Bash enabled/available/固定reasonCode和隔离说明，不返回宿主路径/私密配置或Docker细节；避免与退役 `/agent/*` 路由冲突。
- 维持现有消息/Run/审核/取消/重试路径；未知Bash重试限制由后端执行，不能靠客户端隐藏按钮代替。
- 修改Pydantic后 `pnpm api:generate`，同时更新Zod、Session store/工具UI；六个原生方法、CSP、认证、模型配置/JWE/文本兼容API不改职责。
- 新增Alembic0006与升级测试；ARCHITECTURE/API/ADR/操作/学习/EVALUATION/AGENTS更新。旧账户数据继续只读历史，不重新接入。

## 9. 测试与交付门禁

- AgentPool：并发上限、公平领取、无重复Run、超时孤儿名额、取消/关闭/异常/失租回收，人工等待不占Run名额，有限队列读取。
- 循环/自审查：真实四协议与Mock提出工具→批量复核→规则合并→执行→下一轮；模型allow/deny/ask、漏项/多项/重复ID/畸形JSON/试图执行工具、Key隔离、审核模型超时/崩溃未知计费请求不盲重发；重复失败终止与总预算。
- 权限：旧auto_readonly保持、Bash默认关闭、显式auto_sandbox仅沙箱、ask人工、disabled；Prompt Injection不能改变挂载/网络/用户/环境，人工不能覆盖规则/模型deny或改参数；旧Context/批准无Bash权限。
- 真实容器：pwd/创建读取文件/管道、非零退出/无命令、超时与子进程清理、并发stdout/stderr/输出上限、非UTF8、退出码/OOM/资源限制，Session间隔离/同Session文件继续、宿主密钥/仓库不可见、无外网/特权/宿主挂载。
- 恢复：create前后/start前后/结果提交前crash、取消/换fence、不自动重复写命令、未知工作区隔离和人工再次确认、Docker断开/重连清理、不得删除非本应用资源；命令副作用不承诺回滚。
- 迁移：0005→0006逐表关键字段/旧数据/Key和加密续接可读、旧review值/Session默认/旧接口兼容、原迁移哈希/外键/Alembic元数据。
- 执行 `pnpm api:generate`、lint/typecheck/相关测试和全量测试/build、backend/model/新增Bash smoke；桌面开发/E2E及本机未签名包。Docker分别记录配置检查、镜像构建、真实隔离Bash与Compose原功能业务，缺Docker时必须明确未执行，不能以Mock代替真实沙箱验收。
- 不使用真实Key、自动调用付费模型或正常用户库；实际文件变化/命令结果/限制写交付报告，不自动提交/推送。

## 10. 基础知识与小练习

用前后端类比：数据库队列像待处理订单表，AgentPool像Web服务器的有界请求执行器，Assistant像处理一个订单的Service，模型自审查像额外的业务复核，后端规则像权限校验，Docker沙箱像独立的受限作业环境。复核说allow并不会让操作获得宿主权限。

小练习：画出“Bash写入文件已完成 → ToolResult尚未提交 → Worker崩溃”的时间线。解释为什么lease/fence只能拒绝旧数据库提交、不能撤销文件写入；设计应返回什么状态，以及为什么恢复不能直接再次执行写入。

官方依据：[Python3.12异步子进程](https://docs.python.org/3.12/library/asyncio-subprocess.html)、[Docker create不启动进程](https://docs.docker.com/reference/cli/docker/container/create/)、[容器运行选项](https://docs.docker.com/engine/containers/run/)、[资源约束](https://docs.docker.com/engine/containers/resource_constraints/)、[持久卷](https://docs.docker.com/engine/storage/volumes/)、[Docker安全与daemon边界](https://docs.docker.com/engine/security/)。这些机制组合成上述方案，具体跨平台支持仍以实施时真实测试为准。
