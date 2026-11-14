# 客户端与独立 Python 服务端迁移计划

日期：2026-11-13。本次用户明确授权 Python / FastAPI 架构重构，替代旧规划的 Node-only 方向；不接入真实连接器。

## 现状与职责

当前为 React → Preload IPC → Electron Main → TypeScript Core → Drizzle / SQLite / Mock / LangGraph.js。基线 typecheck、lint、79 个 Vitest 和 build 实际通过；Git 原先不存在，已建立本地基线 898328d 与 refactor/client-server-python 分支。

新链路为 React → API Client / TanStack Query → HTTP REST + SSE → FastAPI Router → Service → Repository → SQLAlchemy / SQLite。连接器与 Python LangGraph 为服务层调用的独立模块。Python 独立启动，客户端退出不会关闭服务。

| 迁移到 Python | 留在 Electron / React |
| --- | --- |
| Account / Connector / Credential / Sync / Event / Agent 服务 | Main 窗口、原生能力、连接配置与客户端认证材料 |
| 数据库、事务、迁移、恢复、调度 | React 页面、样式、短期交互状态、API 缓存与 SSE 展示 |
| 数据权威、权限校验、凭据作用域 | Preload 仅原生白名单，不开放账户/SQL/同步/Agent IPC |

IPC 是桌面进程之间的受限调用；HTTP 是独立进程/设备间的协议，需要额外身份认证、错误码、超时、来源和断线处理。Router 处理协议和参数，Service 处理业务与状态，Repository 处理查询和事务；不在 Router 拼业务 SQL。

## 关键取舍

1. 同进程包装 Node 业务可减少迁移量，但仍依赖桌面运行时；本轮选择 Python 等价迁移，代价是两种语言、独立部署和协议维护。
2. 服务端密钥可使用 OS Keyring 或外部主密钥文件。选择 cryptography Fernet/MultiFernet + 独立 0600 密钥环，避免要求无桌面 Linux 安装 Electron；不是自创密码算法。生产需进一步接入专用秘密管理与备份策略。
3. 同步继续用持久数据库任务和单领导 Worker；使用数据库租约与 fencing 防止多个 API 进程重复调度，不用 FastAPI BackgroundTasks 承担可恢复任务，不引入 Redis/Celery。
4. Pydantic / OpenAPI 为跨语言契约来源，生成 TypeScript 类型；运行时仍校验 HTTP 响应。没有 Python 与 TS 的双向运行时依赖。
5. SSE 从持久事件序号恢复，支持 Last-Event-ID；任务最终状态可通过 REST 查询。连接断开不代表任务失败。

## 迁移步骤

1. 保存已验证源码基线，创建 `refactor/client-server-python` 分支；增加私密目录忽略规则。
2. 初始化 FastAPI / Pydantic / SQLAlchemy / Alembic，先验证健康、身份认证和文档。
3. 迁移账户、连接器、凭据服务；独立数据库建立约束和事务。
4. 迁移调度、增量、重试、幂等、并发、恢复、事件，再迁移等价 Python LangGraph。
5. 客户端 API Client 替换业务 IPC，复用页面与样式，加入后端状态、重连、SSE 和缓存。
6. Python 单元/API/迁移/SSE 测试与旧 TS 回归、真实独立进程和 Electron E2E；验证后通过 Git 保留旧代码历史参考。
7. 更新文档、Docker 与报告；不自动发布或推送。

## 数据与回滚

现有默认数据库为 `appData/Tavo/tavo.sqlite`。检查发现其为 Phase 0 格式：1 条连接器，账户/任务/事件为 0；没有输出内容或凭据。即使空业务数据，也用 SQLite backup API 创建一致性备份。

导入写入独立后端数据库，不原地修改源文件。支持 Phase 0/1 结构，保持账户 ID、事件序号、关联 ID、数据和历史；校验数量及外键关系。safeStorage 密文无法由 Python 直接解密，保留为不可用的历史密文，账户明确标记需重新连接，不伪装为已授权。导入时未完成任务的处理和字段差异必须记录。

回滚时停止新进程，保留新数据库和密钥环，切回基线源码并让旧客户端使用原数据库。新数据不会自动反向同步到旧结构；这是需要单独导出/转换的边界。禁止删除数据库、删除密钥或用 Git 清理命令擦除用户修改。

## 验证边界

逐模块测试状态、异常、事务、租约、凭据隔离与恢复；实际启动无 GUI 依赖的后端，验证客户端退出后任务继续运行；运行 Ruff、Pytest、TS typecheck/lint/tests/build 和可用的桌面/打包验证。Docker/Linux、正式 HTTPS、多用户隔离如无法实际验证，必须标记未验证或未实现。

学习练习：先画出一次同步的 Router → Service → Repository 调用，再说明“提交数据后才推进游标”由哪一个事务保证。完整练习在交付学习笔记补充。
