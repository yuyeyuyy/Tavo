# DeepSeek 网络拒绝与 DNS 默认值修复

2026-11-17。针对关闭代理全局模式后仍返回NETWORK_DENIED的问题，完成只读诊断并调整现有配置默认值；没有增加网络通道或放宽地址隔离。

## 原因与选择

最新实际Run已为策略4/full_access，proxy与公网访问开启，无请求审批，实际使用tavo_http。因此排除旧会话禁网、Bash审批或误用直连库。

宿主在关闭全局模式后仍将api-docs.deepseek.com解析为198.18.0.12/2001:2::c，另两个DeepSeek域名也返回相同网段的虚拟地址。它们未通过现有公网地址校验，导致NETWORK_DENIED。代理路由模式和DNS/Fake-IP模式不同，关闭前者不代表后者失效；本次没有读取或修改代理软件设置，不能断言具体软件开关。

两种方案：由用户调整本机TUN/DNS返回真实公网记录，或让Tavo可信出口使用已有DoH。本次选择后者，避免依赖本机Fake-IP解析，不修改系统DNS。复用固定1.1.1.1连接、Cloudflare域名TLS/SNI验证和原有全部目标地址校验；不允许脚本指定解析器，不放行保留/内网/元数据地址。DoH不可达时失败，不静默切回其他解析方式。运维仍可显式配置system。

## 文件变化

- backend/app/core/sandbox_config.py、backend/sandbox/defaults.toml：默认dns_mode改为cloudflare-doh，类型默认值与TOML一致。
- backend/sandbox.example.toml：示例与默认值一致，说明system及隔离边界。
- backend/tests/test_sandbox_network.py：复现系统Fake-IP，验证默认DoH成功、显式system仍阻断，防止以放行虚拟IP掩盖问题。
- backend/tests/test_sandbox_egress_docker.py：增加不手动覆盖DNS的真实DeepSeek官方文档抓取，验证Agent→Bash→UDS代理→公网路径。
- docs/BASH_EXECUTION_POLICY_GUIDE.md、DEVELOPMENT_PLAN.md、ARCHITECTURE.md、LEARNING_LOG.md、EVALUATION.md、backend/README.md及本报告：更新当前默认值、原因、配置和验证。前一轮权限报告保留历史结果。

本轮不修改模型Key、正常数据库、旧Context、系统代理设置、迁移或前端，不引入依赖，不推送。源码备份位于/private/tmp/tavo-before-dns-default-20261117.tar.gz，不含数据和密钥。

## 验证与使用

收尾结果见下方补充。宿主使用同一Outbound实现的DoH测试已获取官方文档HTTP200；不能单凭宿主测试称容器路径已验证，因此另设真实Docker回归。

需要停止并重新启动Python后端，再在客户端新建对话；运行中的进程不会自动重读TOML，已冻结Run不会升级解析模式。已有自定义TAVO_SANDBOX_CONFIG中若显式设置system，仍优先使用该值，需由管理员调整。此修改不保证运营商、Docker或DoH服务永不发生连接故障。

### 最终执行结果

| 命令 | 结果 |
| --- | --- |
| `cd backend && .venv/bin/python -m pytest -q tests/test_sandbox_network.py tests/test_sandbox_policy.py tests/test_sandbox_lifecycle.py --tb=short` | 71通过；Fake-IP复现、默认DoH、显式system与既有地址边界 |
| `pnpm lint`、`pnpm typecheck` | 通过（ESLint、Ruff、格式及TS） |
| `pnpm test` | 客户端34通过，后端266通过、24真实Docker默认跳过，40.22秒 |
| `pnpm build` | 通过；保留依赖Zod原有注释位置警告 |
| `cd backend && TAVO_TEST_DOCKER=1 .venv/bin/python -m pytest -q tests/test_sandbox_egress_docker.py --tb=short` | 6/6通过，61.92秒；新默认配置经真实Run/Bash/UDS出口读取DeepSeek官方文档HTTP200，固定wheel、合成认证与匿名写方法、旧请求审批均通过 |
| `node scripts/backend-smoke.mjs && node scripts/bash-smoke.mjs` | 独立进程、默认命令能力、文件持久化和后端重启通过 |
| `git diff --check` | 补丁空白检查通过 |

无前端改动，本轮未重跑桌面E2E或重新打包；上一轮8/8桌面与8/8包内是历史验证。没有真实付费模型调用或第三方写业务数据。网络上游写方法为合成传输，DeepSeek实连只读文档。测试使用临时库、合成凭据和独立工作卷，正常用户数据未升级或修改。
