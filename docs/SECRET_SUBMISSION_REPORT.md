# 客户端凭据加密提交报告

> 本报告为凭据提交首轮基线。随后已增加客户端自助厂商/模型配置与 0004 持久目录，当前操作/验证见 CLIENT_MODEL_SETUP_REPORT.md / MODEL_SETUP.md；下文“仅已有服务端 profile”不能当作当前新增配置能力限制。

日期：2026-11-16（Asia/Taipei）。用户确认 SECRET_SUBMISSION_PLAN.md 后实现，仍在 `feat/agent-foundations`。保留此前 Agent/单页对话/模型适配和开发者学习注释；本轮没有提交、推送、发布、读取真实 Key 或迁移正常用户数据库。

## 交付行为与使用

右上角打开设置 → 模型 Key → 选择服务端已启用 profile → 密码框输入 → 加密保存 Key。输入在提交时立即清空，成功后刷新目录/凭据版本，即可在聊天里选择模型。保存不请求厂商，不检查真实账户权限或额度；已配置只说明服务端可以读取本地凭据。关闭表单清空未提交内容，失败要求重新输入。

既有 Python 隐藏 CLI 保留。profile/厂商/模型/目标仍在非秘密 models.json 中配置，不提供前端任意地址或已保存 Key 查看。已有数据库先备份数据库与独立密钥环、停止旧服务，再运行 `pnpm backend:init` 至 `0003_secret_submission_state`，之后重启后端/桌面；不要删库或让旧版本 CLI 同时管理凭据。具体步骤见 [MODEL_SETUP.md](MODEL_SETUP.md)。本轮测试均使用临时数据库，没有代替用户升级正常数据。

## 文件变化与理由

| 文件 | 作用 |
| --- | --- |
| `apps/desktop/src/renderer/security/SecretInput.tsx` | 可复用专用密码 DOM 输入、提交/关闭清理、abort、非秘密通知/刷新回调，不依赖模型目录或聊天 |
| 同目录 `ModelKeySettings.tsx` | 组合模型选择与 SecretInput，显示 ready/revision；版本变化重建空表单 |
| 同目录 `secret-submission.ts` / `contracts.ts` | 严格 Zod、Web Crypto 信封、短暂字节清理、仅密文 POST，不提供明文重试/降级 |
| `renderer/connection.tsx` / `styles.css` | 设置弹窗中的模型 Key 区域，保持单页对话布局 |
| `renderer/api/client.ts` / `api/chat.ts` | 有界 API 方法、目录 revision，区分普通超时与模型超时 |
| `backend/app/credentials/contracts.py` / `envelope.py` | Pydantic 请求/公开 DTO、严格固定算法 JWE 解密和公钥导出 |
| 同目录 `service.py` / `handlers.py` | 会话绑定的一次性挑战、容量/有效期/取消控制，静态用途与 ModelKeyHandler |
| 同目录 `repository.py` | 内容版本 CAS 与删除墓碑 |
| `backend/app/api/credentials.py` / `main.py` / `services/application.py` | 认证 Router 与生命周期装配，关闭时等待有界加密任务 |
| `backend/app/llm/keys.py` / `llm/contracts.py` / `services/chat.py` | HTTP/CLI 共用版本事务、目标绑定与目录版本，轮换保持内容版本 |
| `backend/app/models/entities.py` / `migrations/versions/0003_secret_submission_state.py` | credential_states，现有模型密文只补版本，不改 0001/0002 或旧 SQL |
| `core/errors.py` / `packages/shared/src/contracts.ts` | 四个固定错误码，不回显加密异常或秘密 |
| `backend/openapi.json` / `renderer/api/generated.ts` | 从 Pydantic 重新生成，不手改生成类型 |
| `backend/tests/test_secret_submissions.py` / `tests/secret-submission.test.ts` | 迁移、密码协议、授权/并发/取消与真实跨语言验证 |
| `tests/e2e/desktop.spec.ts` / `scripts/model-harness.*` / `scripts/dev-smoke.mjs` | 无预存 Key 的真实 Electron 流程、网络正文与输入清理；既有模型替身保留 |
| `docs/SECRET_SUBMISSION_PLAN.md` / ADR 0007、架构/API/学习/评测/操作与 AGENTS | 记录用户授权例外、复用边界、命令、迁移和限制 |

没有新增依赖、原生方法、明文 Key IPC、账户连接器或聊天工具。Main/Preload 的六方法、CSP、contextIsolation、sandbox 和 nodeIntegration 策略不因本次输入表单放宽。

## 数据与权限边界

```text
专用密码 DOM → 短暂 UTF-8 缓冲 → Web Crypto JWE → 认证 HTTP
  → 挑战/用途/目标/会话校验 → Python 短暂 SecretStr
  → ModelKeys 版本事务 → SecretStore Fernet 密文 + credential_states
```

前端表单、提交器、后端通用服务和业务用途分开：以后注册新用途，需要增加明确契约/handler/权限/测试，不让客户端指定任意 secretRef、路径、函数或 SQL。当前只有 model.api_key，目标只能是服务端启用的 profile。API 无 Key 读取/回填、通用解密或秘密导出。

采用 [JWE Compact](https://www.rfc-editor.org/rfc/rfc7516) 的受限子集和固定 [RSA-OAEP-256 / A256GCM](https://www.rfc-editor.org/rfc/rfc7518)，使用 [Web Crypto](https://www.w3.org/TR/webcrypto-1/) 与已锁定 cryptography 50.0.2。RSA-3072 进程传输私钥与持久外部 Fernet 密钥环分离；每次提交独立 AES-256 密钥/随机 IV。客户端用 wrapKey 包装 AES 密钥，不手写 RSA/AES 原语。

| 限制 | 实现值 |
| --- | --- |
| 明文 | 1–8192 UTF-8 字节；模型 Key 进一步限定可打印 ASCII、无首尾空格 |
| 信封 | 五段 JWE，最多 16384 字符；HTTP 总正文沿用 32 KiB |
| RSA / AES-GCM | RSA-3072，包装段 384 字节；AES-256，IV 12 字节，tag 16 字节 |
| protected header | 精确 v/typ/alg/enc/kid/challengeId/purpose/resourceId，编码串作为 GCM AAD |
| 挑战 | 120 秒，绑定会话摘要、用途、profile/目标引用与捕获 revision；4/会话、64/API 进程 |
| 并发 | RSA 生成移到线程；最多两个解密任务，取消后底层工作结束才释放名额 |
| 客户端等待 | 挑战/提交各 10 秒，不自动重试；未知异常固定公开错误 |

算法不能由客户端协商，禁止压缩、外部密钥 URL、额外/缺失/重复头与非规范 base64url。挑战先消费再解密，同一尝试至多一次；错误不区分解密细节。保存前及取得数据库写锁后重查会话/有效期，已知断开/取消不再写入。若响应丢失，服务端可能已经保存，需先刷新再决定是否重输。

credential_states 保存内容版本、present 与更新时间，不保存原文/提交信封/传输私钥。首次缺失版本 0，更新/删除递增且保留墓碑，CAS 防两个窗口/CLI 覆盖或删除后复活；即使删除不存在的当前绑定，也增加版本来使旧挑战失效。Fernet 轮换只重加密，不推进内容版本。0003 只为已有 `llm:` 引用补 revision=1/present=true，原密文不重写，连接器秘密不纳入本次版本管理。

## 实际验证

环境：本机 macOS ARM64，Node 24.15、pnpm 11.1.3、Python 3.12.14；全部 Key 为合成值，模型使用回环 HTTP 替身。

| 命令 | 实际结果 |
| --- | --- |
| `pnpm api:generate` | 成功生成 OpenAPI/TypeScript，运行时 Zod 同步 |
| `pnpm lint` | ESLint、Ruff 与 Python 格式检查通过 |
| `pnpm typecheck` | 通过 |
| `pnpm test` | 客户端 34 项 / Python 135 项通过，新增 7 / 28 项 |
| `pnpm build` | 在 test:e2e / package:dir 中执行并通过 |
| `node scripts/backend-smoke.mjs` | 独立进程认证/REST/队列/SSE/记忆/上下文/工具/Agent/重启通过 |
| `node scripts/model-smoke.mjs` | 回环 HTTP 模型替身/认证/加密持久 Key/错误/断开/重试/重启通过，无厂商请求 |
| `node scripts/dev-smoke.mjs` | 实际 pnpm dev 窗口 Web Crypto 提交/清空、随后模型 HTTP、Mock/新对话/设置通过 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm test:e2e` | 5 场景通过（59.0 秒） |
| `pnpm package:dir` | 成功产出 macOS ARM64 未签名 .app，未发布 |
| `TAVO_BROWSER_CHANNEL=chrome pnpm test:packaged` | 直接启动未签名 .app，5 场景通过（53.9 秒） |
| `git diff --check` | 通过；旧 0001/0002、历史 SQL 与 Preload 无本轮变更 |

跨语言测试使用生产 TypeScript 加密函数与生产 Python 解密函数，验证普通及 8192 字节上限、每次随机密钥/IV、作用域拒绝、清零、无加密降级、失败/取消无明文重试。没有只用两个测试辅助函数相互证明兼容。

Python 专项覆盖五段篡改/算法和用途目标修改、会话/认证/过期/进程重启、单会话/全局挑战容量、重复及并发消费、CLI 更新/删除 ABA、轮换与目标变更、非法 Key 不覆盖旧值、断开/取消/解密期间过期、数据库锁等待后过期，以及从 0002 补版本但密文不变/Alembic 元数据一致。日志和公开响应断言不含合成秘密标记。

桌面新增场景在无 Key 状态开始，实际 Electron secure context/Web Crypto 加密，截取请求确认不含 Key/只有五段信封；保存后输入为空，目录 revision=1、可选模型并与本地替身聊天，关闭/重开清空，失败重新输入，后端重启仍 ready。其余四场景回归模型错误/停止/切换、输入法/键盘/焦点/窄窗口、隔离/CSP、历史兼容与退出桌面后的任务。设置截图已目视检查，`test-results/` 为忽略的可覆盖本地产物。

## 残留风险与未验证

- 专用 Renderer 手动输入是用户授权的短暂例外，不能承诺恶意前端/XSS/本机控制下仍保密。DOM/JS 字符串、密码库和原生密码原语可能有内部副本；清空与缓冲清零不是绝对内存擦除。Key 不写聊天、Query、全局状态、存储、URL、IPC 或普通日志。
- HTTPS 仍保护远程公钥分发/对端认证；应用层信封不能替代 TLS。回环 HTTP 仅开发，当前没有公网生产 HTTPS、多用户/租户隔离、KMS 或完整安全审计。
- 挑战和 RSA 私钥仅单 API 进程内存；API 重启需重新输入/提交，不能用于无粘性多实例。持久 Key 与版本不受重启影响。
- 网络超时/取消不保证服务端未保存；不自动重试，不承诺网络 Exactly-once。数据库 busy timeout 与资源收尾也不等于硬实时。
- 没有用真实 Key 调用九家厂商，没有验证真实权限/地区/额度/费用/质量。本轮未重跑 Docker/Linux、Windows、Intel macOS、干净机器安装、签名/公证/升级分发或负载/渗透测试。
- 未新增客户端任意模型配置、已有 Key 读取、OAuth/真实账户、流式、模型工具、持久聊天/RAG/多 Agent。

学习练习见 [LEARNING_NOTES.md](LEARNING_NOTES.md)：画出 DOM/传输密文/服务端明文/数据库密文，并用 0→1→2→3 解释删除墓碑与旧挑战。
