# ADR 0003：独立 Python 服务端与受限桌面客户端

状态：采用，2026-11-13；用户明确授权替代原 Node-only 规划。

背景：桌面退出即停止业务，safeStorage 依赖图形宿主，数据库与调度跟随 Main 生命周期；开发者需要学习标准 Python 后端。

选择：Electron 原生能力 + React 展示；FastAPI Router → Service → Repository → SQLAlchemy / SQLite；Python LangGraph；HTTP REST + 持久事件 SSE。OpenAPI 生成 TS 类型，保留客户端 Zod 运行时验证。后端无 TS 运行时依赖。

方案对照：包装既有 Node Core 能更快交付，但无法实现所要求的 Python 学习与独立安全存储；重写全部 UI 会增加回归，故迁移业务并复用现有视图。Worker 暂用同机 SQLite 单领导者租约与 fencing，避免提前引入 Redis / Celery。Fernet 外部文件环比 OS safeStorage 更便于无桌面 Linux，代价是部署者负责密钥文件保护与备份。

后果：需要两套依赖、跨语言契约、认证、CORS、超时与重连。旧库保持原样，旧 OS 密文保留但需重新授权。单所有者不等于多租户系统，开发 Compose 不等于公网生产部署。

验收：保留旧 TS 79 项回归，新增 Python/API/迁移/Worker/图测试、API Client 测试、独立进程 REST/SSE/重启与真实 Electron 测试。未实际执行的目标平台不标记通过。

后续清理：用户已授权移除旧 TS 实现与专用测试，等价验证保留为历史基线；当前使用客户端/Python/E2E 测试，旧 SQL 夹具继续验证数据迁移，详情见 ../LEGACY_CLEANUP.md。
