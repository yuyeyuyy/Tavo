# 用前后端知识理解这次重构

这是学习路线与练习，不代表已经掌握。旧 Phase 0/1 笔记保存在 `PHASE_0_1_LEARNING_NOTES.md`。先跑通 README，再按下面顺序读代码。

## 1. 先把熟悉的概念对应起来

| 你熟悉的前后端概念 | Tavo 中的实际对象 |
| --- | --- |
| React 页面调用后端 SDK | `apps/desktop/src/renderer/api/client.ts` |
| Controller / Router | `backend/app/api/routes.py` |
| 请求 DTO 与校验 | `backend/app/api/schemas.py` 的 Pydantic 模型 |
| Service / 业务层 | `backend/app/services/accounts.py` 等 |
| DAO / Repository | `backend/app/repositories/workspace.py` |
| ORM Entity / 数据表 | `backend/app/models/entities.py` |
| 后台任务进程 | `backend/app/workers/scheduler.py` |
| 接第三方 API 的适配器 | `backend/app/connectors/registry.py`，当前只有 Mock |
| 前端服务器状态缓存 | TanStack Query；Zustand 仅保存 busy/error 等交互状态 |
| Electron Main / Preload | 窗口和系统能力宿主；本轮不再负责业务后端 |

例如点击“同步账户”：React → API Client POST → Router 验证身份与参数 → AccountService 创建任务 → SQLite 保存 pending → Worker 执行 → REST/SSE 通知页面刷新。HTTP 返回 202 表示“已接收”，不表示“同步已成功”。

## 2. FastAPI 依赖注入

代码：`backend/app/main.py` 的 create_app/lifespan，`api/dependencies.py` 的 ServicesDep/authenticated，`tests/conftest.py`。

可以在每个路由里 new 数据库，也可以由应用生命周期创建 Services，再用 Depends 取得同一依赖。前者直观但难管理连接和替换测试；本项目选择后者。测试通过 services_factory 注入 MockConnector、假时钟与临时库，不需要修改业务代码。

输入是应用配置和请求上下文；输出是已装配的服务对象。依赖不是全局魔法：lifespan 明确 initialize、yield、close，测试必须进入 TestClient 上下文，才能执行启动与关闭。

面试点：依赖注入与“到处读全局变量”的差别；请求作用域和应用作用域；测试替身为何应放在边界。

## 3. Pydantic 请求验证与 OpenAPI

代码：`api/schemas.py` 的 AddAccount、MockConfig、AccountOut，`main.py` 的 validation_error，`app/export_openapi.py`，客户端 `api/generated.ts` / `api/client.ts`。

Python 类型注解单独使用不会自动验证 JSON；FastAPI 配合 Pydantic 在 HTTP 边界解析和验证。例如 usageUnits 要严格整数且有上限，displayName 去除空白，DTO 禁止多余字段。响应模型也检查 connected 与 credentialConfigured 的一致性。

两种做法：前后端各写一套接口类型容易漂移；从 OpenAPI 生成 TS 类型能减少重复。本项目使用后者，但 TS 类型只在编译时存在，因此外部 JSON/SSE 仍用 Zod 校验。不要手改 generated.ts，改 Pydantic 后运行 `pnpm api:generate`。

默认校验异常可能回显输入，当前统一只返回固定 INVALID_INPUT，避免把敏感值拼进异常。面试点：编译期类型和运行时验证、DTO 和 ORM 的区别、400/401/403/404/409/422/202 的语义。

## 4. Python async / await

代码：`connectors/registry.py` 的 sync，`workers/scheduler.py` 的 tick/execute_sync/run，`api/routes.py` 的 stream。

可以用同步函数顺序等待，也可以 async 在 I/O 等待期间让出事件循环。本项目 Mock 以 asyncio.sleep 模拟网络等待；Worker 用 create_task 同时执行有限数量的账户。async 不会自动创建线程，SQLite 同步查询也不会因为外面写了 async 就变成非阻塞。

数据库事务保持短小，不跨连接器等待。超时后 cancel 只是取消请求，不保证第三方库一定响应；保留未结束请求的账户/并发名额，晚到结果必须重新通过状态与 fence 检查。面试点：并发与并行、协作取消、超时与任务终态不是同一概念。

## 5. SQLAlchemy ORM 与事务

代码：`models/entities.py`、`repositories/database.py` 的 session、`repositories/workspace.py` 的 commit_sync；测试 `tests/test_worker.py`。

ORM 把行映射成 Python 对象，session 负责跟踪修改并提交。Session 和 HTTP 客户端 session 同名但职责完全不同。读取会话结束后关闭；写会话 BEGIN IMMEDIATE，成功 commit，异常 rollback。

两种保存方式：数据、cursor、任务和事件分次提交，代码局部简单但可能留下半次同步；同事务提交能保证整体成功或整体失败。项目选择同事务。数据库还有唯一约束，避免“先查询没有，再插入”的并发竞态。

重要例子：如果先推进 cursor，再保存数据失败，下次请求可能永久跳过这批数据。测试在 sync.completed 写入时故意触发数据库错误，证明此前数据、cursor、变化事件和成功状态全部回滚。失败余额不是 0，而是“当前请求失败，保留上次成功值”。

面试点：ACID、幂等、复合/部分唯一索引、WAL、事务持锁时间；外部 API 不在本地数据库事务里，不能声称 Exactly-once。

## 6. Alembic 与数据迁移

代码：`backend/migrations/env.py`、`versions/0001_backend_independent_backend.py`、`services/migration.py`、`tests/test_migration.py`。

Alembic 是数据库结构版本管理，像代码的版本演进。`init` 执行 upgrade head；既有迁移不能随意修改，新增字段应生成新 revision、审查 SQL，再写升级测试。autogenerate 只生成候选变更，不能自动判断业务兼容性。

旧 Drizzle 库的数据转换是另一件事，由 import-legacy 承担：一致性备份 → 独立目标写事务 → 数量/ID/外键核验。直接删库重建会丢失状态，本项目保留源库用于回滚。旧 safeStorage 密文无法由 Python 解密，保留归档并要求重新授权，不能把“有密文字节”当成“授权可用”。

面试点：Schema migration 与 ETL、向后兼容、备份 WAL 数据库、回滚源码不等于回滚数据。

## 7. REST API 与长任务

代码：`api/routes.py`、`services/accounts.py` 的 enqueue、`services/agent.py`，API 清单在 `docs/API.md`。

普通 CRUD 用 HTTP；长同步/分析先创建持久记录，再返回 202 和稳定 id，客户端 GET 状态。不要在请求函数里一直等待完整任务，也不要用 FastAPI BackgroundTasks 承担跨进程恢复需求。

分页有两种：账户/任务用 limit+offset，易理解；事件用 sequence 游标，更适合持续追加的数据。本项目同时使用。删除账户采用软删除并保留审计历史，列表默认不返回已删除连接。

面试点：资源建模、状态码、分页稳定性、幂等写与重复点击、请求取消不等于后台任务取消。

## 8. SSE：后端向前端推事件

代码：`api/routes.py` 的 stream、`services/events.py`、客户端 `APIClient.subscribe`、`renderer/store.ts`；测试 `tests/test_sse.py` 与根目录 `scripts/backend-smoke.mjs`。

轮询每次发新请求，简单但有等待和空响应；SSE 保持一个 HTTP 响应，服务端不断写文本帧，适合单向通知。本项目 SSE 帧有 id、event、data，id 使用持久 sequence，不使用毫秒时间排序。心跳注释保持流活跃。

客户端读流时要考虑分块：一个网络 chunk 不保证刚好是一条事件，所以先缓冲再按空行拆帧。断开后用 Last-Event-ID / after 恢复，按 sequence 去重。SSE 只用于及时展示，最终状态仍在数据库，可用 taskId / runId 查询。

面试点：SSE 与 WebSocket 的适用场景、断线重连、重放去重、浏览器认证方式、代理缓冲。当前未实现 SSE 连接数量限制或水平扩展评测。

## 9. 服务端调度、租约与 fencing

代码：`workers/scheduler.py`，`models/entities.py` 的 WorkerLease，`repositories/workspace.py` 的 require_leader。

进程内定时器只能保证本进程不重复执行；多个 API/Worker 同时启动时需要共同判断。项目用 SQLite 单行租约选领导者，持有者续租，过期者可被接管。每次接管增加 fence，提交数据必须匹配当前 owner/fence。

只有锁还不够：旧 Worker 网络等待太久，租约失效后仍可能带着结果回来。fencing 在数据库写入处拒绝它。任务和 nextAttemptAt 持久化，重启不会忘记重试。永久错误如 401/403 停止自动重试；临时错误指数退避加抖动。

两种方案：Redis/Celery 适合更大规模和跨机器队列；本地 SQLite 队列依赖少，适合当前单机小规模，但需自行管理租约、恢复和短事务。本轮选后者，不能据此宣称完整分布式调度系统。

面试点：至少一次处理、租约与互斥锁、过期执行者、重试风暴、退避抖动、崩溃恢复。

## 10. LangGraph StateGraph

代码：`agent/graph.py`，`workers/scheduler.py` 的 execute_agent，`tests/test_worker.py` 的图等价/MemorySaver 测试。

State 是工作流传递的数据结构；Node 是接收 State、返回部分更新的函数；Edge 决定下一步。当前 START → classify → explain → END，输入只有白名单事件字段。余额使用 Decimal，USD<10 / CNY<50 触发 warning，与旧 JS 输出等价。

纯函数就能完成当前规则，依赖更少；使用 StateGraph 则能学习分步骤状态传递、流式节点更新和将来的暂停恢复。用户明确要求最小图，故采用两个节点，不接真实模型。

分析结果持久化和图 checkpoint 不同：结果在 agent_runs / 原事件 analysis；MemorySaver 仅测试，进程退出会丢。create_graph 的 checkpointer 参数可接将来的持久化实现，但 Tool Calling、Memory、HITL 还需要明确权限、工具幂等和恢复协议，当前只有扩展入口。

面试点：状态图与普通调用栈、checkpoint 与长期记忆、确定性步骤与模型步骤、Human-in-the-loop 的恢复边界。

## 11. 客户端认证与服务端 SecretStore

代码：`core/security.py` 的 SessionService / SecretStore，`core/http_security.py`，`apps/desktop/src/main/backend.ts`，`tests/test_api.py`。

首先区分两种凭据：客户端凭据证明“这个桌面能访问 Tavo 后端”；第三方凭据证明“后端能读取某个平台账户”。后者永不返回客户端或 Agent。

客户端持有 bootstrap 文件，换取 15 分钟短会话；服务器只保存摘要。Main 读取私有文件、保存短会话并为精确后端来源注入 Authorization；Renderer 不拿到认证原文。CORS 只限制浏览器跨域读取，不能代替身份认证；监听 localhost 也不等于安全，仍检查 Host、Origin 和认证。

服务端使用 Fernet/MultiFernet 认证加密第三方凭据。可选 OS Keyring 或独立外部密钥环；本项目选私有文件环以便无桌面运行，代价是备份和部署保护责任。密文和密钥都泄漏时保护失效；文件 0600 不是多用户授权系统。初始化和旋转先取得数据库写锁，避免两个进程覆盖彼此的密钥环；旋转保留新旧键，事务重加密，不自动裁剪旧键。

面试点：加密与认证/授权的区别、摘要与加密的区别、会话撤销、Origin/Host/CORS、TLS、密钥轮换。单所有者尚未实现 SaaS 用户和租户隔离。

## 三个自己动手的小练习

### 练习 1：增加一个后端过滤参数（20–30 分钟）

目标：给 GET /api/v1/events 增加可选 sourceId 过滤，不修改数据库结构。

文件：`backend/app/api/routes.py` 的 events、`services/events.py`、`repositories/workspace.py` 的 EventRepository.list、`backend/tests/test_api.py`。

沿 Router → Service → Repository 传递 source_id：HTTP 别名 sourceId，限制字符串长度；Repository 增加 BusinessEvent.source_id 条件。当前只有 Mock，可以测试 sourceId=mock 返回对应事件，sourceId=unknown 返回空列表，不传参数保持现有结果。验收：分页仍生效；超长参数返回 422；未认证仍返回 401。最后运行 `pnpm api:generate`，在 /docs 确认新参数。先用后端测试完成，再选择是否加 UI 筛选。

### 练习 2：修改 Python 退避抖动（20–30 分钟）

目标：把 retry_delay 的 50%–100% 改为 75%–100%，保留 30 秒基础上限和 Retry-After 优先级。

文件：`backend/app/workers/scheduler.py`、`backend/tests/test_worker.py`。

先手算 attempt=1/random=0、attempt=2/random=1，再改纯函数和断言。验收：首次网络错误仍重试成功，401 不重试，Retry-After 大于基础退避时仍采用它。运行 `pnpm test:backend`；不要为这个练习改数据库。

### 练习 3：给分析结果增加一个解释字段（40–60 分钟）

目标：为 Analysis 增加可选 `ruleVersion` 字符串，给新图结果设置固定公开版本，旧历史缺字段仍可读。

文件：`api/schemas.py` 的 Analysis、`agent/graph.py`、`packages/shared/src/contracts.ts` 的 analysisSchema、Python 图测试与客户端；改完运行 `pnpm api:generate`。

验收：/docs 和生成的 TS 类型出现该字段；新结果保存并重启可读；旧分析 JSON 不含字段也能解析；API 参数不接受任意额外字段。先解释为什么这是 JSON 契约演进，当前无需新增 ORM 列，也不能随意改已执行的 Alembic migration。

共同验证：`pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm build`。涉及桌面交互再做 E2E 或人工点击。每次记录输入、输出、状态变化和实际测试结果。

## 官方资料

阅读项目代码后，可对照 [FastAPI Depends](https://fastapi.tiangolo.com/tutorial/dependencies/) 理解依赖声明；对照 [SQLAlchemy 2.0 Session](https://docs.sqlalchemy.org/en/20/orm/session_basics.html) 学习提交、回滚和会话生命周期；对照 [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) 理解 State、Node、Edge；密钥环原理参考 [Fernet / MultiFernet](https://cryptography.io/en/latest/fernet/)。这些说明支持框架原理，项目实际业务行为仍以测试为准。
