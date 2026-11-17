# 客户端主题、Markdown 与聊天布局验收

日期：2026-11-17。用户已确认 [方案](CHAT_APPEARANCE_MARKDOWN_PLAN.md)，按现有架构增量实施。后端代码、消息契约、数据库和工具执行权限未修改，head 仍为 0008；无需为这次界面升级运行 init。之前任务的未提交改动保留，未提交、推送或发布。

## 交付行为

- 右上角设置 → 外观：跟随系统／浅色／深色，默认跟随系统。公开偏好在本机持久保存，重开应用保留；系统变化只影响跟随系统模式，存储不可用时仍可临时切换。
- 左侧助手、右侧用户的聊天气泡，助手小头像；Mock 在页头明确标记。浅/深色覆盖欢迎页、对话、设置、Key 表单、代码/表格、历史和审核卡。
- 底部只有长条输入和发送／停止，输入随多行增长；Enter 发送、Shift+Enter 换行、IME 不误发送。已移除输入区选择器、计数/快捷键提示和底部状态小字。模型与三档命令权限在设置 → 对话，原有切换开始新会话的语义保留。
- 助手正常回复显示 Markdown：标题、粗体、删除线、列表、引用、代码、表格、只读任务项和脚注。中文换行保留；长代码/表格仅局部横向滚动，不撑宽窗口。
- 历史、重启恢复、停止/重试、旧会话权限和 Key 加密提交/清空保留。工具记录进入聊天滚动区，待审核自动展开，页头待确认可定位；审核对象与详情不变。

主题独立于会话，切换不会重置草稿、模型或权限。服务端仍保存 Markdown 原文，客户端只改变展示。

## 安全与实现取舍

采用已批准的 react-markdown 10.1.0 + remark-gfm 4.0.1，锁定两个直接依赖及完整传递图；锁文件新增 95 个包记录，包含这两个直接依赖及其解析器依赖。自行编写语法解析器的正确性维护成本更高，详见 [ADR0015](ADR/0015-chat-appearance-safe-markdown.md)。

HTML 不渲染，不使用 dangerouslySetInnerHTML/rehype-raw；代码块不执行，图片不自动请求，用户消息和工具输出保持文本。表格使用 CSS 对齐类，生产 CSP 未放宽。HTTP/HTTPS 外链由用户点击后交系统浏览器，Renderer/Main 双层验证，禁止危险协议、凭据式 URL、控制字符；Electron 新窗口和页面导航仍拒绝。自动化用 shell.openExternal 替身验证交接，未实际启动浏览器访问测试域名，不把它称为外部网页业务验收。

未增加原生方法或 Node 权限。六方法桥接、contextIsolation=true、nodeIntegration=false、sandbox=true 保留。脚注使用每条消息独立前缀并在消息内滚动，包含引用返回与无重复 ID 检查。

## 本轮文件

| 文件 | 变更与原因 |
| --- | --- |
| apps/desktop/src/renderer/appearance.tsx（新） | 公开偏好、系统跟随、内存降级、设置单选项 |
| apps/desktop/src/renderer/src.tsx | React 挂载前初始化主题及事件订阅 |
| apps/desktop/src/renderer/styles.css | 语义配色、气泡、长条输入、响应式设置与 Markdown 排版 |
| apps/desktop/src/renderer/chat/Chat.tsx | 消息布局、助手 Markdown、设置组合、简化 Composer、保留审核/焦点 |
| apps/desktop/src/renderer/chat/MarkdownMessage.tsx（新） | 安全解析、链接/图片、表格、只读任务项与独立脚注 |
| apps/desktop/src/shared/links.ts（新） | Renderer/Main 共用的纯 URL/可信文档校验 |
| apps/desktop/src/main/index.ts | 合法外链交系统浏览器，仍拒绝创建窗口 |
| apps/desktop/src/renderer/connection.tsx | 设置中组合外观/对话，保留连接/Key 表单 |
| apps/desktop/package.json、pnpm-lock.yaml | 两个必要直接依赖及锁文件 |
| tests/appearance.test.ts（新） | 主题纯逻辑、存储失败、URL 和可信文档边界 |
| tests/e2e/chat-appearance.spec.ts（新） | 系统/显式主题、重开持久化、草稿/会话、Markdown、安全、窄窗口与脚注 |
| tests/e2e/chat-helpers.ts（新）、tests/e2e/desktop.spec.ts | 复用临时应用/设置操作，原九项流程改从设置选择模型/权限 |
| scripts/dev-smoke.mjs | 开发窗口从设置选择模型 |
| README.md、docs/MODEL_SETUP.md | 当前界面、模型与权限入口说明 |
| AGENTS.md、docs/DEVELOPMENT_PLAN.md、docs/ARCHITECTURE.md | 已批准的客户端约定及数据流 |
| docs/ADR/0015-chat-appearance-safe-markdown.md、docs/LEARNING_LOG.md | 取舍、信任边界与学习练习 |
| docs/CHAT_APPEARANCE_MARKDOWN_PLAN.md、docs/EVALUATION.md、本报告 | 确认状态、测试证据与边界 |

## 验证记录

以下使用临时后端/客户端数据目录、合成 Key、本地 HTTP 模型替身；未调用真实付费模型。普通测试默认跳过的真实容器/宿主用例不会计为已通过。

| 实际命令 | 结果 |
| --- | --- |
| pnpm typecheck | 通过 |
| pnpm lint | ESLint、Ruff 检查/格式通过，111 个 Python 文件保持原格式 |
| pnpm test | Vitest 52/52；Pytest 280 通过、38 默认跳过（47.32 秒） |
| pnpm build | Main/Preload/Renderer 构建通过 |
| TAVO_BROWSER_CHANNEL=chrome TAVO_TEST_DOCKER=1 pnpm test:e2e | 11/11，通过（约 2.1 分钟） |
| pnpm package:dir | 未签名 macOS ARM64 dir 测试包构建通过 |
| TAVO_BROWSER_CHANNEL=chrome TAVO_TEST_DOCKER=1 pnpm test:packaged | 11/11，通过（约 2.1 分钟）；主题用例还包含完整应用关闭重开 |
| pnpm test:dev | 实际开发窗口：添加模型、加密 Key/清空、HTTP 替身聊天、新对话和设置通过 |
| pnpm build && pnpm exec playwright test tests/e2e/chat-appearance.spec.ts | 最终脚注/窄窗口修复后 2/2（12.8 秒），包括往返锚点与两条完整回复的 ID 唯一性 |
| git diff --check | 通过 |

初次桌面回归 8/9：点击示例问题后焦点丢失。根因是 Composer 统一到底部后不再重挂载，原先依赖挂载时聚焦；已在接受发送后主动恢复焦点，最终九项原流程全部通过。开发过程中类型检查发现并修正未使用测试变量及替身声明之外的字段断言，URL 正则改为明确字符检查以通过 lint。最后复核补齐脚注返回锚点与每条消息的注释标签 ID，测试等待第二条完整回复后再检查唯一性。隐藏注释标题还暴露了长消息窄窗口纵向溢出，已为消息设定定位上下文并恢复隐藏标题边距；使用实际窗口布局轮询确认无溢出，最终主题/Markdown 两项定向复验通过（12.8 秒）。

截图实际检查了浅/深色欢迎、普通对话、Markdown 长内容、设置、宿主审批及 620×540 窄窗口。最近完整包内截图位于 test-results/chat-packaged-*.png；这些是临时回归产物，下次 Playwright 运行会清理。包含长表格故意超宽的夹具，表格在自己的滚动区显示。

## 使用与实际边界

安装更新依赖后关闭旧客户端，仓库根目录执行 pnpm dev；后端正常运行即可。在右上角设置选择外观、模型和命令权限。本次无需修改后端配置、迁移数据库或重新录入已有 Key。

Renderer 非压缩 JS 构建从约 1051 kB 增至约 1455 kB（约 +404 kB），CSS 约 27 kB；未做启动/内存性能基准，不声称性能提升。Markdown 不包含语法高亮、数学、图表执行或自动图片下载；可选择文本复制代码。窗口加载前仍使用既有深色原生背景，不承诺手动浅色偏好下完全无启动闪色。

产物 release/mac-arm64/Tavo.app 是本机未签名测试应用，无签名、公证、正式分发或 Linux/Windows 验证。38 项后端真实测试本轮普通命令未运行；桌面相关实际 Docker/宿主临时操作已由显式 E2E 验证，不等于重跑整个后端真实容器/宿主测试集。未进行真实部署或远程写操作；不宣称任意恶意 Renderer 或脚本均已强隔离。
