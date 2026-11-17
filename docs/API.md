# 当前 API

> 2026-11-17策略3增量：新增`GET /sandbox-network-targets`（管理员预配目标/凭据状态，无Token），`GET /sessions/{session_id}/tool-calls/{call_id}/approval`（具体HTTP或工作区版本预览）。原Decision approve/reject+revision接口复用，不接受客户端改参数/目标。专用JWE用途新增`sandbox.api_key`，与`model.api_key`分离绑定。
>
> `/tools`新增`workspace_write`（path/content/offset/expectedSha256）和`http_request`（精确requestId）。Bash参数支持resourceProfile，长参数按策略配置校验；公开结果新增success/errorCode/errorMessage/durationMs/sandboxId，保留exitCode/stdout/stderr/timedOut/truncated。runtime新增networkPolicy，externalNetwork仅表示受控代理模式，执行容器始终禁网。旧错误码保留历史，新调用区分参数、策略、模型、审批、网络、资源、执行和取消。详见[Bash指南](BASH_EXECUTION_POLICY_GUIDE.md)，以生成OpenAPI和Zod为准。

前缀 `/api/v1`。活动契约由 Pydantic → `backend/openapi.json` → `renderer/api/generated.ts` 生成，客户端同时维护严格 Zod 验证。认证文档在 `/docs`，静态资源本地托管。下列 Session 是持久对话，不是认证 Token。

## 认证和模型兼容接口

`POST /auth/session` 用 bootstrap Bearer 换短期认证；其他业务必须携带认证会话或受保护文档 Cookie。精确 Origin/Host 校验、32 KiB 请求上限、安全错误不回显输入。凭据及对话响应 no-store。

保留 `GET /health`、`GET /system/status`、`GET /model-providers`、`GET/POST /model-profiles`、`GET /chat/models`、`POST /chat/completions` 及原凭据挑战/提交端点。文本聊天仍是同步无状态接口，不创建 Session、不执行工具；真实会话请使用下列异步持久接口。Key 操作见 [MODEL_SETUP.md](MODEL_SETUP.md)。

## Session 与 Run

| 方法/路径 | 输入与结果 |
| --- | --- |
| POST `/sessions` | `{profileId:"mock"或配置ID, reviewMode:"auto_readonly"或"ask"或"disabled"或"auto_sandbox", bashEnabled:true}` → 201 Session |
| GET `/sessions?cursor=&limit=30` | 稳定 createdAt/id 降序游标分页，items/nextCursor |
| GET `/sessions/{sessionId}` | 设置、title、revision、activeRunId、latestRunId |
| POST `/sessions/{sessionId}/messages` | `{clientMessageId,content,expectedRevision}` → 202 `{session,message,run}`；content 非空且最多4000码点 |
| GET `/sessions/{sessionId}/messages` | 默认最新页，页内 sequence 升序；`afterSequence` 前进、`beforeSequence` 读取更早页，二者互斥，limit 1–100；items/hasMore/nextSequence |
| GET `/sessions/{sessionId}/runs/{runId}` | status/step/rounds/toolCount/context摘要/toolCalls/固定 errorCode |
| POST `/sessions/{sessionId}/runs/{runId}/cancel` | 持久取消，终态重复调用幂等；返回 Run |
| POST `/sessions/{sessionId}/runs/{runId}/retry` | 仅最新失败/中断/取消 Run 可重试；新 Run 关联原 Run并复用 UserMessage，重复返回同一子 Run；202 Run |
| POST `/sessions/{sessionId}/tool-calls/{callId}/decision` | `{action:"approve"或"reject",expectedRevision}` → Decision；同决定重复幂等、冲突409；只针对该会话合法等待中的调用 |
| GET `/tools` | 已注册工具的 schema/version/readOnly；当前仅时间与 Decimal 计算 |

`clientMessageId` 在 Session 内唯一：内容相同重放返回原消息/Run，不因旧 expectedRevision 重复创建；不同内容409。同 Session 仅允许一个 pending/running/waiting_review Run。查询/审核/取消均校验 Session 归属，无跨会话寻址。

Message 为判别联合：`user`（content/clientMessageId）、`assistant`（content/final/truncated/usage）、`tool_call`（call）、`tool_result`（result）；共同包含 id/sessionId/runId/sequence/createdAt。用户不能 POST 任意 role、工具结果、上下文或策略。

Run 状态为 pending → running → waiting_review（可选）→ pending/running → succeeded/failed/interrupted/cancelled。工具调用 proposed/waiting_review/approved/running/终态与 Run 独立记录。审核过期固定 REVIEW_EXPIRED，不会默默执行；拒绝产生 denied ToolResult，模型可继续解释。

Context API 只返回窗口/裁剪/策略与预算摘要。隐藏思考、协议签名、续接密文、API Key、认证材料不会出现在 Run/Message 中。模型调用期间进程崩溃且响应未知会 interrupted，不自动重发付费请求；客户端显式重试不意味着上次未计费。

## 退役接口

账户、连接器、同步任务、事件/SSE、snapshot、同步设置、旧 `/agent/*` 路由固定 410 FEATURE_REMOVED，并从 OpenAPI 隐藏。导入旧账户的 CLI 同样退役。数据库旧表原样保留，不能把410误当成没有历史数据。

客户端以 REST 轮询展示，非 token 流式。断开轮询不等于取消；停止必须调用 cancel。当前单所有者、单机 SQLite，无租户隔离或公网生产承诺。


## AgentPool/自审查/Bash扩展（0006）

SessionCreate的bashEnabled为严格bool，省略时默认true；reviewMode=disabled且省略该字段时默认false，显式true+disabled仍拒绝。Session返回实际保存的权限。创建会话不再依赖Docker就绪，普通聊天可继续；Bash调用不可用时返回失败BASH_UNAVAILABLE，绝不回落宿主执行。旧会话/冻结Context不自动提权，显式false保留兼容。

新增认证no-store `GET /api/v1/agent-runtime`：pool={capacity,activeRuns,occupiedSlots,cleanupSlots}为当前API进程统计；bash={enabled,available,reasonCode,workspacePolicy:"session-volume",externalNetwork:false}。分离Worker部署时不把API本地计数当全局运行量。

Decision来源扩展model/mock，summary可空且最多240码点。策略2对合法提议做独立无工具自审查，结构化结论由规则合并；工具结果与审核摘要不授予新权限。ToolSpec.readOnly允许false，原有两工具保持true。

Bash输入command/cwd/timeoutSeconds，输出exitCode/stdout/stderr/truncated/timedOut；未知执行固定TOOL_EXECUTION_UNKNOWN，不能自动重跑，未知工作区下一次Bash强制人工确认。现有取消/重试路径保留，限制由后端执行。具体预算与沙箱条件见 [BASH_TOOL.md](BASH_TOOL.md)。

新增响应字段需要严格Zod客户端同步升级；既有请求省略bashEnabled仍为false，模型/Key及旧无状态文本接口保持原契约。

## 2026-11-17：命令权限与公网默认值

`POST /sessions`的`reviewMode`新增`full_access`（默认）、`auto_review`、`request_approval`，仅决定Bash审批；旧`auto_readonly/ask/disabled/auto_sandbox`仍接受并按旧语义执行。已有String列兼容，无迁移。返回ContextSummary.policyVersion为4；生成类型与Zod同步。切换档位由客户端开始新会话，不更新既有Run。

新三档中的其他合法工具免额外审批。HTTP出口默认允许公网HTTPS URL及GET/HEAD/POST/PUT/PATCH/DELETE，不产生独立请求批准；当前配置与冻结权限取交集。绑定凭据仍使用原目标别名与精确方法/路径。`GET /sandbox-network-targets`继续列出预配置别名/凭据目标，不代表所有可访问公网URL列表。管理员显式恢复写审批时，approval preview仍提供具体URL目标摘要。旧Decision与等待批准调用不重解释。

## 2026-11-17：策略5双命令入口

新三档会话返回executionScope=host_and_sandbox，迁移旧会话为sandbox_only。该字段只读，SessionCreate不接受客户端指定执行范围；没有用户切换执行位置开关。策略5声明sandbox、host_bash及原时间/计算/沙箱辅助工具，最多6项；旧bash始终Docker兼容语义。冻结策略1–4与待批准不升级。

host_bash参数command/cwd/timeoutSeconds，结果沿用执行字段并增加executionTarget、executionId；宿主sandboxId=null。/agent-runtime增加hostBash(enabled/available/reasonCode/executionTarget=backend-host)，原bash字段保留Docker语义。错误新增HOST_UNAVAILABLE。OpenAPI生成TS和Zod同步，前端明确执行位置。host_bash当前用户身份与无容器隔离边界见[指南](HOST_BASH_GUIDE.md)。
