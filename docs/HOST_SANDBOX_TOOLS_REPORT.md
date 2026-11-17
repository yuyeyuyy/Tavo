# 沙箱与宿主机 Bash 实施报告

2026-11-17。用户已确认[完整方案](HOST_SANDBOX_TOOLS_PLAN.md)。本轮增量实现，不新增第三方依赖，不推送、不修改正常用户数据库或执行真实项目部署。操作与边界见[指南](HOST_BASH_GUIDE.md)，决策见[ADR0014](ADR/0014-host-and-sandbox-execution.md)。

## 实现结果

新会话同时获得sandbox和host_bash，由模型按任务选择。前者复用原Docker，承担脚本、数据处理、临时文件、网页抓取；后者以Python后端当前OS用户执行真实系统、进程、日志、Docker和项目操作。没有用户执行位置开关。两目标共同使用full_access、auto_review、request_approval，非命令工具审批不扩大。

HostBashRunner使用标准库、独立进程组、受监督启动器、父管道和租约约束的心跳。有界stdout/stderr、真实退出码、超时/取消、单宿主占位及失败预算纳入原循环；不继承完整后端环境。监督器结果FD不传给Bash，正常退出先保存真实退出码，再回收整个受管组。恢复不自动重放未知写入、不仅凭PID杀别的进程，不能证明清理时保留容量。

已保存模型/API凭据、bootstrap/Fernet密钥与常见私钥头进入输出会拒绝发布，检查跨块、超出捕获部分和执行期间新增凭据；发布前刷新凭据检查，原始敏感输出不写ToolResult/LLM/普通日志。已知Tavo私有数据、密钥及敏感目录仍拒绝直接读取。

这是受信任Owner的真实宿主执行，**不提供针对恶意脚本的OS秘密隔离**。路径规则/输出过滤不能阻止所有编码、间接访问、Docker高权限或有意脱离进程组行为。宿主没有沙箱cgroup配额，也没有文件回滚。更多边界在指南中明确说明。

## 兼容与升级

新增0008_host_bash：Session.execution_scope迁移旧行默认sandbox_only，新三档创建的新Session设host_and_sandbox；新增HostExecution持久记录。新Context策略5冻结host配置与能力，原策略1–4/待批准/历史消息不升级。旧bash始终是Docker兼容标识，避免已批准脚本被移到宿主。旧七项迁移与本轮源码备份逐字节一致。

Pydantic、OpenAPI生成TS和Zod同步；Context工具上限改为6；运行状态增加独立hostBash，保留原bash字段的Docker含义。审计与审批明确显示宿主位置、真实cwd和无容器隔离，正常正文仍不主动罗列内部工具。取消刷新期间禁用重试按钮，避免“已取消”先显示但刷新尚未完成时点击被状态层忽略。

## 文件变化与理由

相对此轮开始前备份，不包括以前任务留下的工作区改动。

| 文件 | 修改内容 |
| --- | --- |
| backend/app/agent/host.py（新增）、host_supervisor.py（新增） | 宿主参数、环境、执行/监督/取消、凭据输出防护、占位与恢复 |
| backend/app/core/sandbox_config.py、config.py、errors.py | host统一配置、独立管理员开关、HOST_UNAVAILABLE错误 |
| backend/sandbox/defaults.toml、backend/sandbox.example.toml、backend/.env.example | 默认开启宿主，cwd、字节、时间、输出、单并发与心跳配置示例 |
| backend/app/models/entities.py、backend/migrations/versions/0008_host_bash.py（新增） | Session能力边界与HostExecution；不改旧迁移 |
| backend/app/agent/tools.py、bash_policy.py、assistant.py、context.py、contracts.py、mock.py | 两个新入口、原bash兼容、共同审核/预算/路由、策略5冻结与测试Mock |
| backend/app/agent/bash.py、sandbox.py、egress.py | 结果执行目标、策略5兼容，沿用Docker生命周期与受控出口 |
| backend/app/agent/self_review.py、backend/app/llm/turns.py | 按实际目标审核，系统/Docker操作不再被当沙箱越界误拒；保留正文规范 |
| backend/app/repositories/sessions.py、workers/scheduler.py、services/application.py | 新会话范围、结果元信息、Host恢复/名额和生命周期组合 |
| backend/app/api/sessions.py、backend/openapi.json、apps/desktop/src/renderer/api/generated.ts、sessions.ts | 独立宿主状态、生成协议、Zod与历史兼容 |
| apps/desktop/src/renderer/chat/Chat.tsx、packages/shared/src/contracts.ts | 执行位置审计、Host错误文案、取消时重试状态一致 |
| backend/tests/test_host_bash.py、test_host_contracts.py（新增） | 真实临时项目、三档、异常/修复、超时/取消/管道丢失/容量、输出与升级边界 |
| backend/tests/test_archive_upgrade.py、test_bash_boundaries.py、test_command_permissions.py、test_sandbox_development_docker.py | 更新新合法工具/表数/版本预期，旧兼容用例保留 |
| scripts/host-smoke.mjs（新增）、scripts/bash-smoke.mjs、tests/e2e/desktop.spec.ts | 独立宿主重启/系统/进程/临时日志/Docker只读烟雾、九场景桌面回归与明确就绪等待 |
| docs/HOST_SANDBOX_TOOLS_PLAN.md、HOST_BASH_GUIDE.md、HOST_SANDBOX_TOOLS_REPORT.md、ADR/0014-host-and-sandbox-execution.md、ARCHITECTURE.md、DEVELOPMENT_PLAN.md、API.md、LEARNING_LOG.md、EVALUATION.md、AGENTS.md、README.md、backend/README.md、BASH_EXECUTION_POLICY_GUIDE.md | 设计、操作、迁移、实际验证及限制 |

源码备份：/private/tmp/tavo-before-host-tools-20261117.tar.gz，仅代码/测试/说明，无正常数据、密钥或工作卷。没有提交/推送。

## 验证范围

macOS ARM64上真实宿主测试只在临时项目写文件、执行自建子进程、读取本命令进程/系统状态/临时日志和Docker版本。没有部署真实项目、终止用户服务或操作真实第三方写API。Docker写请求上游仍为合成传输，模型审核为合成HTTP/Mock；没有真实付费模型请求。

最终命令和结果在收尾记录补充。原始迁移七项已核验不变。

中途发现并处理：原测试工具/表数/策略版本预期随新能力更新；取消后的重试按钮曾在pending刷新时可点击，已改为pending禁用并回归；测试包输入法用例在上一轮仍忙时按Enter，其行为本来应拒绝并发发送，改为明确等待可发送再验证输入法；不靠固定sleep掩盖错误。真实宿主输出检查又增加执行期间凭据变化场景。初次失败不算最终通过。

## 使用与未覆盖部分

正常用户库尚未自动升级。停止旧API/Worker、备份数据库与独立密钥后，在仓库根目录执行`pnpm backend:init`升级到0008，再`pnpm dev:backend`，客户端新建对话使用双入口。旧会话继续沙箱语义。TAVO_HOST_BASH_ENABLED与host.enabled为管理员开关，不是用户选择器。

Docker默认启动检查保持，后台实际OS用户/网络权限生效；容器内后端不等于Mac。沙箱/宿主文件不自动共享。默认宿主环境只有PATH/HOME/LANG/TMPDIR，额外部署身份、SSH agent和环境需另行配置。只实现能力，不声称已经完成用户某个真实项目的部署。

结果未知不等于操作没发生；宿主文件、远程效果和由外部服务管理器承接的服务不能自动回滚。unknown_detached占位需运维核验，不建议直接删除未核验记录。恶意程序主动逃离进程组并关闭管道不在强回收保证内。不提供恶意多租户安全认证、主机内存/CPU/PID硬配额或对所有秘密编码形式的检测保证。

Linux走POSIX实现，但本轮没有Linux宿主业务实测；Windows宿主Bash明确不可用。Docker内Linux不算Linux宿主验证。测试包未签名/未公证，不称正式分发。真实厂商工具选择质量与真实部署仍需业务授权和场景验证。

## 最终验证命令与结果

| 命令 | 实际结果与范围 |
| --- | --- |
| `pnpm api:generate` | 通过；Pydantic/OpenAPI生成类型与Zod一致 |
| `pnpm lint`、`pnpm typecheck` | 通过；ESLint、Ruff与格式、TS严格检查 |
| `pnpm test` | Vitest34/34；Pytest280通过、38默认跳过（24 Docker＋14真实宿主），38.20秒 |
| `TAVO_TEST_HOST=1 .venv/bin/python -m pytest -q tests/test_host_bash.py tests/test_host_contracts.py --tb=short`（backend目录） | 28/28通过，14.99秒；其中14个真实宿主临时项目测试、14个契约/审核/恢复替身/迁移测试 |
| `TAVO_TEST_DOCKER=1 .venv/bin/python -m pytest -q tests/test_bash_sandbox.py tests/test_sandbox_development_docker.py tests/test_sandbox_egress_docker.py tests/test_bash_startup_docker.py --tb=short`（backend目录） | 24/24真实Docker通过，168.77秒；旧bash路由、新sandbox、文件、取消/资源/隔离、DoH公网及旧批准 |
| `node scripts/backend-smoke.mjs && node scripts/model-smoke.mjs && node scripts/bash-smoke.mjs && node scripts/host-smoke.mjs` | 全部通过；独立进程认证/旧批准、合成模型、沙箱与默认宿主重启闭环 |
| `TAVO_TEST_DOCKER=1 node scripts/host-smoke.mjs` | 通过；宿主系统、当前命令进程、临时日志、真实Docker版本只读查询及重启，不依赖沙箱开启 |
| `pnpm build` | 通过；保留既有Zod注释位置警告，不影响构建 |
| `TAVO_BROWSER_CHANNEL=chrome TAVO_TEST_DOCKER=1 pnpm test:e2e` | 完整9/9通过；新宿主三档/真实临时文件/目标审计与原桌面回归 |
| `pnpm package:dir` | 最终ARM64未签名测试包构建成功，未发布 |
| `TAVO_BROWSER_CHANNEL=chrome TAVO_TEST_DOCKER=1 pnpm test:packaged` | 最终包内9/9通过，约1.8分钟；取消重试和输入法就绪问题修正后完整重验 |
| `pnpm test:dev` | 开发窗口/模型设置/加密凭据/本地HTTP/新对话/原生桥接通过 |
| 备份逐字节核验、`git diff --check` | 原0001–0007七项迁移一致，补丁空白检查通过 |

真实宿主取消和管道断开用例验证所属进程组消失；PID复用安全与未知占位用例包含替身验证，不能混称全部为真实进程复用测试。自动化部署能力仅以临时项目写入/脚本执行验证，不代表真实外部环境部署成功。没有修改真实Key、真实项目、用户服务或远程数据。
