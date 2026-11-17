/** Only ordinary web URLs may leave the application, never OS/application protocols. */
export function safeWebUrl(value: string | undefined): string | null {
  if (!value || value.length > 4096 || [...value].some(char => char.charCodeAt(0) <= 32 || char.charCodeAt(0) === 127 || char === '\\')) return null;
  try {
    const url = new URL(value);
    if (!['https:', 'http:'].includes(url.protocol) || !url.hostname || url.username || url.password) return null;
    return url.href;
  } catch { return null; }
}

export function externalUrlFromApp(source: string, trusted: string, target: string): string | null {
  try {
    const from = new URL(source); const expected = new URL(trusted);
    from.hash = ''; expected.hash = '';
    return from.href === expected.href ? safeWebUrl(target) : null;
  } catch { return null; }
}
