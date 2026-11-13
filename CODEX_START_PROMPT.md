# 复制下面内容作为 Codex 的第一条任务

你现在是 Tavo 开源项目的架构师、资深 TypeScript/Electron 工程师，同时也是我的开发导师。我想边开发边学习，目标是将项目打磨成可用于 2027 届 AI Agent / 全栈开发校招简历的真实开源软件。

项目目标：Tavo 是 Local-first 的桌面 Personal Agent，通过官方 API / 用户授权连接器聚合 DeepSeek、OpenRouter、GitHub 等平台数据，并用事件驱动的 LangGraph.js Agent 做分析与主动提醒。

请先阅读根目录 `AGENTS.md` 和 `docs/DEVELOPMENT_PLAN.md`，只处理 Phase 0。

本轮要求：
1. **先做架构规划，暂时不要写代码或安装依赖。** 结合现有仓库情况输出当前状态和 Phase 0 的最小可执行计划。
2. 比较 `electron-vite + React + TS + pnpm workspace` 与其他可行初始化方式，给出有依据的选择理由，不要盲目追新。
3. 设计最小 monorepo 目录及依赖关系，保证 `core/connectors/agent` 不依赖 Electron UI，未来可脱离桌面端运行。
4. 明确安全 IPC、SQLite native module 打包、凭据存储的主要风险和技术验证方法。
5. 先写出 Phase 0 的验收条件、验证命令、最小测试、潜在失败和回滚方法。
6. 给出 5 个我需要理解的基础知识点（例如 Electron 进程模型、IPC、Node Worker、SQLite 迁移、LangGraph Checkpoint），每个知识点附一个 10–20 分钟的小练习。
7. 只有在我确认方案后再开始 Phase 0 实现；一次最多实现一个独立里程碑。所有修改完成后必须实际运行测试并提供证据。

重要约束：不得引入 Python 后端、云端服务、浏览器爬取、多 Agent；不得使用真实 API Key；不得未确认就扩大范围、推送 Git、执行破坏性命令。
