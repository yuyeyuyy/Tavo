# ADR0011：Bash在Python后端启动时准备

日期：2026-11-16。状态：用户确认并实施。方案见BASH_STARTUP_PLAN.md，验证见BASH_STARTUP_REPORT.md。

## 背景与选择

用户要求默认启用Bash，并明确选择Python后端启动时准备Docker；Docker服务未启动则提示并停止后端。此前环境缺失仍允许后端聊天的行为据此调整。

可选常驻容器后续使用`docker exec`，或启动创建短命预热容器、实际调用继续独立容器。选择后者，复用已有执行/清理机制与隔离参数，避免引入共享容器中的跨Session状态、后台进程和卷切换问题。

## 决策

- Services初始化先等待prepare，Docker CLI、daemon、隔离能力与镜像验证均成功后执行固定的Bash健康命令。确认预热容器清理后才启动Worker/监听请求。
- 缺失默认镜像仅从仓库已有可信Dockerfile构建，限180秒/输出64KiB；自定义镜像缺失失败。构建阶段可下载基础镜像，不接受模型控制的构建目录、标签或Docker参数。
- 预热复用实际执行的非root/无网络/只读根目录/资源限制，限15秒，不挂任何Session卷或宿主目录。随机名字加归属标签，`--rm`后仍核验清理。
- 错误输出固定中文原因，不暴露Docker原始stderr、构建日志或凭据。启动失败释放已分配Services，取消等待清理后继续传播。
- 不启动系统Docker服务。init、Key管理和轮换独立；管理员关闭Bash则跳过。现有Compose无Docker CLI/socket，显式关闭Bash，不扩大宿主权限。

## 后果

宿主后端默认依赖Docker就绪，首次构建可能需要网络和等待。预热只验证启动时刻，不保证后续Docker持续可用；每次调用的审核、租约、权限与执行清理仍必需。Session权限、工作卷、数据库迁移和模型功能不变。

## 启动进度补充

用户随后要求可见进度与完成提醒。原warning级别和捕获Docker输出导致成功路径静默，且缓存镜像与自动清理使Docker Desktop难以展示准备过程。采用固定阶段+每5秒等待秒数，而非直接流出Docker日志，保持私有路径/认证不进日志；不伪造层下载百分比。stderr立即flush，不依赖INFO日志配置。HTTP就绪使用实际监听后的Uvicorn提示，沙箱就绪和应用初始化完成单独表达。验证见BASH_STARTUP_PROGRESS_REPORT.md。
