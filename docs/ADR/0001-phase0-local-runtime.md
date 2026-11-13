# ADR 0001：Phase 0 的本地运行时与安全边界

- 状态：采用
- 日期：2026-11-12
- 范围：当前用户明确要求的 Mock 同步、持久化、最小 LangGraph 和桌面闭环。

## 选择与理由

1. **electron-vite + React + TypeScript + pnpm workspace**。与 Electron Forge 相比，前者用一个配置处理 Main/Preload/Renderer 与 HMR，适合当前 Vite 技术栈；Forge 有更完整的 maker 体系，但本轮无需增加另一套开发插件。分发验证使用 electron-builder 的本地 dir 目标。
2. **接口注入的单进程 Core**。另一方案为立即引入 Worker/独立服务，隔离更强，但会增加通信、取消和生命周期协议。本轮数据只有两个账户，先通过 Storage/AgentRuntime 端口隔离业务；未来以性能与阻塞证据决定迁移。
3. **SQLite + 稳定 Drizzle 0.45 + better-sqlite3 13.0.3**。另一方案是 node:sqlite，可减少独立二进制，但与选定的稳定 ORM 适配路径不同。13 系列已使用 Node-API，随包提供平台二进制，适合本轮 Node 24 与 Electron 44；必须在两运行时和打包后实际读写。
4. **版本化 SQL 嵌入 Main**。另一方案是 drizzle-kit 生成并分发外部 SQL 文件，自动化程度更高，但增加 CLI、元数据及资源路径维护。本轮一份最小迁移采用版本/hash 表和事务执行；代价是开发者需维护 Drizzle Schema 与 SQL 的一致性。
5. **无模型的 LangGraph 单图**。classify 节点判定阈值，explain 输出带证据的结构化结果。可注入 checkpointer，不引入 LLM Provider SDK、工具调用和多 Agent。分析结果保存在 Event；MemorySaver 只做测试演示。
6. **凭据存储 fail closed**。尚未实现 safeStorage，不使用内存伪装为安全持久存储，更不回退明文。三个 IPC 操作不包含凭据访问能力。

## 影响与验证

所有业务包独立于 Electron；Main 装配实现，Renderer 获得最小 DTO。失败时旧数据保留，任务状态与事件幂等可验证。主要残留风险为 Main 同步 SQLite 的阻塞、Schema/SQL 双维护、跨平台原生包验证、签名公证、实际凭据库与真实平台权限。

验证命令及真实结果统一记在 `docs/EVALUATION.md`。项目未初始化 Git、未选择开源许可证，也未执行提交、推送或发布。

## 官方依据

- [Electron Security](https://www.electronjs.org/docs/latest/tutorial/security)
- [electron-vite 入门](https://electron-vite.org/guide/)
- [Drizzle SQLite](https://orm.drizzle.team/docs/sqlite/get-started-sqlite)
- [better-sqlite3 13 / Node-API](https://github.com/WiseLibs/better-sqlite3/releases/tag/v13.0.0)
- [LangGraph Graph API](https://docs.langchain.com/oss/javascript/langgraph/graph-api)
