# 在客户端配置模型与 Key

> 当前模型请求支持初次＋最多3次自动重试；DeepSeek空正文生成耗尽可逐级增额，保持原60秒总等待和取消。详情见[重试指南](MODEL_RETRY_GUIDE.md)，无需数据库迁移。下文旧无状态接口限制按历史范围阅读，当前Session历史会持久保存。

2026-11-16。现在可以在客户端选择厂商、填写模型 ID 并录入 Key，无需手动编辑服务端 JSON。支持九家现有文本协议预设：OpenAI、Claude、Gemini、DeepSeek、千问、Kimi、智谱、OpenRouter、xAI。自动验证只使用合成 Key 和本地 HTTP 替身，真实账户权限/额度/网络仍需实际测试。

## 1. 本次升级与启动

已有数据先备份 `backend/.local/tavo.sqlite` 与独立的 `master.keys`（数据库应使用一致性备份），停止旧版本后端和客户端。首次或本次升级运行一次下面命令，升级至 `0006_agent_pool_bash`；不要删库。

终端一，在仓库根目录执行：

```bash
cd /Users/macbook/IdeaProjects/Tavo
pnpm backend:init
pnpm dev:backend
```

终端二：

```bash
cd /Users/macbook/IdeaProjects/Tavo
pnpm dev
```

保持两个终端运行。后端默认 `http://127.0.0.1:8000`，右上角应显示后端已连接。本次代码升级需要重启一次；之后通过客户端添加模型/保存 Key 都即时生效，不需要重启。

## 2. 全部在客户端操作

1. 打开右上角设置，在“模型与 Key”中点击“添加模型”；空目录会直接显示添加表单。
2. 从“模型厂商”下拉选择服务商。
3. 填写该厂商控制台中的文本模型 ID，可选填显示名称。模型 ID 是实际 API 标识，不是套餐名称；首轮手动填写，不自动获取厂商模型列表。
4. 点击“添加模型配置”。新配置自动被选中，下方“模型 API Key”密码框出现并获得焦点。
5. 粘贴 Key，点击“加密保存 Key”。输入立即清空，成功后显示凭据版本。
6. 在设置上方“对话 → 模型”选择刚添加的模型，再关闭设置发送测试消息。

添加配置/保存 Key 都不会请求厂商；“已配置”只表示本地凭据可读取，不代表厂商鉴权/额度有效。只有用户选模型并发送消息才发出真实请求。切换模型会开始新对话，失败不自动改用 Mock 或其他厂商；可恢复的模型请求按有界策略自动重试，详见重试指南。

已有模型更换 Key：在“选择凭据目标”选中对应项目，再输入新 Key。密码框不回填已保存 Key，提交/关闭都会清空；失败要求重新输入。两个窗口或 CLI 同时更新会受到版本校验，旧提交不能覆盖新值或复活删除。响应丢失时可能已经保存，先刷新状态再决定是否重输；目录约 10 秒刷新，重新连接也会刷新。

Key 使用 Web Crypto 加密信封上传，最终在服务端 SecretStore/Fernet 加密落库；不放入普通模型配置、聊天、源码、VITE_ 变量或命令参数。远程后端仍必须 HTTPS；不支持 Web Crypto 时禁用提交，保留隐藏 CLI。JavaScript 内存清理尽力而为，不保证被控制的前端仍能保护正在输入的秘密。

首轮新增配置使用厂商默认端点和 2048 输出预算；不支持在界面修改任意 Base URL、输出预算、编辑/删除配置或自动测试连通性。模型总数最多 32，包含导入的禁用项目。进阶能力的范围与原因见 [客户端配置报告](CLIENT_MODEL_SETUP_REPORT.md)。

## 3. 旧配置如何兼容

第一次升级至 0004 并初始化时，在一个数据库事务里导入现有 `models.json`，保留 profile ID、厂商、模型、名称、启用状态、Base URL、输出预算和默认标记，旧 Key 绑定不变，不需要重新输入。缺失文件也会记录为空目录已初始化。原文件不删除、不改写。

完成一次导入后，SQLite 是权威配置；再编辑 models.json 或重启都不会覆盖客户端新增项。当前 `TAVO_LLM_CONFIG_FILE` 仅指定首次兼容导入来源。全新安装直接在客户端开始，无需复制模板；需要迁移旧 JSON 时应在首次 init 前放到指定位置。格式模板为 `backend/models.example.json`，其中占位模型默认禁用，不能直接启用 `SET_MODEL_ID`。

服务端仍负责校验并保存用户在客户端的选择；“由客户端配置”不表示把 Key 长期存入客户端。

## 4. 保留的服务端隐藏录入 CLI

客户端创建的配置也可通过原 CLI 录入/更换/删除 Key。配置 ID 可从认证 API `/api/v1/model-profiles` 的公开 id 查询；旧 JSON ID 保持不变。下面以已导入的 `deepseek-local` 为例：

```bash
cd /Users/macbook/IdeaProjects/Tavo/backend
uv run python -m app.cli model-key --profile deepseek-local
```

在 `Model API key (hidden):` 后粘贴，不接受命令参数或管道传 Key。删除当前绑定的 Key：

```bash
uv run python -m app.cli model-key --profile deepseek-local --delete
```

两种方式都写同一数据库/密钥环，版本同步推进，无需重启。删除保留版本墓碑，不调用厂商撤销接口；密钥环轮换沿用 `rotate-keys`，不推进内容版本。密文在 secrets 表、版本在 credential_states，数据库与外部 master.keys 分别保护和备份。

聊天非流式，服务端最多等待 60 秒；取消停止本地等待并尝试取消上游，不能承诺不计费。最多 20 条输入消息/24 KiB JSON，超限开始新对话。聊天不持久化，也不自动写入 Agent 长期记忆。

## 其他厂商与地区

客户端按厂商选择并填写实际模型 ID/Key。默认端点由以下服务端预设决定；这里是模型端点，与连接设置中的 Tavo 后端地址不同。

| provider | 默认 Base URL / 协议 | 官方资料 |
| --- | --- | --- |
| openai | https://api.openai.com/v1 / Responses | [OpenAI](https://developers.openai.com/api/reference/resources/responses/methods/create) |
| anthropic | https://api.anthropic.com/v1 / Messages | [Claude](https://platform.claude.com/docs/en/api/messages/create) |
| gemini | https://generativelanguage.googleapis.com/v1beta / generateContent | [Gemini](https://aistudio.google.com/api-reference/generate-content) |
| deepseek | https://api.deepseek.com / Chat Completions | [DeepSeek](https://api-docs.deepseek.com/) |
| qwen | https://dashscope.aliyuncs.com/compatible-mode/v1 / Chat Completions | [百炼](https://help.aliyun.com/zh/model-studio/base-url) |
| kimi | https://api.moonshot.cn/v1 / Chat Completions | [Kimi](https://platform.kimi.com/docs/api/chat) |
| zhipu | https://open.bigmodel.cn/api/paas/v4 / Chat Completions | [智谱](https://docs.bigmodel.cn/cn/api/introduction) |
| openrouter | https://openrouter.ai/api/v1 / Chat Completions | [OpenRouter](https://openrouter.ai/docs/quickstart) |
| xai | https://api.x.ai/v1 / Responses | [xAI](https://docs.x.ai/developers/rest-api-reference/inference/responses) |

旧 JSON 首次导入允许保留已校验的 `baseUrl`，基础地址不要附加 `/chat/completions`、`/responses` 等动作路径。新客户端表单不开放任意地址；远程地址要求 HTTPS；HTTP 仅允许明确的回环地址，供本地替身使用。禁止 URL 内用户名/密码、查询串、重定向凭据传递。服务端 HTTP 客户端不自动采用环境代理；本轮没有代理配置。

百炼跨区域 Key 与 Base URL 不通用，推荐使用[官方所列业务空间域名](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)，例如 `https://你的WorkspaceId.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`。使用普通后端模型 API 对应的按量 Key/端点，不将编程产品专属套餐 Key 当通用凭据。智谱也应区分标准 API 与编码套餐；本轮预设为标准 API。

非默认数据目录可用 `--data-dir /绝对路径`（init、model-key、serve 一致传递），或者服务端 `TAVO_DATA_DIR`。首次兼容导入文件可用 `TAVO_LLM_CONFIG_FILE=/绝对路径/models.json`；`.env.example` 不自动加载，设置环境变量的终端必须是启动 Python 服务的终端。客户端向当前连接的后端提交；CLI 必须使用该后端相同的数据目录和密钥环。

## 常见问题

- 设置仍提示去服务端编辑 JSON：客户端是旧版本，关闭后重新 `pnpm dev`；同时完成 0005 初始化并重启后端。
- 厂商加载失败：检查后端是否已升级并连接。首次部署需初始化；客户端不通过普通设置接口传 Key。
- 只有 Mock：在设置中添加模型并加密保存 Key；模型未就绪时聊天选项不可用。
- 模型 ID 不合法：使用控制台提供的 API ID，不能填套餐名或 `SET_MODEL_ID`；Gemini 不带 `models/` 路径前缀。
- 保存失败或响应不确定：先刷新列表，存在的配置可直接选中继续录入 Key，不盲目重复创建。
- 凭据版本冲突/提交失效：刷新状态，重新输入并保存；不复用旧信封、不回填旧 Key。
- 已有 JSON 改动不生效：0004 之后只做一次兼容导入，日常添加改用客户端，重启不重新导入。
- 认证/权限失败：核对服务商、Key、地域、计费产品和模型权限；“本地已配置”不表示厂商鉴权成功。
- 限流/配额/超时：核对账户额度、网络、模型和预算，显式重试可能再次计费。

本地无需真实 Key 的验证：`node scripts/model-smoke.mjs` 使用隔离数据和回环模型替身。完整命令、文件变化和限制见 [CLIENT_MODEL_SETUP_REPORT.md](CLIENT_MODEL_SETUP_REPORT.md)。


## 持久 Session 与工具模式（2026-11-16）

连接后的 Mock/真实模型对话均由后端持久 Session 管理，重启可恢复。在设置中切换模型或命令权限会开始新会话，历史不自动发给另一模型；旧运行继续，明确停止才取消。三档命令权限共同控制宿主和沙箱命令，其他合法工具免额外审批；旧会话保留原策略。实际模型需支持对应工具协议，不会隐式换厂商或降级。

工具闭环最多4轮/8次工具，Context UTF-8预算64 KiB；原始隐藏思考/签名加密留在后端。付费请求响应未知时标记中断，不自动重发；显式重试可能再次计费。原无状态 `/chat/completions` 接口保留。


AgentPool/独立模型自审查/可选隔离Bash已加入。Bash默认关闭，准备Docker镜像并用 `TAVO_BASH_ENABLED=1` 启动宿主Python后端，再在客户端显式启用。操作、模式、额外模型费用、工作卷备份与限制见 [Bash指南](BASH_TOOL.md)；实际验收见 [本轮报告](AGENT_POOL_BASH_REPORT.md)。
