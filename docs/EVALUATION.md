# Phase 0 验证记录

验证日期：2026-11-12（Asia/Taipei）。环境：macOS 27.0.1 / arm64，Node.js 24.15.0，pnpm 11.1.3；Electron 44.11.1 内嵌 Node.js 24.22.2，SQLite 3.53.4。数据均为本地模拟数据。

## 执行命令与真实结果

| 命令 | 结果 | 证据范围 |
| --- | --- | --- |
| `pnpm install --frozen-lockfile` | 退出码 0 | 最终锁文件安装成功；本机安装时加临时 DNS 辅助，见下文 |
| `pnpm typecheck` | 退出码 0 | 应用、业务包、测试和配置的 TypeScript 严格检查 |
| `pnpm lint` | 退出码 0 | ESLint、TypeScript 与 Hooks 规则；已修正中途发现的问题 |
| `pnpm test` | 退出码 0，5 个文件 / 36 个测试通过 | Node 原生加载、单元与 SQLite 集成 |
| `pnpm native:node` | 读写通过 | Node 24.15.0 / Node-API 10 / SQLite 3.53.4 |
| `pnpm native:electron` | 读写通过 | Electron 44.11.1 / Node 24.22.2 / Node-API 10 |
| `pnpm test:dev` | 退出码 0 | 实际启动 `pnpm dev`，通过 React 渲染、IPC 同步和 Graph 分析后清理测试进程 |
| `pnpm build` | 退出码 0 | Main CJS、sandbox Preload CJS、Renderer 静态资源 |
| `pnpm test:e2e` | 退出码 0，1 个桌面场景通过 | 构建模式完整操作与重启持久化 |
| `pnpm package:dir` | 退出码 0 | `release/mac-arm64/Tavo.app`，明确未签名，不发布 |
| `pnpm test:packaged` | 退出码 0，1 个桌面场景通过 | 直接启动 `.app`，重复验证同步、分析、隔离、重启 |
| `cmp TAVO_DEVELOPMENT_PLAN.md docs/DEVELOPMENT_PLAN.md` | 退出码 0 | 原规划内容保留一致 |

安装和首次下载时，实际前缀为 `NODE_OPTIONS='--require=/private/tmp/tavo-dns.cjs'`，用于绕开本机失效的系统 DNS 解析；该临时辅助不属于项目依赖。未修改系统网络设置。最终 lock 安装实际为 `NODE_OPTIONS='--require=/private/tmp/tavo-dns.cjs' pnpm install --frozen-lockfile`。

构建有第三方 PURE 注释提示；electron-builder 提示默认图标、跳过签名；Playwright 有颜色环境变量提示。以上没有导致退出失败。中途的失败及修正记录在 `DEVELOPMENT_LOG.md`，此表只记录修正后的成功执行。

## 自动化测试覆盖

| 测试文件 | 用例数 | 验证内容 |
| --- | ---: | --- |
| connectors.test.ts | 3 | 注册、重复拒绝、能力、初始化失败、清理、数据结构、稳定事件 ID |
| core.test.ts | 8 | 事件分发/退订/观察者错误、单飞锁、完整业务链、失败保留旧数据、跨连接器拒绝、未知事件、凭据不可用 |
| storage.test.ts | 6 | 真实临时文件 CRUD/重开、事务回滚、幂等、同步时间、中断恢复、迁移校验、缺失记录拒绝 |
| agent.test.ts | 5 | USD/CNY 阈值与边界、结构化证据、MemorySaver checkpoint |
| ipc.test.ts | 14 | 非法参数/额外键/不可信 URL/子框架/其他窗口拒绝、错误脱敏、合法调用 |

桌面 E2E 在两个目标上分别执行同一场景：第一次启动空状态 → 同步 → 两账户可见 → Graph 分析保存 → 再同步 → 两次成功任务、仍只有两条事件 → 非法 IPC 参数拒绝 → 完全退出 → 重开 → 账户、事件、任务、分析仍存在。

同时断言 Renderer 没有 `require`、`process`、`ipcRenderer`，桥接对象只有三方法，生产 CSP 禁止内联脚本及网络连接；没有观察到 pageerror。这些断言不等同于完整渗透测试或安全审计。

原生文件实际存在于 `.app/Contents/Resources/app.asar.unpacked/node_modules/better-sqlite3/prebuilds/darwin-arm64.node`。打包后实际 SQLite 写入和重开证明本目标平台加载可用，并非只确认文件存在。

## 截图与人工复核

打包后的界面截图已检查：`docs/images/tavo-phase0.png`。Mock 标签、余额币种、账户/事件数量、分析证据与同步记录均可见。`test-results/` 是可覆盖的本地测试产物，不纳入源码。

人工复现：

1. `pnpm dev`，点击同步、分析并展开结构化 JSON。
2. `⌘Q` 后重开，确认数据；正常数据目录为 `appData/Tavo`。
3. `pnpm package:dir` 后打开 `release/mac-arm64/Tavo.app`，重复操作。
4. 在独立本地数据目录运行故障实验，不向程序输入真实密钥；单元测试已有连接器故障注入及 DB 回滚断言。

## 未验证与未实现

- 未验证 Windows、Intel macOS、Linux、干净机器安装、签名、公证、Gatekeeper 分发、升级安装和大规模性能。
- 未访问真实账户 API，没有对真实连接器成功率作任何声明。
- 没有持久化 Graph checkpoint；MemorySaver 只在内存测试，分析结果另存 SQLite。
- 没有实现 safeStorage、OS Keychain、实际凭据持久化、Worker、定时任务、自动重试、工具调用、RAG、多 Agent、托盘或系统通知。
- 没有运行依赖安全审计、完整秘密扫描或渗透测试；没有设定虚构的性能/召回率指标。

# Phase 1 验证（2026-11-13）

最终结果以 `PHASE_1_REPORT.md` 为准；Phase 0 记录保留为历史证据。

本轮 `pnpm typecheck`、`pnpm lint`、`pnpm test` 均退出 0。Vitest 共 10 文件、79 用例：既有 36 + 新增 43（账户 7、凭据 8、同步 17、事件/存储 5、IPC 6）。真实临时 SQLite 验证事务、文件重开与迁移；纯时钟/随机源注入验证退避、定时计划与恢复。单元加密替身仅验证接口与故障控制，不代表 OS 加密。

真实 Electron 场景使用系统 safeStorage，没有明文降级：多账户完整流程、旧数据升级、关闭 Renderer 后定时同步。`pnpm test:dev` 退出 0；`pnpm test:e2e` 的 3 个场景通过；`pnpm package:dir` 退出 0；`pnpm test:packaged` 的同样 3 个场景通过。最终安全竞争修复均包含在这些产物中。桌面 E2E 检查密文表、重启后同步解密、凭据清理、Renderer 能力白名单、CSP 和 pageerror；这些检查不等同于全面安全审计。

本机打包首次因 `ENOTFOUND github.com` 失败；随后仅对打包进程使用临时 DNS 辅助。该辅助不在仓库，未更改系统设置。版本 1 迁移 SQL 与本轮开始前源码比较，hash 一致。

未验证：真实第三方 HTTP、Windows/Linux/Intel 系统安全后端、正式签名/公证/安装升级、大规模性能、跨 Worker 同步、完整渗透测试。所有重试和 401/429 均为 Mock 场景，不用于声明真实平台成功率。
