import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:net';
import { chromium, expect } from '@playwright/test';
import { freePort } from './backend-harness.mjs';
import { startModelBackend } from './model-harness.mjs';
const devPort = await freePort();
const model = await startModelBackend({ withKey: false, empty: true, origins: [`http://127.0.0.1:${devPort}`, `http://localhost:${devPort}`] });
const backend = model.backend;

const directory = mkdtempSync(join(tmpdir(), 'tavo-dev-'));
const server = createServer();
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
await new Promise((resolve) => server.close(resolve));
const env = { ...process.env, TAVO_USER_DATA: directory, TAVO_DEV_PORT: String(devPort), TAVO_BACKEND_URL: backend.origin, TAVO_CLIENT_TOKEN_FILE: backend.tokenFile, REMOTE_DEBUGGING_PORT: String(port) };
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn('pnpm', ['dev'], { env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
let output = '';
for (const stream of [child.stdout, child.stderr]) stream.on('data', (data) => { output = (output + data).slice(-6000); });
let browser;
try {
  for (let attempt = 0; attempt < 60; attempt++) {
    try { browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`); break; }
    catch { if (child.exitCode !== null) throw new Error(output); await new Promise((resolve) => setTimeout(resolve, 500)); }
  }
  if (!browser) throw new Error(`开发窗口未启动\n${output}`);
  const context = browser.contexts()[0];
  const page = context.pages()[0] ?? await context.waitForEvent('page');
  const errors = []; page.on('pageerror', (error) => errors.push(error.message));
  await page.waitForURL((url) => ['127.0.0.1', 'localhost'].includes(url.hostname) && url.port === String(devPort), { timeout: 20_000, waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: '今天，想聊点什么？' })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('backend-state')).toHaveText('后端已连接');
  await page.getByRole('textbox', { name: '消息', exact: true }).fill('介绍一下 Tavo');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect(page.getByTestId('assistant-message')).toContainText('你好，我是 Tavo');
  await page.getByRole('button', { name: '打开设置', exact: true }).click();
  await expect(page.getByLabel('后端地址', { exact: true })).toHaveValue(backend.origin);
  await page.getByLabel('模型厂商', { exact: true }).selectOption('deepseek');
  await page.getByLabel('模型 ID', { exact: true }).fill('fixture-text');
  await page.getByRole('button', { name: '添加模型配置', exact: true }).click();
  await expect(page.getByLabel('模型 API Key', { exact: true })).toBeVisible();
  const profileId = await page.getByLabel('选择凭据目标', { exact: true }).inputValue();
  await page.getByLabel('模型 API Key', { exact: true }).fill('synthetic-model-test-key');
  await page.getByRole('button', { name: '加密保存 Key', exact: true }).click();
  await expect(page.getByRole('region', { name: '模型 Key 设置' })).toContainText('凭据已加密保存到服务端');
  await expect(page.getByLabel('模型 API Key', { exact: true })).toHaveValue('');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '打开设置', exact: true }).click();
  await page.getByRole('combobox', { name: '选择模型（切换会开始新对话）' }).selectOption(profileId);
  await page.keyboard.press('Escape');
  await page.getByRole('textbox', { name: '消息', exact: true }).fill('测试模型');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect(page.getByTestId('assistant-message')).toContainText('本地 HTTP 替身回复');
  await page.getByRole('button', { name: '新对话', exact: true }).click();
  await expect(page.getByRole('heading', { name: '今天，想聊点什么？' })).toBeVisible();
  expect(errors).toEqual([]);
  await page.screenshot({ path: 'test-results/chat-dev.png' });
  console.log('pnpm dev：空配置添加模型、Web Crypto 凭据提交/清空、服务端模型 HTTP、新对话和原生设置通过。');
} finally {
  // CDP 没有 Electron app.quit 语义；只清理本脚本独立创建的测试进程组。
  for (const signal of ['SIGTERM', 'SIGKILL']) {
    try { process.kill(-child.pid, signal); }
    catch (error) {
      if (error.code !== 'ESRCH') { console.error('测试进程清理失败：', error.code); process.exitCode = 1; }
    }
    if (signal === 'SIGTERM') await new Promise((resolve) => setTimeout(resolve, 500));
  }
  await browser?.close();
  child.stdout.destroy(); child.stderr.destroy();
  await model.cleanup();
  rmSync(directory, { recursive: true, force: true });
}
