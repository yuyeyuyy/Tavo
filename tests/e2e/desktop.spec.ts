import { _electron as electron, chromium, expect, test, type ElectronApplication } from '@playwright/test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { spawnSync } from 'node:child_process';
import type { Snapshot } from '@tavo/shared';
import { startBackend, type BackendHarness } from '../../scripts/backend-harness.mjs';

async function launchApp(directory: string, backend: BackendHarness) {
  const env: Record<string, string> = Object.fromEntries(Object.entries(process.env).filter((pair): pair is [string, string] => pair[1] !== undefined));
  Object.assign(env, { TAVO_USER_DATA: directory, TAVO_BACKEND_URL: backend.origin, TAVO_CLIENT_TOKEN_FILE: backend.tokenFile }); delete env.ELECTRON_RUN_AS_NODE;
  return electron.launch(process.env.TAVO_PACKAGED ? { executablePath: resolve('release/mac-arm64/Tavo.app/Contents/MacOS/Tavo'), env } : { args: [resolve('apps/desktop')], env });
}

test('独立后端 + Electron：多账户、幂等同步、重试、Agent、重启与隔离', async () => {
  const backend = await startBackend(); const directory = mkdtempSync(join(tmpdir(), 'tavo-client-'));
  let app: ElectronApplication | undefined;
  try {
    // Real browser, authenticated docs and local Swagger assets (no CDN dependency).
    const browser = await chromium.launch({ channel: process.env.TAVO_BROWSER_CHANNEL });
    try {
      const context = await browser.newContext({ httpCredentials: backend.docsCredentials() });
      const docs = await context.newPage(); await docs.goto(backend.origin + '/docs');
      await expect(docs.locator('.swagger-ui .info .title')).toContainText('Tavo API');
      await expect(docs.locator('.opblock')).not.toHaveCount(0);
    } finally { await browser.close(); }
    app = await launchApp(directory, backend); let page = await app.firstWindow();
    const errors: string[] = []; page.on('pageerror', (error) => errors.push(error.message));
    await expect(page.getByText('从添加账户开始')).toBeVisible();
    await expect(page.getByTestId('backend-state')).toHaveText('后端已连接');
    const isolation = await page.evaluate(() => ({ node: typeof Reflect.get(window, 'require'), process: typeof Reflect.get(window, 'process'), legacy: typeof Reflect.get(window, 'tavo'), api: Object.keys(window.desktop).sort() }));
    expect(isolation).toEqual({ node: 'undefined', process: 'undefined', legacy: 'undefined', api: ['configure', 'notify', 'preferences', 'profile', 'reconnect', 'selectFile'] });
    const csp = await page.evaluate(async () => (await fetch(location.href)).headers.get('content-security-policy'));
    expect(csp).toContain("script-src 'self';");
    expect(csp).toContain(`connect-src 'self' ${backend.origin};`);
    expect(csp).not.toContain('unsafe-inline');
    const snapshot = () => backend.request<Snapshot>('/snapshot');
    await page.getByRole('button', { name: '账户连接', exact: true }).click();
    for (const [name, id] of [['学习账户 A', 'demo-a'], ['学习账户 B', 'demo-b']]) {
      await page.getByLabel('显示名称', { exact: true }).fill(name!);
      await page.getByLabel('模拟账户标识', { exact: true }).fill(id!);
      await page.getByRole('button', { name: '添加账户', exact: true }).click();
      await expect(page.getByTestId('connection-card').filter({ hasText: name! })).toContainText('已连接');
    }
    const cards = page.getByTestId('connection-card'); await expect(cards).toHaveCount(2);
    await page.getByLabel('显示名称', { exact: true }).fill('重复账户'); await page.getByLabel('模拟账户标识', { exact: true }).fill('demo-a');
    await page.getByRole('button', { name: '添加账户', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('已经添加');
    await cards.first().getByRole('button', { name: '更新模拟凭据', exact: true }).click();
    await cards.first().getByRole('button', { name: '同步账户', exact: true }).click();
    await cards.nth(1).getByRole('button', { name: '同步账户', exact: true }).click();
    await expect.poll(async () => (await snapshot()).jobs.filter((j) => j.status === 'succeeded').length).toBe(2);
    const first = (await snapshot()).connections.find((c) => c.externalAccountId === 'demo-a')!;
    await cards.first().getByRole('button', { name: '同步账户', exact: true }).click();
    await expect.poll(async () => (await snapshot()).jobs.filter((j) => j.status === 'succeeded').length).toBe(3);
    expect((await snapshot()).businessEvents.filter((e) => e.eventType === 'data.changed')).toHaveLength(2);
    await cards.first().getByText('模拟数据与故障设置', { exact: true }).click();
    await page.getByLabel('学习账户 A 模拟余额', { exact: true }).fill('3.50');
    await page.getByLabel('学习账户 A 故障场景').selectOption('network_once');
    await cards.first().getByRole('button', { name: '保存模拟配置' }).click();
    await cards.first().getByRole('button', { name: '同步账户', exact: true }).click();
    await expect.poll(async () => (await snapshot()).data.find((d) => d.connectionId === first.id)?.balance).toBe('3.50');
    expect((await snapshot()).jobs.some((j) => j.connectionId === first.id && j.status === 'succeeded' && j.retryCount === 1)).toBe(true);
    await page.getByRole('button', { name: '总览', exact: true }).click();
    // Backend completion can precede the Query/SSE refresh. Wait for the intended
    // changed event instead of analyzing whichever stale event happens to be first.
    const changedEvent = page.getByTestId('business-event')
      .filter({ has: page.getByRole('heading', { name: '账户数据变化 · 学习账户 A', exact: true }) })
      .filter({ hasText: '3.50 USD' });
    await expect(changedEvent).toBeVisible();
    await changedEvent.getByRole('button', { name: '运行模拟分析', exact: true }).click();
    await expect(changedEvent.getByTestId('analysis')).toContainText('模拟账户余额低于演示阈值');
    await page.getByRole('button', { name: 'Agent', exact: true }).click();
    await expect(page.getByTestId('agent-run')).toContainText('分析完成 · 100%');
    await page.screenshot({ path: process.env.TAVO_PACKAGED ? 'test-results/tavo-packaged.png' : 'test-results/tavo-desktop.png', fullPage: true });
    await page.getByRole('button', { name: '设置', exact: true }).click();
    await page.getByLabel('同步间隔', { exact: true }).fill('60'); await page.getByRole('button', { name: '保存设置' }).click();
    await app.close(); app = undefined;
    expect((await backend.request<{status: string}>('/health')).status).toBe('ok');
    app = await launchApp(directory, backend); page = await app.firstWindow();
    await expect(page.getByText('学习账户 A', { exact: true }).first()).toBeVisible();
    const restored = await snapshot(); expect(restored.data).toHaveLength(2); expect(restored.settings.syncIntervalSeconds).toBe(60);
    expect(restored.businessEvents.some((e) => e.analysis)).toBe(true);
    await backend.stop(); await expect(page.getByTestId('backend-state')).toContainText('后端不可用', { timeout: 12_000 });
    await expect(page.getByText('学习账户 A', { exact: true }).first()).toBeVisible();
    await backend.start(); await page.getByRole('button', { name: '重新连接后端' }).click();
    await expect(page.getByTestId('backend-state')).toHaveText('后端已连接');
    await page.getByRole('button', { name: '账户连接', exact: true }).click();
    const second = page.getByTestId('connection-card').filter({ hasText: '学习账户 B' });
    await second.getByRole('button', { name: '断开连接', exact: true }).click(); await expect(second).toContainText('已断开');
    await second.getByRole('button', { name: '删除连接', exact: true }).click(); await expect(page.getByTestId('connection-card')).toHaveCount(1);
    await page.getByRole('button', { name: '活动记录', exact: true }).click();
    await page.getByTestId('sync-job').first().getByRole('button', { name: '查看关联事件' }).click(); await expect(page.getByTestId('business-event').first()).toBeVisible();
    expect(errors).toEqual([]);
  } finally { await app?.close(); await backend.cleanup(); rmSync(directory, { recursive: true, force: true }); }
});

test('旧数据库导入后历史任务、事件和 Python 分析入口仍可用', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'tavo-legacy-client-'));
  const source = join(directory, 'old.sqlite');
  const fixture = spawnSync(resolve('backend/.venv/bin/python'), ['-c', 'from tests.test_migration import legacy_fixture; from pathlib import Path; import sys; legacy_fixture(Path(sys.argv[1]), 1)', source], { cwd: resolve('backend') });
  expect(fixture.status).toBe(0);
  const backend = await startBackend({ source }); let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory, backend); const page = await app.firstWindow();
    await expect(page.getByText('old', { exact: true }).first()).toBeVisible();
    await page.getByRole('button', { name: '活动记录', exact: true }).click(); await page.getByText('Phase 0 历史记录', { exact: true }).click();
    await expect(page.getByText(/旧同步任务 · succeeded/)).toBeVisible();
    await page.getByRole('button', { name: '分析历史事件', exact: true }).click(); await expect(page.getByText('模拟账户余额处于演示阈值之上。', { exact: true })).toBeVisible();
  } finally { await app?.close(); await backend.cleanup(); rmSync(directory, { recursive: true, force: true }); }
});

test('Electron 完全退出后，独立 Worker 继续定时同步', async () => {
  const backend = await startBackend(); const directory = mkdtempSync(join(tmpdir(), 'tavo-timer-client-')); let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory, backend); const page = await app.firstWindow(); await expect(page.getByText('从添加账户开始')).toBeVisible();
    await backend.request('/accounts', { method: 'POST', body: JSON.stringify({ connectorId: 'mock', externalAccountId: 'timer', displayName: 'timer' }) });
    await backend.request('/settings', { method: 'PATCH', body: JSON.stringify({ autoSync: true, syncIntervalSeconds: 10, startMinimized: false }) });
    await app.close(); app = undefined;
    await expect.poll(async () => (await backend.request<Snapshot>('/snapshot')).jobs.filter((j) => j.trigger === 'scheduled' && j.status === 'succeeded').length, { timeout: 15_000 }).toBeGreaterThanOrEqual(1);
  } finally { await app?.close(); await backend.cleanup(); rmSync(directory, { recursive: true, force: true }); }
});
