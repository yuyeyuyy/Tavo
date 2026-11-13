import { create } from 'zustand';
import type { Response, Snapshot } from '@tavo/shared';

interface DashboardState {
  data: Snapshot | null;
  busy: boolean;
  error: string | null;
  perform: (action: () => Promise<Response>) => Promise<boolean>;
  refresh: () => Promise<void>;
}
// 只缓存展示状态；持久化的唯一事实来源为 Core / SQLite。
export const useDashboard = create<DashboardState>((set, get) => ({
  data: null, busy: false, error: null,
  refresh: async () => {
    if (get().busy) return;
    try { const result = await window.tavo.snapshot(); if (result.ok && !get().busy) set({ data: result.data }); else if (!result.ok) set({ error: result.error.message }); }
    catch { set({ error: '无法连接本地服务，请重新启动应用。' }); }
  },
  perform: async (action) => {
    if (get().busy) return false;
    set({ busy: true, error: null });
    try {
      const result = await action();
      if (result.ok) { set({ data: result.data }); return true; }
      else {
        set({ error: result.error.message });
        // 同步失败也刷新已落盘的任务状态；保留最后一次成功的账户数据。
        const latest = await window.tavo.snapshot();
        if (latest.ok) set({ data: latest.data });
      }
    } catch { set({ error: '无法连接本地服务，请重新启动应用。' }); }
    finally { set({ busy: false }); }
    return false;
  },
}));
