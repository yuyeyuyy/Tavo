import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { publicError } from '@tavo/shared';
import { api } from '../api/client';
import { modelProfileCreateSchema } from '../api/model-profiles';

export function AddModelForm({ onAdded }: { onAdded(id: string): Promise<void> }) {
  const providers = useQuery({ queryKey: ['model-providers'], queryFn: () => api.modelProviders(), retry: false });
  const active = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false); const [notice, setNotice] = useState('');
  const [provider, setProvider] = useState(''); const [model, setModel] = useState(''); const [label, setLabel] = useState('');
  useEffect(() => () => { active.current?.abort(); }, []);
  async function save(event: FormEvent) {
    event.preventDefault(); if (busy) return;
    const parsed = modelProfileCreateSchema.safeParse({ provider, model: model.trim(), ...(label.trim() ? { label: label.trim() } : {}) });
    if (!parsed.success) { setNotice('请选择厂商并填写有效的模型 ID。'); return; }
    const controller = new AbortController(); active.current = controller; setBusy(true); setNotice('');
    try {
      const added = await api.addModelProfile(parsed.data, controller.signal);
      if (!controller.signal.aborted) { setNotice('模型已添加，请在下方输入 Key。'); setModel(''); setLabel(''); await onAdded(added.id); }
    } catch (error) {
      if (!controller.signal.aborted) setNotice(publicError(error).message + ' 若结果不确定，请先刷新模型列表再操作。');
    } finally { if (active.current === controller) { active.current = null; setBusy(false); } }
  }
  return <form className="add-model-form" aria-label="添加模型配置" onSubmit={(event) => void save(event)}>
    <h4>添加模型</h4><p className="field-hint">选择厂商和模型，添加后在下方录入 Key，无需重启。</p>
    <label htmlFor="model-provider">模型厂商</label><select id="model-provider" value={provider} onChange={(event) => setProvider(event.target.value)} required disabled={busy || !providers.isSuccess}><option value="">{providers.isPending ? '读取厂商…' : '选择一家厂商'}</option>{providers.data?.items.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
    {providers.isError && <p role="alert" className="context-error">无法读取厂商，请检查后端是否已升级并连接。</p>}
    <label htmlFor="model-id">模型 ID</label><input id="model-id" value={model} onChange={(event) => setModel(event.target.value)} required maxLength={160} autoComplete="off" spellCheck={false} disabled={busy} placeholder="填写控制台中的文本模型 ID"/><p className="field-hint">{providers.data?.items.find((item) => item.id === provider)?.modelHint ?? '模型 ID 由服务商提供，和显示名称不同。'}</p>
    <label htmlFor="model-label">显示名称（可选）</label><input id="model-label" value={label} onChange={(event) => setLabel(event.target.value)} maxLength={80} autoComplete="off" disabled={busy} placeholder="例如：日常对话"/>
    <button className="settings-save" disabled={busy || !providers.isSuccess}>{busy ? '正在添加…' : '添加模型配置'}</button>
    {notice && <p role="status" className="connection-notice">{notice}</p>}
  </form>;
}
