# Git 分支协作

2026-11-14 用户授权将项目上传到 `yuyeyuyy/Tavo`，使用 main / dev / feat 分支。仓库保持私有，不自动合并、创建 Release 或改变可见性。

| 分支 | 职责与本次内容 |
| --- | --- |
| `main` | 稳定基线：原 Phase 1 提交 `898328d`，加上私有数据/本机配置忽略规则 `57eedd8` |
| `dev` | 从 main 创建的集成分支；本次尚未合并重构 |
| `feat/client-server-python` | Python/FastAPI 独立服务端、桌面 HTTP/SSE 客户端、旧代码清理和相应测试/文档 |
| `feat/agent-foundations` | 持久 Session/AgentPool、多厂商与加密 Key 配置、宿主/沙箱工具、主题/Markdown 聊天客户端及有界模型重试 |

当前工作留在功能分支。后续通常先审查 `feat/... → dev`，完成集成验证后再 `dev → main`。本次只建立分支并上传，不把未合并的重构当作 main 已发布版本。

重构集成到 dev 后，新功能通常从最新 dev 开始；当前 Agent 功能需要 Python 架构，因此以 `feat/client-server-python` 为基线，没有退回旧 dev：

```bash
git switch dev
git pull --ff-only
git switch -c feat/功能名称
```

提交前运行 `pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm build`，按变更补充实际业务验证。数据库、备份、密钥、令牌、.env、.venv、node_modules、打包产物和 .idea 不上传。必要的 `.env.example` 仅有配置示例。

本次使用 GitHub noreply 邮箱提交，没有修改全局 Git 身份。常见密钥格式检查覆盖当前待上传文件及已提交历史，不替代完整安全审计。旧源码/学习注释仍可通过 `git show 898328d:文件路径` 查看。

## 2026-11-17：提交当前功能分支

用户授权将此前连续完成且已验证的功能整理为一个提交，推送到origin/feat/agent-foundations。工作以c9e7f68的Python客户端/服务端架构为基线；main/dev不合并、不改仓库可见性、不创建Release。提交身份沿用既有GitHub noreply，不修改全局Git配置。

最新完整质量结果：前端54、后端326通过/38默认跳过，桌面与未签名ARM64包内各11通过，开发窗口及独立backend/model烟雾通过。DeepSeek合成输入自动增额恢复成功，复杂思考仍受60秒总时限；见[模型重试验收](MODEL_RETRY_OUTPUT_REPORT.md)。提交前检查差异空白、候选文件与常见凭据格式，排除数据库/密钥/Token/.env/测试和打包产物；此检查不称为完整安全审计。
