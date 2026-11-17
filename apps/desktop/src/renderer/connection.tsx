import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from './api/client';
import { queryClient } from './api/query';
import { Icon } from './icons';
import { ModelKeySettings } from './security/ModelKeySettings';
import { AppearanceSettings } from './appearance';
export function useConnection() { return useQuery({ queryKey: ['backend-status'], queryFn: () => api.systemStatus(), refetchInterval: 5000, retry: false }); }
export function connectionLabel(connection: ReturnType<typeof useConnection>) { return connection.isError ? '后端未连接' : connection.isPending ? '正在连接' : '后端已连接'; }
export function ConnectionSettings({ onClose, conversationSettings }: { onClose(): void; conversationSettings?: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const profile = useQuery({ queryKey: ['desktop-profile'], queryFn: () => api.profile() });
  const connection = useConnection(); const [notice, setNotice] = useState(''); const [busy, setBusy] = useState(false);
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => element?.close(); }, []);
  function close() { dialog.current?.close(); onClose(); }
  async function reconnect() {
    setBusy(true); setNotice('');
    try { const result = await window.desktop.reconnect(); setNotice(result.ok ? '连接成功。' : result.message); await queryClient.invalidateQueries(); }
    catch { setNotice('无法连接后端，请检查服务和连接配置。'); } finally { setBusy(false); }
  }
  async function configure(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const backendUrl = String(new FormData(event.currentTarget).get('backendUrl') ?? ''); setBusy(true); setNotice('');
    try { const result = await window.desktop.configure({ backendUrl }); if (!result.ok) setNotice(result.message); }
    catch { setNotice('无法保存连接配置，请检查地址后重试。'); } finally { setBusy(false); }
  }
  return <dialog ref={dialog} className="connection-dialog" aria-labelledby="connection-title" onCancel={(event) => { event.preventDefault(); close(); }}>
    <div className="dialog-heading"><h2 id="connection-title">设置</h2><button className="icon-button" aria-label="关闭设置" onClick={close}><Icon name="close"/></button></div>
    <AppearanceSettings/>{conversationSettings}
    <h3 className="settings-section-title">连接与模型配置</h3>
    <div className="connection-summary"><span className={`status-dot ${connection.isSuccess ? 'online' : ''}`}/><span data-testid="settings-backend-state">{connectionLabel(connection)}</span><button className="text-button" disabled={busy} onClick={() => void reconnect()}>{busy ? '连接中…' : '重新连接'}</button></div>
    {profile.isPending && <p className="subtle" role="status">读取连接配置…</p>}{profile.isError && <p role="alert">无法读取连接配置，请重新打开设置。</p>}
    {profile.data && <form key={profile.data.backendUrl} onSubmit={(event) => void configure(event)}><label htmlFor="backend-url">后端地址</label><input id="backend-url" name="backendUrl" type="url" defaultValue={profile.data.backendUrl} required disabled={busy}/><p className="field-hint">保存时会选择此服务的客户端凭据文件，并重新加载窗口。当前对话不会保留。</p><button className="settings-save" disabled={busy}>保存连接</button></form>}
    {notice && <p className="connection-notice" role="status">{notice}</p>}<ModelKeySettings/><p className="dialog-footnote">模型和 Key 都可在这里配置，Key 加密上传并在服务端加密保存。保存不会请求外部模型。Mock 演示无需连接后端。</p>
  </dialog>;
}
