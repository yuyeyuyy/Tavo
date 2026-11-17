# ADR 0007：通用凭据加密提交与内容版本

日期：2026-11-16。状态：用户确认并实现。范围：已有模型 profile 的手动 Key 输入；不扩展真实账户、任意配置编辑或秘密读取。

## 问题与方案

之前仅服务端隐藏 CLI 可录入 Key。用户希望在客户端输入、加密上传，并复用此功能。HTTPS + 服务端 Fernet 最少代码，但 TLS 终止后的请求正文为明文；本次采用 HTTPS + 受限标准 JWE + 服务端 Fernet，使应用请求正文只携带密文。应用层加密仍依赖可信 HTTPS 分发公钥，不能抵御恶意前端。

依据 [JWE Compact](https://www.rfc-editor.org/rfc/rfc7516) 和 [JWA](https://www.rfc-editor.org/rfc/rfc7518) 固定 RSA-OAEP-256/A256GCM；不提供算法协商、密钥 URL 或压缩。使用已有 Web Crypto / cryptography，不新增 SDK/依赖或自创密码算法。RSA-3072 传输私钥只在单 API 进程内存，每次提交用独立 AES-256 密钥/12 字节 IV，GCM tag 为 128 位。

## 决策

- `SecretInput → secret-submission.ts → SecretSubmissionService → purpose handler` 是通用边界，`ModelKeySettings / ModelKeyHandler` 提供第一项业务适配。未来增加用途时显式增加契约、handler、授权和测试，不提供任意 secretRef 写入接口。
- 明确允许专用 Renderer 密码表单短暂接触用户手动输入；提交/关闭清空，不进入聊天、Query、全局状态、URL、IPC、日志。不回显服务器已有 Key。字节清理尽力而为，JS/运行时内存不保证绝对擦除。
- 120 秒挑战绑定会话摘要、用途、profile/目标、内容版本；每会话 4 个/进程 64 个，先消费再解密。最多两个解密任务，取消后保留名额直到工作结束，不自动重试。
- Alembic 0003 增加 credential_states。持久 revision 和删除墓碑处理两个窗口/CLI 竞争，密文与版本同事务；取得写锁后重新核对授权/过期。CLI put/delete 也推进版本，轮换不推进内容版本。
- 保留服务端隐藏录入 CLI。Web Crypto 不可用时禁用表单，无明文降级。profile/目标地址仍由服务端配置，保存不调用厂商。

## 结果与局限

现有模型密文只补初始版本，不重写；0001/0002 与旧 SQL 夹具不改。传输私钥、挑战及信封不持久化，API 重启需重输，已保存凭据保留。单进程挑战不适合无粘性多实例；没有 KMS、租户权限或生产公网部署承诺。响应丢失时可能已保存，刷新后决定是否重输，单次尝试不能当作 Exactly-once。

跨语言 Web Crypto→Python、篡改/重放/范围/取消/CAS/迁移以及真实 Electron 证据见 [SECRET_SUBMISSION_REPORT.md](../SECRET_SUBMISSION_REPORT.md)。
