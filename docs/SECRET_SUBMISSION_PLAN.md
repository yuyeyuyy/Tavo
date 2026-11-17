# 客户端凭据加密提交方案（已确认并实现）

日期：2026-11-16（Asia/Taipei）。需求：在客户端手动输入模型 Key，加密上传服务端，并封装为以后可复用的能力。用户已回答“按推荐方案实现”；以下保留设计与取舍，实际实现、命令和边界见 [SECRET_SUBMISSION_REPORT.md](SECRET_SUBMISSION_REPORT.md)。

用户此次明确允许客户端输入原始 Key，优先于此前“Renderer 无 Key 输入”的阶段限制。调整仅限专用凭据表单的短暂输入/加密，不把读取已保存 Key、写入聊天或放宽模型/连接器权限视作授权。

## 首轮结果

打开设置 → 选择已有服务端模型 profile → 在密码输入框粘贴 Key → 点击保存 → 客户端加密 → 服务端认证、验证用途/版本与解密 → SecretStore 加密落库 → 返回成功与可用状态。用户不再必须使用终端录入，既有 CLI 保留。

首轮仅录入/更换已配置模型的 Key，不新增客户端任意 Base URL、模型配置编辑、批量凭据管理、OAuth 流程或已保存 Key 查看入口。服务端 profile/厂商/目标地址仍由现有配置管理。

## 两种可行方案

| 方案 | 保证与代价 |
| --- | --- |
| HTTPS 传输 + 服务端加密存储 | 标准且实现较少；TLS 终止后 HTTP 正文是原始 Key，本机回环 HTTP 测试也没有正文加密 |
| HTTPS + 应用层加密信封 + 服务端加密存储（推荐） | HTTP 正文携带密文，适合本次明确要求；需要公钥分发、短期挑战、跨语言协议和重放/版本校验 |

推荐第二种。应用层加密不能替代 HTTPS：远程公钥响应也需要可信 TLS，避免被替换；本机回环 HTTP 仅用于开发，仍不能宣称抵御本机进程控制或恶意前端脚本。当前系统保持单所有者信任域，不据此称为公网多用户安全服务。

## 可复用分层

```text
SecretInput（专用输入与清理）
    → SecretSubmissionClient（挑战、标准信封加密、密文 POST）
        → SecretSubmissionService（认证、一次性挑战、解密、版本检查）
            → 按 purpose 注册的 handler
                → ModelKeys / SecretStore（服务端加密存储）
```

前端通用模块不依赖九家模型协议；后端通用提交服务不直接操作任意表/SQL。第一项注册 `model.api_key`，handler 根据现有 profile 解析实际秘密引用与权限范围。以后增加连接器凭据时新增明确用途和 handler，不接受调用方提供任意 secretRef、存储路径、函数名或数据库引用。

## 加密与请求协议

使用已有浏览器 Web Crypto 与 Python cryptography；不自行发明加密算法，不默认引入新依赖。实现限于 [JWE Compact Serialization](https://www.rfc-editor.org/rfc/rfc7516) 的受限子集，算法固定为 [RSA-OAEP-256 与 A256GCM](https://www.rfc-editor.org/rfc/rfc7518)。客户端原语依据 [Web Cryptography API](https://www.w3.org/TR/webcrypto-1/)，服务端使用锁定 cryptography 版本的 RSA OAEP / AESGCM。

- 服务端进程生成 3072 位 RSA 传输密钥对，私钥仅在服务端进程内存，不复用/下发 Fernet 主密钥；进程重启后旧挑战失效。生成工作移出请求的事件循环。
- 客户端每次提交生成独立 AES-256 密钥与 12 字节随机 IV；AES-GCM 加密 Key，RSA-OAEP/SHA-256 包装 AES 密钥，GCM tag 为 128 位。不直接用 RSA 加密任意长度凭据，也不内置客户端共享解密密钥。
- JWE 五段由 protected header、加密后的 AES 密钥、IV、密文、tag 组成。protected header 包含固定算法、kid，以及本次挑战/用途/资源标识，作为 GCM AAD 认证；服务端必须与自己保存的挑战逐一核对。
- 禁止算法协商降级、压缩、外部密钥 URL 和不受保护的自定义头；严格限制 Base64url、各段长度与总正文。服务端只返回固定错误，不暴露解密失败细节。

已实现接口均沿用现有客户端认证与严格来源校验，响应 `Cache-Control: no-store`：

1. `POST /api/v1/secret-submissions/challenges`：请求 `{purpose:'model.api_key',resourceId:profileId,expectedRevision:N}`。服务端检查 profile、权限与客户端观察的版本，返回短期 challengeId、过期时间、用途/资源、协议版本、kid/publicJwk；不返回任何已保存凭据。增加 expectedRevision 是为了让过时表单在申请挑战时就能发现冲突。
2. `POST /api/v1/secret-submissions`：请求 `{challengeId,envelope}`，envelope 为固定算法 JWE。服务端取得挑战的可信用途/目标，不让密文里的字段自行扩权。解密后交给模型 handler 校验并加密保存。
3. 成功仅返回 `{resourceId,credentialRevision,ready:true}`。既有 `/chat/models` 可增加非秘密凭据版本状态，用于刷新 UI；无 Key、密文、秘密引用或 Key 指纹返回。

挑战绑定当前客户端会话的服务端摘要、用途、profile/目标绑定和创建时的凭据版本；有效期为 120 秒。限制每会话及进程内未过期挑战数，过期清理；同一挑战原子地最多允许一次提交尝试，失败后需重新申请，不自动复用/重传。认证过期/会话变化后也需新挑战。

第一版挑战与 RSA 私钥采用单 API 进程内存存储，不声称适用于无粘性的多 API 实例。进程重启会要求重新输入/提交，不恢复未完成的凭据上传；后端已保存 Key 不受影响。

## 并发与已有 CLI 的一致性

为防两个窗口互相覆盖，或旧提交在删除后复活 Key，模型凭据需要持久 revision 与删除墓碑。新增 Alembic `0003_secret_submission_state` 保存凭据引用的版本/状态元数据，不存原始 Key、提交信封或传输私钥；不修改 0001/0002。

对现有模型密文建立初始版本。上传 handler 与 model-key CLI 的更新/删除统一走同一写事务：比较挑战捕获版本 → 更新 SecretStore 密文/删除 → 增加版本/墓碑。同一目标的旧挑战返回 409，不静默覆盖新 Key。Fernet 轮换只重加密，不把它当作用户更换 Key。连接器现有秘密读写不因本轮扩大权限或改业务行为。

一次性挑战不等于网络 Exactly-once。若服务器保存成功但客户端没收到响应，不自动盲重传；提示刷新状态，必要时重新录入。客户端网络超时/取消不承诺服务端未完成保存。

## 客户端交互与秘密生命周期

设置中增加“模型 Key”区域，列已有服务端 profiles 的名称、厂商、模型和是否已配置。输入框默认空，`type=password`，不回填旧 Key；保存不调用付费模型。成功刷新目录即可选择模型发送，CLI 仍可用于部署和本机排错。

原始 Key 只存在专用输入 DOM 和提交所需的短暂变量，不进入 React 全局状态、Zustand、Query、localStorage/sessionStorage、URL、聊天历史、事件、通知或普通日志。提交时读取后立即清空输入，尽可能清理字节缓冲；关闭表单也清空。加密/上传失败要求重新输入，不偷偷保留明文用于重试。

JavaScript 字符串/DOM/运行时内存无法保证绝对擦除；前端一旦被恶意脚本控制，攻击者仍可能读取正在输入的 Key。这个方案提供提交密文和生命周期约束，不声称加密能消除 XSS/本机调试/剪贴板风险。保持现有 CSP、contextIsolation、sandbox、禁用 nodeIntegration 和六方法 Preload，不新增明文 Key IPC。

加密接口不可用或当前 origin 不支持 Web Crypto 时，明确禁用客户端提交并保留 CLI 方式，不能降级为明文 HTTP 或 Base64“加密”。开发和打包 `tavo://app` 中的实际 Web Crypto 可用性需由 Electron E2E 验证。

## 文件与影响范围

- 客户端新增 `security/secret-submission.ts`、有界 DTO/Zod 与专用 SecretInput 组件；在设置弹窗组合，不与 ChatTransport/消息状态耦合。
- 服务端新增通用 `credentials/` 契约/加密/挑战服务与 Router、模型 purpose handler；复用 ModelKeys/SecretStore 的加密和认证依赖，补凭据版本事务。
- 新 Alembic 0003、版本元数据 Repository/ORM，更新 CLI 一致写入；保留旧密文、Key 绑定和已有轮换语义，不触碰真实凭据内容。
- Pydantic/OpenAPI/生成 TS/Zod 同步；适当补来源/正文限制与有界错误码，禁止请求/响应/解密异常回显秘密。没有通用服务器解密或原始凭据读取端点。
- 更新架构/ADR/学习/验证/操作说明，以及本次明确允许专用表单临时接触 Key 的 AGENTS 补充；保持其他 Renderer 凭据限制。
- 保留工作区已有未提交实现和学习注释，不自动提交、推送、发布或联系真实厂商。

## 验证计划

1. JS Web Crypto → Python 解密的真实跨语言用例，覆盖最大受限 Key、Unicode/非法内容、修改密文/tag/header、错误密钥、无效编码和算法降级拒绝。
2. 鉴权/来源/用途/目标范围，挑战过期、重复/并发提交、会话变化、进程重启、资源名额限制；错误和日志不出现合成秘密标记。
3. 凭据版本 CAS、两个窗口竞争、CLI 更新/删除后旧挑战拒绝、删除墓碑/ABA、现有密文升级和轮换兼容。Key 密文和版本同事务保存。
4. 真实 Electron：在设置选择 profile、输入合成 Key、发出的网络正文只有密文、输入清空、保存状态刷新、随后经本地模型替身聊天；错误要求重输、Escape/关闭清理，Web Crypto 在开发/打包可用。测试只使用合成 Key，截图/trace 不含真实秘密。
5. `pnpm api:generate`、lint/typecheck/test/build、后端和模型 smoke、开发/E2E/未签名测试包；如实区分本地替身与真实厂商验证。

## 学习练习

画出“输入框 → 传输密文 → 服务端短暂明文 → 数据库密文”的四个位置，说明公钥为什么可以发给客户端，而传输私钥/Fernet 主密钥不能。再解释 HTTPS、应用层加密、加密存储分别保护哪个阶段，以及为什么凭据保存接口不应复用聊天消息接口。
