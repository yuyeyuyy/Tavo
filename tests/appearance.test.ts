import { describe, expect, it } from 'vitest';
import { parseTheme, readTheme, resolveTheme, THEME_STORAGE_KEY } from '../apps/desktop/src/renderer/appearance';
import { externalUrlFromApp, safeWebUrl } from '../apps/desktop/src/shared/links';

describe('appearance preferences', () => {
  it('defaults to system and rejects invalid persisted values', () => {
    for (const value of [null, '', 'auto', '{"theme":"dark"}', 'LIGHT']) expect(parseTheme(value)).toBe('system');
    expect(readTheme()).toBe('system');
    expect(readTheme({ getItem() { throw new Error('Storage denied'); } })).toBe('system');
    expect(readTheme({ getItem(key) { expect(key).toBe(THEME_STORAGE_KEY); return 'light'; } })).toBe('light');
  });
  it('follows OS only when selected and respects explicit preferences', () => {
    expect(resolveTheme('system', false)).toBe('light');
    expect(resolveTheme('system', true)).toBe('dark');
    expect(resolveTheme('light', true)).toBe('light');
    expect(resolveTheme('dark', false)).toBe('dark');
  });
});
describe('Markdown external link boundary', () => {
  it.each([undefined, '', 'javascript:alert(1)', 'data:text/html,hello', 'file:///etc/passwd', 'command:run', 'tavo://app/index.html', '/api/v1/sessions', '//example.com', 'https://name:pass@example.com', 'https://example.com\n', 'https://example.com\\@evil.test', 'https://', 'https://example.com/' + 'x'.repeat(4096)])('rejects %s', value => {
    expect(safeWebUrl(value)).toBeNull();
  });
  it('preserves ordinary HTTPS paths and queries without app authentication', () => {
    expect(safeWebUrl('https://example.com/docs?a=1#part')).toBe('https://example.com/docs?a=1#part');
    expect(safeWebUrl('http://example.com')).toBe('http://example.com/');
  });
  it('checks the exact app document for Main, including query/port/path', () => {
    const target = 'https://example.com/docs';
    expect(externalUrlFromApp('tavo://app/index.html#note', 'tavo://app/index.html', target)).toBe(target);
    for (const source of ['tavo://evil/index.html', 'tavo://app/other.html', 'tavo://app/index.html?other', 'https://example.com/']) expect(externalUrlFromApp(source, 'tavo://app/index.html', target)).toBeNull();
    expect(externalUrlFromApp('http://localhost:5174/', 'http://localhost:5173/', target)).toBeNull();
    expect(externalUrlFromApp('tavo://app/index.html', 'tavo://app/index.html', 'file:///tmp/a')).toBeNull();
  });
});
