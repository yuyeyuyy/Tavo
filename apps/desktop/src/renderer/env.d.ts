interface DesktopProfile { backendUrl: string; authenticated: boolean; startMinimized: boolean }
interface NativeDesktopAPI {
  profile(): Promise<DesktopProfile>;
  reconnect(): Promise<{ ok: true; profile: DesktopProfile } | { ok: false; message: string }>;
  configure(input: { backendUrl: string }): Promise<{ ok: true } | { ok: false; message: string }>;
  preferences(input: { startMinimized: boolean }): Promise<DesktopProfile>;
  selectFile(): Promise<string | null>;
  notify(input: { title: string; body: string }): Promise<boolean>;
}
interface Window { desktop: NativeDesktopAPI }
