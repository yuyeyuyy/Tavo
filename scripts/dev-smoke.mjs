/* global window */
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:net';
import { chromium, expect } from '@playwright/test';

const directory = mkdtempSync(join(tmpdir(), 'tavo-dev-'));
const server = createServer();
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
await new Promise((resolve) => server.close(resolve));
const env = { ...process.env, TAVO_USER_DATA: directory, REMOTE_DEBUGGING_PORT: String(port) };
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
  await expect(page.getByText('从添加账户开始')).toBeVisible();
  await page.getByRole('button', { name: '账户连接', exact: true }).click();
  await page.getByLabel('显示名称', { exact: true }).fill('开发模式账户');
  await page.getByLabel('模拟账户标识', { exact: true }).fill('dev-demo');
  await page.getByRole('button', { name: '添加账户', exact: true }).click();
  await expect(page.getByTestId('connection-card')).toContainText('已连接');
  await page.getByRole('button', { name: '同步账户', exact: true }).click();
  await expect.poll(async () => { const value = await page.evaluate(() => window.tavo.snapshot()); return value.ok ? value.data.data.length : 0; }).toBe(1);
  await page.getByRole('button', { name: '总览', exact: true }).click();
  await page.getByRole('button', { name: '运行模拟分析', exact: true }).click();
  await expect(page.getByTestId('analysis')).toContainText('模拟账户余额低于演示阈值');
  expect(errors).toEqual([]);
  await page.screenshot({ path: 'test-results/tavo-dev.png', fullPage: true });
  console.log('pnpm dev：窗口、账户连接、系统安全凭据、IPC 同步、LangGraph 分析通过。');
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
  rmSync(directory, { recursive: true, force: true });
}
