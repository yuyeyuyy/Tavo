# Tavo API v1

契约来源：`backend/app/api/schemas.py` 与 Router。`backend/openapi.json` 和 `apps/desktop/src/renderer/api/generated.ts` 通过 `pnpm api:generate` 生成。运行时 `/docs` 与 `/openapi.json` 均需认证。

## 通用约定

- 本机基址 `http://127.0.0.1:8000`。JSON 字段 camelCase，UTC 时间 ISO 字符串，金额保留两位小数的字符串。
- 所有业务接口、health、SSE 均要求短会话；bootstrap 只用于创建会话。令牌不放 URL。
- 统一错误：`{"error":{"code":"INVALID_INPUT","message":"请求或数据格式不正确。"}}`，不回显请求、SQL、凭据或堆栈。
- 401 未认证/过期；403 来源/权限拒绝；404 不存在；409 重复/忙碌/状态冲突；422 参数或数据不合法；413 正文超过 32 KiB；503 安全存储不可用/租约丢失；500 内部/存储错误。
- Pydantic 禁止请求 DTO 多余字段；ID 和字符串长度受限。CORS 与 Host 精确白名单，不能使用开放跨域代替认证。

## 认证

`POST /api/v1/auth/session`：Authorization Bearer 携带客户端 bootstrap 文件值，返回 `accessToken`、`expiresAt`、`tokenType: bearer`，会话默认 900 秒。响应 no-store；API 客户端后续携带短会话，数据库只存摘要和到期时间。旋转 bootstrap 文件撤销旧会话。

浏览器 `/docs` 使用 Basic 用户 `owner` 与 bootstrap 值，或已建立的 HttpOnly / SameSite=Strict Cookie；Swagger 资源本地托管。Electron 在 Main 注入会话，Renderer 没有获取令牌的方法。第三方账户凭据从不经这些查询接口返回。

## 系统与连接器

| 方法与路径 | 响应 |
| --- | --- |
| GET `/api/v1/health` | `{status:"ok",version:"0.1.0"}`；只表示 API 可响应 |
| GET `/api/v1/system/status` | `workerActive`、`mode:single-owner`、服务端安全存储状态 |
| GET `/api/v1/snapshot` | 兼容现有 UI 的聚合视图；最多 200 连接/任务、300 新事件、旧事件 100/旧任务 50 |
| GET `/api/v1/connectors` | 连接器定义、capabilities、permissions；当前 mock |
| GET `/api/v1/connectors/{id}/capabilities` | 指定连接器能力或 404 |

## 账户与设置

| 方法与路径 | 说明 |
| --- | --- |
| GET `/api/v1/accounts` | limit=50（1–200）、offset=0、可选 status / connectorId；items/total/limit/offset |
| POST `/api/v1/accounts` | 201，创建只读 Mock 账户与后端加密模拟凭据 |
| GET `/api/v1/accounts/{id}` | 公开账户状态，无 credentialRef 或密文 |
| DELETE `/api/v1/accounts/{id}` | 204，取消同步、清理有效凭据与最新数据、软删除连接；保留历史 |
| POST `/api/v1/accounts/{id}/disconnect` | 清理有效凭据、取消同步，保留连接和最近成功数据 |
| POST `/api/v1/accounts/{id}/reconnect` | 后端生成新的模拟凭据，恢复 connected |
| PATCH `/api/v1/accounts/{id}/mock` | 替换完整 MockConfig，取消旧任务，数据配置改变则增加 revision |
| GET / PATCH `/api/v1/settings` | 完整 AppSettings；autoSync、syncIntervalSeconds=10–86400 |

创建示例（无秘密）：

```json
{"connectorId":"mock","externalAccountId":"demo-a","displayName":"学习账户 A"}
```

MockConfig：`balance:"8.50"`、`currency:"USD"|"CNY"`、`usageUnits:100`、`resourceStatus:"healthy"|"degraded"`、`scenario`。场景为 success/network_once/rate_limit_once/server_once/timeout/auth/forbidden/invalid。没有真实外部 HTTP 请求。

账户状态 disconnected/connecting/connected/expired/error。相同 connectorId + externalAccountId 不允许存在两个未删除连接。Settings 中 startMinimized 是历史兼容字段；当前窗口偏好由客户端原生配置单独管理。

## 同步与 Agent 长任务

| 方法与路径 | 说明 |
| --- | --- |
| POST `/api/v1/accounts/{id}/sync` | 202，持久 pending 任务；返回稳定 id，不等待同步完成 |
| POST `/api/v1/accounts/{id}/sync/cancel` | 204，取消当前活动同步；晚到响应不能提交 |
| GET `/api/v1/sync-tasks` | limit=50（1–200）、offset、accountId / status 过滤 |
| GET `/api/v1/sync-tasks/{id}` | 任务状态、attempts/retryCount/maxAttempts、各时间、errorCode |
| POST `/api/v1/agent/analyze` | `{eventId:"..."}` → 202 持久 Run；同事件已有活动分析时 409 |
| GET `/api/v1/agent/runs` | limit=50（1–200）、offset，按新到旧返回运行记录 |
| GET `/api/v1/agent/runs/{id}` | status、progress 0–100、result、错误与时间 |

任务状态 pending/running/succeeded/failed/cancelled。同步失败在任务 errorCode 中表达，GET 一个失败任务仍返回 200。网络/超时/429/临时服务失败最多尝试 3 次；401/403/格式不合法不自动重试。API 请求取消不会自动取消后台任务。

Agent 只接受持久 data.changed 或已迁移的旧余额事件。结果为 eventId、severity、summary、evidence、recommendation、mode=deterministic-mock。两节点 Python StateGraph，无模型调用；进度和最终结果写数据库。

## 历史事件与 SSE

GET `/api/v1/events`：limit=50（1–300），可选 before（sequence）、accountId、correlationId、eventType。返回 items（新到旧）、nextCursor；后续用 before=nextCursor，null 表示没有下一页。

GET `/api/v1/events/stream?after=41`：Authorization 携带会话，Accept text/event-stream；可传 Last-Event-ID，取它与 after 的较大值。有效范围 0–2^63−1，非法返回 422。

```text
id: 42
event: business
data: {"eventId":"...","sequence":42,"eventType":"sync.started",...}

: keepalive

```

事件包括 account.connected/disconnected，sync.started/completed/failed/retry_scheduled/cancelled，data.changed，agent.started/progress/completed/failed。每条保留 sourceId/accountId/occurredAt/correlationId/payload。同步尝试共享任务 ID 作为 correlationId，Agent 使用 runId。

会话过期时发送 `event: session.expired` 后结束；客户端重新认证并重连。事件来自持久数据库，进程重启后可继续回放；客户端按 sequence 去重。服务端不把“已发送”视为业务消费确认。没有持久消费者 offset、全局消息队列或无限连接承诺；任务最终状态用 REST 查询。
