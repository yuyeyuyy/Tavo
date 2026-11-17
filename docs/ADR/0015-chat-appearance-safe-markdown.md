# ADR0015：独立主题偏好与安全 Markdown 聊天展示

日期：2026-11-17。状态：用户已确认，已实施。

## 问题与选择

客户端需要浅色模式和聊天气泡，助手的 Markdown 不应继续显示原始标记。模型与命令权限属于对话配置，移入设置后仍须保持原有会话、Key 和审批行为。

主题可以保存在后端或本机。后端方案能同步设备，但会增加协议并耦合会话；本机公开偏好足以满足当前需求，离线也可切换。因此使用现有 Zustand + localStorage，只保存 system/light/dark，默认 system，通过 matchMedia 解析系统主题。存储不可用时仍在内存工作。CSS 语义变量覆盖正文、气泡、表单、弹窗、代码和审核卡。

Markdown 可以自行实现有限语法或复用解析器。自写解析器减少依赖，但嵌套列表、代码、GFM 和恶意输入的正确性成本高。本次采用用户批准的 react-markdown 10.1.0 + remark-gfm 4.0.1，将 AST 转为 React 节点；不使用原始 HTML 插件。组件 memo 避免相同文本随会话轮询反复解析，消息 DTO 和服务端原文不变。

## 信任边界

- 仅助手正常回复进入 Markdown，用户消息、错误和工具参数/输出保持文本。
- skipHtml，不使用 dangerouslySetInnerHTML；代码块只显示文本，任务列表禁用交互。表格对齐转 CSS 类，不放宽生产 CSP。
- 不渲染远程 img；保留替代文本与查看链接。用户未点击时不访问第三方资源。
- Renderer 和 Main 共享纯 URL 校验，允许完整 HTTP/HTTPS，拒绝凭据式 URL、控制字符、相对地址及危险协议。Markdown 锚点拒绝合成点击，合法用户点击通过 target=_blank 交 Main；Main 检查当前页面精确匹配可信文档且无 POST 体，然后交系统浏览器，始终拒绝 Electron 子窗口。页面导航/webview 禁令保留。
- 脚注只在所属消息内滚动，不导航；每条消息使用独立 ID 前缀。
- 原生六方法、Renderer contextIsolation/nodeIntegration/sandbox、后端请求认证与凭据处理均保持原有边界。该设计防护不可信消息；不宣称已被攻陷的 Renderer 无法发起合法 HTTP 外链。

## 状态与布局

Chat 继续持有模型/审批状态，ConnectionSettings 只组合控件；切换模型/命令权限沿用开始新会话的行为，不自动取消旧 Run。切主题、开关设置不改会话、草稿或权限。仅新对话/发送等预期操作恢复输入焦点，弹窗保持原有模态焦点与 Escape 行为。

底部始终是同一个 Composer，保留 Enter、Shift+Enter、IME、停止、重试及长度上限。工具记录在聊天滚动区，等待审核自动展开，页头提供待确认入口；不把权限审核改为隐藏的正文内容。

## 代价与验证

增加两个直接依赖和解析器传递依赖，Renderer 非压缩构建约增加 404 kB；未添加代码高亮、数学、图表或执行扩展。Mac ARM64 开发、桌面和未签名测试包分别验证，实际结果见[实施报告](../CHAT_APPEARANCE_MARKDOWN_REPORT.md)。不据此声称正式分发、跨平台或真实厂商验收。
