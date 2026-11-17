import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { queryClient } from '../api/query';
import { SecretInput } from './SecretInput';
import { AddModelForm } from './AddModelForm';
import { NetworkKeySettings } from './NetworkKeySettings';

export function ModelKeySettings() {
  const catalog = useQuery({ queryKey: ['chat-models'], queryFn: () => api.models(), refetchInterval: 10000, retry: false });
  const [selected, setSelected] = useState(''); const [notice, setNotice] = useState(''); const [focusKey, setFocusKey] = useState(false);
  const [adding, setAdding] = useState(false);
  const items = catalog.data?.items ?? []; const model = items.find((item) => item.id === selected);
  async function added(id: string) {
    setNotice('模型已添加，请在下方输入 Key。'); setSelected(id); setFocusKey(true); setAdding(false);
    await queryClient.invalidateQueries({ queryKey: ['chat-models'] });
  }
  return <section className="model-key-settings" aria-label="模型 Key 设置">
    <h3>模型与 Key</h3>
    {items.length === 0 || adding ? <><AddModelForm onAdded={added}/>{items.length > 0 && <button className="text-button" onClick={() => setAdding(false)}>取消添加</button>}</> : <button className="text-button add-model-toggle" onClick={() => { setSelected(''); setNotice(''); setAdding(true); }}>添加模型</button>}
    {catalog.isPending && <p role="status">读取模型配置…</p>}
    {catalog.isError && <p className="context-error">无法读取模型配置，请检查后端连接。</p>}
    {catalog.isSuccess && items.length === 0 && <p className="field-hint">还没有模型。请在上方选择厂商、填写模型 ID 并添加配置。</p>}
    {items.length > 0 && <><label htmlFor="secret-target">选择凭据目标</label><select id="secret-target" value={selected} onChange={(event) => { setNotice(''); setFocusKey(false); setSelected(event.target.value); }}><option value="">选择一个模型配置</option>{items.map((item) => <option key={item.id} value={item.id}>{item.label} · {item.model}</option>)}</select>
      {model && <SecretInput key={`${model.id}:${model.credentialRevision}`} target={{ purpose: 'model.api_key', resourceId: model.id, expectedRevision: model.credentialRevision }} label="模型 API Key" description={`${model.provider} · ${model.model} · 凭据版本 ${model.credentialRevision} · ${model.ready ? '已配置，输入新 Key 可更换' : '尚未配置 Key'}`} onNotice={setNotice} onRefresh={() => queryClient.invalidateQueries({ queryKey: ['chat-models'] })} focusOnMount={focusKey}/>}</>}
    {notice && <p role="status" className="connection-notice">{notice}</p>}
    <NetworkKeySettings/>
  </section>;
}
