import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { create } from 'zustand';
import { AppError, publicError, type Response, type Snapshot } from '@tavo/shared';
import { api } from './api/client';
import { queryClient } from './api/query';

// Zustand 仅保存交互状态；服务端数据由 TanStack Query 缓存。
export const useUI = create<{ busy: boolean; error: string | null; streamConnected: boolean }>(() => ({ busy: false, error: null, streamConnected: false }));
const refresh = async () => { await queryClient.invalidateQueries(); };
const perform = async (action: () => Promise<Response>): Promise<boolean> => {
  if (useUI.getState().busy) return false;
  useUI.setState({ busy: true, error: null });
  try {
    const result = await action();
    if (result.ok) { queryClient.setQueryData(['snapshot'], result.data); await refresh(); return true; }
    useUI.setState({ error: result.error.message }); await refresh(); return false;
  } catch (error) { useUI.setState({ error: publicError(error).message }); return false; }
  finally { useUI.setState({ busy: false }); }
};
export function useDashboard() {
  const ui = useUI();
  const query = useQuery({ queryKey: ['snapshot'], queryFn: ({ signal }) => api.getSnapshot(signal), refetchInterval: 5000 });
  return { ...ui, data: query.data ?? null, offline: query.isError, loading: query.isPending, error: ui.error ?? (query.error ? publicError(query.error instanceof AppError ? query.error : new AppError('BACKEND_UNAVAILABLE')).message : null), perform, refresh };
}
export function useBackendEvents(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    const initial = queryClient.getQueryData<Snapshot>(['snapshot']);
    const sequence = Math.max(0, ...(initial?.businessEvents.map((event) => event.sequence) ?? []));
    void api.subscribe(sequence, controller.signal, () => {
      if (!timer) timer = setTimeout(() => { timer = undefined; void refresh(); }, 100);
    }, (connected) => useUI.setState({ streamConnected: connected }));
    return () => { controller.abort(); clearTimeout(timer); };
  }, [enabled]);
}
