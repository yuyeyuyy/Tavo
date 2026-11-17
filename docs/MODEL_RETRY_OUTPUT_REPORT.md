# 模型重试与输出上限修复验收

日期：2026-11-17。用户已确认 [完整方案](MODEL_RETRY_OUTPUT_PLAN.md)。本轮完成 ModelGateway 的有界重试、空回复/预算耗尽分类和脱敏链路日志；没有修改工具执行器、历史/数据库结构、模型初始配置或Key。无新增依赖，不提交/推送/发布。既有未提交工作保留，源码基线备份于 /private/tmp/tavo-before-model-retry-20261117.tar.gz（不含正常用户数据库/密钥）。

## 最终行为

- 文本、Agent模型轮次与模型自审查共用请求循环：初次＋最多3次重试，共至多4次HTTP尝试。运行总时限/取消可提前停止，服务重启不自动重放未知在途请求。
- 支持的 deepseek-flash/deepseek-v4-pro 显式保留思考。仅空正文且length时扩大预算，2048→4096→8192→16384；达到配置上限停止。存储初始预算和原8192约束不变；其他厂商不注入思考参数或增加预算。
- 连接/读取抖动、408、受控429、500/502/503/504和DeepSeek生成中断可重试；鉴权/余额/参数/模型错误、404、已识别配额耗尽、拒绝/非法数据/敏感响应校验失败不盲目重试。短异步指数退避，Retry-After超出允许等待或剩余时间时停止。
- MODEL_OUTPUT_LIMIT 表示没有正文便达到单次生成上限，MODEL_EMPTY_RESPONSE 表示正常结束却为空；不再把这两种情况误报为MODEL_REJECTED。原接口不支持的响应与拒绝保持原有兼容分类，非法工具参数仍拒绝。
- 有正文的截断回复正常展示，完整工具调用正常交原审核流程。工具、用户消息、逻辑模型轮次和模型Decision不因HTTP重试重复生成/执行。
- 模型60秒、模型审核30秒及Run剩余总预算保留。读取/退避可取消，连接关闭后可继续处理下一次调用；取消不保证上游停止计费。

## 真实 DeepSeek 验收

仅固定合成内容，经实际生产 ModelGateway、官方端点、trust_env=false；使用现有合法Key作官方认证，不读取或发送会话表，不输出正文/私有思考/Key，不执行工具，SQLite只读。正常用户配置未改。获准的两个逻辑调用共发起5次HTTP尝试，没有继续调用或重发原对话。

| 合成任务与尝试 | 上限 | 观测 | 结果 |
| --- | ---: | --- | --- |
| 复杂执行池设计 #1 | 2048 | HTTP200，length；2048全部为思考，无正文，约13.7秒 | MODEL_OUTPUT_LIMIT，自动重试 |
| 复杂执行池设计 #2 | 4096 | HTTP200，length；4096全部为思考，无正文，约26.4秒 | MODEL_OUTPUT_LIMIT，自动重试 |
| 复杂执行池设计 #3 | 8192 | 已收到HTTP200头，读取约19.1秒后达到逻辑总60秒 | MODEL_TIMEOUT，停止；此项生成验收未通过 |
| 有限CSV实现 #1 | 2048 | HTTP200，length；2048全部为思考，无正文，约10.7秒 | MODEL_OUTPUT_LIMIT，自动重试 |
| 有限CSV实现 #2 | 4096 | HTTP200，stop；上游输出1850，其中思考992，有公开正文，约7.9秒 | ACCEPTED，未截断，无工具 |

成功案例证明新链路能从真实“思考耗尽且无正文”自动恢复并取得完整答案。复杂任务明确暴露现有60秒总等待的限制，不能称为全部真实生成成功；也不能估算超时请求的最终计费token。无需账户充值才能修复这类单次生成上限问题。

## 文件变化与理由

| 文件 | 修改 |
| --- | --- |
| backend/app/llm/retry.py（新） | 有界ModelRetryPolicy、预算/Retry-After纯规则、秘密检查与响应元数据白名单 |
| backend/app/llm/adapter.py | reply/turn共享HTTP/读取/解析/重试循环；取消/总时限、阶段日志与错误分类 |
| backend/app/llm/turns.py | 后端预算覆盖、支持模型显式思考、空正文/预算耗尽区分 |
| backend/app/core/config.py | Settings环境配置与不可变策略 |
| backend/app/services/application.py、chat.py | 将同一策略注入文本/Agent共享网关，构造默认参数兼容 |
| backend/app/core/errors.py、packages/shared/src/contracts.ts | 两个公开错误码、固定消息与状态，Zod/TS同步 |
| backend/.env.example | 重试次数、生成上限与退避变量说明，不存Key |
| backend/tests/test_model_retry.py（新） | 46项确定性策略、网关、安全、取消及Agent/模型审核集成用例 |
| backend/tests/test_models.py | 更新可恢复429/500的有界尝试期望，永久错误仍一次 |
| tests/model-client.test.ts | 新错误码在HTTP边界被识别并显示正确固定文本 |
| scripts/model-harness.mjs / .d.mts | 可模拟指定次数的临时/持续失败；仅合成本地服务 |
| scripts/model-smoke.mjs、tests/e2e/desktop.spec.ts | 验证短暂限流自动恢复、持续失败后手动重试及取消，按实际请求数同步 |
| docs/MODEL_RETRY_GUIDE.md（新）、MODEL_RETRY_OUTPUT_PLAN.md、本报告（新） | 使用/配置/边界、确认状态和实际证据 |
| docs/ADR/0016-bounded-model-retries.md（新） | 保留思考、有界预算/重试与工具不重放的取舍 |
| README.md、AGENTS.md、docs/DEVELOPMENT_PLAN.md、ARCHITECTURE.md、MODEL_SETUP.md | 当前约定、入口与模块数据流 |
| docs/EVALUATION.md、LEARNING_LOG.md、DEEPSEEK_RESPONSE_DIAGNOSIS.md | 验证、学习练习与历史诊断后续链接 |

与本轮源码备份核对：entities、Assistant、ContextBuilder、ToolRegistry、SessionRepository、Main、Preload、生成API类型均字节不变。api:generate确实执行，OpenAPI/生成类型无需手工修改。head仍0008，无SQL迁移、删除或正常用户数据升级。

## 实际验证命令

| 命令 | 结果 |
| --- | --- |
| pnpm api:generate | 通过；生成结果字节不变 |
| pnpm typecheck | 通过 |
| pnpm lint | ESLint/Ruff检查/格式通过，113个Python文件 |
| cd backend && .venv/bin/python -m pytest -q tests/test_model_retry.py | 46/46，通过（1.45秒） |
| pnpm test | 客户端54/54，后端326通过/38默认跳过（50.72秒） |
| pnpm build | Main/Preload/Renderer通过 |
| node scripts/backend-smoke.mjs && node scripts/model-smoke.mjs | 独立后端、本地HTTP模型、重试/断线、持久Agent工具闭环通过 |
| TAVO_BROWSER_CHANNEL=chrome TAVO_TEST_DOCKER=1 pnpm test:e2e | 11/11，通过（约2.1分钟），含实际临时宿主/沙箱流程 |
| pnpm test:dev | 实际开发窗口的模型添加、Key加密/清空、HTTP聊天及设置通过 |
| pnpm package:dir | 未签名macOS ARM64 dir测试包构建通过 |
| TAVO_BROWSER_CHANNEL=chrome TAVO_TEST_DOCKER=1 pnpm test:packaged | 11/11，通过（约2.1分钟） |
| 临时真实合成验收脚本，两次逻辑调用 | 复杂任务最终MODEL_TIMEOUT（退出2）；有限CSV任务自动重试后ACCEPTED（退出0），见上表 |
| git diff --check | 通过 |

新增测试覆盖3种调用入口、重试0/3次、增长与封顶、普通空回复、明确拒绝、配额/永久HTTP错误、Retry-After、八家其他厂商参数、截断文本/工具、敏感回显/非法JSON、临时超时恢复、总时限、读取/退避取消、连接释放、上游生成中断、Agent工具/消息不重复、模型审核只产生一次决定，以及四协议空预算分类。

初次相关测试66/69通过：两个旧测试仍假设429/500绝不重试，已按获准行为更新；未知文本传输异常的旧公开错误被统一函数改动影响，已保持其原MODEL_UNAVAILABLE兼容且不盲目重试。格式检查还指出一条修改过的断言未格式化，已修正；最终类型/Lint和全量普通测试通过。

## 使用与残留边界

重启后端与客户端即可，命令 `pnpm dev:backend` / `pnpm dev`。无需init、重录Key或编辑模型JSON。配置/日志字段见 [操作指南](MODEL_RETRY_GUIDE.md)。本轮临时验收脚本清理，不向日常自动化加入真实付费测试。

三次重试是上限，不保证慢推理任务在60秒内完成，也不保证每次失败都适合重试。公开成功usage保留最后一次响应语义，日志含各次可观测计数，不能当完整账单。诊断仅当前进程日志，没有新持久调用审计表；Worker重启未知在途不自动重放。

38项后端真实专项测试在普通命令中跳过，不能算已通过；桌面相关实际临时Docker/宿主场景另行通过。不调用其他八家真实付费模型、不执行真实部署或远程写操作；测试包未签名，未做公证、正式分发、跨平台或完整负载验收。
