# 客户端自助模型配置报告

日期：2026-11-16（Asia/Taipei）。用户确认 CLIENT_MODEL_SETUP_PLAN.md 后实施。当前仍在 `feat/agent-foundations`，保留此前未提交工作和学习注释；未自动提交/推送/发布，未对正常用户数据目录运行迁移或改模型配置。

## 可观察结果

客户端设置中直接选择厂商、填模型 ID/可选名称，添加后自动选中新配置、收起添加区并聚焦 Key 密码框。空目录也显示添加表单，不再要求去服务端编辑 JSON。保存 Key 仍经已有 Web Crypto/JWE→认证 API→SecretStore/Fernet，提交/关闭清空，不回填旧 Key。

配置保存后立即可申请凭据挑战，Key 就绪后用户在聊天目录选择并发送。添加配置和保存 Key 都不联系厂商、不自动测试付费模型。当前模型 ID 手动填写控制台 API 标识，不提供未经验证的最新模型下拉列表。

本次升级需先停旧进程、备份数据库/独立密钥环，执行 `pnpm backend:init` 至 `0004_client_model_profiles`，然后重启后端/客户端一次。此后添加模型或 Key 不用重启；具体命令和操作见 [MODEL_SETUP.md](MODEL_SETUP.md)。本轮没有代替用户迁移正在使用的数据。

## 文件变化与设计理由

| 文件 | 变化与原因 |
| --- | --- |
| `renderer/security/AddModelForm.tsx` | 公开配置表单、九厂商选择、模型 ID/可选名称、有界输入/取消/禁止重复点击，无 Key 字段 |
| `renderer/security/ModelKeySettings.tsx` | 空目录入口、添加后目录刷新/自动选中/收起、组合原 SecretInput |
| `renderer/security/SecretInput.tsx` | 可选聚焦/滚动到新建模型的 Key 输入，保留清理与加密行为 |
| `renderer/api/model-profiles.ts` / `api/client.ts` | 严格 Zod 与公开 API 方法；普通配置请求拒绝 apiKey/baseUrl 等字段 |
| `renderer/connection.tsx` / `styles.css` | 更新操作说明和小表单布局，仍为单页对话/滚动设置弹窗 |
| `backend/app/llm/repository.py` | 持久目录、首次 JSON 事务导入/完成标记、32 项并发写限制 |
| `backend/app/services/model_profiles.py` | 公开目录、服务端 ID/预设/校验，不接受前端任意域名或秘密引用 |
| `backend/app/llm/config.py` / `contracts.py` | 九厂商公开名称、DTO 与纯配置测试注入入口，保留原协议校验 |
| `backend/app/api/model_profiles.py` / `api/dependencies.py` | 认证/no-store 的厂商/配置列表/创建，复用会话依赖并在写锁后重查 |
| `backend/app/api/credentials.py` | 共享 SessionToken 移到通用依赖模块，不改变加密接口 |
| `services/chat.py` / `services/application.py` / `main.py` / `cli.py` | 运行目录改为 DB 权威，Chat/handler/CLI 共用；init 兼容导入，Key CLI 即时可见 |
| `models/entities.py` / `migrations/versions/0004_client_model_profiles.py` | 新 model_profiles/model_catalog_state，不改既有迁移/密文/历史 SQL |
| `core/errors.py` / `packages/shared/src/contracts.ts` | MODEL_PROFILE_LIMIT 与当前配置错误提示；固定公开消息 |
| `backend/openapi.json` / `renderer/api/generated.ts` | Pydantic 导出/生成，不手改生成类型 |
| `backend/tests/test_model_profiles.py` | 21 项新测试：API 权限/校验/实时可用/并发容量/授权复查/导入/Key 保留/升级 |
| `backend/tests/test_models.py` / `test_agent_foundations.py` | CLI 夹具改为持久目录；0001 迁移夹具只 seed 原 schema，不在旧表上运行当前 Services |
| `tests/model-profiles.test.ts` / `tests/e2e/desktop.spec.ts` | 三项客户端 API 边界与空目录真实桌面流程，既有 Key/聊天回归加入输入与提交状态断言 |
| `scripts/backend-harness.*` / `model-harness.*` / `dev-smoke.mjs` | 测试初始化前准备旧 JSON；空目录测试仅在测试 Python 入口注入回环预设，生产无后门开关 |
| `.env.example`、README、架构/API/ADR 0008/学习/AGENTS/操作/评测 | 明确 JSON 一次导入、数据库权威与本次升级步骤 |

方案比较见 ADR 0008：原子写 JSON/热加载较少表结构，但需要额外文件并发和缓存刷新；SQLite 目录复用已有事务，更适合来自客户端的持久配置。没有增加依赖或改 Electron Main/Preload 原生方法与隔离/CSP。

## 数据、接口和兼容

- `GET /model-providers` 固定九家 `{id,label,modelHint}`；`GET /model-profiles` 返回公开项目（含导入的禁用项）；`POST /model-profiles` 只接受 provider/model/可选 label。
- 服务器生成稳定 ID，默认启用、默认预算 2048、默认可信厂商端点；拒绝 id/enabled/baseUrl/maxOutputTokens/apiKey/headers/system/secretRef。总数 32 含禁用项，写锁后检查授权和容量；超限 409 MODEL_PROFILE_LIMIT。
- 新建配置持久在 model_profiles，排序/创建时间为公开元数据。model_catalog_state 的单例存在表示导入完成，并保留旧 defaultProfileId；配置和标记同事务，包括空目录。
- 旧 JSON 保留 id/provider/model/label/enabled/baseUrl/预算/default，不改原文件。先校验完整 JSON，失败回滚；成功后不再读取它覆盖数据库。原秘密引用依然由 ID/厂商/端点派生，所以已有 Key/密文/内容 revision 保留。
- ChatService/ModelKeyHandler/model-key CLI 共用 ModelProfileService，读取 DB 不依赖进程可写缓存。单次聊天捕获已验证 profile；当前不开放配置修改/删除，因此没有运行中目标修改语义。
- 新建与 Key 保存分两步，不宣称跨 HTTP 原子性。未录入 Key 的配置保留并显示未就绪，可选中后继续录入；不自动重试，响应不确定先刷新目录，避免重复创建。
- 客户端决定选择，服务端决定可信协议/目标/校验和加密保管。普通模型信息可以进入 React/Query，Key 仍仅专用密码 DOM/短暂缓冲，不进入普通 JSON 配置或聊天。

## 实际验证

环境：本机 macOS ARM64、Python 3.12.14、Node 24.15、pnpm 11.1.3。自动请求全部使用合成凭据/本地 HTTP 替身，没有真实厂商调用。

| 命令 | 结果 |
| --- | --- |
| `pnpm api:generate` | 成功，OpenAPI/TS 与 Zod 同步 |
| `pnpm lint` / `pnpm typecheck` | 通过 |
| `pnpm test` | 客户端 37 / Python 156 项通过（新增 3 / 21） |
| `node scripts/backend-smoke.mjs` | 独立进程认证/REST/持久同步/SSE/记忆/上下文/工具/Agent/重启通过 |
| `node scripts/model-smoke.mjs` | 持久目录/合成 Key/回环 HTTP 模型/错误/断开/重试/重启通过 |
| `node scripts/dev-smoke.mjs` | 实际 pnpm dev 从空配置添加模型、加密 Key/清空、HTTP 聊天/新对话/设置通过 |
| `pnpm build` | 已执行通过，含最终表单版本 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm exec playwright test --grep '客户端输入 Key' --repeat-each=3` | 既有 Key 流程连续三次通过（18.6 秒） |
| `TAVO_BROWSER_CHANNEL=chrome pnpm test:e2e` | 最终构建版 6 场景通过；该命令退出 0 后继续执行 package:dir |
| `pnpm package:dir` | 构建成功，macOS ARM64 未签名 .app，未发布 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` | 去掉临时诊断后的完整最终包内 6 场景通过（约 1.1 分钟） |
| `git diff --check` | 通过，0001 与旧 SQL/Preload 保持原样 |

新增 Python 用例验证从空目录创建→立即挑战/加密保存→聊天，普通 DTO/响应/日志不含合成秘密；13 类非法或扩权输入、认证/来源、并发最后一个名额、锁等待后会话失效。兼容测试验证旧自定义地址/默认项/禁用项/原 Key 和密文不变、导入失败无状态、后续坏 JSON 不覆盖新配置，以及真实 0003→0004 / Alembic metadata 一致。

新增桌面场景从空数据库开始，真实 Electron 中选 DeepSeek/填模型/添加、自动定位 Key、只提交加密信封、保存时零厂商请求、无需重启随即聊天并重启验证配置/Key 保留。测试 harness 在自己启动的 Python 入口替换 DeepSeek 预设为回环替身，生产客户端/CLI 没有任意地址注入接口。原五场景继续回归。

迭代中旧 0001 夹具因调用当前 Services 缺新表失败，已改为仅 seed 原 schema 后再升级；两个新测试的替身缺 assistant role/cookie 未清理问题已修正。完整桌面迭代曾两次出现 Key 提交 422，另一轮旧生命周期场景预期问候却得到通用 Mock；已补输入值/用户消息与提交响应断言。附加诊断中隔离后端 Node Web Crypto 连续 24 次、打包客户端直接操作连续 8 次、单独 Key 场景连续 6 次以及“先添加再录入”的 3 轮顺序场景均通过，未稳定复现原因。临时诊断只记校验位置/合成值长度与状态，不记原文；最终已移除。最终完整构建/包内结果以上表为准，不能把初次失败说成一次全通过，也不能声称已确定并修复这项偶发原因。

## 未实现与风险

- 当前只能新增、选择并换 Key；无配置编辑/删除、地区/网关切换、预算修改、模型发现或自动测试。使用厂商默认端点，旧自定义地址仅导入兼容保留；32 项包含旧禁用模板。
- 自动桌面迭代出现过上述偶发 422/输入不一致，最终全量与定向复验通过，但原因未确认；仍需用户按实际使用环境观察，不能将复验通过当作已找到根因。不要为规避错误保留或盲重传原始 Key。
- 0004 首次导入后改 JSON 不生效，不是文件热加载。要迁移旧 JSON 应在首次 init 前准备；本次升级必须停止旧版本进程并备份，不能继续用旧 CLI 修改 JSON 期待新目录更新。
- 新建请求取消/响应丢失可能已经保存，先刷新；Key 保存继续有 CAS/墓碑，网络不保证 Exactly-once。普通配置与密文两个请求不共同回滚。
- 模型真实账户/权限/额度/费用/质量未验证，未重建 Docker/重跑 Linux、Windows、Intel、干净安装、签名公证或大规模性能/完整安全审计。未签名 .app 不等于正式分发。
- 远程 HTTPS、单所有者边界、JS 内存尽力清理及单进程 JWE 挑战限制沿用 SECRET_SUBMISSION_REPORT.md；不新增已保存秘密读取、真实账户、模型工具、流式/持久聊天/RAG/多 Agent。

学习练习见 LEARNING_NOTES.md：区分“选择发生在客户端”和“权威配置保存于服务端”，跟踪普通配置与秘密的两条数据流。
