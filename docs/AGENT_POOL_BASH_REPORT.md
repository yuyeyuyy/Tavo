# AgentPool、自审查与隔离Bash交付报告

> 后续用户已要求默认启用Bash并移除开关；初版开关/默认关闭描述为历史，最新行为和验证见 [BASH_DEFAULT_REPORT.md](BASH_DEFAULT_REPORT.md)。

日期：2026-11-16。按用户确认的 [完整方案](AGENT_POOL_BASH_PLAN.md) 实施。当前分支 `feat/agent-foundations`，保留之前未提交工作；没有自动提交/推送、发布、改正常用户数据或新增Python/npm依赖。源码实施前备份位于 `/private/tmp/tavo-before-agent-pool-bash.tar.gz`，不包含正常私有数据目录。

## 已交付

- AgentPool统一有界Run task、取消/关闭/异常回收及外部清理名额；Scheduler保留持久队列/lease/fence，只领取可用名额。默认2个Run、Bash并发1，审核等待释放Run槽位。
- 现有LangGraph循环加入独立模型自审查。策略2对合法工具批量复核，tools=[]，严格JSON/调用ID/枚举/摘要边界；后端收紧合并规则/模型/人工决定，不能通过审批扩大能力。Mock独立标记，连续重复失败/拒绝终止。
- BashTool使用标准库异步子进程调用Docker，宿主不解析脚本；每条命令独立非root、无外网、只读根目录容器，Session命名卷持久文件，不挂宿主home/仓库/数据库/密钥/socket，不传Key/认证环境。
- 明确bashEnabled与auto_sandbox；旧会话默认false，auto_readonly下Bash人工，ask逐次确认，disabled无工具。客户端开关/模式/命令、复核来源/摘要、输出/退出码和真实执行提示已接入。
- 执行意图在start前持久化，create后再次校验；Bash不采用只读工具的重播策略。未知结果中断，未知工作区下一次Bash确认；清理失败保留名额，旧Worker关闭不清理新Worker容器。
- 0006增量迁移：Session能力与工作区确认标记、模型轮次purpose和唯一约束、Decision摘要、bash_executions。旧策略1、原密文绑定/Key与账户历史保留；主模型与审核续接分离并加密。

## 文件变化与理由

| 文件/模块 | 改动与职责 |
| --- | --- |
| `backend/app/agent/pool.py` | 提取进程内执行所有权，持久队列仍在DB，不新增重复内存队列 |
| `agent/assistant.py`、`self_review.py`、`tools.py`、`context.py`、`mock.py` | 合法提议复核、决策合并/版本、Bash分流、重复失败终止、用途隔离、Mock与真实执行共用链路 |
| `agent/bash.py`、`backend/sandbox/Dockerfile` | Docker能力预检、可信CLI argv、容器/卷归属、输出/超时/清理、工作目录真实路径与资源边界 |
| `workers/scheduler.py`、`repositories/sessions.py` | Pool调度、有限领取、未知写操作恢复、作用域与所有权校验、审核/结果事务 |
| `agent/contracts.py`、`models/entities.py`、`migrations/versions/0006_agent_pool_bash.py` | 默认关闭能力/新模式/审核摘要来源、模型purpose、执行意图持久化，旧迁移不改 |
| `llm/turns.py`、`llm/adapter.py` | 复用四协议网关，内部可信审核系统指令与无工具请求，保留原文本接口 |
| `services/application.py`、`services/sessions.py`、`core/config.py`/`errors.py`、`api/sessions.py` | 服务端能力开关/镜像/实例装配、503固定错误、公开进程Pool与沙箱就绪状态 |
| `renderer/api/sessions.ts` / `client.ts`、生成OpenAPI/TS、shared错误 | 严格Zod与新增默认字段/来源/模式，禁止客户端控制Docker参数 |
| `renderer/chat/Chat.tsx`、`styles.css` | 显式能力与模式、Mock真实执行提示、决策摘要、失败输出；保留单页与六个原生方法 |
| 新Pool/审核/Bash边界/0005升级/真实Docker测试、扩展协议/客户端契约/E2E | 覆盖权限与恢复关键边界；真实Docker单独显式执行 |
| `scripts/backend-harness.*`、`model-harness.mjs`、`bash-smoke.mjs` | 隔离测试启用能力、额外审核请求、专属资源标签与规范路径清理 |
| BASH_TOOL/ADR0010/ARCHITECTURE/API/README/学习/评测/AGENTS | 当前行为、启动/备份、额外模型费用、部署与未验证边界 |

## 实际验证

macOS ARM64、Node24/pnpm11/Python3.12，本机Docker运行Linux容器；合成Key/HTTPX及本地HTTP模型替身，无真实厂商付费请求。

| 实际命令 | 结果 |
| --- | --- |
| `pnpm api:generate` | OpenAPI→TypeScript生成成功，Zod同步维护 |
| `pnpm lint` | ESLint/Ruff检查及格式通过 |
| `pnpm typecheck` | 通过 |
| `pnpm test` | Vitest **34通过**，Pytest **163通过、6项真实Docker用例按设计跳过** |
| `TAVO_TEST_DOCKER=1 backend/.venv/bin/python -m pytest -q backend/tests/test_bash_sandbox.py` | **6/6真实容器用例通过**，补齐默认跳过组 |
| `node scripts/backend-smoke.mjs` | 独立进程认证、Session/审核/工具/重启/退役路由通过 |
| `node scripts/model-smoke.mjs` | 真实本地HTTP协议、额外独立模型复核、工具循环、旧文本/Key/限流/重启通过 |
| `node scripts/bash-smoke.mjs` | 独立进程实际Bash、Mock标记复核、文件跨后端重启、专属资源清理通过 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm test:dev` | 真实开发窗口模型添加/JWE Key/HTTP聊天/新对话/设置通过 |
| `TAVO_TEST_DOCKER=1 TAVO_BROWSER_CHANNEL=chrome pnpm test:e2e` | 构建成功，**7/7桌面场景通过**，含真实Bash |
| `pnpm package:dir` | ARM64未签名测试.app成功，无签名/公证/发布 |
| `TAVO_TEST_DOCKER=1 TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` | **7/7包内场景通过**；最终cwd约束后Bash场景又单独通过 |
| `docker build -t tavo-bash:local -f backend/sandbox/Dockerfile backend/sandbox` | 实际执行镜像构建成功，无额外包安装 |
| `docker compose config --quiet`、`docker compose build`、`pnpm test:docker` | 分别完成配置检查/镜像构建/实际Linux独立API-Worker业务，均通过；Compose没有增加Docker socket或Bash权限 |
| `git diff --check`、实施前后迁移逐文件哈希 | 通过；既有0001–0005 **5/5完全一致** |

后端普通测试与显式Docker组共**169项通过**，默认命令中的“跳过”不被算作真实沙箱验证。0005→0006测试保留旧Session、策略1审核和原加密续接，新增默认关闭能力，外键/Alembic元数据无漂移。原0004及历史SQL/Key升级回归继续通过。

关键覆盖：Pool并发/重复Run/孤儿名额/关闭；九家预设四协议独立无工具审核与加密/公开隔离；模型deny/ask/格式失败、规则和模型deny不可人工升级、旧冻结策略1；Bash严格参数/显式权限/不可用503、崩溃未知不重播、清理失败保留名额、旧Worker不清理新归属；真实容器同会话文件继续/跨会话隔离、非零退出、输出超限/无效UTF8/控制字符、超时/子进程清理、非root/根目录只读/no-new-privileges/capabilities/无外网路由、256MiB下OOM失败、单文件限制、cwd符号链接不能越出工作区。能力预检对缺少内存/交换/PID/CPU限额或seccomp支持直接不可用，不默默降级。

桌面7项包含原六项模型/Key、输入法/键盘/CSP/隔离/离线/会话与审核恢复，加上显式Bash、自动沙箱、Mock提示、文件跨窗口和后端重启、人工拒绝。已目视复核 `test-results/chat-packaged-bash.png`，命令和审核来源明确。

测试中定位并修正了额外审核请求的替身契约、不可用状态缺503映射，以及容器未退出/CLI状态不一致不能记录成功、macOS `/var`→`/private/var`规范路径导致测试清理实例hash不一致。三个仅含smoke-ok/desktop-ok标记的遗留测试卷逐个核验后清理，修正后smoke/包内Bash复验并核对所属容器/卷为空。没有全局prune或删除正常用户工作区。Docker内可存在DOWN的内核隧道设备，因此网络验收检查路由/实际连接失败，不错误要求网卡列表只有lo。

## 使用与限制

操作见 [BASH_TOOL.md](BASH_TOOL.md)：先停止旧进程并备份，`pnpm backend:init`升级0006；构建沙箱镜像，`TAVO_BASH_ENABLED=1 pnpm dev:backend`，另一终端`pnpm dev`，客户端勾选沙箱Bash。Mock发送 `bash: printf hello > note.txt; cat note.txt`；真实模型需支持工具，自审查使用当前模型并额外计费。

当前只支持宿主Python Worker控制本机Docker；原Compose默认Bash不可用。没有宿主文件操作、任意网络、远程Runner、文件导入导出、多角色Agent或后台服务。没有新依赖、真实厂商调用、正常用户库迁移、提交或推送。

Docker工作卷需独立备份/迁移，默认实例归属依赖数据库规范路径，不能将移动SQLite等同于搬迁工作卷。卷没有统一总容量硬配额；CPU/PID配置和本机资源预检已检查，未做高负载/磁盘容量压力评测。容器及管理员/镜像仍是信任边界，不宣称恶意多租户安全或绝对防逃逸。

文件副作用不能由SQLite回滚；超时/取消不保证未写入。fence保护数据库提交，不承诺命令在失租瞬间停止/外部Exactly-once。无法确认清理时可能保留名额等待Docker恢复。普通消息/命令/结果明文存盘，Key和私有模型续接加密；严格Zod旧客户端需随新增响应字段升级。

当前仍非token流式、单所有者、本机规模，无真实厂商/Windows/签名公证/公网多租户/完整安全审计结果。上一轮Key偶发422原因未查明，本轮未复现，不宣称已修复。
