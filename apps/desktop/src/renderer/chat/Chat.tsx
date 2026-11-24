import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent, type MouseEvent, type ReactNode, type RefObject } from 'react';
import { useStore } from 'zustand';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { ConnectionSettings, connectionLabel, useConnection } from '../connection';
import { Icon, TavoMark } from '../icons';
import { mockTransport } from './mock';
import { createSessionStore } from './session-store';
import type { AgentMessage, AgentRun, AgentSession, SessionCreate } from '../api/sessions';
import { AppError, errorCodeSchema, publicError } from '@tavo/shared';
import { queryClient } from '../api/query';
import { createChatStore } from './store';
import { MESSAGE_LIMIT, type ChatMessage } from './types';
import { MarkdownMessage } from './MarkdownMessage';
const suggestions = ['介绍一下 Tavo', '记忆和上下文有什么区别？', 'Agent 是怎么调用工具的？'];
function Composer({ draft, setDraft, busy, send, stop, inputRef }: {
    draft: string;
    setDraft(value: string): void;
    busy: boolean;
    send(): void;
    stop(): void;
    inputRef: RefObject<HTMLTextAreaElement | null>;
}) {
    const composing = useRef(false);
    useLayoutEffect(() => { inputRef.current?.focus(); }, [inputRef]);
    useLayoutEffect(() => { const input = inputRef.current; if (input) {
        input.style.height = 'auto';
        input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
    } }, [draft, inputRef]);
    function submit(event: FormEvent) { event.preventDefault(); if (!busy)
        send(); }
    function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && !composing.current && event.nativeEvent.keyCode !== 229) {
            event.preventDefault();
            if (!busy)
                send();
        }
    }
    return <form className="composer" onSubmit={submit} aria-label="发送消息"><textarea ref={inputRef} aria-label="消息" placeholder="发送消息…" rows={1} maxLength={MESSAGE_LIMIT} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={keyDown} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}/>{busy ? <button type="button" className="send-button" aria-label="停止回复" onClick={stop}><Icon name="stop"/></button> : <button className="send-button" aria-label="发送消息" disabled={!draft.trim()}><Icon name="arrow"/></button>}</form>;
}
function MessageList({ messages, retry, mode, pendingLabel, retryDisabled = false, children, older }: {
    messages: ChatMessage[];
    retry(): void;
    mode: string;
    pendingLabel?: string;
    retryDisabled?: boolean;
    children?: ReactNode;
    older?: ReactNode;
}) {
    const scroll = useRef<HTMLDivElement>(null);
    const following = useRef(true);
    useLayoutEffect(() => { const element = scroll.current; if (element && following.current)
        element.scrollTop = element.scrollHeight; }, [messages]);
    return <div className="conversation-scroll" ref={scroll} onScroll={() => { const element = scroll.current; if (element)
        following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96; }}><div className="message-stack" role="log" aria-label="对话消息" aria-live="polite" aria-relevant="additions text">{older}{messages.map((message, index) => <article className={`message message-${message.role}`} key={message.id} data-testid={`${message.role}-message`} aria-label={message.role === 'user' ? '你' : 'Tavo'}>
    {message.role === 'assistant' && <div className="message-avatar"><TavoMark size={17}/></div>}<div className="message-body">
    {message.status === 'pending' ? <div className="reply-pending" role="status"><span className="thinking-dot"/>{pendingLabel ?? (mode === 'MOCK' ? '正在准备演示回复' : '等待模型回复')}<span className="thinking-dots">…</span></div> : message.status === 'cancelled' ? <p className="reply-cancelled">已停止回复</p> : <div className={`message-content ${message.status === 'failed' ? 'reply-error' : ''}`} role={message.status === 'failed' ? 'alert' : undefined}>{message.role === 'assistant' && message.status === 'complete' ? <MarkdownMessage content={message.content}/> : message.content}</div>}
    {message.truncated && <p className="reply-cancelled">回复已达到输出限制，内容可能不完整。</p>}
    {index === messages.length - 1 && ['failed', 'cancelled'].includes(message.status) && <button className="retry-button" disabled={retryDisabled} onClick={retry}><Icon name="retry" size={14}/>{message.status === 'failed' ? '重试' : '重新生成'}</button>}
  </div></article>)}{children}</div></div>;
}
const isActive = (run: AgentRun | null) => !!run && ['pending', 'running', 'waiting_review'].includes(run.status);
function errorText(code: string | null) { const value = errorCodeSchema.safeParse(code); return publicError(new AppError(value.success ? value.data : 'INTERNAL')).message; }
function displayMessages(messages: AgentMessage[], run: AgentRun | null): ChatMessage[] {
    const result: ChatMessage[] = messages.flatMap(m => m.kind === 'user' || m.kind === 'assistant' ? [{ id: m.id, role: m.kind, content: m.content, createdAt: m.createdAt, status: 'complete' as const, ...(m.kind === 'assistant' ? { truncated: m.truncated } : {}) }] : []);
    const hasFinal = messages.some(m => m.kind === 'assistant' && m.runId === run?.id && m.final);
    if (run && run.status !== 'succeeded' && !(isActive(run) && hasFinal))
        result.push({ id: 'run-' + run.id, role: 'assistant', content: run.errorCode ? errorText(run.errorCode) : '', createdAt: run.createdAt, status: run.status === 'cancelled' ? 'cancelled' : run.status === 'failed' || run.status === 'interrupted' ? 'failed' : 'pending' });
    return result;
}
function ApprovalDetails({ sessionId, callId, index, pending, decide }: { sessionId: string; callId: string; index: number; pending: boolean; decide(id: string, action: 'approve' | 'reject'): void }) {
    const preview = useQuery({ queryKey: ['tool-approval', sessionId, callId], queryFn: () => api.approvalPreview(sessionId, callId), retry: false });
    return <div className="tool-output">
        {preview.isPending && <p role="status">正在读取具体操作…</p>}
        {preview.isError && <p className="context-error">无法读取操作详情，请刷新后再批准。</p>}
        {preview.data && <><p>{preview.data.description}</p><pre>{JSON.stringify(preview.data.details, null, 2)}</pre><p className="field-hint">操作摘要：{preview.data.digest}</p></>}
        <div className="tool-review-actions"><button disabled={pending || !preview.data} onClick={() => decide(callId, 'approve')} aria-label={`批准工具调用 ${index + 1}`}>批准此操作</button><button disabled={pending} onClick={() => decide(callId, 'reject')} aria-label={`拒绝工具调用 ${index + 1}`}>拒绝</button></div>
    </div>;
}
function ToolTimeline({ run, messages, pending, decide }: { run: AgentRun | null; messages: AgentMessage[]; pending: boolean; decide(id: string, action: 'approve' | 'reject'): void }) {
    const calls = [...new Map([
        ...messages.flatMap(message => message.kind === 'tool_call' ? [message.call] : []),
        ...(run?.toolCalls ?? []),
    ].map(call => [call.id, call])).values()].reverse();
    // Put pending decisions first so older audit entries cannot hide the action.
    calls.sort((a, b) => Number(b.status === 'waiting_review') - Number(a.status === 'waiting_review'));
    if (!calls.length) return null;
    const results = new Map(messages.flatMap(message => message.kind === 'tool_result' ? [[message.result.toolCallId, message.result] as const] : []));
    const labels: Record<string, string> = { proposed: '待审核', waiting_review: '等待确认', approved: '已批准', running: '执行中', succeeded: '已完成', failed: '执行失败', denied: '已拒绝', cancelled: '已取消' };
    return (
        <details className="tool-timeline" id="tool-review" open={run?.status === 'waiting_review' || undefined}>
            <summary>工具记录 · {calls.length}{run?.status === 'waiting_review' ? ' · 等待确认' : ''}</summary>
            {calls.map(call => {
                const index = run?.toolCalls.findIndex(current => current.id === call.id) ?? -1;
                const decision = call.decisions.at(-1);
                const result = results.get(call.id);
                return (
                    <section key={call.id} className="tool-card">
                        <div className="tool-title"><strong>{call.name === 'host_bash' ? '宿主机 Bash' : ['sandbox','bash'].includes(call.name) ? '沙箱' : call.name}</strong><span>{labels[call.status]}</span></div>
                        {call.name === 'host_bash' && <p className="field-hint">执行于 Python 后端所在主机 · 当前用户权限 · 无容器隔离</p>}
                        {call.status === 'waiting_review' && index >= 0 && (call.name === 'http_request' || (call.attempts > 0 && /^[a-f0-9]{64}$/.test(decision?.summary ?? ''))) && run && <ApprovalDetails sessionId={run.sessionId} callId={call.id} index={index} pending={pending} decide={decide}/> }
                        {call.status === 'waiting_review' && index >= 0 && call.name !== 'http_request' && !(call.attempts > 0 && /^[a-f0-9]{64}$/.test(decision?.summary ?? '')) && (
                            <div className="tool-review-actions">
                                <button disabled={pending} onClick={() => decide(call.id, 'approve')} aria-label={`批准工具调用 ${index + 1}`}>批准</button>
                                <button disabled={pending} onClick={() => decide(call.id, 'reject')} aria-label={`拒绝工具调用 ${index + 1}`}>拒绝</button>
                            </div>
                        )}
                        <pre>{JSON.stringify(call.arguments, null, 2)}</pre>
                        {decision && <p className="field-hint">{decision.source === 'human' ? '用户决定' : decision.source==='model'?'模型自审查':decision.source==='mock'?'Mock 自审查':'规则审核'} · {decision.action === 'allow' ? '通过' : decision.action === 'ask' ? '需要确认' : '拒绝'}</p>}
                        {call.decisions.filter(d=>d.source==='model'||d.source==='mock').map(d=><p className="field-hint" key={d.id}>{d.source==='mock'?'Mock 复核':'模型复核'}：{d.summary??d.action}</p>)}
                        {result && <div className="tool-output">{result.errorCode&&<p>{errorText(result.errorCode)}</p>}<pre>{JSON.stringify(result.output,null,2)}</pre></div>}
                    </section>
                );
            })}
        </details>
    );
}
export function Chat() {
    const [serverStore] = useState(() => createSessionStore());
    const server = useStore(serverStore);
    const [localStore] = useState(() => createChatStore(mockTransport));
    const local = useStore(localStore);
    const [targetProfile, setTargetProfile] = useState('mock');
    const [targetReview, setTargetReview] = useState<SessionCreate['reviewMode']>('full_access');
    const [offline, setOffline] = useState(false);
    const [draft, setDraft] = useState('');
    const [settingsOpen, setSettingsOpen] = useState(false);
    const [listOpen, setListOpen] = useState(false);
    const [extraSessions, setExtraSessions] = useState<AgentSession[]>([]);
    const [extraCursor, setExtraCursor] = useState<string | null | undefined>(undefined);
    const [listError, setListError] = useState('');
    const input = useRef<HTMLTextAreaElement>(null);
    const settingsOpener = useRef<HTMLButtonElement>(null);
    const restored = useRef('');
    const connection = useConnection();
    const profile = useQuery({ queryKey: ['desktop-profile'], queryFn: () => api.profile() });
    const runtime = useQuery({queryKey:['agent-runtime'],queryFn:()=>api.agentRuntime(),refetchInterval:10000,retry:false});
    const catalog = useQuery({ queryKey: ['chat-models'], queryFn: () => api.models(), refetchInterval: 10000, retry: false });
    const sessions = useQuery({ queryKey: ['sessions'], queryFn: () => api.listSessions(), enabled: connection.isSuccess, refetchInterval: 10000, retry: false });
    const backendUrl = profile.data?.backendUrl;
    useEffect(() => { serverStore.getState().startWatching(); return () => serverStore.getState().stopWatching(); }, [serverStore]);
    useEffect(() => { if (!backendUrl || !sessions.data || restored.current === backendUrl)
        return; restored.current = backendUrl; if (serverStore.getState().interacted)
        return; let saved: string | null = null; try {
        saved = localStorage.getItem('tavo.session:' + backendUrl);
    }
    catch { /* Storage is optional; server history remains authoritative. */ } const id = saved ?? [...sessions.data.items].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))[0]?.id; if (id)
        void serverStore.getState().load(id); }, [backendUrl, sessions.data, serverStore]);
    useEffect(() => { if (!backendUrl || !server.session)
        return; try {
        localStorage.setItem('tavo.session:' + backendUrl, server.session.id);
    }
    catch { /* No message/key storage fallback. */ } }, [backendUrl, server.session]);
    const selected = server.session?.profileId ?? targetProfile;
    const review = server.session?.reviewMode ?? targetReview;
    const model = catalog.data?.items.find(m => m.id === selected);
    const mode = selected === 'mock' ? 'MOCK' : model?.label ?? selected;
    const messages = offline ? local.messages : displayMessages(server.messages, server.run);
    const empty = messages.length === 0;
    const busy = offline ? !!local.activeRequestId : server.pending || isActive(server.run);
    function reset() { setTargetProfile(selected); setTargetReview(review); serverStore.getState().reset(); localStore.getState().newConversation(); setOffline(false); setDraft(''); setListOpen(false); if (backendUrl)
        try {
            localStorage.removeItem('tavo.session:' + backendUrl);
        }
        catch { /* Optional selection. */ } if (!settingsOpen) input.current?.focus(); }
    function changeModel(value: string) { reset(); setTargetProfile(value); }
    function changeReview(value: SessionCreate['reviewMode']) { reset(); setTargetReview(value); }
    async function sendText(text: string) {
        if (offline || (connection.isError && selected === 'mock' && !server.session)) {
            if (!offline) {
                serverStore.getState().reset();
                setOffline(true);
            }
            if (localStore.getState().send(text)) {
                setDraft('');
                input.current?.focus();
            }
            return;
        }
        const sent = await serverStore.getState().send(text, { profileId: selected, reviewMode: review });
        if (sent) {
            setDraft(current => current === text ? '' : current);
            input.current?.focus();
            await queryClient.invalidateQueries({ queryKey: ['sessions'] });
        }
    }
    function openSettings(event: MouseEvent<HTMLButtonElement>) { settingsOpener.current = event.currentTarget; setSettingsOpen(true); }
    function closeSettings() { setSettingsOpen(false); settingsOpener.current?.focus(); }
    async function moreSessions() { const cursor = extraCursor === undefined ? sessions.data?.nextCursor : extraCursor; if (!cursor)
        return; try {
        const page = await api.listSessions(cursor);
        setExtraSessions(old => [...new Map([...old, ...page.items].map(s => [s.id, s])).values()]);
        setExtraCursor(page.nextCursor);
    }
    catch (error) {
        setListError(publicError(error).message);
    } }
    const conversationSettings = <section className="conversation-settings" aria-label="对话设置"><h3>对话</h3>
      <label htmlFor="chat-model">模型</label><select id="chat-model" aria-label="选择模型（切换会开始新对话）" value={selected} onChange={event => changeModel(event.target.value)}><option value="mock">Mock 演示</option>{catalog.data?.items.map(m => <option key={m.id} value={m.id} disabled={!m.ready}>{m.label} · {m.model}{m.ready ? '' : '（未配置 Key）'}</option>)}{selected !== 'mock' && !model && <option value={selected} disabled>模型不可用</option>}</select>
      <label htmlFor="command-permission">命令权限</label><select id="command-permission" aria-label="命令权限（切换会开始新对话）" value={review} onChange={event => changeReview(event.target.value as SessionCreate['reviewMode'])}><option value="full_access">完全访问权限</option><option value="auto_review">帮我审批</option><option value="request_approval">请求审批</option>{!['full_access', 'auto_review', 'request_approval'].includes(review) && <option value={review}>旧会话权限（{review}）</option>}</select>
      <p className="field-hint">切换模型或命令权限会开始新对话，原会话保留在会话列表中。</p>
      {catalog.isError && <p className="context-error">模型目录不可用，请检查后端连接。</p>}
      <details className="runtime-details"><summary>运行状态</summary><p className="field-hint">{offline ? '离线 Mock 演示 · 对话未保存到后端' : `${selected === 'mock' ? '服务端 Mock' : mode} · 会话保存在所连后端`}</p>{runtime.data && <p className="field-hint">沙箱：{runtime.data.bash.available ? '可用' : runtime.data.bash.enabled ? '不可用（Docker/镜像）' : '服务端未启用'}；宿主机命令：{(server.session?.executionScope ?? 'host_and_sandbox') === 'host_and_sandbox' && runtime.data.hostBash?.available ? '后端主机当前用户权限，无容器隔离' : '当前会话未提供'}。</p>}{server.run?.context && server.run.context.droppedTurns > 0 && <p className="field-hint">本轮省略较早的 {server.run.context.droppedTurns} 轮上下文，完整历史仍保留。</p>}</details>
    </section>;
    const composer = <Composer draft={draft} setDraft={setDraft} busy={busy} send={() => void sendText(draft)} stop={() => { if (offline) localStore.getState().stop(); else void serverStore.getState().cancel(); }} inputRef={input}/>;
    const allSessions = [...new Map([...(sessions.data?.items ?? []), ...extraSessions].map(s => [s.id, s])).values()];
    return <div className="chat-shell">
      <header className="chat-header"><a className="brand" href="#" aria-label="Tavo 首页" onClick={event => { event.preventDefault(); reset(); }}><TavoMark size={23}/><span>tavo</span></a>{selected === 'mock' && <span className="mode-badge">{offline ? '离线 Mock' : 'Mock 演示'}</span>}<div className="header-actions">{!offline && server.run?.status === 'waiting_review' && <button className="review-alert" onClick={() => document.getElementById('tool-review')?.scrollIntoView({ block: 'nearest' })}>待确认</button>}<button className="text-button" aria-label="会话列表" onClick={() => setListOpen(!listOpen)}>会话</button><button className="new-chat" onClick={reset}><Icon name="plus" size={17}/><span>新对话</span></button><span className="header-divider"/><button className="connection-badge" data-testid="backend-state" onClick={openSettings} aria-label={`连接设置：${connectionLabel(connection)}`}><span className={`status-dot ${connection.isSuccess ? 'online' : ''}`}/><span>{connectionLabel(connection)}</span></button><button className="icon-button" aria-label="打开设置" onClick={openSettings}><Icon name="settings" size={19}/></button></div></header>
      {listOpen && <aside className="session-panel" aria-label="会话列表"><h2>会话</h2>{sessions.isPending && <p>读取会话…</p>}{sessions.isError && <p role="alert">无法读取后端会话，历史数据仍保留。</p>}{allSessions.map(s => <button key={s.id} aria-label={`打开会话 ${s.title}`} aria-current={s.id === server.session?.id ? 'true' : undefined} onClick={() => { setOffline(false); setDraft(''); setListOpen(false); void serverStore.getState().load(s.id); }}>{s.title}{s.activeRunId ? ' · 进行中' : ''}</button>)}{allSessions.length === 0 && sessions.isSuccess && <p>暂无已保存会话。</p>}{(extraCursor === undefined ? sessions.data?.nextCursor : extraCursor) && <button onClick={() => void moreSessions()}>更多会话</button>}{listError && <p role="alert">{listError}</p>}</aside>}
      <main className={`chat-main ${empty ? 'is-empty' : ''}`}>
        {empty ? <div className="welcome"><div className="welcome-content"><div className="welcome-mark"><TavoMark size={35}/></div><h1>今天，想聊点什么？</h1><p className="welcome-description">从一个问题开始。</p><div className="suggestions" aria-label="示例问题">{suggestions.map(text => <button key={text} onClick={() => void sendText(text)}>{text}<Icon name="chevron" size={13}/></button>)}</div>{server.pending && <p role="status">正在读取会话…</p>}</div></div> : <MessageList messages={messages} retry={() => { if (offline) localStore.getState().retry(); else void serverStore.getState().retry(); }} mode={mode} retryDisabled={!offline && server.pending} pendingLabel={!offline && server.run?.status === 'waiting_review' ? '等待操作确认' : undefined} older={!offline && server.hasOlder && <button className="load-history" disabled={server.pending} onClick={() => void serverStore.getState().older()}>加载更早消息</button>}>{!offline && <ToolTimeline run={server.run} messages={server.messages} pending={server.pending} decide={(id, action) => void serverStore.getState().decide(id, action)}/>}</MessageList>}
        <div className="composer-dock">{(offline ? local.error : server.error) && <p role="alert" className="context-error">{offline ? local.error : server.error}</p>}{composer}</div>
      </main>
      {settingsOpen && <ConnectionSettings onClose={closeSettings} conversationSettings={conversationSettings}/>}
    </div>;
}
