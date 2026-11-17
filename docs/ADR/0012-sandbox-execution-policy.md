# ADR0012：实用脚本执行与受控网络能力

日期：2026-11-17。状态：用户已确认BASH_EXECUTION_POLICY_PLAN.md，增量实现。此前只读/3000字节/强制模型复核和0006每调用容器约定由本决策更新；原队列、LangGraph、租约、凭据框架保留。

## 选择

统一TOML配置资源和Run预算，工具参数解析、上下文、结果与客户端校验一并升级。冻结策略3，旧策略不重新解释；当前更严格的配置仍可收紧恢复任务。

审核分为基础参数与权限、风险分类、必要模型复核、具体人工决定。LOW不依赖模型自审查；任意Bash/Python并不能靠语法分析证明安全，MEDIUM的授权由容器、版本化工作区及受控出口执行。关键词只提供保守提示，不作为主要边界。

SandboxManager维护单执行容量、Run容器复用、空闲/取消/失租/崩溃恢复。工作副本停止执行并核验后提交到Session版本；高风险diff批准前不替换原有文件。容器、后台进程和授权不跨Run自动继承，文件跨Run保持兼容。未知作用不自动重放。

## 网络方案取舍

普通bridge加HTTP_PROXY不能防止脚本直连、自定义DNS和隧道；独立内部网络+防火墙可以实现，但增加宿主网桥/路由/DNS部署要求。选择执行容器继续network=none，通过只读命名卷的AF_UNIX连接固定代码出口代理。真实原型与容器集成验证该通道，HTTPX原生支持UDS运输。[HTTPX官方文档](https://www.python-httpx.org/advanced/transports/)

出口代理是应用请求网关，不提供CONNECT或任意TCP转发。按精确目标、路径、方法、能力授权，验证全部解析地址并固定IP连接、保留SNI/TLS域名校验；重定向逐跳检查，同源凭据不扩大路径，跨源不转发认证。Docker普通bridge本身不具备这种限制，只有不执行用户代码的代理容器拥有出网路由。[Docker bridge说明](https://docs.docker.com/engine/network/drivers/bridge/)、[HTTPCore SNI说明](https://www.encode.io/httpcore/extensions/)

默认关闭网络；管理员选择系统解析或固定端点的Cloudflare DoH。Fake-IP/保留地址不被当作公网，不能靠放行该网段适配代理。DoH也是管理员明确选择，不接受脚本提供解析器。[Cloudflare DoH说明](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)

POST等请求精确审批，凭据由专用sandbox.api_key用途绑定后代理注入；批准不重跑父脚本。持久请求意图与结果加密，未知inflight请求不自动再发。配额先预留，封闭UDS新请求并等待已接收操作结束后核销；无法核验用量保留额度，回收不能重置次数。

## Python与边界

基础开发依赖以哈希锁文件装入专用镜像，宿主依赖不变。额外安装只接收批准来源/版本/哈希的wheel，临时区检查下载、解压和文件数量，再使用无索引/无依赖解析的pip写入任务目录。锁定依赖仍需信任其代码。[pip可重复安装说明](https://pip.pypa.io/en/stable/topics/repeatable-installs/)

Docker命名卷不提供本实现可跨平台承诺的总容量硬配额；工作区监测、提交限制和单文件上限不能替代它。敏感任务副本保留待核验，用户负责备份数据、密钥及工作卷。当前单所有者本机服务，不宣称恶意多租户安全、任意SDK透明联网、回滚外部副作用或公网生产部署。操作与实测范围见指南和验收报告。
