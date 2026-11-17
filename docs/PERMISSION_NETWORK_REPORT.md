# 命令权限、默认公网与对话表达实施报告

日期：2026-11-17。依据用户已确认的[方案](PERMISSION_NETWORK_PLAN.md)。本轮为增量修改，复用现有Assistant、Decision、SandboxManager、UDS出口、SecretStore及客户端组件，无新增第三方依赖、结构迁移或推送。

## 最终行为

| 新会话档位 | Bash | 其他合法工具调用 |
| --- | --- | --- |
| 完全访问权限 full_access（默认） | 无模型/人工审批，按沙箱权限执行；工作区大范围提交自动完成 | 免额外审批 |
| 帮我审批 auto_review | 每条合法命令交独立模型复核；可以批准、拒绝或询问用户 | 免额外审批 |
| 请求审批 request_approval | 每条合法命令先确认；大范围提交绑定具体diff，必要时再次确认 | 免额外审批 |

自动决定记录为policy或model/mock，不伪装human。参数、认证、归属、RunScope/fence、取消、工具预算与执行资源约束保留。全访问仍限非root隔离容器与授权工作区，不能读宿主密钥、Docker socket、系统敏感目录或越过网络隔离。

新会话默认经受控出口访问任意公网HTTPS443；GET/HEAD/POST/PUT/PATCH/DELETE不做独立请求审批。Python中使用`tavo_http.request('https://host/path', method='POST', json={...})`，也兼容旧目标别名。执行容器保持network=none，普通curl/requests/pip没有透明直连。HTTP明文、IP直连、私网/localhost/云元数据、Fake-IP、自定义DNS/代理/隧道仍受阻；DNS记录全部校验，连接固定公网IP并验证TLS/SNI，重定向逐跳验证。

任意URL不注入保存的Token，包括域名恰好与凭据目标相同的情况。带凭据仍须预绑定别名、路径和方法。跨域去掉认证，保留原凭据的响应防反射检查，拒绝在Location中携带凭据。模型Key不自动授予API权限。依赖来源/版本/哈希仍由原wheel策略管理。

策略4冻结新档位含义；旧auto_readonly/ask/disabled/auto_sandbox及已冻结策略1/2/3不重解释，等待中的旧批准继续有效。收紧管理员配置即时约束后续调用，扩大配置不会升级现有Run。切换档位继续开始新会话；旧会话显示兼容选项。

正文提示统一到四协议、兼容文本聊天、服务端及离线Mock，不主动介绍内部工具/函数/框架，直接说明任务与结果；不删除正文字符串或掩盖执行失败。审计卡片继续保留。提示词不是绝对防泄漏机制。

## 修改文件及原因

以下是相对此轮开始前源码备份的改动，不包括先前任务已存在的工作区修改。

| 文件 | 修改理由 |
| --- | --- |
| backend/app/agent/contracts.py、context.py | 三档枚举、新默认值、策略4与冻结网络范围；旧模式保留 |
| backend/app/agent/bash_policy.py、assistant.py | Bash独立审批；其他工具免额外审批；审核来源与版本保持真实，沿用既有循环 |
| backend/app/agent/sandbox.py | 新三档提交行为；关闭socket卷重试需证明确已不存在，修复中断清理后的二次启动失败 |
| backend/app/agent/egress.py、backend/app/repositories/sessions.py | 当前/冻结网络权限取交集，兼容旧请求批准与结构化结果 |
| backend/app/api/sessions.py | 显式恢复请求审批时支持公网URL目标的具体预览 |
| backend/app/core/sandbox_config.py、backend/sandbox/defaults.toml、backend/sandbox.example.toml、backend/.env.example | 公网范围、写方法审批与默认proxy统一配置 |
| backend/sandbox/runtime/tavo_sandbox/network.py、http.py | 公网URL入口、受控方法、URL/地址验证、重定向及凭据防反射，沿用原限流与配额 |
| backend/app/llm/prompts.py（新增）、adapter.py、turns.py、backend/app/agent/mock.py、tools.py | 共享正文规范、真实联网入口说明、Mock表达 |
| apps/desktop/src/renderer/api/sessions.ts、generated.ts、backend/openapi.json | Zod与Pydantic/OpenAPI契约同步，生成类型由api:generate维护 |
| apps/desktop/src/renderer/chat/Chat.tsx、mock.ts | 三档选择、新默认值、旧会话兼容显示、离线回复规范与受控出口提示 |
| backend/tests/test_command_permissions.py（新增）、test_sandbox_policy.py、test_sandbox_network.py、test_sandbox_lifecycle.py | 模式决策、非Bash自动、旧策略、URL/凭据/重定向、重复清理等边界 |
| backend/tests/test_sandbox_egress_docker.py、test_bash_startup.py | 真实公网无别名、写方法免审批、凭据绑定、批量文件提交与清理；伪Docker测试显式禁网避免混入未模拟的网关 |
| tests/contracts.test.ts、chat.test.ts、e2e/desktop.spec.ts | 新枚举、离线表达、历史批准恢复、三档与真实Bash桌面测试 |
| scripts/bash-smoke.mjs、backend-harness.mjs | 默认全访问独立进程烟雾；给网关足够退出清理时间，错误仅输出公开码 |
| README.md、backend/README.md、AGENTS.md、docs/API.md、ARCHITECTURE.md、BASH_EXECUTION_POLICY_GUIDE.md、PERMISSION_NETWORK_PLAN.md、ADR/0013-command-permissions-public-egress.md（新增）、LEARNING_LOG.md、EVALUATION.md、本报告 | 当前行为、选择理由、配置、学习练习与验证记录；旧报告保留历史 |

## 验证

全部使用临时数据库、合成凭据及测试工作卷。真实公网仅访问PyPI只读资源；POST/PUT/PATCH/DELETE上游为合成httpx传输，容器、Unix socket和Agent循环真实运行。没有调用真实付费模型或向第三方写业务数据。

| 实际命令 | 结果与范围 |
| --- | --- |
| `pnpm api:generate` | 通过；Pydantic OpenAPI与生成TS同步 |
| `pnpm lint` / `pnpm typecheck` | 通过；ESLint、Ruff检查与格式、TS严格检查 |
| `pnpm test` | Vitest 34/34；Pytest 265通过、23项真实Docker默认跳过（最终46.61秒） |
| `pnpm build` | Main/Preload/Renderer构建通过；依赖Zod的已有注释位置警告，不影响构建 |
| `cd backend && TAVO_TEST_DOCKER=1 .venv/bin/python -m pytest -q tests/test_bash_sandbox.py tests/test_sandbox_development_docker.py tests/test_sandbox_egress_docker.py tests/test_bash_startup_docker.py --tb=short` | 全组23/23通过，195.65秒；命令、Python修复、持久工作区、超时/取消/资源、默认公网、隔离与启动 |
| `cd backend && TAVO_TEST_DOCKER=1 .venv/bin/python -m pytest -q tests/test_sandbox_egress_docker.py --tb=short` | 最后网络文件补充防反射检查后5/5复验通过，54.55秒；真实PyPI TLS与固定wheel下载、合成认证/匿名POST/PUT/PATCH/DELETE均通过 |
| `node scripts/backend-smoke.mjs && node scripts/model-smoke.mjs && node scripts/bash-smoke.mjs` | 通过；独立进程认证、历史批准恢复、合成模型HTTP循环、默认全访问Bash文件及重启，无human伪造记录 |
| `TAVO_BROWSER_CHANNEL=chrome TAVO_TEST_DOCKER=1 pnpm test:e2e` | 8/8通过；三档、旧会话批准/拒绝/重启、新会话默认自动、API Token加密、大范围diff批准及原界面回归 |
| `pnpm package:dir` | ARM64未签名测试包成功，不发布 |
| `TAVO_BROWSER_CHANNEL=chrome TAVO_TEST_DOCKER=1 pnpm test:packaged` | 最终包内8/8通过；约1.8分钟 |
| `pnpm test:dev` | 开发窗口、模型选择/加密凭据/合成HTTP/新对话/原生设置通过 |
| 原始迁移与本轮备份逐字节比较、`git diff --check` | 0001–0007七项一致，补丁空白检查通过 |

新增测试覆盖三档Bash审核（允许/拒绝/询问）、非Bash不受命令档位影响、默认与旧冻结权限、公网URL与写方法、同域URL不继承Token、私网和重定向阻断、跨域凭据反射、Helper路径查询、全访问批量版本提交与重复清理。原先测试继续覆盖租约/fence、Run预算、取消及容器边界。

中途失败及修复如实记录：伪Docker启动用例原来未模拟默认网关，现显式禁网隔离测试职责；合成写方法测试追加在heredoc外的脚本已修正；本机缺Playwright下载版Chromium，改用已安装Chrome；桌面重试等待曾匹配到上一条回复，现等待回复结束；默认网关增加清理耗时，测试给予足够退出/执行等待，生产重复socket卷清理则需确认不存在后才成功。最后发现跨域去认证后原凭据防反射状态需要保留，补充规则与2项回归，并在最终单元和真实出口专项重验通过。以上初次失败均不计为最终通过。

## 部署与剩余边界

重启前后端并开始新会话可使用新默认值。原head仍为0007，本轮没有结构迁移，0001–0007迁移与本轮备份字节一致；没有升级/删除正常用户数据、改动本机已有配置文件或读取用户真实Key。旧数据尚未到0007时仍按现有指南备份后init。

管理员已有`network.mode="none"`或限制配置继续生效，不自动重写。若系统DNS使用Fake-IP，地址校验会拒绝，管理员可按指南显式配置`dns_mode="cloudflare-doh"`；公网实连测试使用该明确配置，不能将其称为默认系统DNS的实连验收。代理不是透明全协议网络。带Token的API仍需绑定目标，额外依赖仍需固定wheel配置。

自动外部请求不保证Exactly-once；脚本应使用业务幂等键并核实未知结果，不自动重跑未知外部副作用。资源上限、普通Docker卷软监测、单所有者部署边界与策略3指南一致。没有进行恶意多租户安全认证、完整负载、签名/公证、其他桌面平台或真实厂商模型遵循提示质量验证。测试包是本机ARM64未签名产物。
