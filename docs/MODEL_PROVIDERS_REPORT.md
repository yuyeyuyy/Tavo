# 多厂商文本聊天接入报告

> 本报告是先前模型协议/隐藏 CLI 的验收基线。同日已按用户确认增加客户端手动输入和加密提交，当前边界与结果见 SECRET_SUBMISSION_REPORT.md；不要把此处“仅终端输入”当作当前限制。

日期：2026-11-16（Asia/Taipei）。用户确认 MODEL_PROVIDERS_PLAN.md 后实施。当前本地分支仍为 `feat/agent-foundations`；上一轮 Agent/客户端改动与开发者新增学习注释保留。本轮没有提交、推送、发布、读取真实 Key 或对正常用户数据目录执行迁移。

## 交付行为

已实现 OpenAI、Claude、Gemini、DeepSeek、百炼/千问、Kimi、智谱、OpenRouter、xAI 九家服务的文本协议预设和模型配置模板。四类薄适配器转换消息、认证、输出预算、公开文本、finish/usage；不是九个完整 SDK，也不声称所有高级/多模态模型兼容。官方依据与适配选择见设计和 MODEL_SETUP.md 的逐厂商链接。

用户在 Python 服务端终端通过 model-key 隐藏录入 Key，沿用 SecretStore / 外部 Fernet/MultiFernet 密钥环加密；非秘密 profile JSON 管理模型 ID/启用/目标地址/预算。客户端只查询目录并选择 profile，通过认证 Chat API 请求完整文本。显式 Mock 仍可离线运行；真实模型错误不会伪装成 Mock 成功，不自动重试或跨厂商转发。

本机填写与运行步骤见 [MODEL_SETUP.md](MODEL_SETUP.md)，模板为 `backend/models.example.json`。没有替用户启用真实服务或填写任何 Key。

## 文件范围和理由

| 文件范围 | 本轮变化 |
| --- | --- |
| `backend/app/llm/contracts.py`、`config.py` | 有界消息/目录/回复 DTO、九家协议与地址、启动配置校验；禁止请求方提交 Key/URL/system/tools |
| `backend/app/llm/keys.py` | 模型作用域的 SecretStore 访问，引用绑定 profile/厂商/目标地址，复用轮换和加密，不放宽 for_connector |
| `backend/app/llm/adapter.py` | 四类异步 HTTP 协议、256 KiB 上游限制、公开文本提取、隐藏推理排除、固定错误与 Key 回显拒绝；无自动重试/重定向/环境代理 |
| `backend/app/services/chat.py`、`api/chat.py` | 认证目录/聊天端点；每 API 进程 2 并发、总预算/断开监视和取消清理；不是持久 Agent 队列 |
| `core/config.py`、`services/application.py`、`main.py` | 服务端配置路径、聊天装配/关闭和 Router 注册；保留开发者注释 |
| `backend/app/cli.py` | model-key / --profile / --delete；无回显输入，拒绝参数或管道形式的 Key，不允许 getpass 明文回退 |
| `backend/models.example.json`、`.env.example` | 九家全部默认禁用的非秘密模板、配置路径说明，不含真实 Key/价格或自动启用 |
| `backend/pyproject.toml`、`uv.lock` | httpx 0.28.1 从开发提升为运行依赖，其他版本/依赖图不升级 |
| 客户端 `api/chat.ts`、`api/client.ts`、`api/generated.ts`、共享错误码 | Zod、OpenAPI 生成类型、仅聊天的 65 秒等待和受控错误；其他请求保持原 5 秒 |
| `chat/http.ts`、`types.ts`、`store.ts`、`Chat.tsx`、`connection.tsx`、`styles.css` | 模型目录/选择、HTTPTransport、真实模式/截断标记、切换清空、取消与历史预算；不新增业务页面或原生 IPC |
| `backend/openapi.json` | 经 api:generate 生成，未手改生成类型 |
| `backend/tests/test_models.py`、`tests/model-client.test.ts`、E2E | 九家协议夹具、Key/预算/失败/取消、真实终端和桌面链路 |
| `scripts/model-harness.*`、`model-smoke.mjs` | 合成凭据、独立 Python 进程与真实回环 HTTP 替身，避免自动消耗真实 Key |
| README、后端说明、API/架构/设计/ADR/学习/评测/开发记录与 AGENTS | 维护现状、配置教程、真实验证边界和练习 |

本轮启动基线 lint 发现开发者学习注释旁的导入/空行问题。对受影响配置/装配和另外几个既有文件仅执行 Ruff 导入/空行格式整理，保留注释和业务行为；不借此重写连接器、同步仓库或旧 API。原学习注释文件在临时源码副本保留。本轮未改现有迁移、旧 SQL、账户/同步业务或 Agent 规则。

## 安全和状态约定

- 配置由后端启动时读取，缺文件等于无真实模型。默认模板各家 `enabled=false`，启用时必须替换 SET_MODEL_ID；目录最多 32 条。模型权限、地区与计费产品由用户控制，不自动扫描厂商模型/余额。
- Key 加密在原 secrets 表；模型引用含 profile ID 及厂商/目标地址摘要，改目标需要重新录入。目录仅暴露 id/label/provider/model/ready，CLI 不打印秘密。外部密钥环仍需独立保护/备份，不存在明文降级或 HTTP Key 入口。
- API 请求只接受交替 user/assistant 文本，首尾 user；用户最多 4000 字符、助手历史最多 16000，最多 20 条 / 24 KiB JSON。后者允许回传本轮真实模型的较长结果；不把助手回传范围等同于用户单条输入范围。客户端稍保守预检，服务端重新校验；不静默丢历史。
- 只发送窗口内已完成会话和固定 system 提示，不注入账户、AgentMemory、SecretStore 或工具。取消/失败后新用户消息不重发未回答的旧用户轮次；重试替换助手占位，保留已完成轮次。
- 输出默认 2048 tokens、profile 可在 128–8192 内调整；token 含义按厂商协议，不能视为统一费用上限。最多返回 16000 字符，原始响应上限 256 KiB，截断明确标记，usage 未提供就是 null。隐藏推理/工具计划不进入返回或普通日志。
- 模型协议使用厂商认证头，Key 不在消息/查询串。禁跟随重定向，远程必须 HTTPS，HTTP 仅回环测试。OpenAI/xAI 明确 store=false，不等于承诺厂商所有数据零保留。HTTP 客户端不自动采用环境代理。
- 每 API 实例最多 2 个请求，连接预算 10 秒、处理等待预算 60 秒；取消/超时尝试终止上游并等待清理后释放名额，不承诺硬实时清理或厂商不计费。未做跨 API 进程的付费队列/全局限流。
- 错误固定映射，厂商 400/422 不猜测成具体余额或额度；429 显示限流/配额提示。网络/权限/不存在模型/无公开文本/非法响应不回显原文。默认无重试/换厂商，用户显式重试可产生新费用。
- 模型切换销毁旧会话并中止请求；requestId 继续阻挡晚到回复。客户端初始为 Mock，不因后台配置默认值而自动发起真实请求。窗口重启仍清空会话，模型 Key 和后端业务数据保持持久。

## 实际执行和结果

macOS arm64 / Python 3.12.14 / Node 24.15.0 / pnpm 11.1.3 / Electron 44.11.1。浏览器文档测试使用已安装 Chrome。所有自动模型调用均为合成凭据、本地替身或注入式 HTTP Mock。

| 实际命令 | 结果与证据范围 |
| --- | --- |
| `uv lock --offline`（backend） | 使用已有缓存解析 60 包，只有 httpx 依赖分组变化，未升级版本 |
| `uv sync --frozen --offline`（backend） | 检查 56 个环境包完成，无下载 |
| `pnpm api:generate` | 生成 OpenAPI / TS 契约成功 |
| `pnpm lint` | ESLint / Ruff / Python 格式全部通过 |
| `pnpm typecheck` | 严格 TypeScript 检查通过 |
| `pnpm test` | 客户端 27 项 / Python 107 项通过，上一轮为 23 / 62 |
| `pnpm build` | 桌面三部分构建通过，最终 package:dir 也重建 |
| `node scripts/model-smoke.mjs` | 真实回环 HTTP + 独立后端：加密 Key、认证目录/聊天、错误、断开、手动重试和重启读取通过 |
| `node scripts/backend-smoke.mjs` | 原同步、SSE、偏好/上下文/只读工具和独立进程重启通过 |
| `pnpm test:dev` | 真实开发窗口聊天、设置、新对话与目录组件通过 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm test:e2e` | 4 场景通过（47.7 秒），含服务端模型的实际 HTTP 桌面链路 |
| `pnpm package:dir` | 最终 macOS ARM64 未签名测试包构建成功，不发布 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` | 直接启动最终 `.app`，4 场景通过（47.9 秒） |
| `git diff --check` | 通过；已执行 SQL 迁移未改 |

新增 45 个 Python 测试包含：九家 profile 请求/认证/输出映射、HTTP 错误不重试、不跟重定向、Key/原始错误/隐藏推理不泄漏、无效/超大响应、四类 usage 缺失与截断、未知工具结果拒绝、密文/轮换/目标绑定/删除、缺失或禁用 Key/认证拒绝、客户端参数越权、JSON/消息预算、超时/断开/并发名额清理、CLI 注入测试及真实 PTY 无回显测试。

客户端新增 4 项验证只发 profile 与消息/信号、不包含 Key/认证头、DTO 越界与 profile 不匹配、停止后新轮次排除旧未回答消息、输出截断和对话预算。原 23 项继续通过。

Electron 新场景用本地模型替身验证：未配置 Key 的模型不可选、真实 HTTP 回复、限流明确提示/手动重试、停止、切换清空和无凭据目录。原三个场景仍覆盖聊天键盘/composition/窄窗口、设置/离线/重连/焦点，以及旧后端历史和桌面退出后的任务。模型页截图 `test-results/chat-packaged-model.png` 已目视复核，产物忽略 Git。

中途离线 uv lock 使用空的临时缓存未能解析，随后使用已存在的标准缓存成功，无联网安装。Ruff 最初检测到基线注释空行/导入问题，按前述最小格式整理后通过；没有将基线失败当新增业务失败。构建仍有第三方 PURE 注释、默认图标/跳过签名和 Playwright 颜色环境提示，最终退出码均为 0。

## 未验证与边界

没有任何真实模型 Key，因此没有九家实际厂商成功率、费用、时延或回答质量的测量；协议夹具与本地 HTTP 通过不等于外部服务可用。真实模型 ID/权限/地区可能变化，须由用户按 MODEL_SETUP.md 在本机逐家测试。

没有 token 流式、多模态、模型工具调用/联网、聊天长期记忆注入、会话数据库、持久 checkpoint、自动 fallback 或多 Agent。本轮没有重建 Docker/运行新 Linux 容器；部署新代码必须重建安装包含 httpx 运行依赖，旧容器结果不能当作本次模型验证。没有 Windows/Intel macOS/其他平台/完整无障碍/规模负载或安全渗透验收。

本机 Key 录入可与已有后端数据共存，表结构仍为 Alembic 0002；没有更改正常用户数据库或密钥文件。未签名测试包不是签名、公证或生产分发。没有提交、推送、合并或发布。

学习练习：用自己的一个 profile 发送一条公开测试消息，先预测请求里哪些字段在客户端、哪些由服务端补充；再停止/换模型，解释为什么请求 URL、Key 和系统身份都不能由消息内容决定。不要在练习记录中写 Key。
