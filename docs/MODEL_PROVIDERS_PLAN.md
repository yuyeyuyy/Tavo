# 多厂商文本聊天接入方案（已确认并实施）

日期：2026-11-16（Asia/Taipei）。用户已确认“按该方案开始（推荐）”；实现与验证见 MODEL_PROVIDERS_REPORT.md，本机使用见 MODEL_SETUP.md。下文保留设计取舍，实际命令以操作指南为准。

## 首轮最小结果

本机终端录入模型 Key → Python SecretStore 加密保存 → 服务端配置启用模型 profile → 客户端选择该 profile 并发送聊天 → Python 适配厂商协议 → 返回真实文本。保留显式 Mock 选项；真实调用失败时显示错误，不自动伪装成 Mock 成功。

本次只接纯文本、完整回复后返回，不做 token 流式、多模态、工具调用、RAG、自动记忆写入、多 Agent 或持久聊天会话。窗口重启仍清空聊天；已有 Agent 事件分析/工具链与业务数据保持独立。

## 两种适配方案

| 方案 | 取舍 |
| --- | --- |
| 各厂商 SDK / 第三方统一网关依赖 | 能快速覆盖更多高级能力，但引入多套依赖、默认重试/日志/参数行为，版本和秘密边界更复杂 |
| 项目内薄协议适配器（推荐） | 一个异步 HTTP 客户端，按协议转换请求/结果；当前文本范围明确，便于注入测试，日后再按需求采用官方 SDK |

推荐后者：不是写九套完整客户端，而是四类适配器 + 厂商预设。将当前已锁定的 `httpx==0.28.1` 从仅开发依赖提升为运行依赖，维护 pyproject/uv.lock；不额外安装九个 SDK。

## 首批厂商与协议

以下是 2026-11-16 核对的官方协议依据，具体模型 ID 由服务端配置，不将“最新版”或全部模型可用性写死在代码。

| 服务商 | 拟采用协议 | 官方依据 |
| --- | --- | --- |
| OpenAI | Responses | [Create response](https://developers.openai.com/api/reference/resources/responses/methods/create)、[迁移说明](https://developers.openai.com/api/docs/guides/migrate-to-responses) |
| Anthropic / Claude | Messages | [Messages](https://platform.claude.com/docs/en/api/messages)、[认证](https://platform.claude.com/docs/en/manage-claude/authentication) |
| Google Gemini | generateContent | [生成接口](https://aistudio.google.com/api-reference/generate-content) |
| DeepSeek | Chat Completions 兼容协议 | [首次调用](https://api-docs.deepseek.com/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) |
| 阿里云百炼 / 通义千问 | Chat Completions 兼容协议 | [兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)、[地域与 Base URL](https://help.aliyun.com/zh/model-studio/base-url) |
| Moonshot / Kimi | Chat Completions | [Chat API](https://platform.kimi.com/docs/api/chat) |
| 智谱 GLM | Chat Completions | [快速开始与认证](https://docs.bigmodel.cn/cn/api/introduction) |
| OpenRouter（聚合服务） | Chat Completions 兼容协议 | [Quickstart](https://openrouter.ai/docs/quickstart) |
| xAI / Grok | Responses | [Responses](https://docs.x.ai/developers/rest-api-reference/inference/responses) |

相同协议的厂商仍需各自的契约夹具，不能仅改 URL 就宣称所有参数兼容。消息角色、system 字段位置、输出预算参数、内容块、finish/usage 和错误单独映射；默认不发送 temperature、任意 extra_body 或未经核对的推理参数。首轮仅保证配置的文本模型子集，不能宣称所有视觉/推理/工具模型均支持。

OpenAI 使用显式 `store=false`，不建立厂商托管会话或 previous_response_id 链；这不等于承诺厂商所有日志零保留。xAI 的存储选项按其独立文档处理，不盲传 OpenAI 所有参数。各家的数据处理条款由用户所选账户/区域决定。

百炼按量 API Key 与地域/计费入口必须配套，配置模板说明业务空间域名与现有共享域名；不能把编程产品套餐 Key 当作普通后端模型 Key。智谱普通 API 与编码套餐的 Key/端点也分开。模型列表/权限与实际计费由服务商控制，Tavo 不猜测账户权限或价格。

## Key 与本机配置

两种可行办法：后端进程环境注入，适合部署且不用落盘；复用现有加密 SecretStore，适合用户本机多厂商长期测试。推荐本轮实现后者，避免新增另一套明文配置凭据库。

- 新增服务端 CLI，例如 `uv run python -m app.cli model-key --profile deepseek-local`，通过 `getpass` 无回显录入并加密；对应删除命令单独明确。Key 不作为命令行参数、URL 或 HTTP/Renderer 输入。
- 密文存现有 secrets 表，引用使用模型专属命名空间；使用现有外部私有 Fernet/MultiFernet 密钥环，不增加通用凭据读取 API。模型解密入口只接受已验证的服务端 profile，不放开连接器的 for_connector 权限。
- 提供非秘密 `backend/models.example.json`，本地复制为 Git 忽略的 `backend/.local/models.json`。配置包含 profile id、provider、model、enabled、label、有限预算和可选 Base URL；不接受 apiKey 字段。CLI 的模型凭据引用由 profile id 派生，不允许前端提交 secretRef。
- `TAVO_LLM_CONFIG_FILE` 只指定服务端配置路径；程序启动时读取并校验。更改配置后重启服务。未启用模型时只提供 Mock；已启用但未录入 Key 时显示“未配置”，不发送请求。
- 模板提供九家预设/填写说明；不会自动启用、自动拉全量模型或启动时请求所有厂商。客户端只获取必要的 id/label/provider/model/ready，不返回 Key、密文、密钥引用、文件路径、环境或完整配置。
- 后端地址与模型 Base URL 明确区分。模型 URL 只由服务端管理；必须 HTTPS、无用户信息/查询凭据，禁跟随重定向。HTTP 仅对明确回环测试服务开放。聊天请求不能传 URL/header、Key、system 指令或扩大模型白名单。
- Key 仅在服务端内存解密并用于指定模型 HTTP 头，不进入消息、模型上下文、异常、日志、客户端、测试快照或示例。保留密钥轮换/备份约定，不改既有账户凭据和数据。

CLI 已按该接口实施，操作步骤见 MODEL_SETUP.md；不要把真实 Key 写入仓库或发给助手。

## 接口与调用路径

```text
Chat UI → HTTPChatTransport → FastAPI ChatRouter → ChatService
                                                ↓ profile 白名单/预算
                                         ProviderAdapter → 厂商 HTTPS
                                                ↑
                                         模型专属 SecretStore
```

- `GET /api/v1/chat/models`：受现有认证保护，返回有界 profile 摘要和默认选择；目录不会触发真实模型调用。
- `POST /api/v1/chat/completions`：请求 `{profileId, messages:[{role,content}]}`；仅 user/assistant，最后一条必须 user。system 提示由服务端固定，不允许客户端伪造系统身份。profile 只能来自已启用配置。
- 响应为受限 `{profileId, provider, model, message:{role:'assistant',content}, finishReason, truncated, usage?}`。usage 缺失就是 null，不伪造为 0 或价格；只取最终公开文本，不输出隐藏推理内容、任意原始响应或工具计划。
- Pydantic/OpenAPI 生成 TS，与客户端 Zod 同步；不能手改生成类型。保持现有 Main/Preload 六方法与 Renderer CSP，浏览器只连 Tavo 后端，不直连模型域名。
- 客户端增加模型选择（只列已配置 profiles + Mock）、真实模式标签、HTTPChatTransport，沿用停止/新对话的 requestId 防护。切换模型先开始新对话，避免无提示地把旧厂商会话发送到新厂商。
- 首轮聊天为有界即时请求，不冒充持久 Agent 队列；关闭窗口/停止会中止等待并尝试取消上游请求。取消不承诺退费或厂商已停止生成。持久任务/流式恢复另行设计。

## 预算、失败与测试边界

- 初步最多 20 条消息，单条 4000 字符，输入 JSON 总量 24 KiB，保持既有 HTTP 32 KiB 上限。客户端先提示超限，服务端再次校验；不静默裁掉用户消息。字节预算不是准确 token 计数，厂商上下文不足返回明确错误。
- 初步输出预算 2048 tokens（profile 可在限定范围内调整），公开文本最多 16000 字符，上游原始响应有字节上限；预算耗尽有明确截断状态，不把空结果当成功。
- 聊天请求总预算 60 秒，连接预算 10 秒；客户端仅聊天方法使用相应较长超时，不把全局 5 秒 API 超时统一放大。服务端全局最多 2 个模型请求，取消/失败资源清理后释放名额。
- 缺 Key、无权限、模型不存在、配额/限流、超时、网络、拒答/无文本、非法响应均映射固定可读错误，不回显上游正文/请求头。模型调用默认不自动重试或跨厂商 fallback，避免不透明重复花费/数据转发；用户可显式重试。
- 不声明统一 token 成本、全部厂商可用或严格 Exactly-once。后台和 HTTP 客户端日志不得输出认证头、请求/响应正文。

## 影响范围

新增 `backend/app/llm/`（配置/契约/预设/协议适配器）、ChatService 与 API Router；扩展 SecretStore 的模型作用域和 CLI、Services 生命周期；将 httpx 列入运行依赖。首轮复用现有 secrets 表，不新增业务表或修改已执行迁移。

客户端新增 HTTPChatTransport、模型摘要 Zod/选择状态、真实模式/固定错误，继续使用当前单页布局。不恢复仪表盘，不把聊天绑定已有账户分析图。补 `.env.example` 非秘密路径说明、模板/本机操作文档、ADR、架构/学习/评测；保留工作区用户新增的学习注释及上一轮未提交实现，不做无关格式化或重构。

## 验收计划

1. 四类协议和九家 profile 夹具：URL/认证/消息转换/最终文本/usage/截断，使用注入式 HTTP Mock，不访问真实厂商。
2. 真实本地 HTTP 替身：认证完整链路、非流式请求、错误、超大结果、60 秒预算的受控替身、用户断开/取消、并发名额和资源清理。模拟厂商不等于真 Key 验证。
3. Key CLI 无回显、密文落库/重启读取/删除、密钥轮换兼容；公开目录/响应/日志/错误/快照不含合成秘密标记；非法请求不能传 Key/URL/system/越权 profile。
4. TS 消息与错误、模型切换清空、取消/晚到回复；API 契约生成与 Zod；真实 Electron 连接、选择 profile、发送到本地替身、失败/重试、CSP/隔离、重启不保留聊天。
5. `pnpm api:generate`、lint/typecheck/test/build、后端 smoke、开发/E2E；按最终桌面影响执行测试包。不会把现存用户学习注释导致的原有格式问题混成新功能错误；检查结果如实记录。
6. 提供本机测试步骤：用户录入其中一家 Key/模型后点击发送才访问该厂商；未拿到用户本地验证结果的其余厂商只标为协议/契约验证，不声称九家真实账户均已实测。自动化不批量消耗真实 Key。

## 学习练习

把“同一个 ChatRequest 经 Responses / Messages / generateContent 得到统一 ChatReply”的输入输出画出来；解释为什么 HTTPChatTransport 没有 apiKey/baseUrl 参数，为什么模型请求失败不能自动改用 Mock，及为什么本机测试也应经过服务端。
