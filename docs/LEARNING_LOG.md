# 学习日志

## 2026-11-12：Phase 0 复盘入口

- 数据流：从按钮事件沿 Preload → Main → Core → Connector → SQLite 追踪，再解释 UI 为什么不直接调用连接器。
- 状态：分别观察账户更新时间、事件发生时间、任务状态和分析结果；这些记录有不同生命周期。
- 设计经验：先看锁定版本的实际实现。better-sqlite3 13 已采用 Node-API，过去的 ABI 重建经验需要更新；两个运行时实际读写才是证据。
- 故障经验：npm 系统 DNS 解析失败，经临时进程解析修复后完成安装；没有修改系统 DNS。pnpm 11 构建白名单使用 `allowBuilds`。
- 验证经验：编译、业务测试、真实 Electron E2E、打包后测试分别证明不同事情；结果见 `EVALUATION.md`。
- 待开发者自己完成：从 `LEARNING_NOTES.md` 选一个练习，记录预期、执行结果和一个失败原因，再决定下一项任务。

## 2026-11-13：Phase 1 复盘入口

本轮可复盘：Controller / Service / Repository 在 Electron 中的对应关系；Connector 与 Connection 的区别；async/await 不自动保证互斥；Promise.race 与取消的区别；游标与数据的事务边界；CAS 防止旧密文覆盖；持久事件与内存通知的不同保证。

请从 `LEARNING_NOTES.md` 的两项练习选一项独立完成，先写预期再改代码，记录实际命令和失败原因。下一阶段仍需先确认真实平台范围，不以本轮授权推定可读取真实账户或发布。

## 2026-11-13：客户端与 Python 后端分离

本轮可学习并独立复述的内容：HTTP 与 IPC 边界、Router/Service/Repository、Depends 生命周期与测试注入、Pydantic/OpenAPI/Zod、SQLAlchemy session 和原子同步事务、Alembic 与旧库 ETL、数据库任务队列/租约/fencing、SSE 回放、Python StateGraph、客户端会话与第三方凭据的区别。

这些是练习材料，不代表开发者已掌握。代码路径、两种方案的取舍、面试追问及 3 个验收明确的小练习见 LEARNING_NOTES.md。建议先画一张“点击同步直到数据展示”的数据流，再自己完成一个小练习。

## 2026-11-13：清理代码与保留数据兼容的区别

旧业务实现可以由 Git 保存，不必一直留在当前工作区维护依赖。旧 SQL 测试夹具、导入器和历史 DTO 承担数据兼容，需要继续保留。学习练习见 LEGACY_CLEANUP.md：用哈希证明搬移 SQL 没有改变已执行的迁移，再说明 Python 如何独立生成并导入旧库。
