# Tavo Phase 1 交付报告

日期：2026-11-13。增量构建于 Phase 0。所有账户、凭据和外部错误均为 Mock；没有访问真实第三方账户。

## 1. 已完成功能

- **Account Manager**：同一连接器多个实例，添加、重复拒绝、状态转换、重新连接/更新模拟凭据、断开、删除；状态为 disconnected / connecting / connected / expired / error。
- **Credential Manager**：Main 使用系统 safeStorage；SQLite 只保存密文字节。新增、读取、更新、删除、不可用拒绝、作用域授权、等待后复查、OS 重加密 CAS；凭据不进入 Renderer、Agent 或普通日志。
- **Sync Engine**：手动与应用运行期间定时同步；pending / running / succeeded / failed / cancelled 持久任务；增量游标、幂等、超时、有限退避与抖动、模拟 Retry-After、每账户互斥、全局并发上限、取消、崩溃恢复和晚到响应保护。
- **Event System**：独立持久化 EventRepository、明确事件 Schema、顺序、去重与 correlationId；事务后发布进程内通知。原样数据不重复提醒，余额/用量/资源变化可追踪。
- **Desktop**：Overview、Connections、Activity、Settings。支持 Mock 修改与故障演示，保存同步间隔/启动隐藏窗口，显示安全后端与本地位置；加载、空、错误状态齐备。旧数据、任务、事件和 LangGraph 手动分析入口保留。

完整链路：创建连接 → Main 生成并加密模拟凭据 → 任务落盘 → Connector 读取增量 → 数据/游标/成功状态/事件同事务提交 → UI 展示 → 重启恢复。

## 2. 架构变更

`Electron Main → WorkspaceRuntime → AccountManager / SyncEngine → WorkspaceStorage`；同步通过 Registry 选择连接器，通过 ScopedCredentialAccess 使用对应凭据。Main 提供 OS 加密适配器，Core 只依赖接口，没有 Electron 或 React 引用。

新增文件主要为：

- `packages/shared/src/phase1.ts`：DTO、状态组合、业务事件、设置和端口。
- `packages/core/src/accounts.ts`、`credentials.ts`、`sync-engine.ts`、`workspace-runtime.ts`。
- `packages/storage/src/workspace.ts`、`workspace-schema.ts`、`phase1-migration.ts`。
- `apps/desktop/src/main/security.ts`、新增测试 `tests/phase1/`。

原 Shared 内容移到 contracts.ts，index.ts 保持统一导入路径。原 Graph、Event Bus、SQLite 基础和 36 个既有测试保留。版本 1 SQL 内容校验不变，新版本 2 创建六张表并迁移历史 Mock 账户。旧表只作为兼容历史，不双写新任务。

未新增第三方依赖；没有 Redis、Kafka、HTTP 服务、通用 DI 框架或多 Agent。

## 3. 核心技术点

### Sync Engine

默认全局两个账户执行名额、每账户一个活动任务，SQLite 部分唯一索引兜底。任务先 pending，执行前保存 running 与尝试次数。默认超时 5 秒，临时错误最多尝试 3 次；退避基础 1 秒、上限 30 秒，抖动范围 50%–100%，Retry-After 优先且最大等待 1 天。

重试时间落盘而不是长时间 sleep。恢复 running 时保留原任务与 correlationId，剩余次数可继续只读 Mock，耗尽则失败。正常退出取消活动请求；已排队任务保留。忽略 AbortSignal 的请求仍占名额，晚到数据不得提交。外部调用没有 Exactly-once 保证。

数据、游标、任务成功和事件在单一事务内提交。测试用 SQL trigger 在最终 sync.completed 写入时强制失败，确认此前写入全部回滚。

### 安全存储

OS 负责加密，密文存 SQLite；原始凭据只在 Main/Core 受控流程中短暂使用。Main 为每账户生成独立模拟值，Renderer 只有公开名称/标识和配置状态。读取需要匹配当前账户、连接器、权限和引用，解密等待后复查授权，超时不再发起连接器请求。

OS 密钥轮换用密文 compare-and-swap，旧读取不能覆盖新凭据或复活已删记录；断开通过持久清理意图应对中断。测试加密替身仅用于故障注入，真实系统读写证据来自 macOS 桌面测试。

平台区别：macOS Keychain 与应用身份/签名相关；Windows DPAPI 不隔离同一用户下所有其他进程；Linux 依赖系统密钥服务。当前 Linux 分支只接受明确后端并拒绝 basic_text / unknown，未启用明文开关。Linux 同步分支在升级 Electron 时需重新评估。依据：[Electron safeStorage 官方文档](https://www.electronjs.org/docs/latest/api/safe-storage)。

### Event System

内存 Event Bus 和持久业务事件职责分开。业务事件有 eventId、eventType、sourceId、accountId、occurredAt、payload、correlationId、自增 sequence；生命周期按任务/尝试去重，数据变化按账户/版本去重。A→B→A 产生两次实际变化事件，同数据重放无新提醒。

提交后发布业务通知，观察者错误不改变成功结果。尚无持久事件消费进度；未来自动 Agent 必须加入消费幂等与恢复设计。现有 Graph 仍由用户手动触发，没有新增复杂推理。

## 4. 测试结果

测试环境：macOS 27.0.1 / ARM64，Node.js 24.15.0，pnpm 11.1.3，Electron 44.11.1（Node 24.22.2），SQLite 3.53.4。

| 命令 | 实际结果 |
| --- | --- |
| `pnpm typecheck` | 退出码 0 |
| `pnpm lint` | 退出码 0 |
| `pnpm test` | 退出码 0；10 个文件、79 个用例通过 |
| `pnpm build` | 退出码 0；Main / Preload / Renderer 构建成功 |
| `pnpm native:electron` | 真实 SQLite 加载与读写成功 |
| `pnpm test:dev` | 退出码 0；实际添加账户、系统加密、同步与分析成功 |
| `pnpm test:e2e` | 退出码 0；3 个桌面场景通过 |
| `pnpm package:dir` | 退出码 0；生成未签名的 macOS ARM64 .app |
| `pnpm test:packaged` | 退出码 0；3 个桌面场景通过 |

新增 43 个单元/集成用例：Account 7、Credential 8、Sync 17、Events/Storage 5、IPC 6；保留原有 36 个。覆盖模拟 401/403/429、网络/服务器/超时、增量/幂等/事务回滚、并发、恢复、OS 不可用、凭据更新/删除/竞争、授权撤销、事件排序/去重、IPC 拒绝。HTTP 错误通过 Mock 分类模拟，没有发真实 HTTP 请求。

真实桌面测试包含三场景：

1. 两账户、重复拒绝、更新系统凭据、手动同步、余额修改、重复增量、网络重试、Graph 分析、设置、重启解密、断开/删除与密文清理。
2. 从真实 Phase 0 格式数据库升级后，旧账户、任务、事件和分析入口仍可用。
3. 开启 10 秒定时同步后关闭 Renderer 窗口，从数据库确认 Main 继续执行 scheduled 任务。

上述场景分别在构建模式和打包 `.app` 上运行。桌面测试不跳过安全后端检查；系统不可用会明确失败。原生文件通过 ASAR unpack，重启同步证明加密数据与 SQLite 可继续读取。

打包命令实际使用前缀 `NODE_OPTIONS='--require=/private/tmp/tavo-dns.cjs'`，解决本机系统 DNS 解析 github.com 失败；该辅助未提交，未修改系统 DNS。构建保留第三方 PURE 注释提示、默认图标与未签名提示，均为已记录的非失败提示。

## 5. 风险和不足

- 实际系统只验证 macOS ARM64。Windows/Linux 后端为替身策略测试；Intel macOS、干净机器安装、签名、公证、升级分发未验证。未签名构建可能出现 Keychain 授权重询问。
- 当前安全流程只生成模拟凭据；没有开放真实密钥输入、OAuth、真实平台连接，也没有声称上游权限已经验证。
- SQLite 业务数据库没有整体加密；删除密文不是取证级擦除，旧 WAL/备份、OS 应用级主密钥不由账户删除保证擦除。
- 数据库同步访问仍在 Main，仅验证小规模 Mock；无 Worker、跨进程执行租约、历史分页/归档/保留策略。当前快照最多展示 200 任务、300 事件。
- 无法取消的连接器可能占满并发名额，需等待其结束或重启；这是避免请求重叠的保守取舍。
- 内存通知不保证提交后崩溃重放；无持久消费者、Graph checkpoint、自动 Agent、系统通知或托盘。
- 没有对大规模性能、安全渗透、依赖审计或全部秘密扫描作出通过声明；没有虚构成功率、召回率或性能提升。

## 6. 下一阶段建议

先单独设计真实凭据的受控主进程导入/系统授权方案，不让原始凭据进入 Renderer；再一次接入一个平台，用 HTTP 替身完成坏响应、认证、限流、取消与分页契约测试。只读工具权限、固定官方域名、超时与返回大小限制应在适配器内执行。

- **DeepSeek**：从官方余额查询开始，区分币种、赠送与充值余额，使用上游十进制字符串。即使凭据本身权限更广，本地连接器也只实现只读查询；不推断不存在的历史消费接口。[官方余额接口](https://api-docs.deepseek.com/api/get-user-balance/)
- **OpenRouter**：Credits 接口需要 Management Key，先在能力/权限说明中明确更高授权要求；区分累计充值、累计使用与可用额度，普通模型 Key 不应静默替代。[官方 Credits 文档](https://openrouter.ai/docs/api/api-reference/credits/get-remaining-credits)
- **GitHub**：先选择公开仓库或明确授权的最小仓库范围；按 Issues、Pull requests、Actions 的目标只读 endpoint 核对 fine-grained token 权限，再设计分页、增量及限流处理，不统一申请宽泛写权限。[官方权限表](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)

以上是后续建议，本轮没有调用这些平台账户 API。进入实际接入前仍需核对当时的官方文档并确认范围。

## 7. 可证明的简历素材

- 在 Electron + TypeScript 本地应用中实现多实例账户连接、状态转换和最小 IPC 权限边界。
- 通过 OS safeStorage 保存模拟凭据密文，实现限定范围使用、不可用拒绝、删除恢复与 CAS 竞争保护；在 macOS 实际验证重启解密。
- 基于 SQLite 构建持久同步任务，支持增量游标、事务幂等、有限退避、并发限制、取消和崩溃恢复。
- 实现可关联、可排序、可去重的持久业务事件，与进程内通知区分，保留手动 LangGraph 分析入口。
- 通过 79 个单元/集成测试，并建立开发、构建与打包后的真实 Electron 回归；详细证据见本报告验证表。

两项独立练习、各模块技术解释和面试追问见 `LEARNING_NOTES.md`。没有提交、推送或发布，工作目录尚未初始化 Git。
