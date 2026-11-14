# 迁移前代码清理

2026-11-13，用户明确授权清理迁移前的代码。本轮只清理已脱离当前运行链路的实现，不改变业务协议和存储行为。

## 范围与选择

有两种处理方式：继续把完整旧实现放在 legacy 目录，便于直接阅读，但仍要维护过时依赖和测试；或从工作区删除，使用 Git 查看历史。此前 Python 等价测试与桌面/Linux 验证已通过，本轮选择后者，同时保留真实数据迁移所需内容。

删除前逐文件核对，31 个受 Git 管理的旧文件均与基线 `898328d` 相同，没有覆盖用户新增源码。两个旧 Main/Preload 副本也与基线相同。`.idea/`、当前 Python/客户端实现、数据库、密钥和备份不在清理范围内；不提交、不推送、不运行 Git reset/clean。

| 文件/目录 | 处理与理由 |
| --- | --- |
| `packages/core/` | 删除旧 TS Account、Sync、凭据、EventBus 和业务装配；当前业务在 Python |
| `packages/storage/` | 删除 Drizzle / better-sqlite3 实现；原 SQL 先逐字节提取再删除包装代码 |
| `packages/connectors/`、`packages/agent/` | 删除旧 Mock Registry 与 LangGraph.js，Python 对应测试已存在 |
| `apps/desktop/src/main/ipc.ts`、`security.ts` | 删除未注册的旧业务 IPC 和 safeStorage 适配；保留当前原生 IPC 与客户端会话 |
| `legacy/` | 删除 3 个重复归档文件，历史源码/学习注释改从 Git 基线查看 |
| `scripts/check-native.cjs` | 删除旧 SQLite 原生检查脚本与 native:node/native:electron 命令 |
| 5 个旧顶层 TS 测试、`tests/phase1/` | 随旧实现移除，不再维护另一套业务运行时；当前 Python/API/E2E 是行为验证入口 |
| `packages/shared/src/contracts.ts` | 删除旧 Storage/AgentRuntime/CredentialStore/业务 IPC 等端口，保留当前错误和历史数据 DTO |
| `packages/shared/src/phase1.ts` → `workspace.ts` | 去掉旧持久连接记录、连接器同步结果、凭据/Repository 接口，保留当前客户端契约；消除阶段名造成的入口混淆 |
| 根 package.json / pnpm-workspace.yaml / lockfile | 移除四个旧 workspace 包、better-sqlite3 及类型、JS LangGraph、间接 Drizzle 等依赖；保留当前工具链 |
| `backend/tests/fixtures/legacy/` | 新增原样旧 SQL、SHA256 manifest 与说明，替代 Python 测试读取 TS 源码 |
| `tests/contracts.test.ts` | 补充当前公开 DTO 拒绝凭据字段、账户状态不变量及迁移历史可读性验证 |
| README / AGENTS / 架构 / 日志 | 更新当前结构和验证入口；历史报告标注为旧版本资料，不改写过去结果 |

## 保留的迁移能力

`backend/app/services/migration.py`、Alembic、旧账户/事件/任务 DTO、Activity 历史显示和分析入口保持。旧 SQL 位于 `backend/tests/fixtures/legacy/phase0.sql` / `phase1.sql`，与 Git 基线模板内的 SQL 字节一致，包含原始首尾换行；manifest 记录来源和 SHA256，测试校验哈希。

SQL 是重建旧格式测试库的夹具，不是当前启动入口。当前后端结构仍由 Alembic 管理。历史源码、原测试与学习注释可用只读命令查看：

```bash
git show 898328d:packages/core/src/sync-engine.ts
git show 898328d:apps/desktop/src/main/index.ts
```

旧实现被删除，不代表旧数据或导入支持被删除。回滚完整旧架构仍使用基线与原数据库；新后端数据没有自动反向转换。

## 验证记录

清理前提：此前重构阶段实际通过旧 79 项 TS 回归、Python 等价图/Worker/迁移测试、桌面开发/构建/测试包、Linux Compose 业务验证，详见 REFACTOR_REPORT.md。

原 85 项 TS 是清理前的历史计数，不能继续用作当前测试成绩。

| 实际命令 | 结果 |
| --- | --- |
| `CI=true NODE_OPTIONS='--require=/private/tmp/tavo-dns.cjs' pnpm install --frozen-lockfile` | 通过；当前仅根项目、desktop、shared，未新增依赖，旧依赖从锁文件和安装图移除 |
| `pnpm lint` | ESLint / Ruff / Python 格式检查通过 |
| `pnpm typecheck` | 严格 TS 检查通过，无已删除包的引用 |
| `pnpm test` | 客户端 12 项、Python 34 项通过，含 SQL 哈希与旧库导入验证 |
| `pnpm build` | Main / Preload / Renderer 构建通过；Zod 的 PURE 注释提示不影响产物 |
| 依赖/目录检查与 `pnpm list -r --depth -1` | 四个旧包与 legacy 目录已不存在，锁文件没有 JS LangChain / Drizzle / better-sqlite3 / 旧 workspace 引用 |
| `TAVO_BROWSER_CHANNEL=chrome NODE_OPTIONS='--require=/private/tmp/tavo-dns.cjs' pnpm exec playwright test` | 3 场景通过（37.1 秒）：当前业务/认证/CSP/重连、旧库导入/历史分析、桌面退出后同步 |
| `git diff --check` | 通过 |

桌面测试首次因后端已完成但 Query/SSE 缓存尚未刷新，取“第一个事件”误选了旧记录；分析进度事件又把它挤出总览最近 12 条，导致断言失败。测试已改成等待指定账户的 3.50 USD 变化事件再分析，没有改变业务行为。

依赖重装初次因本机 DNS 解析失败中止，随后使用进程级解析辅助完成，无系统 DNS 配置修改。本轮不重跑 Docker 或正式分发验证，未签名测试包与 Linux 重构阶段的历史结果见原报告。

## 学习练习

阅读 `backend/tests/test_migration.py`，说明为什么删除 Drizzle 运行代码后仍能验证旧数据库迁移。自己运行哈希测试，再用 Git 查看旧 migration，比对 SQL 的首尾换行；不要修改已执行的 SQL 或业务数据库。

## 边界

工作区不再提供旧业务实现的单元测试运行环境；需要研究或复现旧架构时，先保存当前修改，再单独取出 Git 基线。旧报告的文件路径只对应历史版本。此次没有改变业务接口和数据库内容，也不扩展真实连接器或公网部署能力。
