# Python后端启动时准备Bash沙箱

日期：2026-11-16。状态：用户已确认并实施，实际验证见 [BASH_STARTUP_REPORT.md](BASH_STARTUP_REPORT.md)。用户已明确选择“Python后端启动时准备容器；Docker未启动就提示并退出后端”。这覆盖之前Docker不可用仍允许启动聊天的默认行为；桌面进程继续独立。

## 推荐行为

`serve`、独立`worker`和直接ASGI启动在进入可用状态前完成：

1. Bash启用时检查Docker CLI、daemon连通性及所需隔离/资源支持。Docker未启动或不可访问时明确打印“Docker服务未启动或无法连接，请启动Docker Desktop / Docker Engine后重试；后端已停止”，非零退出，不进入监听/任务领取。
2. 检查执行镜像。缺少默认`tavo-bash:local`时，用项目现有`backend/sandbox/Dockerfile`自动构建；构建可能下载已有架构所用基础镜像，设等待与输出上限，失败/超时则明确提示并停止。不引入Python/npm依赖。管理员自定义镜像缺失不擅自改成默认镜像，明确报错。
3. 使用现有非root/无外网/只读根目录/资源限制创建短命预热容器，运行固定的Bash健康命令，不接模型输入、不挂任何Session卷或宿主目录。校验成功并确认容器清理后，后端才继续启动。
4. 实际工具调用保持每次独立容器，Session文件仍由既有命名卷持久化。预热容器不是常驻执行环境，启动后不会保留一个空闲容器或让不同Session共用它。
5. `init`、模型Key管理、密钥轮换不依赖Docker；管理员显式`TAVO_BASH_ENABLED=0`保留为部署开关。默认启用时执行严格启动检查。启动过程中失败也须关闭DB/HTTP客户端/执行器资源。

## 两种方案与取舍

| 方式 | 取舍 |
| --- | --- |
| 启动常驻容器，后续docker exec复用 | 可减少创建开销，但需重做Session卷归属、后台进程/超时清理与恢复，扩大当前生命周期改动 |
| 启动创建短命容器预热，工具仍独立容器（推荐） | 启动即验证Docker/镜像/Bash真实可执行，复用现有隔离和审计方式，改动较小；不保留常驻容器 |

## 接口与影响范围

- `BashRunner.prepare()`负责编排严格启动检查、默认镜像准备及预热；`probe()`仍是运行时只读状态检查，不在每次轮询时构建/启动容器。复用CLI封装和容器安全参数，避免两套权限配置。
- `Services.initialize()`在其他初始化/Worker开始前等待prepare；`main.py` lifespan与`cli.py` worker确保失败路径释放资源，输出固定安全提示，不泄露Docker原始错误、凭据或后端私有路径。
- 细分启动原因（CLI缺失、daemon不可达、镜像/构建失败、隔离不支持、预热/清理失败），失败不伪装成后端可用；需要时同步OpenAPI/公开错误映射。
- 既有Compose容器没有Docker CLI/socket，显式配置`TAVO_BASH_ENABLED=0`维持原部署的聊天/只读工具，不为启动检查挂宿主socket或扩大容器权限。宿主Python后端默认仍启用并严格检查Docker。
- 无数据库迁移、原有Session/Key/冻结权限和工具审核不变，不自动启动Docker Desktop/daemon、不修改系统服务。
- 更新BASH_TOOL、README、架构/ADR、学习/评测和AGENTS的最新行为说明。

## 验证计划

- 单元：Docker缺失/不可达、隔离不支持、已有镜像复用、默认镜像缺失自动构建、自定义缺失拒绝、构建超时/失败、预热失败/清理失败、初始化资源关闭、禁用Bash与非运行CLI不依赖Docker。
- 独立进程：将测试子进程指向不存在的Docker socket，验证明确提示、非零退出、没有监听或领取任务；不关闭或改动用户当前Docker服务。
- 真实Docker：启动即实际创建/运行/回收预热容器，随后原Bash工具/持久会话仍工作；检查测试标签资源不遗留。缺失镜像构建使用隔离测试标签/配置，不删除用户默认镜像。
- 执行lint/typecheck/相关单元及全量测试/build、backend/model/Bash smoke；Compose区分配置、构建和实际业务验证。若没有客户端改动，不重复改变原生方法或UI流程；实际执行范围/结果明确记录。

小练习：解释为什么“docker命令存在”和“Docker daemon可用”是两个检查，以及为什么启动预热成功不能替代每次命令执行前的权限/租约校验。
