# Git 分支协作

2026-11-14 用户授权将项目上传到 `yuyeyuyy/Tavo`，使用 main / dev / feat 分支。仓库保持私有，不自动合并、创建 Release 或改变可见性。

| 分支 | 职责与本次内容 |
| --- | --- |
| `main` | 稳定基线：原 Phase 1 提交 `898328d`，加上私有数据/本机配置忽略规则 `57eedd8` |
| `dev` | 从 main 创建的集成分支；本次尚未合并重构 |
| `feat/client-server-python` | Python/FastAPI 独立服务端、桌面 HTTP/SSE 客户端、旧代码清理和相应测试/文档 |

当前工作留在功能分支。后续通常先审查 `feat/... → dev`，完成集成验证后再 `dev → main`。本次只建立分支并上传，不把未合并的重构当作 main 已发布版本。

新功能从最新 dev 开始：

```bash
git switch dev
git pull --ff-only
git switch -c feat/功能名称
```

提交前运行 `pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm build`，按变更补充实际业务验证。数据库、备份、密钥、令牌、.env、.venv、node_modules、打包产物和 .idea 不上传。必要的 `.env.example` 仅有配置示例。

本次使用 GitHub noreply 邮箱提交，没有修改全局 Git 身份。常见密钥格式检查覆盖当前待上传文件及已提交历史，不替代完整安全审计。旧源码/学习注释仍可通过 `git show 898328d:文件路径` 查看。
