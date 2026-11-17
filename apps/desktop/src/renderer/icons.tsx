export function Icon({ name, size = 20 }: { name: 'arrow' | 'plus' | 'settings' | 'close' | 'stop' | 'retry' | 'chevron'; size?: number }) {
  const paths = {
    arrow: <path d="M12 19V5m-6 6 6-6 6 6"/>, plus: <path d="M12 5v14M5 12h14"/>,
    settings: <><path d="m10 3-.7 2.4-2 .9L5 5.7 3 9l1.7 1.8v2.4L3 15l2 3.3 2.3-.6 2 .9L10 21h4l.7-2.4 2-.9 2.3.6 2-3.3-1.7-1.8v-2.4L21 9l-2-3.3-2.3.6-2-.9L14 3Z"/><circle cx="12" cy="12" r="3"/></>,
    close: <path d="m6 6 12 12M6 18 18 6"/>, stop: <rect x="7" y="7" width="10" height="10" rx="1" fill="currentColor" stroke="none"/>,
    retry: <path d="M4 10a8 8 0 1 1 1 7M4 4v6h6"/>, chevron: <path d="m9 5 7 7-7 7"/>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
export function TavoMark({ size = 30 }: { size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M16 3v26M3 16h26M7 7l18 18M7 25 25 7" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"/><circle cx="16" cy="16" r="6" fill="currentColor"/></svg>;
}
