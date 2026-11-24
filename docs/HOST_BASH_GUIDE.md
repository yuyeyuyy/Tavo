# 沙箱与宿主机命令使用指南

2026-11-17，Context策略5，数据库head为0008_host_bash。设计见[方案](HOST_SANDBOX_TOOLS_PLAN.md)，实现与实际验收见[报告](HOST_SANDBOX_TOOLS_REPORT.md)。

## 升级与默认行为

停止旧API与Worker，备份数据库、私有密钥和需要保留的Docker会话工作卷，再在仓库根目录执行：

```bash
pnpm backend:init
pnpm dev:backend
```

另一终端`pnpm dev`启动客户端。新建对话即可同时使用沙箱与宿主机入口，默认完全访问，由Agent判断执行位置，无用户开关。旧会话和已冻结Run保留Docker权限；它们不会因升级获得宿主机能力。0001–0007不修改，不删除原始数据、Key或工作卷。

## 两个目标

| 工具 | 适合任务 | 实际范围 |
| --- | --- | --- |
| sandbox（沙箱） | Python脚本、数据处理、临时文件、测试、网页抓取 | 现有非root Docker、会话工作卷和受控HTTPS出口/DoH |
| host_bash（宿主机 Bash） | 系统状态、进程、日志、Docker管理、真实项目部署 | Python后端当前OS用户与实际文件/网络权限，无容器隔离 |

Docker 沙箱统一使用 `sandbox`。旧工具名 `bash` 保留在历史记录，尚未执行的旧调用会被拒绝。宿主是 Python 后端或 Worker 所在的环境，原生 Mac 后端操作 Mac，普通 Compose 后端操作自身容器。宿主实现面向 macOS/Linux POSIX，实际平台验证以报告为准。

两个目标共用完全访问/帮我审批/请求审批三档。全访问自动执行，代审对每条命令做独立模型复核，询问档每条命令先确认；沙箱大范围版本提交继续确认具体diff。其他合法工具不增加命令审批。

沙箱文件不会自动出现在宿主目录，宿主文件也不会自动挂进沙箱。真实部署使用真实项目cwd；宿主写入直接生效，没有沙箱版本提交和自动回滚。取消能终止受管命令，不撤销已经写入的文件、外部API效果或由Docker/系统服务管理器启动的持久服务。

## 宿主配置与边界

复用`TAVO_SANDBOX_CONFIG`指定的管理员TOML，新增[host]节，详见backend/sandbox.example.toml。默认启用，管理员可设置`TAVO_HOST_BASH_ENABLED=0`或`host.enabled=false`关闭；与`TAVO_BASH_ENABLED`（Docker沙箱）独立。原Docker启动检查/预热不改变：默认沙箱开启时Docker不可用仍阻止后端启动；管理员显式关闭沙箱后，宿主入口可独立运行。

默认cwd为后端启动目录，推荐管理员配置绝对项目路径。每条命令cwd可为真实绝对目录，或相对此默认目录的路径；不存在、非目录、Tavo私有数据/密钥及已知敏感目录拒绝。command限制32768 UTF-8字节，默认60秒/最多300秒，捕获stdout/stderr各64KiB、公开各2KiB并标记truncated。每数据库同时最多一个宿主执行，占位纳入AgentPool。宿主没有Docker的CPU/内存/PID/cgroup配额，不把这些资源数值冒充宿主保证。

只运行`/bin/bash --noprofile --norc`，不自动添加sudo或处理管理员口令，不继承BASH_ENV等启动钩子。子进程环境只含PATH、HOME、LANG、TMPDIR；Tavo API Key、会话Token、密钥环路径等不注入，SSH_AUTH_SOCK及其他部署环境也不会自动透传。现有OS身份/配置能完成的授权部署可正常使用，额外部署凭据需要单独配置，不把聊天Key当部署凭据。

已保存凭据、bootstrap/Fernet密钥及常见私钥头在输出中出现时，拒绝输出；跨读取块、超出捕获上限和执行期间新增凭据均有检查。结果发布前重新读取当前凭据，无法核验时不返回原始输出。原始敏感输出不写ToolResult或普通日志。

这些措施防止误泄漏，**不能将当前用户任意Bash变成秘密隔离环境**。恶意编码、间接读取、原始Docker权限、主动脱离进程组等不能靠命令黑名单或模型审核彻底约束。此功能适用于信任本地Owner及任务执行流程的部署，不适用于不可信多租户；更强边界需独立OS账号/Runner并限制Docker权限。

## 取消与恢复

每次执行先持久记录意图、owner/fence与占位，再启动独立进程组和受监督Bash。父管道断开、心跳停止、超时、取消时先TERM后KILL并等待回收；默认心跳1秒/watchdog10秒，并按当前Worker租约缩短有效期限，避免旧执行长时间超过租约。正常命令结束也回收同组后台子进程。

重启不会重复结果未知的部署命令。恢复只向能验证监督器路径、执行ID和进程组归属的进程发送终止信号，绝不只靠旧PID误杀别的进程。清理未确认保留容量；若输出管道显示进程已脱离受管组，记录unknown_detached并保留占位，需人工核验，不能靠重启或直接删记录声称清理完成。主动脱离进程组且关闭输出的恶意程序不在监督强保证内。

stdout/stderr及真实退出码、超时、错误、耗时、executionTarget/executionId写入原工具结果。host结果sandboxId为空；审计显示宿主位置与cwd，审批绑定具体参数。正常对话正文只讲任务与结果。

Mock下可在临时目录测试`host: pwd`、`bash: python --version`；前者真实宿主，后者真实沙箱，Mock只替代模型。真实模型按任务自然选择。不要用真实项目部署或敏感文件作为功能验收样本。
