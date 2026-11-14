> 历史资料：以下文件路径和测试计数对应旧架构。旧代码已清理，可在 Git 基线 `898328d` 查看；当前入口见 ARCHITECTURE.md / LEARNING_NOTES.md，清理说明见 LEGACY_CLEANUP.md。

# Phase 0 学习笔记

这些是本轮实现涉及的知识与复盘题，不代表开发者已经掌握。建议用代码和测试自己复述一遍。

## 1. Electron 进程模型与安全 IPC

**解决的问题**：网页 UI 不能因为一个输入就取得文件系统或密钥权限。Main 运行 Node 业务，Preload 用 contextBridge 暴露最小方法，Renderer 只消费 DTO。

**技术**：context isolation、sandbox、CommonJS preload、精确 URL 和 sender 主框架/窗口校验、unknown + Zod、判别联合 `Response`。TypeScript 类型只在编译期有效；IPC 和外部输入仍需运行时校验。

**两种方案**：每操作一个白名单方法容易审计；通用 `invoke(channel,args)` 方便但暴露过多权限。本项目选择前者。开发 HMR 需要本地连接和内联脚本，生产 CSP 维持严格策略。

**扩展注意**：新增操作必须同时定义输入、输出、权限和错误码，不能把任意工具、SQL、URL、文件路径变成参数。

**面试追问**：启用 contextIsolation 后为什么仍要校验 sender？为什么只校验 origin 不够？

**10–20 分钟练习**：给 IPC 测试增加一条“同一路径、不同查询字符串”或“同 URL、其他窗口”的拒绝用例，解释预期原因。

## 2. TypeScript 端口、依赖注入与连接器

**解决的问题**：相同 Core 既能在 Electron，也能在 Node 测试中运行。Storage、AgentRuntime 是接口，由宿主决定具体实现；Capability 描述可提供的数据，宿主权限约束实际可调用行为。

**技术**：interface、泛型事件映射、`keyof`、Zod infer、构造参数注入时钟、async 生命周期、Map 注册表。

**两种方案**：Core 直接 `new SqliteStorage()` 简单但耦合环境；构造参数注入使替换和故障测试容易。本项目采用轻量注入，不引入 DI 框架。Registry 是内置连接器生命周期管理器，并不隔离任意第三方代码。

**扩展注意**：动态插件加载需要代码信任和执行隔离，不能把 capability 当作 OS 沙箱。并发任务、清理失败、注销中同步等情况应在增加真实连接器前补充设计。

**面试追问**：类型安全 Event Bus 如何保证事件名与 payload 对应？为什么监听器异常不能影响已提交事务？

**10–20 分钟练习**：自己实现一个只在测试使用的故障连接器，第二次 sync 抛错，断言旧余额和失败任务状态。

## 3. SQLite 事务、幂等与迁移

**解决的问题**：一次同步不能只保存半批数据；重复拉取不能重复新增同一上游事件；重启不能丢失记录。

**技术**：Drizzle 类型化查询、SQLite transaction、外键、复合唯一索引、upsert、WAL、SQL 版本/hash 表。余额保存字符串，规则比较时转 BigInt 最小货币单位。

**两种方案**：在业务代码中先查再插可能遭遇并发竞态；让数据库唯一约束与 upsert 处理幂等更可靠。本项目采用后者。迁移可手写 SQL，也可 drizzle-kit 生成；当前用嵌入式 SQL 控制本轮依赖和打包路径。

**扩展注意**：不能修改已经执行的迁移；新增版本和升级测试。失败不等于余额为零；当前账户采集时间和上游事件时间是两个概念。备份 WAL 数据库不能在运行时只复制主文件。

**面试追问**：外部请求失败与本地事务失败有何区别？唯一索引为何比“查重 if”可靠？

**10–20 分钟练习**：仿照回滚测试，让第二条事件引用不存在的账户，验证第一条账户也没有落盘，然后自行说明事务边界。

## 4. 原生模块、Node-API 与打包

**解决的问题**：Node 中能加载的原生模块未必能在 Electron 或 ASAR 中加载。

**技术**：Node-API 稳定边界、平台/CPU 预构建、external dependency、electron-builder、asarUnpack、用户数据目录。

**两种方案**：传统 V8 ABI 模块需针对 Electron 重建；采用 Node-API 的模块可在兼容运行时复用二进制，但仍依赖 OS/CPU 和正确打包。本轮锁定的 better-sqlite3 13 属于后者，不能把空重编译成功当作实际读写证据。

**扩展注意**：升级驱动后重新执行双运行时检查和打包后 E2E。签名、公证、Gatekeeper、安装更新属于独立验收。Node Worker 可以让同步查询不阻塞 Main，但要自行设计消息、关闭和错误传播。

**面试追问**：为什么 `.node` 需要从 ASAR 解包？为什么数据库不能放在应用安装目录？

**10–20 分钟练习**：运行两个 native 检查命令，对比 Node/Electron 版本；在 `.app/Contents/Resources/app.asar.unpacked` 找到平台原生文件，并说明仍需测试的目标平台。

## 5. LangGraph State、Node 与 Checkpoint

**解决的问题**：将输入事件的规则判断和解释输出拆成可验证步骤，之后可逐步增加暂停和工具权限检查。

**技术**：Annotation.Root、StateGraph、START/END、节点部分状态更新、compile/invoke、MemorySaver、thread_id。classify 与 explain 是真实图节点；没有调用任何模型。

**两种方案**：一个纯函数足以完成当前规则，代码最少；LangGraph 状态图增加依赖和编排成本，但能提前验证未来所需的状态传递与 checkpoint。本轮用户明确要求最小 LangGraph，采用两节点图并约束范围。

**扩展注意**：分析结果持久化与 Graph checkpoint 不是一回事。MemorySaver 随进程退出丢失；SQLite 中的 Event.analysis 才是当前 UI 重启后仍有结果的原因。Checkpoint 不等于长期记忆，未来 HITL 和工具执行还需权限、幂等和恢复设计。

**面试追问**：checkpoint 保存什么？为什么 thread_id 很关键？有状态图是否意味着一定用了 LLM？

**10–20 分钟练习**：自己补充 `0.00 USD` 和 `999999999999.99 USD` 的边界测试，说明 BigInt 如何避免金额精度损失。

# Phase 1：从前后端分层理解桌面服务

以下章节记录本轮实际实现。可以把 IPC Handler 理解为 Controller、WorkspaceRuntime / AccountManager / SyncEngine 理解为 Service、SqliteWorkspaceRepository 理解为 Repository。Renderer 通过白名单 SDK 请求服务，进程通信采用 IPC。

## 账户管理：适配器、接口与依赖倒置

- 核心设计：Connector 表示“平台类型”，Connection 表示“授权账户实例”。两个 Mock 账户共享连接器实现，各自拥有独立数据、凭据引用、游标和任务。
- TypeScript / Node 技术：interface、判别状态、纯状态转换函数、构造参数注入、UUID、每账户异步互斥。
- 方案选择：可以将账户操作写进页面，也可以集中在业务服务。集中在 AccountManager 后，页面、测试和未来 Node Worker 都调用同一套状态规则。直接依赖 Electron 的实现被放在 Main 的适配器里，Core 只依赖接口。
- 典型错误：把“连接器存在”当成“账户已授权”，或让 connected 与 credentialConfigured=false 同时存在。当前使用状态转换和 Zod 状态组合校验，重复身份由唯一索引兜底。
- 面试点：账户与连接器为什么分开？依赖倒置如何降低测试成本？类型接口为什么不能代替运行时输入校验？

## 凭据管理：受限回调与最小权限

- 核心设计：OS 负责加密，CiphertextRepository 负责存储字节，CredentialAccess 负责限定谁可在何种账户状态下使用凭据。Renderer 只提交公开账户标识；模拟凭据由 Main 流程生成。
- 技术：异步接口、`use<T>(connection, callback): Promise<T>` 泛型、依赖注入、Buffer / Uint8Array、固定错误映射、重新授权检查、密文 compare-and-swap。
- 方案选择：可以使用独立加密文件，也可以保存 OS 密文到 SQLite。当前选择后者，便于原子替换和断开清理；没有自行设计密码算法或硬编码加密密钥。macOS / Windows 使用异步 safeStorage，Linux 保守地只接受可明确识别的系统后端。
- 典型错误：解密还在等待时账户已经断开；或者旧值触发 OS 密钥轮换重加密，覆盖了并发更新的新凭据。当前在等待后重新检查账户，超时后不启动连接器；重加密只在旧密文仍相同时替换，避免覆盖新值或复活已删记录。
- 面试点：加密与授权有什么区别？为什么保护了磁盘文件仍不能把密钥交给 Renderer？同一用户进程、应用签名和 OS 后端影响什么安全保证？CAS 如何避免异步竞争？

## 同步引擎：Promise、调度与并发控制

- 核心设计：请求先落为 pending 任务；独立 Node 定时器取得名额后执行，任务与重试时间持久化。定时器不依赖 React 挂载或窗口是否打开。
- 技术：Promise / async-await、AbortController、Promise.race、Map / Set、setInterval、finally 释放资源、注入时钟与随机源。
- 方案选择：可以在 Promise 中 sleep 等待重试，也可以保存 nextAttemptAt 等调度器再运行。当前采用后者，重启可恢复，等待阶段不占用工作 Promise。默认每账户一个活动任务、全局两个名额。
- 典型错误：Promise.race 超时后，底层请求仍可能运行，立刻重试会产生同账户重叠请求。当前保留底层请求占用的名额；晚到响应被丢弃。连接器应遵守 AbortSignal，无法取消的请求可能阻塞名额直到结束或重启。
- 面试点：并发和并行有什么区别？async 函数是否自动创建线程？Promise.race 为什么不等于取消？进程内锁与数据库唯一索引分别保证什么？

## 幂等、增量与 SQLite 事务

- 核心设计：把数据、游标、成功状态、变化事件放在同一事务。游标是已经完成本地处理的位置，提前推进可能永久跳过数据。
- 技术：Drizzle 类型化查询、事务、部分唯一索引、upsert、版本比较、SQL trigger 故障注入、迁移 hash。
- 方案选择：只在业务层先查询再插入容易出现并发竞态；数据库约束能在最后写入边界阻止重复。业务层与数据库双重检查还可以返回更友好的错误。
- 典型错误：数据只写了一半就更新游标，或者原样重放生成重复提醒。测试在最终 sync.completed 事件写入处强制失败，断言此前数据、游标、变化事件、成功状态整体回滚；重复同步则不产生新 data.changed。
- 面试点：事务的原子性是什么？幂等是否等于不产生任何新记录？为什么同数据重放仍会产生新的同步任务？为什么 A→B→A 需要两次变化事件？外部 Exactly-once 为什么不能凭本地事务保证？

## 指数退避与错误分类

- 核心设计：区分可重试错误与永久错误，按任务限制最多尝试次数，等待时间落盘。
- 技术：错误码枚举、自定义 ConnectorError、纯函数 retryDelay、注入 random、模拟时钟推进。
- 方案选择：固定间隔实现简单，但多个账户可能同时重试；指数退避加抖动可分散重试。当前基础 1 秒、上限 30 秒、50%–100% 抖动，尊重模拟 Retry-After，默认最多 3 次尝试。
- 典型错误：401 / 403 仍无限重试，或将外部失败当成余额为零。当前认证过期、权限不足、格式错误不重试，失败保留上次成功数据和游标。
- 面试点：429 与 401 应怎样分别处理？重试次数与尝试次数有何区别？为什么自动调度也要识别永久错误？

## 事件系统与可观察性

- 核心设计：Event Bus 负责进程内通知；EventRepository 负责可靠记录。系统的每次 timer tick 不需要写库，账户连接、同步生命周期和业务数据变化则持久化。
- 技术：泛型 Event Bus、受限 payload Schema、自增 sequence、correlationId、来源 dedupeKey、事务后通知。
- 方案选择：只使用内存事件很简单，但崩溃后无法追踪；所有函数调用都落盘又会制造噪声。当前把明确业务事件独立存储，每次同步所有重试共享同一 correlationId。
- 典型错误：用毫秒时间或随机 UUID 排序导致顺序不确定；通知监听器抛错后把已完成业务标为失败。当前依赖自增 sequence，观察者异常不改变提交结果。
- 面试点：correlationId 如何定位一次同步的全部尝试？持久事件是否自动等于可靠消息队列？提交后、通知前崩溃怎样处理？为什么未来自动 Agent 需要消费进度与幂等？

## Desktop Integration 与 IPC 安全

- 核心设计：React 只显示快照和发起明确操作，Core 统一维护账户、任务与事件。Settings 的启动检测状态与每次凭据读写检查分开。
- 技术：Zustand、受控表单、useEffect 展示轮询、contextBridge、固定 channel、Zod、顶层 frame / 窗口所有权 / URL 校验、CSP。
- 方案选择：通过业务白名单方法开放能力，比任意 invoke(channel,args) 更容易审查。Renderer 刷新用于展示，自动同步由 Main 的 Node 引擎负责。
- 典型错误：把定时同步放进页面 useEffect，切换页面或关闭窗口后任务消失；或者只校验参数，忽略请求来自哪个窗口。当前分别处理服务生命周期和 IPC 来源。
- 面试点：Renderer 为什么应按不可信客户端处理？写入型接口为什么仍不能接受任意路径和工具名？Zustand 缓存与数据库事实来源有什么区别？

## 两个可以独立完成的小练习

### 练习 1：修改退避抖动策略（15–25 分钟）

目标：将当前抖动范围从基础退避的 50%–100% 调整为 75%–100%，继续尊重 Retry-After，保持 30 秒上限。

涉及文件：`packages/core/src/sync-engine.ts`、`tests/phase1/sync.test.ts`。

先手算 attempt=1、random=0 和 attempt=2、random=1 的结果，再改纯函数和相关断言。验证命令为 `pnpm test`、`pnpm typecheck`、`pnpm lint`。验收：Retry-After 大于抖动结果时仍优先采用它；401 仍然不重试。不要修改已执行迁移。

### 练习 2：给 Activity 增加“只看数据变化”开关（20–30 分钟）

目标：用页面内状态筛选 `eventType === 'data.changed'`，关闭开关恢复全部当前加载事件，不改变数据库、同步引擎或事件生成逻辑。

涉及文件：`apps/desktop/src/renderer/src.tsx` 的 Activity、可选 `tests/e2e/desktop.spec.ts`。

验收：创建连接并同步，修改余额再同步；打开开关只显示变化事件，关闭后仍能查看同步开始/成功/重试记录。运行 `pnpm typecheck`、`pnpm lint`、`pnpm build` 并手工检查开关。该练习可以帮助区分“展示筛选”和“业务数据删除”。
