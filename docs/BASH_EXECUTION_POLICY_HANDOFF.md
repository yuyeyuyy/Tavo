# Bash执行策略优化暂停记录

> 2026-11-17已按用户“继续”恢复并完成。本文件是暂停时的历史记录，实际完成项、验证与边界见[BASH_EXECUTION_POLICY_REPORT.md](BASH_EXECUTION_POLICY_REPORT.md)。

日期：2026-11-16。状态：**按用户“剩下的工作明天再做，先暂停”明确要求暂停**。完整设计已获确认，恢复后不需要重复请求设计批准；不得自行恢复或创建定时任务。当前代码尚未最终验收/交付，无提交、推送或正常用户数据升级。

## 已实施

- `sandbox/defaults.toml`、`core/sandbox_config.py`统一资源配置：32768 UTF-8字节、默认60/最大300秒、CPU1/最大2、内存512/最大1024MiB、PID128、单文件64MiB、执行Sandbox容量1。新Bash策略Run的轮次/工具/活动时间及Context预算联动；旧冻结策略保留。
- Bash/文件工具的模型参数解析、分块`workspace_write`、结构化错误/阶段审计；LOW本地操作不强制模型复核，MEDIUM可用，敏感/越界能力独立审核。
- `agent/sandbox.py`的SandboxManager与0007迁移：持久单容量claim、Run容器复用、任务取消/失租/回收、残留进程处理。Session工作区跨Run保留，通过工作副本、版本化checkpoint、diff审批和CAS提交保护已有文件。
- 独立UDS出口代理：执行容器仍network=none，代理验证域名/方法/路径、公网DNS、固定连接IP/TLS SNI、重定向、响应/频率/Run配额；默认网络仍关闭。新增系统DNS或管理员选择的Cloudflare DoH，脚本不能自选解析器。
- 专用`sandbox.api_key`复用SecretStore/JWE/隐藏CLI输入；只在可信代理注入凭据。敏感HTTP操作精确审批、加密续接、未知结果不自动重放父脚本。
- 专用Python3.12开发镜像，锁定哈希的pip/pytest/requests/httpx/pydantic，`tavo_http`和`tavo_pip`受控客户端/安装helper。
- 相关REST/OpenAPI/生成TS/Zod、API凭据设置与具体操作审批预览。六个原生方法未变。后端Docker镜像新增默认策略文件，原Compose仍明确关闭Bash。

## 已执行验证（当前工作树）

- `pnpm api:generate`、`pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm build`通过。
- 客户端34通过；后端普通215通过/19项真实Docker默认跳过。
- 显式真实Docker组19/19通过，91.30秒：
  `TAVO_TEST_DOCKER=1 backend/.venv/bin/python -m pytest -q backend/tests/test_bash_startup_docker.py backend/tests/test_bash_sandbox.py backend/tests/test_sandbox_development_docker.py backend/tests/test_sandbox_egress_docker.py --tb=short`
- 真实场景含命令/文件持久化、Python异常后修复、长命令、OOM/PID、容器复用/单容量、取消回收、大范围文件变更批准、真实公网PyPI HTTPS及six精确哈希wheel安装。认证POST上游使用测试专用Mock传输，真实UDS/审批/凭据隔离；未调用真实付费模型或第三方写操作。
- 暂停时上述测试均已结束，无本任务仍在运行的验证进程。`git diff --check`通过。

日志（临时文件仅作本次恢复参考）：`/private/tmp/tavo-policy-real-docker.log`、`/private/tmp/tavo-policy-build.log`、`/private/tmp/tavo-policy-all-tests.log`。修改前源码备份为`/private/tmp/tavo-before-bash-policy-20261116.tar.gz`，不含正常数据库/密钥目录。

## 明天继续的顺序

1. 重新核对当前工作树与本记录，保留本任务之前已有的大量未提交修改；不要reset或批量清理。阅读完整计划和新模块，先完成代码边界复查。
2. 补足关键定向测试：专用API凭据JWE/CAS与轮换、旧0006→0007数据/审批兼容、旧Worker不回收新Worker资源、网络配额跨容器复用/回收不重置、expanded资源profile、代理模式启动UDS健康检查。现有19项通过不能替代这些尚未执行的用例。
3. 复查取消结果的duration/sandboxId填充、排队时间与Run预算、冻结配置与当前限制的合并，以及未知工作副本保留/清理策略。不要为了通过测试放宽内网、Fake-IP、凭据或宿主边界。
4. 跑独立backend/model/Bash smoke、Compose配置/构建/实际业务。相关UI已改，需要开发窗口、E2E与未签名测试包回归；本任务这些尚未重跑。
5. 更新`BASH_TOOL.md`（目前仍包含旧限制，不能当作新版本完整指南）、README/backend README、ARCHITECTURE、AGENTS、ADR0012、LEARNING_LOG、EVALUATION和最终实施报告。补`backend/sandbox.example.toml`与代理/API目标/依赖pin配置示例。
6. 做最终lint/typecheck/test/build、必要真实Docker回归和差异检查，核验0001–0006未改，再交付。需要正常用户升级时明确备份并执行init；本次只升级测试库，未自动升级`backend/.local`。

## 必须保留的边界和已知事项

- 默认`network.mode=none`，不会自动授权真实API、付费POST或任意依赖。普通requests/curl不能透明直连；脚本使用`tavo_http`/`tavo_pip`，不能宣称所有SDK已透明适配。
- 本机Docker系统DNS给PyPI返回198.18.x.x/2001:2::等保留网段，已正确拒绝；真实出口测试显式选择Cloudflare DoH获得真实公网地址后通过。部署文档必须说明这一条件，不能允许Fake-IP网段来掩盖问题。没有改用户系统DNS/代理或正常配置。
- 新默认镜像为`tavo-bash:dev-v3`，版本/源码指纹检查会触发必要重建；原`tavo-bash:local`未删除。编辑runtime/Dockerfile/锁文件会改变指纹，真实测试应在源码稳定后串行运行，避免边改镜像边验证。
- 单执行沙箱不包括固定代码的出口代理/工作区控制helper；这些不执行Agent命令，也不挂宿主目录/socket/整个密钥环。需在最终资源说明中准确区分。
- 普通Docker命名卷没有跨平台通用总容量硬配额。依赖临时区有容量约束，工作区为文件/总量监测及受控提交；不要把软监测或单文件64MiB称为卷硬配额。中断/未知工作副本保留，避免误删文件，需要文档说明运维边界。
- 不执行全局Docker prune，不删除用户工作卷。暂停检查只读，不停止用户自己的后端或容器。
