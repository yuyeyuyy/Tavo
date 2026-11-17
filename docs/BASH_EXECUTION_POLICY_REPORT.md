# Bash执行策略3实施与验收

2026-11-17。按用户已确认的BASH_EXECUTION_POLICY_PLAN.md增量实施，11月16日暂停、17日按“继续”恢复。保留FastAPI Service/Repository、LangGraph、AgentPool、SQLite lease/fence与JWE/SecretStore，没有重做Agent架构或新增宿主Python/npm依赖。未提交/推送，未升级正常用户库或使用真实厂商Key。

操作见[Bash与Python开发沙箱指南](BASH_EXECUTION_POLICY_GUIDE.md)，普通配置见[示例TOML](../backend/sandbox.example.toml)，决策见[ADR0012](ADR/0012-sandbox-execution-policy.md)。

## 结果

- 命令32768 UTF-8字节，默认60/最大300秒；CPU1/最高2核，内存512/最高1024MiB，PID128、单文件64MiB、每数据库1执行Sandbox。TOML统一默认、严格校验，非法配置拒绝启动。
- 贯通原4KiB参数、24KiB复核及Run180秒的隐藏限制。新Bash策略Run默认12轮/24工具/1200秒，冻结权限和当前更严格限制共同生效；旧策略/批准不升级。
- LOW确定性本地操作不强制模型复核，MEDIUM脚本开发可以自动执行；越界拒绝和具体敏感审批分开，参数/模型/规则/执行/网络/取消错误不再统一FORBIDDEN。
- SandboxManager复用同Run容器，任务级副本、Session文件持久化与版本提交、敏感diff批准、后台进程/cgroup回收、取消/失租/崩溃恢复；未知清理保留容量，未知作用不重放。
- 执行容器仍无网络接口，UDS应用代理按域名/方法/路径/能力控制公网HTTPS，固定核验IP并验证TLS/SNI、逐跳重定向、防环境代理/内网/元数据/隧道绕过。默认网络关闭。
- 专用API Token用途、目标绑定和CAS复用现有加密框架；只在代理注入。POST等具体请求先审批，结果加密，批准不重跑脚本。配额预留/封闭请求后核销，未知计数不释放额度。
- 独立Python3.12开发镜像预装pip/pytest/requests/httpx/pydantic；额外依赖仅批准来源/版本/哈希的wheel，临时区有界检查，不进入宿主venv。长脚本用workspace_write分块/CAS保存后执行。
- 客户端只同步新契约、相关API凭据表单、网络状态和具体审批预览；六个原生方法与原模型配置功能保持。

## 文件变化与原因

| 范围 | 主要文件及职责 |
| --- | --- |
| 配置 | `core/sandbox_config.py`、`core/config.py`、`sandbox/defaults.toml`、`sandbox.example.toml`、`.env.example`：集中资源/网络/预算，上下限与失败关闭。 |
| 执行管理 | `agent/bash.py`、`sandbox.py`、`bash_policy.py`：兼容旧执行入口，新增Run容器/容量/工作副本/复用/回收，风险分级与结构化Bash结果；启动继续实际预热、指纹更新和可见进度。 |
| Agent循环 | `assistant.py`、`context.py`、`tools.py`、`contracts.py`、`self_review.py`、`mock.py`、`llm/turns.py`：长参数、分块写入、工具schema、冻结策略、预算、必要复核、内部请求关联和四协议历史，取消/异常可修复，重复失败有界。 |
| 状态/恢复 | `models/entities.py`、`repositories/sessions.py`、`workers/scheduler.py`、`migrations/versions/0007_sandbox_execution_policy.py`：容量/操作/请求/checkpoint表、精确审批、CAS提交、失租和未知结果恢复。0001–0006逐字节不改。 |
| 出口/凭据 | `agent/egress.py`、`api_credentials.py`、`credentials/contracts.py`、`core/security.py`、`private_state.py`、`services/application.py`、`cli.py`：目标级Token、JWE/版本/轮换、加密HTTP意图与结果、配额预留/核销，终端隐藏录入。 |
| 隔离环境 | `sandbox/Dockerfile`、`requirements.in/lock`、`runtime/tavo_sandbox/{network,egress,control,http,workspace,supervisor}.py`、`tavo_http.py`、`tavo_pip.py`：可信代理/文件控制、子进程监督、固定依赖与受控安装。`backend/Dockerfile`仅补入默认配置，Compose仍不挂宿主socket。 |
| API/客户端 | `api/sessions.py`、`backend/openapi.json`、生成TS、`renderer/api/{client,sessions}.ts`、`security/{contracts,ModelKeySettings,NetworkKeySettings}`、`chat/Chat.tsx`、`shared/contracts.ts`：公开网络目标状态、精确操作预览、Key用途和错误映射。生成类型通过api:generate，不手改掩盖漂移。 |
| 验证/文档 | `test_sandbox_{policy,network,lifecycle,development_docker,egress_docker}.py`和相关旧测试、`backend-harness.mjs`、`bash-smoke.mjs`、客户端契约/E2E，以及README/架构/ADR/学习/评测/AGENTS：新边界回归、测试专属资源清理与实际结果。 |

新增迁移0007只建当前所需表。正常数据库、Key、旧Session卷和历史源文件不作为测试夹具。修改前源码备份为`/private/tmp/tavo-before-bash-policy-20261116.tar.gz`，不包含正常凭据/数据库；不能替代用户自己的数据备份。

## 实际执行的验证

| 命令 | 结果与边界 |
| --- | --- |
| `pnpm api:generate` | 通过，Pydantic→OpenAPI→TS；客户端Zod另同步。 |
| `pnpm lint`、`pnpm typecheck` | ESLint、Ruff/格式、严格TS通过。 |
| `pnpm test` | 最终客户端34、后端223通过/21项真实Docker默认跳过；新增定向测试覆盖配置、凭据、回收和网络边界。 |
| `pnpm build` | 通过；现有Zod PURE注释产生Rollup提示，不影响产物。 |
| `TAVO_TEST_DOCKER=1 backend/.venv/bin/python -m pytest -q backend/tests/test_bash_startup_docker.py backend/tests/test_bash_sandbox.py backend/tests/test_sandbox_development_docker.py backend/tests/test_sandbox_egress_docker.py --tb=short` | 完整真实Docker21/21通过，最终完整组126.97秒；后续有界排队/配置收紧后相关开发/出口组10/10再次通过。 |
| `node scripts/backend-smoke.mjs` | 独立进程认证、持久Session/幂等、审核重启、工具与退役路由通过。 |
| `node scripts/model-smoke.mjs` | 合成Key、本地HTTP协议、加密续接、错误/取消/重试/重启和两轮工具通过，无真实厂商请求。 |
| `node scripts/bash-smoke.mjs` | 默认能力、真实命令/文件、后端重启和归属清理通过。 |
| `docker compose config --quiet` | 配置检查通过。 |
| `docker compose build` | 镜像构建通过。 |
| `pnpm test:docker` | 实际Linux独立API/Worker队列、审核/续接/重启业务通过；没有以配置通过代替运行验证。 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm test:dev` | 真实开发窗口的模型配置、Web Crypto提交/清空、本地模型HTTP、聊天/原生设置通过。 |
| `TAVO_TEST_DOCKER=1 TAVO_BROWSER_CHANNEL=chrome pnpm test:e2e` | 桌面8/8通过，含新API Token加密用途、审批详情、文件跨窗口/后端重启与Bash确认。 |
| `pnpm package:dir` | 本机ARM64未签名测试包构建通过，非正式分发。 |
| `TAVO_TEST_DOCKER=1 TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` | 最终包内8/8通过，1.5分钟。审批截图已目视检查。 |
| 迁移备份字节比较、Alembic check/升级测试、`git diff --check` | 旧迁移0001–0006六项均与修改前备份一致；0005/0006→head的旧密文/审批/外键/元数据检查通过；差异无空白错误。 |

真实公网出口访问PyPI HTTPS，额外依赖安装使用官方six1.17.0的固定wheel和SHA256，验证跨Run复用。敏感POST上游使用测试专用合成传输，UDS、审批、凭据隔离和不重放是真实流程；没有真实厂商Token、付费模型/第三方写操作。真实公网测试不等于所有API/SDK都适配。

## 用户17项用例覆盖

| 用例 | 证据 |
| --- | --- |
| 1/2/7/15：Bash、脚本生成执行、文件保留、Python异常后修复 | 真实Docker命令、workspace_write/CAS、同Run容器复用及跨Run文件测试。 |
| 3/4：3000–32768字节通过、超长COMMAND_TOO_LONG | UTF-8精确边界单元测试和5000字节真实命令/错误修复循环。 |
| 5/6：超时、内存/PID | 真实sleep超时终止、OOM及pids.events核验，保留命令退出码。 |
| 8：普通只读不误拒 | LOW分类免模型复核；合法Python/pytest/文件操作MEDIUM自动可用，旧模式保持。 |
| 9/10：未授权出网拒绝、授权API受控出口 | 真实无网络执行容器直连失败、未知目标拒绝；真实PyPI HTTPS和wheel下载通过代理。 |
| 11：SSRF/元数据/DNS重绑定/重定向 | 确定性传输/解析替身覆盖保留/内网/元数据/映射IP、IP固定/SNI、重定向与同源凭据路径绑定；真实禁网容器验证不可直连。未把Mock当作真实元数据服务访问。 |
| 12/13：Docker socket/宿主敏感目录 | 真实执行环境不存在Mac目录、宿主数据/socket，不能写容器系统目录；规则拒绝明显越界，执行层不靠关键词保护。 |
| 14：真实非零退出 | 真实exit7及stderr保持，EXECUTION_ERROR与策略拒绝区分。 |
| 16/17：单执行容量、取消回收 | 两Run竞争、持久唯一slot、有界等待、孙进程清理、取消结果ID/耗时、旧Worker不回收新Worker和崩溃启动恢复。 |

附加测试：API JWE用途/目标绑定/CAS/轮换、网络计数预留与回收不重置、expanded→default资源切换、代理模式预热UDS健康检查、清理不确定禁止提交工作副本。

## 测试中发现并修正的问题

- 原模型参数4KiB和Run180秒使只调Bash上限无效，已联动；JSON Schema及客户端限制同时维护。
- 新卷在/source挂载初始化时归属不正确，修复镜像目录的UID10001；实际执行/文件持久化复验通过。
- 本机Docker系统DNS对PyPI返回Fake-IP保留网段，策略正确拒绝。通过管理员显式选择的固定端点DoH获取真实公网记录后实测通过；没有放行保留网段或改变用户系统DNS。
- 旧测试期待Mock必做复核、16MiB/256MiB、超输出直接失败等旧行为，按新策略调整；输出有界截断与真实退出码分开。取消观测改为实际Docker PID/进程名，确认sleep运行后取消。
- 桌面首轮3项失败来自旧文案/非唯一选择器/未等待发送就绪，按真实状态修正。没有增加盲目重试或把失败跳过，最终桌面与包内均8/8。
- 复查补齐冻结资源上限、队列预算、取消结果、崩溃启动容量恢复、网络配额预留/封闭核销，以及无法确认进程清理时不提交工作副本。对应定向回归通过。

## 部署需要完成的配置与限制

1. 备份并执行init升级0007；默认镜像由后端按可信源码指纹更新。原`tavo-bash:local`不删除，历史Session卷身份保持。
2. 默认network.mode=none。使用代理时，管理员复制示例、显式开启proxy并配置精确目标/方法/路径、依赖pin；有Fake-IP时明确选择可信解析方式。客户端不编辑任意出口权限。
3. 授权API的Token由客户端专用表单或隐藏api-key CLI录入；模型聊天Key不会自动授权该API。GET也须选择实际只读端点，敏感操作逐请求批准。
4. 普通requests/curl/任意SDK没有透明联网，使用tavo_http或兼容受控Transport的适配；额外安装不含任意sdist/git包。此限制是本次明确的能力边界，不标作已支持。
5. 普通命名卷的总容量为监测/提交限制，非跨平台硬配额；未知工作副本保留待核验，暂不含客户端导出/恢复/删除。需要独立备份工作卷，不能只复制SQLite和Key。
6. 单执行Sandbox不包含固定代码的代理/helper容器，它们没有Agent命令执行接口或宿主挂载；资源统计与运维需区分。当前仍单所有者本机服务，没有恶意多租户/完整安全审计/压力或其他桌面平台结果。
7. 未签名测试包不是签名、公证或正式分发。没有推送、发布、改变用户系统Docker/DNS服务或执行全局prune。
