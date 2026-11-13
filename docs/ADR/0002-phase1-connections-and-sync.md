# ADR 0002：多账户、安全凭据与持久同步

- 状态：采用
- 日期：2026-11-13
- 授权范围：本轮 Phase 1 的 Mock 多账户与安全同步基础，不接真实平台。

## 决策与取舍

1. Connector 是平台类型，Connection 是独立实例；每实例独立凭据引用、游标和状态。同平台多账户不需要注册多个相同连接器。
2. 采用 Account Manager + Sync Engine + WorkspaceRuntime，保留原 Graph、Event Bus、旧历史和测试。旧表兼容与新表迁移使本轮不需重写已执行迁移；代价是暂时保留历史兼容路径。
3. OS 加密后存 SQLite 密文，替代独立加密文件；可以原子更新、CAS 重加密和恢复删除意图，数据库本身并不因此整体加密。系统密钥后端不可用时拒绝，Renderer 无凭据输入/读取入口。
4. 使用 nextAttemptAt 持久化重试，替代长 Promise sleep；由 Node 定时器运行，未来可移植到 Worker，无外部队列服务。当前仅单进程调度，跨 Worker 需要额外租约设计。
5. 数据、游标、成功状态和业务事件同事务；Event Bus 事务后通知。没有把它宣称为外部 Exactly-once，也没有实现持久消费者。
6. 对忽略取消的底层请求保留并发名额，牺牲可用名额以防请求重叠；异常连接器可能需等待结束或重启。
7. 凭据解密后重新检查授权；OS 轮换重加密使用 CAS，避免等待过程中的更新或删除被旧值覆盖。账户管理操作自身按实例互斥。
8. macOS / Windows 使用异步 safeStorage。Linux 的异步提供者回退语义难以通过当前 API 完整核验，本轮保守采用可明确识别的系统后端同步 API，拒绝 basic_text / unknown。升级 Electron 前需重新评估这一适配分支。

## 验证与后续

测试证据统一见 `PHASE_1_REPORT.md`。实际 OS 验证限 macOS ARM64；Windows/Linux 为替身策略测试。下一步需签名/公证、真实系统矩阵、用户密钥输入隔离方案、真实 HTTP 适配与权限契约测试；不在本轮扩大实现。

依据：[Electron safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage)、[Electron Security](https://www.electronjs.org/docs/latest/tutorial/security)。
