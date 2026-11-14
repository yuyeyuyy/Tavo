import { StrictMode, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { AppError, publicError, type BusinessEvent, type Connection, type ErrorCode, type MockConfig, type Settings, type Snapshot } from '@tavo/shared';
import { useDashboard, useBackendEvents, useUI } from './store';
import { api } from './api/client';
import { queryClient } from './api/query';
import { QueryClientProvider, useInfiniteQuery, useQuery } from '@tanstack/react-query';
import './styles.css';

const time = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—';
const statusLabels = { disconnected: '已断开', connecting: '连接中', connected: '已连接', expired: '认证过期', error: '连接异常' };
const jobLabels = { pending: '等待执行', running: '同步中', succeeded: '同步成功', failed: '同步失败', cancelled: '已取消' };
const eventLabels = { 'account.connected': '账户已连接', 'account.disconnected': '账户已断开', 'sync.started': '同步开始', 'sync.completed': '同步完成', 'sync.failed': '同步失败', 'data.changed': '账户数据变化', 'sync.retry_scheduled': '已安排重试', 'sync.cancelled': '同步已取消', 'agent.started': '分析开始', 'agent.progress': '分析进度', 'agent.completed': '分析完成', 'agent.failed': '分析失败' };
const nav = { overview: '总览', connections: '账户连接', activity: '活动记录', agent: 'Agent', settings: '设置' };
type Tab = keyof typeof nav;
const message = (code: ErrorCode) => publicError(new AppError(code)).message;

function EventList({ events, data }: { events: BusinessEvent[]; data: Snapshot }) {
  const { busy, perform } = useDashboard();
  return <div className="events-panel">{events.length === 0 ? <div className="empty">暂无业务事件。</div> : events.map((event) => <article className="event" key={event.eventId} data-testid="business-event">
    <div className="event-line"><span className="event-symbol">↳</span><div className="event-title"><h3>{eventLabels[event.eventType]} · {data.connections.find((c) => c.id === event.accountId)?.displayName ?? '已删除的账户'}</h3><p className="muted small">#{event.sequence} · {time(event.occurredAt)}</p></div>
      {event.eventType === 'data.changed' && <button className="secondary" disabled={busy} onClick={() => void perform(() => api.analyze({ eventId: event.eventId }))}>{event.analysis ? '重新分析' : '运行模拟分析'}</button>}
    </div>
    {event.payload.current && <p className="event-data">{event.payload.current.balance} {event.payload.current.currency} · 用量 {event.payload.current.usageUnits} · {event.payload.current.resourceStatus === 'healthy' ? '资源正常' : '资源降级'}</p>}
    {event.payload.errorCode && <p className="event-data">{message(event.payload.errorCode)}</p>}
    {event.analysis && <div className="analysis" data-testid="analysis"><strong>{event.analysis.summary}</strong><ul>{event.analysis.evidence.map((item) => <li key={item}>{item}</li>)}</ul><p>{event.analysis.recommendation}</p></div>}
    <details className="event-details"><summary>关联与结构化数据</summary><p>同步 / 操作 ID：<code>{event.correlationId}</code></p><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>
  </article>)}</div>;
}
function MockEditor({ connection }: { connection: Connection }) {
  const [config, setConfig] = useState<MockConfig>(connection.mock);
  const { busy, perform } = useDashboard();
  return <details className="mock-editor"><summary>模拟数据与故障设置</summary><form onSubmit={(event) => { event.preventDefault(); void perform(() => api.configureMock({ connectionId: connection.id, config })); }}>
    <div className="form-grid">
      <label>模拟余额<input aria-label={`${connection.displayName} 模拟余额`} value={config.balance} pattern="[0-9]{1,12}\.[0-9]{2}" required onChange={(e) => setConfig({ ...config, balance: e.target.value })}/></label>
      <label>币种<select value={config.currency} onChange={(e) => setConfig({ ...config, currency: e.target.value as MockConfig['currency'] })}><option>USD</option><option>CNY</option></select></label>
      <label>模拟用量<input type="number" min="0" max="1000000000" step="1" value={config.usageUnits} onChange={(e) => setConfig({ ...config, usageUnits: Number(e.target.value) })}/></label>
      <label>资源状态<select value={config.resourceStatus} onChange={(e) => setConfig({ ...config, resourceStatus: e.target.value as MockConfig['resourceStatus'] })}><option value="healthy">正常</option><option value="degraded">降级</option></select></label>
      <label className="full-width">故障场景<select aria-label={`${connection.displayName} 故障场景`} value={config.scenario} onChange={(e) => setConfig({ ...config, scenario: e.target.value as MockConfig['scenario'] })}>
        <option value="success">正常同步</option><option value="network_once">首次网络错误，随后成功</option><option value="rate_limit_once">首次 429 限流，随后成功</option><option value="server_once">首次服务器错误，随后成功</option><option value="timeout">请求超时</option><option value="auth">401 认证过期</option><option value="forbidden">403 权限不足</option><option value="invalid">无效响应</option>
      </select></label>
    </div><p className="muted small">保存后手动同步即可验证变化。保存会取消该账户当前任务；所有配置仅作用于 Mock。</p><button className="secondary" disabled={busy}>保存模拟配置</button>
  </form></details>;
}
function Connections({ data }: { data: Snapshot }) {
  const [displayName, setName] = useState(''); const [externalAccountId, setExternalId] = useState('');
  const { busy, perform } = useDashboard();
  async function add(event: FormEvent) {
    event.preventDefault();
    const submitted = { connectorId: 'mock' as const, displayName, externalAccountId };
    if (await perform(() => api.addConnection(submitted))) {
      // Query/SSE can render the new account before the mutation finishes refreshing.
      // Clear only the submitted values, preserving any newer user input.
      setName((current) => current === submitted.displayName ? '' : current);
      setExternalId((current) => current === submitted.externalAccountId ? '' : current);
    }
  }
  return <>
    <div className="section-heading"><h2>支持的连接器</h2><span className="badge">只读</span></div>
    {data.connectors.map((connector) => <div className="connector-summary" key={connector.id}><strong>{connector.name}</strong><span className="muted small">{connector.capabilities.join(' · ')}</span></div>)}
    <form className="connection-form" onSubmit={(event) => void add(event)}><h2>添加 Mock 账户</h2><div className="form-grid">
      <label>显示名称<input aria-label="显示名称" disabled={busy} value={displayName} onChange={(e) => setName(e.target.value)} maxLength={100} required placeholder="例如：学习账户 A"/></label>
      <label>模拟账户标识<input aria-label="模拟账户标识" disabled={busy} value={externalAccountId} onChange={(e) => setExternalId(e.target.value)} maxLength={80} pattern="[a-zA-Z0-9:_-]+" required placeholder="例如：demo-a"/></label>
    </div><p className="muted small">模拟凭据由后端生成并加密保存在服务端，客户端不读取凭据原文。每个标识只能有一个未删除连接。</p><button className="primary" disabled={busy}>{busy ? '处理中…' : '添加账户'}</button></form>
    <div className="section-heading"><h2>账户连接 <span>{data.connections.length}</span></h2></div>
    {!data.connections.length && <div className="empty">尚未添加账户连接。</div>}
    {data.connections.map((connection) => {
      const working = data.jobs.some((job) => job.connectionId === connection.id && ['pending', 'running'].includes(job.status));
      return <article className="connection-card" key={connection.id} data-testid="connection-card">
        <div className="connection-heading"><div><h3>{connection.displayName}</h3><p className="muted small">{connection.connectorId} / {connection.externalAccountId}</p></div><span className={`status-pill ${connection.status}`}>{statusLabels[connection.status]}</span></div>
        <div className="connection-meta"><span>{connection.credentialConfigured ? '模拟凭据已配置' : '凭据未配置'}</span><span>最近成功同步：{time(connection.lastSyncAt)}</span></div>
        {connection.errorCode && <p className="inline-error">{message(connection.errorCode)}</p>}
        <div className="button-row"><button className="secondary" disabled={busy || working || !connection.credentialConfigured || !['connected', 'error'].includes(connection.status)} onClick={() => void perform(() => api.syncConnection({ connectionId: connection.id }))}>{working ? '任务进行中' : '同步账户'}</button>
          {working && <button className="secondary" disabled={busy} onClick={() => void perform(() => api.cancelSync({ connectionId: connection.id }))}>取消同步</button>}
          <button className="secondary" disabled={busy} onClick={() => void perform(() => api.reconnect({ connectionId: connection.id }))}>{connection.status === 'connected' ? '更新模拟凭据' : '重新连接'}</button>
          <button className="secondary" disabled={busy || connection.status === 'disconnected'} onClick={() => void perform(() => api.disconnect({ connectionId: connection.id }))}>断开连接</button>
          <button className="secondary danger" disabled={busy} onClick={() => void perform(() => api.removeConnection({ connectionId: connection.id }))}>删除连接</button>
        </div><MockEditor connection={connection}/>
      </article>;
    })}
  </>;
}
function Overview({ data, openConnections }: { data: Snapshot; openConnections: () => void }) {
  return <><div className="stat-grid"><article><span>已连接账户</span><strong>{data.connections.filter((c) => c.status === 'connected').length}</strong></article><article><span>进行中的任务</span><strong>{data.jobs.filter((j) => ['pending', 'running'].includes(j.status)).length}</strong></article><article><span>最近同步</span><strong className="stat-text">{data.jobs[0] ? jobLabels[data.jobs[0].status] : '暂无记录'}</strong></article></div>
    <div className="section-heading"><h2>最新账户数据</h2><button className="secondary" onClick={openConnections}>管理连接</button></div>
    {!data.connections.length ? <div className="empty"><strong>从添加账户开始</strong><p>添加 Mock 连接并同步，查看本地数据与变化事件。</p></div> : <div className="account-grid">{data.connections.map((connection) => {
      const current = data.data.find((row) => row.connectionId === connection.id);
      return <article className="account-card" key={connection.id}><div className="card-top"><h3>{connection.displayName}</h3><span className="badge">模拟数据 · {statusLabels[connection.status]}</span></div>
        {current ? <><div className="balance"><span>{current.currency}</span> {current.balance}</div><p className="muted">用量 {current.usageUnits} · {current.resourceStatus === 'healthy' ? '资源正常' : '资源降级'}</p><div className="card-footer"><span>采集于 {time(current.updatedAt)}</span><span>Mock Connector</span></div></> : <p className="muted">尚无同步数据</p>}
        {connection.errorCode && <p className="inline-error">{message(connection.errorCode)} 已保留最近成功数据。</p>}
      </article>;
    })}</div>}
    <div className="section-heading"><h2>最近业务事件</h2><span className="muted small">规则分析 · 无模型调用</span></div><EventList events={data.businessEvents.slice(0, 12)} data={data}/>
  </>;
}
function Activity({ data }: { data: Snapshot }) {
  const [correlation, setCorrelation] = useState('');
  const history = useInfiniteQuery({ queryKey: ['event-history', correlation], initialPageParam: undefined as number | undefined, queryFn: ({ pageParam }) => api.events({ before: pageParam, correlationId: correlation }), getNextPageParam: (last) => last.nextCursor ?? undefined });
  return <><div className="section-heading"><h2>同步任务历史</h2><span className="muted small">任务最近 200 条；事件支持翻页</span></div>
    {!data.jobs.length ? <div className="empty">尚无同步任务。</div> : <div className="job-table">{data.jobs.map((job) => <article key={job.id} data-testid="sync-job"><div className="connection-heading"><strong>{data.connections.find((c) => c.id === job.connectionId)?.displayName ?? '已删除的账户'}</strong><span className={`task-status ${job.status}`}>{jobLabels[job.status]}</span></div><p>尝试 {job.attempts} / {job.maxAttempts} · 重试 {job.retryCount} 次 · {job.trigger === 'manual' ? '手动' : '定时'}</p><p className="muted small">创建 {time(job.createdAt)} · 开始 {time(job.startedAt)} · 结束 {time(job.finishedAt)}</p>{job.status === 'pending' && <p className="muted small">下次执行 {time(job.nextAttemptAt)}</p>}{job.errorCode && <p className="inline-error">{message(job.errorCode)}</p>}<button className="text-button" onClick={() => setCorrelation(job.id)}>查看关联事件</button></article>)}</div>}
    <div className="section-heading"><h2>持久化业务事件</h2>{correlation && <button className="secondary" onClick={() => setCorrelation('')}>显示全部事件</button>}</div>
    {correlation && <p className="muted small">关联 ID：{correlation}</p>}<EventList events={history.data?.pages.flatMap((page) => page.items) ?? data.businessEvents.filter((event) => !correlation || event.correlationId === correlation)} data={data}/>{history.hasNextPage && <button className="secondary" disabled={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>加载更早事件</button>}
    {(data.tasks.length > 0 || data.events.length > 0) && <LegacyActivity data={data}/>}
  </>;
}
function LegacyActivity({ data }: { data: Snapshot }) {
  const { busy, perform } = useDashboard();
  return <details className="settings-panel"><summary>Phase 0 历史记录</summary><p className="muted small">旧同步任务、余额事件和分析保留在后端迁移归档中。新操作使用账户连接与可靠同步引擎。</p>
    {data.tasks.map((task) => <p key={task.id}>旧同步任务 · {task.status} · {time(task.startedAt)}</p>)}
    {data.events.map((event) => <article className="event" key={event.id}><h3>历史模拟余额 {event.balance} {event.currency}</h3><p className="muted small">{time(event.occurredAt)}</p><button className="secondary" disabled={busy} onClick={() => void perform(() => api.analyze({ eventId: event.id }))}>分析历史事件</button>{event.analysis && <div className="analysis"><strong>{event.analysis.summary}</strong><ul>{event.analysis.evidence.map((item) => <li key={item}>{item}</li>)}</ul></div>}</article>)}
  </details>;
}
function SettingsPanel({ data }: { data: Snapshot }) {
  const [settings, setSettings] = useState<Settings>(data.settings); const { busy, perform } = useDashboard();
  return <><form className="settings-panel" onSubmit={(event) => { event.preventDefault(); void perform(() => api.updateSettings(settings)); }}><h2>同步与启动</h2>
    <label className="checkbox-label"><input type="checkbox" checked={settings.autoSync} onChange={(event) => setSettings({ ...settings, autoSync: event.target.checked })}/>服务端运行期间定时同步</label>
    <label>同步间隔（秒，10–86400）<input aria-label="同步间隔" type="number" min="10" max="86400" required value={settings.syncIntervalSeconds} onChange={(event) => setSettings({ ...settings, syncIntervalSeconds: Number(event.target.value) })}/></label>
    <label className="checkbox-label"><input type="checkbox" checked={settings.startMinimized} onChange={(event) => setSettings({ ...settings, startMinimized: event.target.checked })}/>下次启动隐藏窗口，通过 Dock 打开</label>
    <p className="muted small">桌面客户端退出后，独立后端仍继续同步。网络、超时、限流和临时服务器错误最多尝试 3 次；认证或权限错误停止自动重试。</p><button className="primary" disabled={busy}>保存设置</button>
  </form><section className="settings-panel"><h2>本地数据与安全</h2><p>数据目录：<code className="storage-location">{data.security.dataLocation}</code></p><p>服务端安全存储：<strong>{data.security.available ? '可用' : '不可用'}</strong> · {data.security.backend}</p><p className="muted">第三方凭据只以服务端密文保存；后端不可用时拒绝保存和读取。删除连接会清理凭据和最新数据，保留任务与事件历史。SQLite 业务数据库未整体加密。</p></section></>;
}
function ConnectionBar() {
  const profile = useQuery({ queryKey: ['desktop-profile'], queryFn: () => api.profile(), refetchInterval: 5000 });
  const status = useQuery({ queryKey: ['backend-status'], queryFn: () => api.systemStatus(), refetchInterval: 5000 });
  const stream = useUI((state) => state.streamConnected);
  const [url, setUrl] = useState(''); const [notice, setNotice] = useState('');
  async function reconnect() {
    const result = await window.desktop.reconnect();
    setNotice(result.ok ? '连接成功。' : result.message); await queryClient.invalidateQueries();
  }
  async function configure(event: FormEvent) {
    event.preventDefault(); const result = await window.desktop.configure({ backendUrl: url });
    if (!result.ok) setNotice(result.message);
  }
  return <section className="settings-panel" aria-label="后端连接"><div className="connection-heading"><strong data-testid="backend-state">{status.isSuccess ? '后端已连接' : status.isPending ? '正在连接后端…' : '后端不可用 · 显示最近缓存'}</strong><button className="secondary" onClick={() => void reconnect()}>重新连接后端</button></div><p className="muted small">{profile.data?.backendUrl ?? '读取连接配置…'} · {stream ? '实时事件已连接' : '实时事件重连中'} · {status.data?.workerActive ? 'Worker 运行中' : 'Worker 等待或未启动'}</p>{notice && <p role="status">{notice}</p>}<details><summary>配置后端地址</summary><form onSubmit={(event) => void configure(event)}><label>服务地址<input aria-label="后端地址" type="url" required placeholder={profile.data?.backendUrl} value={url} onChange={(event) => setUrl(event.target.value)}/></label><p className="muted small">本机允许 HTTP；远程地址需要 HTTPS。提交后在原生窗口中选择此服务专用的客户端凭据文件。</p><button className="secondary">配置连接</button></form></details></section>;
}
function AgentPanel() {
  const runs = useQuery({ queryKey: ['agent-runs'], queryFn: () => api.runs(), refetchInterval: 1000 });
  return <><div className="section-heading"><h2>Agent 运行记录</h2><span className="badge">Python LangGraph · Mock</span></div><p className="muted">在总览或活动记录中选择“运行模拟分析”，服务端创建持久化任务。关闭桌面后任务仍会继续。</p>{runs.isPending && <p role="status">加载运行记录…</p>}{runs.isError && <p role="alert">无法读取运行记录。</p>}{runs.data?.items.length === 0 && <p className="empty">暂无分析任务。</p>}{runs.data?.items.map((run) => <article className="event" key={run.id} data-testid="agent-run"><h3>{{ pending: '等待分析', running: '分析中', succeeded: '分析完成', failed: '分析失败', cancelled: '已取消' }[run.status]} · {run.progress}%</h3><progress value={run.progress} max={100}/><p className="muted small">任务 {run.id} · 事件 {run.eventId} · {time(run.createdAt)}</p>{run.result && <div className="analysis"><strong>{run.result.summary}</strong><ul>{run.result.evidence.map((item) => <li key={item}>{item}</li>)}</ul><p>{run.result.recommendation}</p></div>}{run.errorCode && <p className="inline-error">分析失败，请查看任务状态后重试。</p>}</article>)}</>;
}
function App() {
  const [tab, setTab] = useState<Tab>('overview'); const { data, busy, error, refresh } = useDashboard();
  useBackendEvents(!!data);
  return <div className="app-shell antialiased"><aside className="sidebar"><div className="brand"><span className="brand-mark">t</span><span>Tavo<span className="version"> / Client</span></span></div><p className="nav-label">WORKSPACE</p>
    <nav>{(Object.keys(nav) as Tab[]).map((key) => <button className={tab === key ? 'nav-active nav-button' : 'nav-button'} key={key} onClick={() => setTab(key)}>{nav[key]}</button>)}</nav><div className="sidebar-note">本地账户工作空间<br/>所有账户均为 Mock 演示。</div><div className="local-indicator"><span className="dot"/>Local-first</div></aside>
    <main><header><span>工作空间 <span className="slash">/</span>{nav[tab]}</span><span className="badge">MOCK MODE</span></header><div className="content"><ConnectionBar/><div className="page-title"><div><p className="eyebrow">YOUR PERSONAL WORKSPACE</p><h1>{nav[tab]}<span className="title-dot">.</span></h1><p className="muted">安全连接、可靠同步，理解你的账户变化。</p></div>{busy && <span role="status" className="muted">处理中…</span>}</div>
      <div className="demo-notice"><span>◈</span><div><strong>本地模拟环境</strong><p>账户、凭据与故障均为模拟；同步任务和业务事件真实持久化。无需真实 API Key。</p></div></div>
      {error && <div role="alert" className="error">{error}<button disabled={busy} onClick={() => void refresh()}>重新加载</button></div>}
      {!data ? <p role="status" className="empty">等待后端连接…</p> : <>{tab === 'overview' && <Overview data={data} openConnections={() => setTab('connections')}/>} {tab === 'connections' && <Connections data={data}/>} {tab === 'activity' && <Activity data={data}/>} {tab === 'settings' && <SettingsPanel data={data}/>} {tab === 'agent' && <AgentPanel/>}</>}
      <footer><span><span className="dot"/>SQLite 本地持久化</span><span>Independent Backend / FastAPI</span></footer></div></main></div>;
}
createRoot(document.getElementById('root')!).render(<StrictMode><QueryClientProvider client={queryClient}><App/></QueryClientProvider></StrictMode>);
