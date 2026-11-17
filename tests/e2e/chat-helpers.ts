import { _electron as electron, type Page } from '@playwright/test';
import { resolve } from 'node:path';
import type { BackendHarness } from '../../scripts/backend-harness.mjs';

export async function launchApp(directory: string, backend: BackendHarness) {
  const env: Record<string, string> = Object.fromEntries(Object.entries(process.env).filter((pair): pair is [string, string] => pair[1] !== undefined));
  Object.assign(env, { TAVO_USER_DATA: directory, TAVO_BACKEND_URL: backend.origin, TAVO_CLIENT_TOKEN_FILE: backend.tokenFile }); delete env.ELECTRON_RUN_AS_NODE;
  return electron.launch(process.env.TAVO_PACKAGED ? { executablePath: resolve('release/mac-arm64/Tavo.app/Contents/MacOS/Tavo'), env } : { args: [resolve('apps/desktop')], env });
}
export const screenshot = (state: string) => `test-results/chat-${process.env.TAVO_PACKAGED ? 'packaged' : 'desktop'}-${state}.png`;

export async function openSettings(page: Page) {
  if (!await page.getByRole('dialog', { name: '设置', exact: true }).isVisible()) await page.getByRole('button', { name: '打开设置', exact: true }).click();
}
export async function selectChatSetting(page: Page, kind: 'model' | 'permission', value: string) {
  await openSettings(page);
  await page.getByLabel(kind === 'model' ? '选择模型（切换会开始新对话）' : '命令权限（切换会开始新对话）', { exact: true }).selectOption(value);
  await page.keyboard.press('Escape');
}
