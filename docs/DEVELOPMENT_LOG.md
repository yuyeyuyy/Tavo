# Phase 0 实际开发记录

日期：2026-11-12。范围按本次用户请求，仅实现本地 Mock 最小闭环。

## 初始检查与保护

- 初始目录只有 `AGENTS.md`、`CODEX_START_PROMPT.md`、`TAVO_DEVELOPMENT_PLAN.md`，没有源码、依赖或 `.git`。
- `git status --short` 实际返回 `fatal: not a git repository`。未初始化 Git、未改历史、未提交或推送。
- 已阅读三份原文件。根目录原始开发计划完整复制到 `docs/DEVELOPMENT_PLAN.md`，`cmp` 验证一致。原启动提示保留；AGENTS 只追加本轮工程说明。
- 本次请求明确要求解释后直接实施，并授权 Mock、SQLite 和 LangGraph 闭环；未执行真实连接器或下一阶段业务。

## 小步实施与设计理由

| 步骤 | 文件范围 | 作用与理由 | 验证 |
| --- | --- | --- | --- |
| 工程初始化 | 根 package/lock/workspace、TS、ESLint、Vitest、Playwright 配置 | 锁定稳定版本，严格类型，统一门禁；pnpm 11 显式脚本白名单 | frozen-lockfile 安装、typecheck、lint |
| Electron 基础 | desktop 配置、main/preload | 三方法桥接、窗口与生命周期；业务不依赖 Electron | 真实开发命令、桌面 E2E |
| 协议、Core、Connector | shared/core/connectors | unknown 入边界、能力声明、初始化清理、注入端口、单飞锁、类型事件 | 注册/结构/事件/并发/故障测试 |
| SQLite | storage/schema、migrations、index | 事务、外键、上游唯一键；迁移嵌入构建，重启保留数据 | CRUD、回滚、幂等、迁移损坏、中断状态测试 |
| LangGraph | agent/index | State + classify/explain 两节点，无 LLM；保留 checkpointer 注入 | 图执行、金额阈值、MemorySaver 状态测试 |
| UI 集成 | renderer/store、tsx、css、html | 黑白灰界面、空/忙/错状态、余额币种、事件分析与 JSON、同步记录 | 开发与构建模式点击验证、截图检查 |
| 分发验证 | builder、native 检查脚本、E2E | 原生库 external + ASAR unpack，未签名 dir 包 | `.app` 启动、同步、分析、关闭后重启 |
| 文档 | README、架构、ADR、学习、评测 | 说明输入输出、状态、限制与可复现证据 | 与最终文件和命令核对 |

每个模块的 TypeScript / Node / Electron 技术、扩展注意事项和面试追问见 `LEARNING_NOTES.md`，重要取舍见 ADR 0001。

## 实际遇到的问题

1. **网络**：普通 npm 请求和放宽沙箱后的请求均遇到系统 DNS `ENOTFOUND`；HTTPS 本身可用。通过 `/private/tmp/tavo-dns.cjs` 为本次安装子进程设置临时解析回退，完成官方注册表和 Electron 下载；未修改系统 DNS、Hosts 或提交该辅助文件。以后在干净机器上安装仍需网络正常。
2. **pnpm 11 脚本审批**：早期使用旧式配置导致安装拒绝未批准构建脚本，改为 `allowBuilds`，仅允许必要依赖、拒绝 Windows installer 脚本后安装成功。
3. **SQLite 版本事实**：开始按传统 ABI 模块规划重编译；检查已选包与官方 v13 发布说明后确认其采用 Node-API。删除不必要的直接 rebuild 依赖，改为双运行时实际读写检查；electron-builder 保留自己的原生依赖处理。
4. **测试 API 和构建临时文件**：移除不存在于当前公开 Electron 类型中的测试调用，改验证 Renderer 能力和生产 CSP；ESLint 忽略 electron-vite 构建期间生成并立即删除的配置临时文件，避免并行读取竞态。
5. **开发烟雾清理**：CDP 断开不能保证 macOS Electron 退出。脚本现在在独立进程组中启动测试子进程，先 TERM、再 KILL 清理，关闭连接并删除隔离目录；普通退出和重启由 Playwright Electron E2E 另外验证。最终脚本已正常返回退出码 0。清理代码曾被 `no-unsafe-finally` 拦截，修正后重新运行 lint。
6. **非失败警告**：Zod 的 PURE 注释触发 Rollup 提示，注释被移除，构建成功；builder 提示默认图标和未签名，符合本轮产物设置。没有为消除依赖提示而扩展需求。

## 实际验证与残留风险

完整命令、数量和结果见 `EVALUATION.md`。没有把类型检查或构建成功当作业务通过；开发模式、生产构建模式、打包模式均另行执行了真实操作。

残留：主线程同步 SQLite 仅验证演示规模；无真实凭据库、自动队列、持久 checkpoint、真实 API；未签名、公证或做跨平台安装。没有发布、上传账户数据或使用真实密钥。

# 2026-11-13：Phase 1 增量开发

## 范围与审查

本次用户明确授权 Phase 1 五个模块，并要求分析后直接实施。已重读 AGENTS、README、开发计划、架构、开发记录、受影响源码与原有测试；工作目录仍无 `.git`，`git status --short` 返回非仓库。没有初始化 Git、提交、推送或发布。

Phase 0 已具备隔离 IPC、独立 Core、Registry、SQLite、Mock 与图执行，但没有多连接实例、安全凭据库、可靠队列、定时调度和持久业务事件。保留 Main 中用户添加的学习注释，以及原始规划和启动提示。修改前将源码和文档备份到本地临时目录，仅用于本轮回看。

## 文件变化与设计理由

| 文件范围 | 修改与理由 |
| --- | --- |
| shared/contracts、phase1、index | 保持原导入路径；新增连接、任务、事件、设置和端口，严格校验状态组合；统一固定错误码 |
| core/accounts、credentials、sync-engine、workspace-runtime | Account Manager、安全存储抽象、限定范围的凭据使用、独立持久同步、业务入口；无 Electron 依赖 |
| connectors/index | 保留原 Mock 接口，新增每账户增量协议、AbortSignal、模拟故障和标准错误分类 |
| storage/phase1-migration、workspace-schema、workspace | 新增版本 2 和六张表；独立 EventRepository、密文仓库、游标与事件的原子提交 |
| storage/index、migrations | 接入新仓库和迁移；版本 1 SQL hash 对比确认未变 |
| desktop main/security、ipc、preload | OS 加密适配器、业务白名单、安全来源/参数验证、独立调度生命周期；移除 Renderer 的旧全局同步入口 |
| renderer | 四个区域、账户表单、同步/断开/删除/凭据更新、Mock 配置、活动关联、设置；旧历史与分析继续可用 |
| tests/phase1、tests/e2e、scripts/dev-smoke | 新增故障、安全、恢复测试；真实开发、构建和打包场景验证 |
| README、架构、学习、报告、ADR、评测 | 记录实际行为、原理、两项练习、验证和未解决边界 |

本轮没有新增第三方依赖，也没有大规模改写原 Event Bus、Graph 或 SQLite 基础实现。旧同步表作为历史兼容保留，新链路只写新任务/连接表。

## 分模块验证与发现

1. 账户与安全：多实例、重复拒绝、状态转换、密文更新/删除、不可用失败、Linux 后端策略和凭据作用域；真实 macOS safeStorage 的加密与重启解密在 E2E 单独验证，单元测试加密替身不被当成 OS 证据。
2. 同步：增量空页、数据变更、401/403/429/网络/服务器/超时、有限重试、并发限制、取消、文件重开恢复，以及 SQL 最终事件写入失败后的整批回滚。
3. 事件：顺序、去重、correlationId、通知与持久记录一致、观察者错误、A→B→A 变化，以及原 Graph 分析保存。
4. 同毫秒任务原先按随机 ID 排序，测试发现队列顺序不可靠，已改为 SQLite 入队顺序。
5. Promise.race 不取消底层请求：新增请求名额保留，晚到结果丢弃；超时发生在解密等待阶段时，也不启动连接器请求。
6. 安全复查发现旧值的 OS 轮换重加密可能覆盖并发更新；改用密文 CAS，并在解密后重新校验账户授权，补充可复现竞争测试。
7. 首次打包因系统 DNS 无法解析 github.com 失败。使用已有临时 `/private/tmp/tavo-dns.cjs` 仅为打包子进程提供解析回退后成功；未改系统设置，未把该辅助文件放进仓库。
8. ESLint 捕获开发脚本中浏览器回调的 window 环境声明问题，已明确浏览器上下文并通过复查。第三方 PURE 注释、默认图标、未签名和颜色环境提示仍是非失败提示。

最终实际命令、测试数量和边界见 `PHASE_1_REPORT.md` 与 `EVALUATION.md`。没有把 401/429 模拟测试当作真实 HTTP 平台验证，也没有把构建成功当作实际业务成功。
