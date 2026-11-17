import { useEffect, useRef, useState, type FormEvent } from 'react';
import { publicError } from '@tavo/shared';
import type { SecretTarget } from './contracts';
import { submitSecret } from './secret-submission';

// A reusable credential form: no model catalog, chat, global state or plaintext callback.
export function SecretInput({ target, label, description, onNotice, onRefresh, focusOnMount = false }: { target: SecretTarget; label: string; description: string; onNotice(message: string): void; onRefresh(): Promise<void>; focusOnMount?: boolean }) {
  const input = useRef<HTMLInputElement>(null); const active = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const supported = globalThis.isSecureContext && !!globalThis.crypto?.subtle;
  const identity = `secret-${target.purpose}-${target.resourceId}`;
  useEffect(() => { if (focusOnMount) { input.current?.focus(); input.current?.scrollIntoView({ block: 'nearest' }); } }, [focusOnMount]);
  useEffect(() => { const element = input.current; return () => { if (element) element.value = ''; active.current?.abort(); active.current = null; }; }, []);
  async function save(event: FormEvent) {
    event.preventDefault(); if (busy || !input.current) return;
    const controller = new AbortController(); active.current = controller; setBusy(true); onNotice('');
    try {
      await submitSecret(input.current, target, controller.signal);
      if (!controller.signal.aborted) { onNotice('凭据已加密保存到服务端。'); await onRefresh(); }
    } catch (error) {
      if (!controller.signal.aborted) { onNotice(publicError(error).message + ' 请重新输入。'); await onRefresh(); }
    } finally { if (active.current === controller) { active.current = null; setBusy(false); } }
  }
  return <form className="secret-input" onSubmit={(event) => void save(event)}><p className="field-hint">{description}</p><label htmlFor={identity}>{label}</label><input ref={input} id={identity} name={identity} type="password" required maxLength={8192} autoComplete="off" spellCheck={false} autoCapitalize="none" disabled={busy || !supported} placeholder="粘贴凭据，仅用于本次加密提交"/><p className="field-hint">提交后立即清空输入，不回显已保存的凭据。保存不会请求外部模型。</p>{!supported && <p role="alert" className="context-error">当前环境不支持加密提交，请使用服务端终端录入。</p>}<button className="settings-save" disabled={busy || !supported}>{busy ? '正在加密保存…' : '加密保存 Key'}</button></form>;
}
