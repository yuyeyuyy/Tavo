import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { queryClient } from '../api/query';
import { SecretInput } from './SecretInput';

export function NetworkKeySettings() {
  const catalog = useQuery({ queryKey: ['sandbox-network-targets'], queryFn: () => api.networkTargets(), retry: false });
  const [selected, setSelected] = useState('');
  const [notice, setNotice] = useState('');
  const targets = catalog.data?.items.filter(item => item.credentialRequired) ?? [];
  const target = targets.find(item => item.id === selected);
  return <section aria-label="沙箱 API 凭据">
    <h3>沙箱 API 凭据</h3>
    <p className="field-hint">{catalog.data?.mode === 'proxy' ? '通过受控出口访问已授权目标。凭据由代理注入，脚本无法读取。' : '受控网络未启用；目标域名、操作与依赖来源由服务端配置。'}</p>
    {targets.length > 0 && <><label htmlFor="sandbox-secret-target">授权 API 目标</label><select id="sandbox-secret-target" value={selected} onChange={event => { setSelected(event.target.value); setNotice(''); }}><option value="">选择服务端已配置的目标</option>{targets.map(item => <option key={item.id} value={item.id}>{item.id} · {item.hostname}</option>)}</select></>}
    {target && <SecretInput key={`${target.id}:${target.credentialRevision}`} target={{ purpose: 'sandbox.api_key', resourceId: target.id, expectedRevision: target.credentialRevision }} label="API Token" description={`${target.hostname} · ${target.ready ? '已配置，输入可更换' : '尚未配置'}；模型聊天 Key 不会自动用于此目标。`} onNotice={setNotice} onRefresh={() => queryClient.invalidateQueries({ queryKey: ['sandbox-network-targets'] })}/>}
    {catalog.isError && <p className="field-hint">无法读取沙箱网络配置，请检查后端连接。</p>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}
