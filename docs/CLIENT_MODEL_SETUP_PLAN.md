# 客户端自助模型配置方案（已确认）

日期：2026-11-16（Asia/Taipei）。用户澄清：厂商与模型应由用户在客户端选择，不应要求先在服务端编辑 models.json。此前 SECRET_SUBMISSION 仅覆盖既有 profile 的 Key 输入，未覆盖完整首次配置，本设计补齐该最小流程。用户已回答“按新方案实现（推荐）”，实际文件与验收见 CLIENT_MODEL_SETUP_REPORT.md。

## 用户可见结果

后端连接成功后，即使没有任何模型配置，设置中也始终显示“添加模型”：

1. 下拉选择厂商：OpenAI、Claude、Gemini、DeepSeek、千问、Kimi、智谱、OpenRouter、xAI。
2. 填写模型 ID，可选填写显示名称。模型 ID 使用用户控制台提供的值；首轮不请求厂商模型目录，也不维护未经验证的“最新模型”列表。
3. 点击“添加模型”，服务端校验并保存非秘密配置；立即选中刚创建的配置、显示并聚焦 Key 密码框。
4. 输入 Key，点击“加密保存 Key”，沿用已实现的 JWE 提交与 SecretStore；成功后模型出现在聊天可选目录。
5. 用户主动选模型发送消息时才调用厂商。添加配置/保存 Key 不调用厂商，不自动发送测试消息或清空已有聊天。

整个流程在客户端完成，无需编辑 JSON 或重启 Python。Key 更新仍使用现有模型选择与密码表单，不回显旧 Key。两次保存分别明确显示“模型已添加，待录入 Key”与“Key 已加密保存”，避免把缺失 Key 的配置当作可用模型。

## 两种可行方案与取舍

| 方案 | 优点 | 代价 |
| --- | --- | --- |
| A：API 改写 models.json，再热加载 | 沿用现有配置文件，新增存储较少 | 需处理文件写锁/原子替换/多进程刷新，与 SQLite 凭据事务不共享边界；文件与运行时容易不一致 |
| B：模型配置持久化到 SQLite，JSON 作为一次性兼容导入（推荐） | 与现有 Repository/事务一致，保存后立即可查，CLI/API 重启使用同一权威数据 | 新 Alembic 迁移、兼容导入与调用方适配，需要升级测试 |

推荐 B。用户在客户端作出选择，服务端仍负责校验和持久化，类似普通后台管理页面通过 API 保存设置。Key 继续单独加密，不能把普通 JSON 设置接口变成明文 Key 入口。

## 接口与权限

新增认证、no-store 的模型配置接口（最终 DTO 在实施时与 OpenAPI/Zod 同步）：

- `GET /api/v1/model-providers`：返回固定九家 provider id/显示名称，以及 modelId 的输入说明。只提供受支持厂商，不请求外部服务。
- `GET /api/v1/model-profiles`：返回已配置项目及公开状态；可保留 imported disabled 条目供兼容展示。不返回 Key、密文或秘密引用。
- `POST /api/v1/model-profiles`：只接受 `{provider,model,label?}`，新增一个启用配置，返回服务端生成的 profile id 和公开状态。客户端不能决定存储路径、secretRef、协议、系统提示、headers 或任意 baseUrl。总数沿用最多 32 项；输出预算沿用 2048 默认值。
- 既有 `/chat/models` 与 `/chat/completions`、secret-submissions 接口保持职责，改为解析持久模型目录。创建配置和录入 Key 分两步，不宣称跨请求原子性；客户端禁用重复点击、无自动重试，响应不确定时先刷新目录。

首轮新增配置使用服务端既有可信厂商端点预设。已导入 JSON 的自定义 baseUrl 原样保留验证、绑定和现有 CLI 能力，但不通过新表单任意修改。需要地域/自定义网关选择时另外补受限设计；不把本次“客户端选择”扩展成任意 URL 转发或全部配置管理权限。

本轮不增加配置编辑/删除、实时模型发现、模型连通性自动测试或完整 CRUD。已有项目可选、可换 Key，新模型可直接新增；后续改名/禁用/删除需单独限定凭据与运行中聊天的语义。

## 持久化与兼容

- 新增 Alembic `0004_client_model_profiles`，不改 0001/0002/0003 或旧 SQL。模型配置表存公开 profile 字段；单例目录元数据记录是否完成首次导入及原默认 profile。
- 首次初始化在写事务中校验并导入现有 models.json：保留 id/provider/model/enabled/baseUrl/预算/label/default，对应 SecretStore 引用不变，既有 Key 不需要重新输入。缺失文件导入为空目录也记录已初始化。
- 导入状态与配置同事务，崩溃/重启不产生半导入；已有初始化标记后不再用 JSON 覆盖客户端新增项。原文件不删除、不改写；文档明确其变为兼容导入源。
- ChatService、ModelKeyHandler 和 model-key CLI 统一从同一个持久目录解析 profile，不保留只在某个 API 进程生效的缓存副本。一次聊天捕获一个已验证的 profile；当前不开放修改/删除，避免运行中目标变化。
- 新配置由服务器生成稳定 ID，凭据引用仍绑定该 ID/厂商/端点；录入沿用 revision CAS/删除墓碑/会话绑定挑战。模型配置创建不接触任何原始 Key。
- 新库也能直接通过客户端开始；既有默认九项禁用模板仍保留为导入数据，不强行启用占位模型，也不联系真实厂商。
- 正常用户数据库升级前备份数据库与密钥环、停止旧版本服务；实施测试使用隔离目录，不代替用户自动升级正在运行的服务或修改其真实配置。

## 文件与影响范围

- `renderer/security/ModelKeySettings.tsx` 拆分/组合厂商选择、模型信息表单和既有 SecretInput；空目录不再指示用户去编辑服务端文件。保持单页对话、小设置弹窗与 Key DOM 生命周期。
- `renderer/api/` 增加模型配置 DTO/Zod/API 方法，Query 刷新公开配置与聊天目录；Key 不进入任何普通状态。
- `backend/app/llm/` 增加持久目录 Repository/Service 与非秘密契约，复用原 ModelProfile 的校验及固定预设；API Router/Services/ChatService/handler/CLI 使用同一目录。
- ORM/Alembic 新 revision、一次性导入、迁移兼容测试；生成 OpenAPI/TS，保留原 JWE/SecretStore 和 CLI。
- 更新架构、ADR、MODEL_SETUP、AGENTS、学习与评测记录。保留此前未提交代码与学习注释，无自动提交/推送或发布。

## 验证计划

1. Python：认证/来源/严格字段，厂商白名单、模型 ID/名称边界、容量、拒绝 URL/header/apiKey；初次空目录/JSON 导入、重复启动不覆盖、已有 id 与密文绑定不变，CLI/API 共同可见。
2. 配置保存后不重启即可申请加密挑战、保存合成 Key 并聊天；非法/失败创建不能覆盖已有配置。JWE/CAS/授权/取消的既有测试继续通过。
3. 客户端：空配置仍显示厂商选择和模型信息；添加后自动定位 Key 输入，普通请求不包含 Key，失败反馈清楚且密码不被保留用于重试。
4. 真实 Electron：从空后端开始，仅在界面选厂商/填模型/加密录入合成 Key，随后向本地模型替身聊天；重启保留，保存时无厂商调用；原连接/Mock/隔离/CSP/停止流程回归。
5. lint/typecheck/test/api:generate/build、后端和模型 smoke、开发/E2E/未签名测试包。全部外部厂商使用本地替身，不宣称真实账户或公网部署通过。

## 小练习

把 provider/modelId/displayName 与 apiKey 分成普通配置和秘密两组，说明为什么前三项可以进入 React 状态和普通 DTO，而 Key 必须走独立密码表单和加密接口。再画“客户端选择 → POST 配置 → 数据库 → 目录刷新”的普通前后端数据流，解释它与“把选择权留给用户”并不冲突。
