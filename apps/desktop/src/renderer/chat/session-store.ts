import { createStore } from 'zustand/vanilla';
import { publicError } from '@tavo/shared';
import { api, type APIClient } from '../api/client';
import type { AgentMessage, AgentRun, AgentSession, SessionCreate } from '../api/sessions';
type SessionAPI = Pick<APIClient, 'createSession' | 'getSession' | 'sessionMessages' | 'sessionRun' | 'sendMessage' | 'cancelRun' | 'retryRun' | 'decide'>;
interface State {
    session: AgentSession | null;
    messages: AgentMessage[];
    run: AgentRun | null;
    pending: boolean;
    error: string | null;
    hasOlder: boolean;
    interacted: boolean;
    load(id: string): Promise<void>;
    send(text: string, settings: SessionCreate): Promise<boolean>;
    reset(): void;
    cancel(): Promise<void>;
    retry(): Promise<void>;
    decide(callId: string, action: 'approve' | 'reject'): Promise<void>;
    older(): Promise<void>;
    refresh(): Promise<void>;
    startWatching(): void;
    stopWatching(): void;
}
const active = (run: AgentRun | null) => !!run && ['pending', 'running', 'waiting_review'].includes(run.status);
const merge = (old: AgentMessage[], incoming: AgentMessage[]) => [...new Map([...old, ...incoming].map(m => [m.id, m])).values()].sort((a, b) => a.sequence - b.sequence);
export function createSessionStore(client: SessionAPI = api) {
    let generation = 0;
    let controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let watching = true;
    let selectedId: string | null = null;
    let refreshing = -1;
    const begin = () => { generation++; controller.abort(); controller = new AbortController(); clearTimeout(timer); return generation; };
    const current = (value: number) => value === generation;
    const schedule = () => { clearTimeout(timer); if (!watching || !store.getState().session)
        return; const run = store.getState().run; timer = setTimeout(() => void store.getState().refresh(), active(run) ? (run?.status === 'waiting_review' ? 1200 : 350) : 4000); };
    async function refresh() {
        const id = selectedId;
        const captured = generation;
        if (!id || refreshing === captured)
            return;
        refreshing = captured;
        try {
            const session = await client.getSession(id, controller.signal);
            const run = session.latestRunId ? await client.sessionRun(id, session.latestRunId, controller.signal) : null;
            let messages = current(captured) ? store.getState().messages : [];
            let after = messages.at(-1)?.sequence;
            do {
                const page = await client.sessionMessages(id, after === undefined ? {} : { afterSequence: after }, controller.signal);
                if (!current(captured))
                    return;
                messages = merge(messages, page.items);
                after = page.items.at(-1)?.sequence ?? after;
                if (!page.hasMore || !page.items.length || messages.length === page.items.length) {
                    if (store.getState().messages.length === 0)
                        store.setState({ hasOlder: page.hasMore });
                    break;
                }
            } while (current(captured));
            if (!current(captured))
                return;
            const calls = new Map(run?.toolCalls.map(c => [c.id, c]) ?? []);
            messages = messages.map(m => m.kind === 'tool_call' && calls.has(m.call.id) ? { ...m, call: calls.get(m.call.id)! } : m);
            store.setState({ session, run, messages, error: null });
        }
        catch (error) {
            if (current(captured))
                store.setState({ error: publicError(error).message });
        }
        finally {
            if (current(captured)) {
                refreshing = -1;
                schedule();
            }
        }
    }
    const store = createStore<State>((set, get) => ({
        session: null, messages: [], run: null, pending: false, error: null, hasOlder: false, interacted: false,
        async load(id) {
            const captured = begin();
            selectedId = id;
            set({ session: null, messages: [], run: null, pending: true, error: null, hasOlder: false, interacted: true });
            await refresh();
            if (current(captured))
                set({ pending: false });
        },
        async send(text, settings) {
            const value = text.trim();
            if (!value || value.length > 4000 || get().pending || active(get().run))
                return false;
            const captured = begin();
            set({ pending: true, error: null, interacted: true });
            try {
                let session = get().session;
                if (!session) {
                    session = selectedId ? await client.getSession(selectedId, controller.signal) : await client.createSession(settings, controller.signal);
                    if (!current(captured))
                        return false;
                    selectedId = session.id;
                    set({ session });
                }
                session = await client.getSession(session.id, controller.signal);
                if (!current(captured))
                    return false;
                const accepted = await client.sendMessage(session.id, { clientMessageId: crypto.randomUUID(), content: value, expectedRevision: session.revision }, controller.signal);
                if (!current(captured))
                    return false;
                set({ session: accepted.session, run: accepted.run, messages: merge(get().messages, [accepted.message]) });
                await refresh();
                return current(captured);
            }
            catch (error) {
                if (current(captured)) {
                    await refresh();
                    set({ error: publicError(error).message });
                }
                return false;
            }
            finally {
                if (current(captured)) {
                    set({ pending: false });
                    schedule();
                }
            }
        },
        reset() { begin(); selectedId = null; set({ session: null, messages: [], run: null, pending: false, error: null, hasOlder: false, interacted: true }); },
        async cancel() {
            const id = selectedId;
            const captured = begin();
            set({ pending: !!id, error: null });
            if (!id)
                return;
            try {
                const session = await client.getSession(id, controller.signal);
                if (!current(captured))
                    return;
                if (session.activeRunId) {
                    const run = await client.cancelRun(id, session.activeRunId);
                    if (current(captured))
                        set({ run });
                }
                await refresh();
            }
            catch (error) {
                if (current(captured))
                    set({ error: publicError(error).message });
            }
            finally {
                if (current(captured)) {
                    set({ pending: false });
                    schedule();
                }
            }
        },
        async retry() {
            const { session, run, pending } = get();
            if (!session || !run || pending || active(run))
                return;
            const captured = begin();
            set({ pending: true, error: null });
            try {
                const next = await client.retryRun(session.id, run.id);
                if (current(captured)) {
                    set({ run: next });
                    await refresh();
                }
            }
            catch (error) {
                if (current(captured))
                    set({ error: publicError(error).message });
            }
            finally {
                if (current(captured)) {
                    set({ pending: false });
                    schedule();
                }
            }
        },
        async decide(callId, action) {
            const { session, run, pending } = get();
            const call = run?.toolCalls.find(c => c.id === callId);
            if (!session || !call || pending || call.status !== 'waiting_review')
                return;
            const captured = begin();
            set({ pending: true, error: null });
            try {
                await client.decide(session.id, callId, { action, expectedRevision: call.reviewRevision });
                if (current(captured))
                    await refresh();
            }
            catch (error) {
                if (current(captured)) {
                    await refresh();
                    set({ error: publicError(error).message });
                }
            }
            finally {
                if (current(captured)) {
                    set({ pending: false });
                    schedule();
                }
            }
        },
        async older() {
            const { session, messages, pending, hasOlder } = get();
            if (!session || pending || !hasOlder || !messages.length)
                return;
            const captured = begin();
            set({ pending: true, error: null });
            try {
                const page = await client.sessionMessages(session.id, { beforeSequence: messages[0]!.sequence }, controller.signal);
                if (current(captured))
                    set({ messages: merge(page.items, get().messages), hasOlder: page.hasMore });
            }
            catch (error) {
                if (current(captured))
                    set({ error: publicError(error).message });
            }
            finally {
                if (current(captured)) {
                    set({ pending: false });
                    schedule();
                }
            }
        },
        refresh,
        startWatching() { watching = true; if (selectedId)
            void get().load(selectedId); },
        stopWatching() { watching = false; begin(); },
    }));
    return store;
}
