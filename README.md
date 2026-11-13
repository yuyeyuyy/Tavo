# Tavo

Local-first 的桌面 Personal Agent。当前完成 **Phase 1：桌面核心与安全数据基础设施**。所有连接器和账户仍为 Mock，不请求真实平台 API。

可以添加多个账户连接、使用系统安全存储保存模拟凭据、手动或定时同步、模拟故障并观察重试、查看持久化任务与变化事件。Phase 0 的 LangGraph 演示与历史记录保留。

## 安装与启动

环境：Node.js 24 LTS（`>=24.13 <25`）、pnpm 11。当前实际验证平台为 macOS ARM64。

```bash
pnpm install --frozen-lockfile
pnpm dev
```

首次安装和 Electron 二进制下载需要可用网络。必要依赖构建脚本由 `pnpm-workspace.yaml` 的 `allowBuilds` 白名单控制。本轮没有新增第三方依赖。

## 演示完整流程

1. 进入「账户连接」，添加两个 Mock 账户，例如显示名称「学习账户 A / B」，标识 `demo-a / demo-b`。
2. Main 为每个连接生成不同的模拟凭据，通过系统 safeStorage 加密后保存。界面只有“凭据已配置”状态，不接收凭据原文。
3. 点击「同步账户」，在「活动记录」查看 pending / running / succeeded 状态和尝试次数。
4. 展开「模拟数据与故障设置」，修改余额、用量或资源状态，保存后同步；Overview 出现最新数据和 `data.changed`。
5. 重复同步原样数据，只增加同步记录，不重复产生变化事件。
6. 将场景改成「首次网络错误，随后成功」或「首次 429 限流」，同步并查看重试事件、下次执行时间及最终成功。
7. 401 会变为认证过期；403 和无效数据会直接失败，均不自动重试。恢复正常场景后可重新连接。
8. 「设置」可开启定时同步、修改间隔（10–86400 秒），默认间隔 300 秒，默认关闭自动同步。完全退出应用后不继续监控。
9. 对变化事件运行现有的模拟 LangGraph 分析；结果保存在本地，无模型请求。
10. `⌘Q` 完全退出再启动，连接、数据、配置、任务、事件和分析仍在。断开会清理凭据，删除还会移除连接和最新数据，历史任务和事件保留。

正常更新模拟数据会取消该账户当前任务，避免正在处理的旧配置覆盖新版本。模拟故障仅作用于 Mock。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `pnpm dev` | 检查 SQLite 原生加载，启动开发窗口 |
| `pnpm typecheck` | 严格 TypeScript 检查 |
| `pnpm lint` | ESLint / React Hooks 检查 |
| `pnpm test` | Node 原生检查、Vitest 单元与集成测试 |
| `pnpm build` | 构建 Main、Preload、Renderer |
| `pnpm test:dev` | 真实开发窗口中添加账户、加密凭据、同步、分析 |
| `pnpm test:e2e` | 构建模式完整桌面链路与 Phase 0 升级回归 |
| `pnpm package:dir` | 生成本地 macOS ARM64 `.app`，不发布 |
| `pnpm test:packaged` | 对已有 `.app` 执行桌面和升级测试 |
| `pnpm native:node` / `pnpm native:electron` | 两种运行时下实际加载与读写 SQLite |

桌面测试需要 GUI 和真实可用的系统安全存储，使用隔离临时数据库；不会启用明文降级。开发烟雾脚本只对自己的临时子进程开放调试端口并清理该进程组。普通启动不开放调试端口。

构建结果不是安装包。打包产物为 `release/mac-arm64/Tavo.app`，未签名、公证，也未发布。验证证据见 [Phase 1 报告](docs/PHASE_1_REPORT.md) 与 [评测记录](docs/EVALUATION.md)。

## 工程目录

```text
apps/desktop/src/
  main/index.ts          窗口、生命周期、依赖装配
  main/ipc.ts            来源与参数校验的 Controller
  main/security.ts       Electron safeStorage 适配器
  preload/index.ts       明确的业务方法白名单
  renderer/              Overview / Connections / Activity / Settings
packages/
  shared/src/
    contracts.ts         Phase 0 契约、公共错误与基础端口
    phase1.ts            连接、任务、事件、配置与业务端口
    index.ts             统一导出，保持原有导入路径
  core/src/
    index.ts             原运行时、Event Bus、基础 Task Manager
    accounts.ts          Account Manager 与状态转换
    credentials.ts       密文存储适配与限定范围的凭据访问
    sync-engine.ts       持久化任务、重试、取消、并发和调度
    workspace-runtime.ts 桌面业务服务，保持无 Electron 依赖
  connectors/src/        Registry、Mock、错误分类与增量协议
  storage/src/
    index.ts             SQLite 初始化、迁移、旧数据兼容
    workspace.ts         连接/任务/事件/密文 Repository 与原子提交
    workspace-schema.ts  Phase 1 Drizzle Schema
    phase1-migration.ts  新增版本 2 迁移
  agent/src/             既有最小 LangGraph，无复杂 Agent 推理
scripts/                 原生检查、开发烟雾
tests/phase1/            新增故障与安全测试
tests/e2e/              真实 Electron 回归
docs/                   架构、ADR、报告、学习和验证
```

## 本地数据与安全边界

- SQLite 在 `appData/Tavo/tavo.sqlite`，macOS 通常为 `~/Library/Application Support/Tavo/tavo.sqlite`。`TAVO_USER_DATA` 仅为本地测试指定隔离目录，不能通过 IPC 设置。
- 同步数据、游标、任务成功状态、`data.changed` 和 `sync.completed` 在同一事务中提交。失败保留上次余额和游标，不伪装成余额 0。
- 同账户最多一个活动任务；默认全局最多两个。暂时性错误最多尝试 3 次，指数退避带抖动，429 尊重模拟 Retry-After。任务和重试时间落盘。
- 崩溃后按剩余次数恢复只读 Mock 的 running 任务；正常退出取消活动请求，已排队的任务可在重启后继续。外部请求不保证 Exactly-once。
- `credential_records` 只存 OS 加密后的字节。密钥不硬编码、不存普通字段、不发给 Renderer / Agent / 普通日志；没有任意执行或读取凭据的 IPC。
- macOS / Windows 使用异步 safeStorage。Linux 只接受明确识别的系统密钥后端并使用与该后端匹配的同步 API；`basic_text`、`unknown` 明确拒绝。实际存取时会重新检查可用性；Settings 展示启动时检测状态。
- Renderer 开启 context isolation 和 sandbox，禁用 Node integration。校验请求窗口、顶层框架、精确页面 URL、严格 Zod 参数；阻止导航、弹窗、webview 与权限请求。生产 CSP 禁止网络连接和内联脚本。
- Phase 0 旧表与迁移保留，升级后历史账户显示为已断开，需要重新连接生成模拟凭据；Activity 可查看原历史和重新分析。

[Electron safeStorage 官方说明](https://www.electronjs.org/docs/latest/api/safe-storage) 描述了平台保护差异。macOS 未签名构建的 Keychain 身份可能随版本变化而重新询问授权；Windows DPAPI 不阻止同一用户下所有其他进程；SQLite 业务数据库未整体加密。删除密文记录不等于擦除 WAL、旧备份或系统应用级主密钥。

## 当前限制与下一步

真实平台 API、OAuth、真正的用户密钥输入流程、Worker 部署、持久化 Graph checkpoint、通知/托盘和复杂 Agent 尚未实现。Windows、Linux、Intel macOS 只做了部分策略替身测试，未做真实系统验证。正式分发仍需签名、公证、升级和干净机器安装测试。

Snapshot 展示最近 200 条任务和 300 条业务事件，尚未做历史分页、归档与数据保留策略；数据库的同步访问仍在 Main，未做规模化性能评测。无法取消的连接器会继续占用账户和并发名额，直到其请求结束或应用退出，以避免请求重叠。

下一阶段建议一次接入一个只读官方连接器，先核对最新权限和返回格式，再建立认证、限流、超时与坏响应契约测试。详见 [Phase 1 报告](docs/PHASE_1_REPORT.md)。开源发布前还需确定许可证。

阅读：[架构](docs/ARCHITECTURE.md) · [学习笔记](docs/LEARNING_NOTES.md) · [开发记录](docs/DEVELOPMENT_LOG.md) · [协作规则](AGENTS.md)。
