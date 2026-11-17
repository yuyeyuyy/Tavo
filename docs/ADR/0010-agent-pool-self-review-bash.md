# ADR0010：有界AgentPool、独立自审查与隔离Bash

2026-11-16，用户确认完整方案后实施。

当前Scheduler的active字典和Assistant的只读线程池职责不同。提取AgentPool统一Run生命周期/资源占用，数据库队列与lease/fence继续由Scheduler/Repository管理；不引入多角色Agent或第二套SDK。Pool默认2个Run，Bash异步执行并发1，未确认清理的执行继续占用名额。

比较宿主shell加提示词审核与独立Docker工作区：采用后者。模型自审查通过当前模型的无工具独立请求返回有界结构化结论，后端合并规则、模式和人工决定。规则或模型deny不能被人工覆盖，模型allow只能使用已声明能力。Mock复核单独标记。

Session显式bashEnabled，旧会话默认false；增加auto_sandbox作为自动写沙箱的明确授权，auto_readonly不自动放行Bash。策略2冻结，策略1仍沿用原有只读执行。主模型轮次和self_review目的分离，加密续接与上下文分离。

Bash每次使用非root、无外网、只读根目录的短命容器，Session命名卷保存文件；不挂载宿主目录或Docker socket，不传认证/模型Key。用标准库异步子进程调用可信Docker CLI，宿主不解析shell脚本。镜像由服务端固定，运行请求不安装/拉取。

写作用不能靠数据库回滚。执行意图在start前持久化，结果未知时中断、不自动重跑；清理失败保留名额，未知工作区要求人工确认。租约保护写回，不能保证命令在失租瞬间停止。旧Worker关闭仅清理自己的资源。

代价：额外模型延迟/费用、Docker依赖、卷需单独备份/迁移，通用部署无卷总容量硬配额。首轮只支持宿主Python Worker控制本机Docker，现有Compose不扩大权限；不承诺多租户恶意代码安全。

官方依据与选型细节见 [批准方案](../AGENT_POOL_BASH_PLAN.md)；操作见 [Bash指南](../BASH_TOOL.md)，验证见 [报告](../AGENT_POOL_BASH_REPORT.md)。
