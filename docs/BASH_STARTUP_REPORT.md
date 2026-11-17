# Bash启动准备实施报告

> 后续启动进度和就绪提示已按用户要求补充，最新文件与验证见 [BASH_STARTUP_PROGRESS_REPORT.md](BASH_STARTUP_PROGRESS_REPORT.md)。以下保留首轮实施结果。

2026-11-16，按用户确认的BASH_STARTUP_PLAN.md实施。没有新增依赖或迁移，head仍为0006；没有修改正常用户数据库/Key，未提交、推送或自动启动系统Docker服务。

## 结果和使用

先打开Docker Desktop / Docker Engine，再在仓库根目录执行`pnpm dev:backend`。后端先检查Docker，缺少默认`tavo-bash:local`时自动构建，再实际运行并清理预热容器；成功才监听/领取任务。无需手动创建容器。Docker不可达会输出“Docker 服务未启动或无法连接，请启动 Docker Desktop / Docker Engine 后重试；后端已停止。”并非零退出。

适用于serve、独立worker和直接ASGI生命周期，`serve --no-worker`同样检查。init、模型Key管理及密钥轮换不经过启动准备。管理员可显式`TAVO_BASH_ENABLED=0`；原Compose因没有Docker CLI/socket明确关闭，不新增宿主socket挂载。

## 文件与设计理由

| 文件 | 变化与理由 |
| --- | --- |
| `backend/app/agent/bash.py` | 新增prepare与共享container_options；probe只读，prepare编排默认镜像构建、固定命令预热和归属清理。复用实际执行限制，避免两套权限配置。 |
| `backend/app/services/application.py` | 在模型/Worker租约初始化前等待prepare，失败不领取任务。 |
| `backend/app/main.py`、`cli.py` | 初始化也纳入生命周期try/finally，失败关闭Services；ASGI与独立Worker输出固定安全原因。 |
| `backend/app/core/errors.py`、`packages/shared/src/contracts.ts` | 增加Docker命令缺失/不可达、隔离不支持、镜像缺失/构建失败和预热失败代码，维护公开中文映射。ErrorDetail.code仍为字符串，无额外DTO变更。 |
| `compose.yaml` | 显式关闭当前无Docker执行环境的Bash，保留已有独立API/Worker部署权限。 |
| `backend/tests/test_bash_startup.py` | 12项：镜像复用/自动构建、各失败原因、超时、关闭模式、ASGI资源释放、运行中和清理中取消。 |
| `backend/tests/test_bash_startup_process.py` | 2项：真实CLI serve/worker进程使用假Docker，init成功而运行入口中文失败、非零退出、无Worker租约；原始transport日志不泄漏。 |
| `backend/tests/test_bash_startup_docker.py` | 3项显式真实Docker：预热运行/回收、隔离标签镜像自动构建、无效socket下启动失败且没有监听。 |
| README、backend/README、BASH_TOOL、ARCHITECTURE、ADR0011、AGENTS、DEVELOPMENT_PLAN/LOG、LEARNING_LOG、EVALUATION | 同步启动依赖、Compose边界、学习练习和实际验证；旧默认行为报告标记为历史。 |

## 实际验证

| 命令 | 结果 |
| --- | --- |
| `pnpm api:generate` | 成功，Pydantic导出和生成类型检查通过；未手改生成类型。 |
| `pnpm lint` | ESLint、Ruff和格式检查通过，最后代码修改后复验。 |
| `pnpm typecheck` | 通过。 |
| `pnpm test` | 客户端34通过；当次后端177通过/9跳过，随后补充清理中取消回归。 |
| `pnpm test:backend` | 最终178通过/9项真实Docker默认跳过，31.35秒。 |
| `TAVO_TEST_DOCKER=1 backend/.venv/bin/python -m pytest -q backend/tests/test_bash_startup_docker.py backend/tests/test_bash_sandbox.py` | 最终真实9/9通过，13.17秒；含自动构建、预热、不可达退出，以及原有非root/网络/文件系统、OOM/超时/输出/Session隔离。后端合计187项分别验证。 |
| `pnpm build` | 成功；现有Zod PURE注释产生Rollup提示，不影响产物。 |
| `node scripts/backend-smoke.mjs` | 通过：独立后端认证、Session/幂等、重启审核与工具。 |
| `node scripts/model-smoke.mjs` | 通过：本地HTTP合成Key、加密/错误/断开/重试/重启和两轮Agent协议，无厂商请求。 |
| `node scripts/bash-smoke.mjs` | 通过：默认能力、真实命令/文件、后端重启和归属清理；启动已实际经过预热。 |
| `docker compose config --quiet` | 配置检查通过。 |
| `docker compose build` | 镜像构建通过。 |
| `pnpm test:docker` | 实际Linux Compose独立API/Worker、队列/审核、加密续接、工具与重启业务通过；测试项目自行清理。 |

最后只读检查`docker ps -a --filter label=tavo.kind=warmup --format '{{.Names}}'`与`docker image ls tavo-startup-test --format '{{.Repository}}:{{.Tag}}'`均为空，没有遗留预热容器或独立构建测试标签。没有执行全局prune或删除用户默认镜像/工作卷。

普通进程测试最初尝试在受限执行环境分配TCP端口，被系统EPERM拒绝；改为端口0且验证启动前失败/未创建租约，真实Docker显式组另验证无监听。未用跳过掩盖失败。取消路径补查发现清理中取消可能被吞，已改为等待清理后重新抛出，并增加对应回归。

## 边界

默认镜像构建最多180秒，可能需要网络；Docker输出上限64KiB，超限会安全失败。自定义镜像需管理员预先准备。预热最多15秒并确认清理，不保留常驻容器；实际命令继续每次独立容器，Session卷和历史权限不变。运行中Docker断开仍按既有工具错误/未知结果处理，启动检查不能替代执行前审核/租约验证。

本轮未更改客户端UI/原生流程，未重复桌面E2E或未签名包验证；共享错误映射通过TS/客户端测试和构建。没有真实厂商付费请求、跨平台桌面、签名分发、压力测试或完整安全审计结果。初次网络下载路径按实际Docker构建执行，但本机已有基础层缓存，不代表冷网络下载性能。
