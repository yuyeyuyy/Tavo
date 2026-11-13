import { _electron as electron, expect, test, type ElectronApplication } from '@playwright/test';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import Database from 'better-sqlite3';
import { createHash } from 'node:crypto';
import { migrations } from '../../packages/storage/src/migrations';

async function launchApp(directory: string) {
    const env: Record<string, string> = Object.fromEntries(Object.entries(process.env).filter((pair): pair is [string, string] => pair[1] !== undefined));
    env.TAVO_USER_DATA = directory; delete env.ELECTRON_RUN_AS_NODE;
    return electron.launch(process.env.TAVO_PACKAGED ? { executablePath: resolve('release/mac-arm64/Tavo.app/Contents/MacOS/Tavo'), env } : { args: [resolve('apps/desktop')], env });
  }

test('Phase 1：多账户、系统密文、增量变化、重试、隔离与重启', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'tavo-e2e-'));
  let app: ElectronApplication | undefined;
  const launch = () => launchApp(directory);
  try {
    app = await launch(); let page = await app.firstWindow();
    const errors: string[] = []; page.on('pageerror', (error) => errors.push(error.message));
    await expect(page.getByText('从添加账户开始')).toBeVisible();
    const initial = await page.evaluate(() => window.tavo.snapshot());
    expect(initial.ok && initial.data.security.available, '本机真实系统安全存储必须可用，不允许测试明文降级').toBe(true);
    const isolation = await page.evaluate(() => ({ node: typeof Reflect.get(window, 'require'), process: typeof Reflect.get(window, 'process'), api: Object.keys(window.tavo).sort() }));
    expect(isolation.node).toBe('undefined'); expect(isolation.process).toBe('undefined');
    expect(isolation.api).toEqual(['addConnection', 'analyze', 'cancelSync', 'configureMock', 'disconnect', 'reconnect', 'removeConnection', 'snapshot', 'syncConnection', 'updateSettings']);
    expect(await page.evaluate(() => Reflect.get(window, 'ipcRenderer'))).toBeUndefined();
    const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content');
    expect(csp).toContain("script-src 'self';"); expect(csp).toContain("connect-src 'none';");
    await page.getByRole('button', { name: '账户连接', exact: true }).click();
    for (const [displayName, externalAccountId] of [['学习账户 A', 'demo-a'], ['学习账户 B', 'demo-b']] as const) {
      await page.getByLabel('显示名称', { exact: true }).fill(displayName);
      await page.getByLabel('模拟账户标识', { exact: true }).fill(externalAccountId);
      await page.getByRole('button', { name: '添加账户', exact: true }).click();
      await expect(page.getByTestId('connection-card').filter({ hasText: displayName })).toContainText('已连接');
    }
    const cards = page.getByTestId('connection-card'); await expect(cards).toHaveCount(2);
    await page.getByLabel('显示名称', { exact: true }).fill('重复账户');
    await page.getByLabel('模拟账户标识', { exact: true }).fill('demo-a');
    await page.getByRole('button', { name: '添加账户', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('已经添加'); await expect(cards).toHaveCount(2);
    await cards.first().getByRole('button', { name: '更新模拟凭据', exact: true }).click();
    await cards.first().getByRole('button', { name: '同步账户', exact: true }).click();
    await cards.nth(1).getByRole('button', { name: '同步账户', exact: true }).click();
    await expect.poll(async () => { const result = await page.evaluate(() => window.tavo.snapshot()); return result.ok ? result.data.jobs.filter((j) => j.status === 'succeeded').length : 0; }).toBe(2);
    const before = await page.evaluate(() => window.tavo.snapshot()); if (!before.ok) throw new Error('snapshot failed');
    const first = before.data.connections.find((c) => c.externalAccountId === 'demo-a')!;
    const second = before.data.connections.find((c) => c.externalAccountId === 'demo-b')!;
    const invalid = await page.evaluate((id) => window.tavo.syncConnection({ connectionId: id, secret: 'synthetic' } as never), first.id);
    expect(invalid).toMatchObject({ ok: false, error: { code: 'INVALID_INPUT' } });
    await cards.first().getByRole('button', { name: '同步账户', exact: true }).click();
    await expect.poll(async () => { const value = await page.evaluate(() => window.tavo.snapshot()); return value.ok ? value.data.jobs.filter((j) => j.status === 'succeeded').length : 0; }).toBe(3);
    let current = await page.evaluate(() => window.tavo.snapshot());
    expect(current.ok && current.data.businessEvents.filter((e) => e.eventType === 'data.changed').length).toBe(2);
    await cards.first().getByText('模拟数据与故障设置', { exact: true }).click();
    await page.getByLabel('学习账户 A 模拟余额', { exact: true }).fill('3.50');
    await page.getByLabel('学习账户 A 故障场景').selectOption('network_once');
    await cards.first().getByRole('button', { name: '保存模拟配置' }).click();
    await cards.first().getByRole('button', { name: '同步账户', exact: true }).click();
    await expect.poll(async () => { const value = await page.evaluate(() => window.tavo.snapshot()); return value.ok ? value.data.data.find((d) => d.connectionId === first.id)?.balance : null; }).toBe('3.50');
    current = await page.evaluate(() => window.tavo.snapshot()); if (!current.ok) throw new Error('snapshot failed');
    expect(current.data.jobs.some((j) => j.connectionId === first.id && j.status === 'succeeded' && j.retryCount === 1)).toBe(true);
    expect(current.data.businessEvents.filter((e) => e.eventType === 'data.changed')).toHaveLength(3);
    await page.getByRole('button', { name: '总览', exact: true }).click();
    await page.getByRole('button', { name: '运行模拟分析', exact: true }).first().click();
    await expect(page.getByTestId('analysis')).toContainText('模拟账户余额低于演示阈值');
    await page.screenshot({ path: process.env.TAVO_PACKAGED ? 'test-results/tavo-packaged.png' : 'test-results/tavo-desktop.png', fullPage: true });
    await page.getByRole('button', { name: '设置', exact: true }).click();
    await page.getByLabel('同步间隔', { exact: true }).fill('60'); await page.getByRole('button', { name: '保存设置' }).click();
    await app.close(); app = undefined;
    const raw = new Database(join(directory, 'tavo.sqlite'));
    const encrypted = raw.prepare('SELECT reference,ciphertext FROM credential_records').all() as { reference: string; ciphertext: Buffer }[];
    expect(encrypted).toHaveLength(2); expect(encrypted.every((row) => row.ciphertext.length > 0 && !row.ciphertext.includes(Buffer.from('mock-only:')))).toBe(true);
    raw.close(); expect(readFileSync(join(directory, 'tavo.sqlite')).includes(Buffer.from('mock-only:'))).toBe(false);
    app = await launch(); page = await app.firstWindow();
    await expect(page.getByText('学习账户 A', { exact: true }).first()).toBeVisible();
    const restored = await page.evaluate(() => window.tavo.snapshot()); if (!restored.ok) throw new Error('snapshot failed');
    expect(restored.data.connections).toHaveLength(2); expect(restored.data.data).toHaveLength(2);
    expect(restored.data.settings.syncIntervalSeconds).toBe(60); expect(restored.data.businessEvents.some((e) => e.analysis)).toBe(true);
    const roundtrip = await page.evaluate((id) => window.tavo.syncConnection({ connectionId: id }), first.id); expect(roundtrip.ok).toBe(true);
    await expect.poll(async () => { const value = await page.evaluate(() => window.tavo.snapshot()); return value.ok ? value.data.jobs.filter((j) => j.status === 'succeeded').length : 0; }).toBe(5);
    await page.getByRole('button', { name: '账户连接', exact: true }).click();
    const secondCard = page.getByTestId('connection-card').filter({ hasText: '学习账户 B' });
    await secondCard.getByRole('button', { name: '断开连接', exact: true }).click(); await expect(secondCard).toContainText('已断开');
    await secondCard.getByRole('button', { name: '删除连接', exact: true }).click(); await expect(page.getByTestId('connection-card')).toHaveCount(1);
    await page.getByRole('button', { name: '活动记录', exact: true }).click(); await expect(page.getByTestId('sync-job').first()).toBeVisible();
    await page.getByTestId('sync-job').first().getByRole('button', { name: '查看关联事件' }).click(); await expect(page.getByTestId('business-event').first()).toBeVisible();
    expect(errors).toEqual([]);
    await app.close(); app = undefined;
    const checked = new Database(join(directory, 'tavo.sqlite'));
    expect(checked.prepare('SELECT count(*) AS n FROM credential_records').get()).toEqual({ n: 1 });
    expect(checked.prepare('SELECT count(*) AS n FROM credential_records WHERE reference = ?').get(`credential:${second.id}`)).toEqual({ n: 0 }); checked.close();
  } finally { await app?.close(); rmSync(directory, { recursive: true, force: true }); }
});

test('Phase 0 升级后的旧账户、任务、事件与分析入口仍然可见', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'tavo-upgrade-e2e-'));
  const raw = new Database(join(directory, 'tavo.sqlite')); const initial = migrations[0];
  raw.exec('CREATE TABLE _migrations (version INTEGER PRIMARY KEY, hash TEXT NOT NULL)'); raw.exec(initial.sql);
  raw.prepare('INSERT INTO _migrations VALUES (?,?)').run(1, createHash('sha256').update(initial.sql).digest('hex'));
  raw.prepare('INSERT INTO connectors VALUES (?,?,?,?,NULL)').run('mock', 'Mock Connector', '["account","balance","event"]', '["read:mock"]');
  raw.prepare('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)').run('mock:old', 'mock', 'old', '旧模拟账户', '12.00', 'USD', '2026-11-12T00:00:00.000Z', 'mock');
  raw.prepare('INSERT INTO sync_tasks VALUES (?,?,?,?,?,?)').run('old-task', 'mock', 'succeeded', '2026-11-12T00:00:00.000Z', '2026-11-12T00:00:01.000Z', null);
  raw.prepare('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)').run('mock:old-event', 'mock', 'mock:old', 'old-event', 'balance.observed', '12.00', 'USD', '2026-11-12T00:00:00.000Z', 'mock', null); raw.close();
  let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory); const page = await app.firstWindow();
    await expect(page.getByText('旧模拟账户', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '活动记录', exact: true }).click();
    await page.getByText('Phase 0 历史记录', { exact: true }).click();
    await expect(page.getByText(/旧同步任务 · succeeded/)).toBeVisible();
    await page.getByRole('button', { name: '分析历史事件', exact: true }).click();
    await expect(page.getByText('模拟账户余额处于演示阈值之上。', { exact: true })).toBeVisible();
  } finally { await app?.close(); rmSync(directory, { recursive: true, force: true }); }
});

test('关闭 Renderer 窗口后，Main 仍按配置周期同步', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'tavo-scheduler-e2e-')); let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory); const page = await app.firstWindow();
    await expect(page.getByText('从添加账户开始')).toBeVisible();
    const added = await page.evaluate(() => window.tavo.addConnection({ connectorId: 'mock', externalAccountId: 'timer-demo', displayName: '定时同步账户' })); expect(added.ok).toBe(true);
    const settings = await page.evaluate(() => window.tavo.updateSettings({ autoSync: true, syncIntervalSeconds: 10, startMinimized: false })); expect(settings.ok).toBe(true);
    await page.close(); expect(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length)).toBe(0);
    await expect.poll(() => {
      const raw = new Database(join(directory, 'tavo.sqlite'), { readonly: true });
      try { return (raw.prepare("SELECT count(*) AS n FROM sync_jobs WHERE trigger='scheduled' AND status='succeeded'").get() as { n: number }).n; }
      finally { raw.close(); }
    }, { timeout: 15_000, intervals: [250, 500] }).toBeGreaterThanOrEqual(1);
  } finally { await app?.close(); rmSync(directory, { recursive: true, force: true }); }
});
