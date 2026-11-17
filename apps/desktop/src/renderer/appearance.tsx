import { useEffect } from 'react';
import { createStore, useStore } from 'zustand';

export type ThemePreference = 'system' | 'light' | 'dark';
export const THEME_STORAGE_KEY = 'tavo.theme';
export function parseTheme(value: string | null): ThemePreference {
  return value === 'light' || value === 'dark' ? value : 'system';
}
export function resolveTheme(preference: ThemePreference, systemDark: boolean): 'light' | 'dark' {
  return preference === 'system' ? systemDark ? 'dark' : 'light' : preference;
}
export function readTheme(storage?: Pick<Storage, 'getItem'>): ThemePreference {
  try { return parseTheme(storage?.getItem(THEME_STORAGE_KEY) ?? null); } catch { return 'system'; }
}
function storage(): Storage | undefined {
  try { return typeof window === 'undefined' ? undefined : window.localStorage; } catch { return undefined; }
}
const preferences = createStore(() => ({ theme: readTheme(storage()) }));
export function initializeAppearance() {
  if (typeof window === 'undefined') return;
  const theme = preferences.getState().theme;
  document.documentElement.dataset.theme = resolveTheme(theme, window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.themePreference = theme;
}
export function setTheme(theme: ThemePreference) {
  preferences.setState({ theme });
  try { storage()?.setItem(THEME_STORAGE_KEY, theme); } catch { /* Appearance still works without persistence. */ }
  initializeAppearance();
}
export function ThemeSync() {
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const sync = () => initializeAppearance();
    const stored = (event: StorageEvent) => {
      if (event.key === THEME_STORAGE_KEY || event.key === null) {
        preferences.setState({ theme: readTheme(storage()) });
        sync();
      }
    };
    sync(); media.addEventListener('change', sync); window.addEventListener('storage', stored);
    return () => { media.removeEventListener('change', sync); window.removeEventListener('storage', stored); };
  }, []);
  return null;
}
export function AppearanceSettings() {
  const theme = useStore(preferences, value => value.theme);
  return <fieldset className="appearance-settings"><legend>外观</legend><div className="theme-options">{(['system', 'light', 'dark'] as const).map(value => <label key={value}><input type="radio" name="appearance" value={value} checked={theme === value} onChange={() => setTheme(value)}/><span>{value === 'system' ? '跟随系统' : value === 'light' ? '浅色' : '深色'}</span></label>)}</div></fieldset>;
}
