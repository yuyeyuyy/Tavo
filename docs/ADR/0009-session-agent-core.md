# ADR 0009：用持久 Session 替换账户事件 Agent

日期：2026-11-16。状态：用户确认后实施。

用户要求移除账户/余额功能、完善通用 Agent，并明确选择真实模型工具闭环与保留旧数据。旧事件分析依赖 accountId/eventId；窗口内聊天与其没有统一会话/工具记录。

比较“在无状态聊天内加循环”和“持久 Session/消息/Run + 既有 LangGraph/Worker”：选后者，增加七张表而不引入第二套框架。复用认证、模型配置、JWE、SecretStore、SQLite 事务及 lease/fence。账户运行代码和 API 能力退役，旧表及已执行迁移保持，旧 API 明确返回 410。保留无状态文本 API 兼容。

比较“模型决定工具安全”和“确定性规则 + 可选人工审核”：选后者。只注册时间与 Decimal 计算。模型只能提议；未知/非法调用不能由 prompt 或人工审批扩大权限。Decision 和 ToolResult 是独立可审计记录。

Context 按完整成功轮次选取/裁剪并冻结；协议私有续接状态以现有密钥环加密，绑定模型/会话/运行/轮次。持久化 inflight 防止崩溃后盲目重复付费请求，未知响应必须显式重试。只读工具有限恢复，已保存结果复用。代价是明确的中断体验、非流式轮询，以及 SQLite 单机规模约束。

没有引入长期记忆/RAG、多 Agent、checkpoint 插件、任意 HTTP/shell 或真实账户。会话现在有意存盘，备份需同时保护数据和密钥。

协议依据：[OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling)、[OpenAI reasoning](https://developers.openai.com/api/docs/guides/reasoning)、[Claude tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)、[Gemini generateContent](https://aistudio.google.com/docs/function-calling?generate-content=true&hl=en)、[DeepSeek tool calls](https://api-docs.deepseek.com/guides/tool_calls/)、[DeepSeek thinking](https://api-docs.deepseek.com/guides/thinking_mode/)、[Qwen function calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)、[Kimi thinking](https://platform.kimi.com/docs/guide/use-thinking-models)、[OpenRouter reasoning](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)、[xAI text practices](https://docs.x.ai/developers/model-capabilities/text/best-practices)。这里的兼容推断由四类协议替身验证，真实厂商权限/模型能力仍需用户独立测试。
