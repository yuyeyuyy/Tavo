# ADR 0013：命令权限与公网出口独立管理

日期：2026-11-17。状态：用户明确确认并实施。

新会话采用full_access、auto_review、request_approval，只控制Bash审批。其他已注册工具的合法调用免额外审批；认证、scope/fence、参数、文件路径、资源预算及网络隔离继续由后端验证。自动批准来源为policy或model/mock，不伪造human决定。全访问不意味着宿主root权限。

采用Context策略4冻结这些含义，旧模式与策略1/2/3保留。使用独立request_approval而不是重解释ask，保护旧会话、待批准调用和历史审计。ReviewMode使用既有字符串列，无需结构迁移；OpenAPI与Zod同步更新。

公网默认proxy/allow_public_https=true/require_write_approval=false。选择保留Unix socket应用层出口：相比给执行容器普通bridge，复用现有全地址校验、IP固定连接、TLS/SNI、逐跳重定向校验及配额，仍能防止脚本直接访问私网/宿主。代价是普通curl/requests无透明联网能力，须使用tavo_http的HTTPS URL入口。明文HTTP、IP直连、自定义DNS/隧道仍拒绝。

任意公网URL不注入已保存Token，即使域名碰巧等于已有凭据目标；凭据只能经原别名在绑定的方法/路径内注入。管理员可禁网、退回别名列表或恢复写请求批准。运行时取当前与冻结权限交集，不因配置放宽升级既有Run。

对话表达由四协议共享系统提示、兼容文本入口及Mock统一约束，不主动介绍内部工具。保留模型调用schema和独立审计，避免用正则过滤正文或掩盖错误。模型遵循提示的可靠性与实际外部API语义仍需业务评估，本地替身测试不能代表所有厂商。

验证覆盖三档决策、非Bash免审批、旧策略冻结、公网自动写方法、凭据绑定、URL/私网/重定向、真实容器与桌面重启恢复，记录见[本轮报告](../PERMISSION_NETWORK_REPORT.md)。
