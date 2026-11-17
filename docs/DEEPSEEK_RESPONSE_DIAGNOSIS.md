# DeepSeek 空正文与 MODEL_REJECTED 诊断

> 后续：用户已确认重试方案并实施。历史诊断保留；当前行为、两次真实合成验收与60秒限制见 [MODEL_RETRY_OUTPUT_REPORT.md](MODEL_RETRY_OUTPUT_REPORT.md)。

日期：2026-11-17。范围：用户要求查清当前错误原因，未修改生产代码、模型配置、数据库或权限。已确认存在可复现的模型适配与错误分类缺陷。

## 本地证据

最近失败的 Run 使用 deepseek / deepseek-flash，冻结输出上限 2048。第一轮 model 阶段失败，工具调用次数 0，错误 MODEL_REJECTED；前一个相同模型 Run 成功。未读取或输出对话正文、凭据、私有思考或会话标识。

失败的上游响应在解析抛错后未保存，模型轮次被标记 unknown，只有通用错误码。因此无法从原记录追溯那一次的 finish_reason；不能声称已恢复原始响应。

## 真实官方 API 对照

使用同一已配置模型和官方端点、固定合成编程问题，无历史对话、无工具声明/执行、无自动重试。只读取模型配置和加密凭据，SQLite mode=ro + query_only；Key 只用于既有官方认证头，不输出。调用最多两次，每次 max_tokens=2048。

| 指标 | 当前请求参数 | 仅增加 thinking.type=disabled |
| --- | --- | --- |
| HTTP 状态 | 200 | 200 |
| finish_reason | length | length |
| completion_tokens | 2048 | 2048 |
| reasoning_tokens | 2048 | 上游未提供该值 |
| 是否有思考内容 | 是 | 否 |
| 是否有公开正文 | 否 | 是 |
| 是否有明确 refusal | 否 | 否 |
| tool_calls 数量 | 0 | 0 |
| 当前 Tavo parse_turn 结果 | MODEL_REJECTED | ACCEPTED（截断文本） |

这证明当前配置能把全部生成预算用在思考上，尚无正文即达到上限；Tavo 随后把空正文误归类为模型拒绝。控制组只改变思考开关，2048 上限不变，即能正常取得正文。这是对同一适配缺陷的真实复现，不把合成输入说成用户历史失败响应。

账户可用额度与 max_tokens 是不同限制：前者决定账户是否有消费额度；后者限制本次请求生成多少 token。两次 HTTP 200 已完成实际模型调用，本次复现没有额度不足或认证失败证据。

## 代码根因

1. backend/app/llm/config.py：ModelProfile.max_output_tokens 默认 2048；客户端创建配置沿用默认值。
2. backend/app/llm/turns.py 的 turn_request，以及 adapter.py 的 request_for：传入 max_tokens，但未为 DeepSeek 显式配置 thinking 或 reasoning_effort。
3. DeepSeek 当前默认开启 thinking，默认 effort=high；思考与公开正文分别返回 reasoning_content/content。官方文档：[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。
4. turns.py 的 parse_turn 已识别 finish_reason=length 为截断，但在公开正文和工具调用都为空时，仍直接抛 MODEL_REJECTED，丢失预算耗尽这个原因。adapter.py 的旧文本接口也把空正文归入相同错误。
5. Assistant 失败收尾只保存通用 error_code，失败响应的结束原因、用量等安全元数据没有保存。当前提示因此把预算耗尽、空回复和明确拒绝混在一起。

正常无正文的完整工具调用可被 parse_turn 接受，已用合成工具响应验证；本次缺陷不是所有“无文本”都被一概拒绝。

## 修复取舍（尚未实施）

| 方案 | 行为与影响 |
| --- | --- |
| A：明确使用非思考模式 | 对确认支持该开关的 DeepSeek 模型显式关闭 thinking，保留 2048；较快且不需扩展数据库预算，但改变模型推理行为，长正文仍可能截断。对照只验证其能绕开此类空正文，未将其偷偷应用到正常配置。 |
| B：明确配置思考及独立生成预算 | 保留推理，允许管理员配置足够且有界的生成预算；同时细分输出耗尽、空回复、拒绝，记录安全结束原因和计数。推荐用于 Agent 的后续修复，需要明确配置/API/数据库预算边界。现有 SQLite CHECK 和 Pydantic 上限均为 8192，不能只改一个默认值就声称支持更大预算。 |

两者都应修正错误分类，不应把 reasoning_content 当正文，也不应自动重试付费请求或悄悄换模型/思考模式。必要回归：思考耗尽且无正文、有正文但截断、正常工具调用、截断工具参数、真实拒绝/空回复、非 DeepSeek 参数不变、Key/思考原文不进入公开错误/日志。

小练习：账户余额足够时，将单次 max_tokens 设为 2048，为什么仍能出现 length？再解释为什么“只显示 reasoning_content”不能修复没有最终答案的问题。

## 生产网关与现有测试

用户明确拒绝原对话外发，并要求最多三次自动重试和自查链路后，额外使用一次固定合成输入，调用实际 ModelGateway.turn。底层 AsyncHTTPTransport 与生产配置保持 trust_env=false、retries=0，仅观察受限响应元数据；没有读取或发送会话表。结果仍为官方HTTP200、length、completion_tokens=2048、reasoning_tokens=2048、无正文/拒绝/工具，ModelGateway 返回 MODEL_REJECTED。定位链路为请求参数 → 官方生成预算耗尽 → parse_turn 错误分类，未触发工具执行。

本轮共三次真实官方请求，全部是合成输入；前两次为模式对照，后一次为实际生产网关复验。现有 `.venv/bin/python -m pytest -q tests/test_models.py tests/test_agent_protocols.py`：69/69（10.52秒）。这些既有用例通过并不代表空正文预算耗尽已正确处理，缺失场景将在确认后补充。

用户要求的完整重试方案见 [MODEL_RETRY_OUTPUT_PLAN.md](MODEL_RETRY_OUTPUT_PLAN.md)，尚未修改运行代码。

## 安全审批与实际边界

自动审批拒绝了使用原失败对话上下文再次访问官方 API：理由是可能含私人内容，排查授权未明确包括该上下文再次外发。此后采用获准的固定合成内容对照，不读取/发送任何会话表；原上下文诊断未执行。合成验证已足够确认适配缺陷，不再需要原对话外发。

实际诊断命令：在 backend 下使用 .venv/bin/python 执行临时 synthetic-diagnose 脚本；只输出上述布尔标志/结束原因/用量，退出码 0。未发送用户原对话，未执行模型提议的命令，未更新正常用户配置；本报告是诊断记录，不是修复完成报告。
