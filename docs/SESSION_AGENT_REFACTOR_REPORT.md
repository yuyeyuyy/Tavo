# Session Agent 核心实施报告

日期：2026-11-16。用户已确认完整设计（见 [方案](SESSION_AGENT_REFACTOR_PLAN.md)），本轮完成账户功能退出与通用 Session Agent 闭环。当前工作区沿用 `feat/agent-foundations`；保留此前未提交的客户端/模型/凭据工作，没有提交、推送、发布或迁移正常用户数据。

## 交付行为

- 移除账户连接/余额/同步/事件/SSE/账户偏好/旧事件分析的运行代码与客户端死契约。旧接口明确410 FEATURE_REMOVED，旧导入 CLI 退役。旧表、原有数据、原始SQL、0001–0004、模型配置与Key均保留。
- 持久 Session、UserMessage、AssistantMessage、ToolCall、ToolResult 和 Message 判别联合；Run 支持幂等入队、单会话互斥、分页、取消、显式重试和关联审计。认证会话与对话会话独立。
- Assistant 复用 LangGraph model/review/tools 循环；Context 冻结完整历史窗口，按 UTF-8 请求预算裁剪完整旧轮次。持久步骤恢复，不声称完整 LangGraph checkpoint。
- Tool Registry 仅时间和有界 Decimal 计算；Reviewer 的确定性规则返回 allow/deny/ask。人工批准/拒绝持久化，绑定 Session/Run/调用参数摘要/revision/策略/到期；不能升级白名单或更改参数。
- 四类真实模型协议复用既有网关，保留九家预设；必要隐藏推理/签名续接仅加密留在服务端，轮换一并处理。保留原文本 API、模型目录、客户端添加模型与JWE/CLI Key功能。
- 桌面继续单页对话，新增会话选择/重启恢复、工具审计与确认操作。新对话或切换模型/工具模式切换会话，显式停止才取消。离线 Mock 不自动上传/合并。

## 文件变化与设计理由

| 文件/模块 | 变化与理由 |
| --- | --- |
| `backend/app/agent/contracts.py`、`api/sessions.py`、`services/sessions.py` | 明确的 Pydantic 领域/公开契约和认证服务边界，用户只可提交用户文本或决定 |
| `repositories/sessions.py`、`models/entities.py`、`migrations/versions/0005_session_agent_core.py` | 七张新表、事务入队/审核/结果、序列/幂等/活动运行约束，保留旧结构 |
| `models/base.py`、`models/legacy.py`、`migrations/env.py` | 活动和归档元数据分离，Alembic仍认识保留表，防止误删 |
| `agent/assistant.py`、`context.py`、`tools.py`、`mock.py`、`graph.py` | 可注入运行对象、受限工具/规则、冻结上下文、Mock与真实模型共用执行路径 |
| `workers/scheduler.py`、`services/application.py` | 通用Run队列、lease/fence、审核到期/恢复，退出账户定时分支 |
| `llm/turns.py`、`llm/adapter.py`、`core/private_state.py`、`json_data.py`、`security.py` | 四协议关联/白名单续接、响应上限/严格JSON/Key回显拒绝、绑定加密和轮换 |
| `api/routes.py`、`api/schemas.py`、`main.py`、`cli.py`、`core/config.py`、`core/errors.py` | 活动入口和错误清理，旧能力明确退役；认证/模型/凭据链路保留 |
| 删除 `connectors/`、账户/事件/旧Agent/导入 Service、旧 Repository | 删除依赖账户概念的活动实现，保留历史数据库及SQL。改动前源码已备份到 `/private/tmp/tavo-before-session-agent-XXXXXX.tar.gz`，不包含私有数据目录 |
| `renderer/chat/Chat.tsx`、`session-store.ts`、`mock.ts`、`styles.css` | 持久会话/轮询/取消/审核/历史工具展示、generation拒绝晚到请求、离线演示文案 |
| `renderer/api/client.ts`、`sessions.ts`、生成 `generated.ts` / `backend/openapi.json`、shared | Zod验证新的判别联合、精确Mutation参数；删除账户/SSE死契约，保留旧错误ID兼容 |
| 新Session/协议/恢复/升级/HTTP安全测试、客户端会话测试、E2E和烟雾脚本 | 以当前功能验证替换已退出的账户专属测试；保留模型、凭据、密码学/并发/隔离回归 |
| ARCHITECTURE/API/ADR0009/README/MODEL_SETUP/学习与评测/AGENTS | 更新当前行为与数据流，旧阶段报告仅作历史 |

上述是本轮变化，不把之前未提交的模型/凭据实现全部归为新交付。没有新依赖；既有锁文件变化来自之前已授权工作。

## 自动化证据

本机 macOS ARM64，Node24 / pnpm11 / Python3.12；容器Linux Python3.12。所有运行测试使用临时数据、合成Key和本地/HTTPX协议替身，不使用真实厂商或用户账户。

| 实际命令 | 结果及含义 |
| --- | --- |
| `pnpm api:generate` | 成功生成OpenAPI/TS，未手改生成文件掩盖漂移 |
| `pnpm lint` | ESLint、Ruff检查/格式均通过 |
| `pnpm typecheck` | 通过 |
| `pnpm test` | Vitest **33**、Pytest **138**，全部通过 |
| `node scripts/backend-smoke.mjs` | 实际独立后端：认证、Session入队幂等、审核跨重启、批准工具闭环、410退役，通过 |
| `node scripts/model-smoke.mjs` | 实际独立后端+本地HTTP模型：加密Key、文本兼容/错误/断开/重启、两轮工具关联，通过 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm test:dev` | 实际开发窗口：空目录添加模型、WebCrypto提交/清空Key、HTTP模型回复、新对话/设置，通过 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm test:e2e` | 构建成功，**6/6**桌面场景通过 |
| `pnpm package:dir` | ARM64未签名测试.app成功；默认Electron图标，无签名公证/发布 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` | **6/6**包内场景通过（含审核/停止重试/窗口及后端重启恢复） |
| `docker compose config --quiet` | 配置检查通过 |
| `docker compose build` | 实际镜像构建通过，最终恢复/响应校验也更新到测试镜像 |
| `pnpm test:docker` | 实际隔离Linux容器：非root/无Node、API无Worker时排队、独立Worker重启/人工审核/工具、加密续接/API重启，通过；专用测试卷已清理 |
| `git diff --check` | 通过 |
| 旧迁移逐文件与实施前源码备份比较、SQL fixture哈希、升级测试中的Alembic `command.check` | 0001–0004 **4/4**未改变；旧SQL哈希/元数据无漂移，通过 |

关键覆盖：Session/消息归属和顺序、幂等/冲突/并发活动约束、跨会话审核拒绝、人工决定重复/冲突/过期/参数变更、非法/未知工具、除零、重复调用ID/超量/截断、畸形/过大/重复JSON参数、非有限数字、无效Unicode输入、4轮预算、完整旧轮次裁剪/冻结、失租/崩溃未知计费请求、已保存提议恢复、取消后迟到模型响应、工具超时名额保留和迟到结果丢弃、续接绑定/轮换/公开隔离、HTTP认证/Host/Origin/正文限制。

0004→0005隔离升级测试逐表比较所有既有行（包含合成账户、余额、pending旧队列、模型/密文），只增加七张表；升级后Key仍可解密、外键检查通过、旧任务保持原状态且新Worker不执行。正常用户库尚未升级，由用户按下节操作。

桌面场景覆盖空配置添加模型/加密Key、失败清空与版本状态、真实HTTP替身/限流重试/停止、输入法/换行/纯文本XSS/窄窗口/六方法隔离+CSP、认证离线文档/重连/焦点，以及工具批准/拒绝/取消重试/会话切换/双重启恢复。截图在 `test-results/chat-desktop-*` / `chat-packaged-*`；已目视检查审核和窄窗。修正了轮询短暂不同步时最终回复与等待占位并存，以及审核操作易被历史记录挤到滚动下方的展示。

初次回归失败包含新测试对返回Run形状的错误假设、裁剪测试预算过宽、Mock“几点”未识别和动态等待占位的严格locator；均定位后修正并执行相关回归，没有加入自动重试掩盖。上一轮客户端Key偶发422未在本轮完整开发/E2E/包内回归复现，不能据此认定历史偶发原因已查明。

## 升级与手工体验

1. 停止你原来的客户端和后端；一致性备份数据库及独立密钥文件（常见位置 `backend/.local`）。不要删库，也不要只备份数据库而丢掉密钥。
2. 仓库根目录执行 `pnpm backend:init`；增量升级至0005后 `pnpm dev:backend`。另一个根目录终端执行 `pnpm dev`。
3. Mock发送“计算12 / 3”；或新对话选“工具逐次确认”，发送“计算8 * 7”，批准/拒绝查看审计。等待审核时重启再继续，确认恢复。正常Key仍在设置中使用。
4. 使用真实模型时需模型ID支持对应工具协议。不支持时明确选择纯文本；保存Key不发模型请求，显式重试可能再次计费。

## 残留边界

- 非token流式轮询、单所有者/单机SQLite，无公网多租户或大规模吞吐验证。未接真实厂商付费/权限/网络；九家预设不是所有模型ID工具能力保证。
- 会话正文和普通业务字段有意存盘且未整体加密；Key/隐藏协议续接加密，保护密钥与备份。删除偏好/退出账户功能不等于清除历史/备份。
- 仅时间/计算只读工具、4轮/8工具/64KiB窗口等受控预算；没有长期记忆、RAG、MCP、多Agent、写工具或完整graph checkpoint。
- 超时不能杀线程，名额留至结束；失租/取消拒绝晚到写入。崩溃未知模型请求不会自动重发，但不保证厂商未计费或外部Exactly-once。
- `.app`未签名，未验证Windows、正式分发/签名/公证、规模性能或完整安全审计。没有自动提交/推送，工作区仍含之前的未提交工作及用户 `.AGENTS.md.swp`，未处理该编辑器文件。
