# 启动进度与完成提示修复

2026-11-16。用户反馈没有看到镜像拉取和容器启动，要求显示启动进度并在完成时提醒。本次在已确认的启动方案内补齐终端反馈，没有更改容器隔离、持久Session、模型、权限或迁移，没有新增依赖、修改正常用户数据、提交或推送。

## 原因与行为

本机只读检查确认`tavo-bash:local`已存在，因此无需重复拉取或构建；预热容器按设计运行后自动删除。原CLI将Uvicorn设为warning，prepare成功路径没有提示，构建原始日志又被安全捕获，导致准备过程几乎不可见。

现在`pnpm dev:backend`立即显示：

```text
[Tavo 启动] [1/4] 正在检查 Docker 服务、隔离能力与本地镜像…
[Tavo 启动] [1/4] Docker 服务与隔离检查通过。
[Tavo 启动] [2/4] 本地执行镜像已存在，直接复用，无需拉取或构建。
[Tavo 启动] [3/4] 正在创建并运行隔离预热容器…
[Tavo 启动] [4/4] 正在确认预热容器已清理…
[Tavo 启动] [4/4] Bash 沙箱准备完成，预热容器已清理；工具调用时将创建独立容器。
[Tavo 启动] 后端初始化完成，正在启动 HTTP 监听。
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

这是缓存命中的典型输出节选，实际还含Uvicorn的进程/等待启动日志。镜像缺失时第2阶段改为“默认镜像缺失，正在构建；需要时下载基础镜像（最长180秒）”，构建并验证后提示完成。检查、构建、预热、清理中的耗时步骤每5秒提示已等待秒数，不声称精确的层下载百分比。

沙箱准备完成不等于HTTP已绑定；最终`Uvicorn running on`在实际监听后出现，端口占用仍报错退出。独立worker安装信号处理后提示启动完成。管理员关闭Bash时明确提示跳过Docker检查，Compose可据此理解没有预热的原因。没有新增系统通知或客户端弹窗，所有反馈位于启动终端。

## 文件与选择

| 文件 | 变化与理由 |
| --- | --- |
| `backend/app/core/startup.py` | 固定文案的stderr立即flush输出、每5秒耗时心跳；阶段结束/异常时取消并等待心跳，避免后台刷屏。不依赖普通日志级别。 |
| `backend/app/agent/bash.py` | 四阶段进度，明确缓存/构建分支；只有预热与清理均成功才显示沙箱完成，不转发Docker原始日志。 |
| `backend/app/main.py` | 初始化完成与HTTP监听分开表达，避免提前声称服务已可连接。 |
| `backend/app/cli.py` | Uvicorn改为info以保留实际监听地址和启动状态，access log仍关闭；独立Worker增加完成提示。 |
| `backend/tests/test_bash_startup.py` | 扩展阶段顺序/缓存/构建/脱敏/失败无成功提示的断言，新增2项慢构建成功/失败的持续等待与返回后静默测试。 |
| `backend/tests/test_bash_startup_process.py` | 独立失败进程验证第一阶段可见，没有错误的完成提示或原始Docker错误。 |
| `backend/tests/test_bash_startup_docker.py` | 真实构建/预热日志断言；新增serve与worker两项真实进程就绪测试，确认提示顺序并关闭测试进程。 |
| README、backend/README、BASH_TOOL、ARCHITECTURE、ADR0011、AGENTS、DEVELOPMENT_PLAN、BASH_STARTUP_REPORT、LEARNING_LOG、EVALUATION、DEVELOPMENT_LOG | 同步阶段、完成语义、历史报告入口与验证。 |

直接转发Docker原始输出能显示细节，但会引入本机路径、认证和不受控日志内容。采用固定阶段与真实等待耗时，复用原有有界CLI捕获，不改执行环境或新增进度API。

## 实际验证

| 命令 | 结果 |
| --- | --- |
| `backend/.venv/bin/python -m pytest -q backend/tests/test_bash_startup.py backend/tests/test_bash_startup_process.py` | 16通过，含取消、清理、失败、持续进度。 |
| `pnpm lint` | ESLint/Ruff/格式检查通过，84个Python文件格式合格。 |
| `pnpm typecheck` | 通过。 |
| `pnpm test` | 客户端34通过；后端180通过/11项真实Docker默认跳过，30.30秒。 |
| `pnpm build` | 通过；现有Zod PURE注释产生Rollup提示，不影响产物。 |
| `TAVO_TEST_DOCKER=1 backend/.venv/bin/python -m pytest -q backend/tests/test_bash_startup_docker.py backend/tests/test_bash_sandbox.py` | 真实Docker11/11通过，15.68秒，含镜像隔离测试标签构建、真实预热/清理、CLI监听与Worker就绪、无效socket失败及原Bash边界。 |
| `node scripts/backend-smoke.mjs` | 独立后端认证、Session/幂等/重启审核/工具闭环通过。 |
| `node scripts/bash-smoke.mjs` | 默认能力、真实命令/文件持久化、后端重启与归属清理通过。 |

本轮没有修改Pydantic/DTO，不需要重新生成契约。没有重跑Compose镜像/业务、model HTTP smoke、桌面E2E或包内测试，不将上一轮结果当作本轮执行。没有真实付费厂商请求；自动构建使用本机已有基础层缓存，不代表冷网络下载性能。默认镜像/用户工作卷不删除，Docker服务不停止。

## 剩余边界

显示阶段和等待耗时，不显示下载字节/总百分比。镜像存在时不会强制更新，Bash仍每调用创建独立容器，预热不会留下常驻容器。Docker/构建失败仍停止后端，进度不改变原180秒构建、15秒预热及64KiB捕获上限。
