# Bash默认启用调整

> 历史记录：随后用户确认BASH_STARTUP_PLAN.md，当前默认开启Bash时Docker不可达会阻止后端启动；镜像自动准备和预热见 [BASH_STARTUP_REPORT.md](BASH_STARTUP_REPORT.md)。本报告中的“不阻断普通聊天”仅描述当时实现。

2026-11-16，按用户“默认启用bash，不要让用户自己选择”的直接要求实施，无新增依赖或数据库迁移，未修改正常用户数据、提交或推送。

## 行为和文件

- `backend/app/core/config.py` / `.env.example`：服务端默认启用Bash，管理员显式TAVO_BASH_ENABLED=0仍可关闭。
- `agent/contracts.py`：新会话省略bashEnabled时默认true；纯文本省略时false，显式非法组合仍422。已有Session和冻结Context保持原值，0001–0006不改。
- `services/sessions.py` / `application.py`：创建会话与Docker就绪分开，环境缺失不阻断普通聊天；实际工具调用仍受能力检查和明确失败约束，不回落宿主。
- `renderer/chat/Chat.tsx` / `styles.css`：移除Bash开关及对应状态/样式，新对话自动使用默认能力，保留运行环境提示。历史关闭Bash的会话保持原权限，开始新对话即使用新默认。
- OpenAPI / generated.ts同步生成；更新默认值、纯文本、管理员关闭、无Docker聊天和真实容器/E2E用例。普通单元测试明确关闭外部Docker依赖，真实Docker组单独执行。
- BASH_TOOL/README/ARCHITECTURE/API/AGENTS及学习/评测说明同步更新。

原auto_readonly下Bash仍需人工确认；auto_sandbox可在规则和模型自审查通过后自动执行。本次修改默认能力，不扩大审核模式或容器权限。Docker、已准备的镜像和宿主Python Worker仍为执行条件；只改默认值，无需再次初始化数据库。

## 验证

- `pnpm api:generate`、`pnpm lint`、`pnpm typecheck`、`git diff --check`：通过。
- `pnpm test`：客户端34通过、后端164通过/6项真实Docker默认跳过。
- `TAVO_TEST_DOCKER=1 backend/.venv/bin/python -m pytest -q backend/tests/test_bash_sandbox.py`：6/6真实容器通过，Session请求省略bashEnabled验证默认值。
- `node scripts/bash-smoke.mjs`：独立后端默认能力、真实命令、文件跨重启和归属清理通过。
- `TAVO_BROWSER_CHANNEL=chrome pnpm test:dev`：开发窗口通过。
- `TAVO_TEST_DOCKER=1 TAVO_BROWSER_CHANNEL=chrome pnpm test:e2e` 和 `pnpm test:packaged`（相同环境变量）：顺序完整运行各7/7通过。
- `pnpm package:dir`：生成ARM64未签名测试包，非正式签名/分发。
- `docker compose config --quiet` / `docker compose build` / `pnpm test:docker`：配置、构建及独立API/Worker实际业务分别通过，无Docker socket或CLI的容器仍可处理普通会话/只读工具。

移除开关后，重启用例原本依赖checkbox状态等待恢复，需改为等待实际历史消息出现，再继续读取旧会话文件；未改变会话恢复策略或用重试掩盖。真实厂商付费请求未执行；Docker工作卷、历史权限及未知写操作限制沿用BASH_TOOL.md。
