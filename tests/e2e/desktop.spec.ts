import { chromium, expect, test, type ElectronApplication } from '@playwright/test';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startBackend } from '../../scripts/backend-harness.mjs';
import { startModelBackend } from '../../scripts/model-harness.mjs';
import { launchApp, screenshot, openSettings, selectChatSetting } from './chat-helpers';

test('空配置从客户端选择厂商、添加模型、加密 Key 并聊天，无需文件配置或重启', async () => {
  const fixture = await startModelBackend({ withKey: false, empty: true }); const directory = mkdtempSync(join(tmpdir(), 'tavo-model-setup-')); let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory, fixture.backend); const page = await app.firstWindow();
    const errors: string[] = []; page.on('pageerror', (error) => errors.push(error.message));
    await expect(page.getByTestId('backend-state')).toHaveText('后端已连接');
    expect((await fixture.backend.request<{ items: unknown[] }>('/chat/models')).items).toEqual([]);
    await openSettings(page);
    const form = page.getByRole('form', { name: '添加模型配置' });
    await expect(form).toBeVisible();
    await expect(page.getByLabel('模型厂商', { exact: true }).locator('option')).toHaveCount(10);
    await page.getByLabel('模型厂商', { exact: true }).selectOption('deepseek');
    await page.getByLabel('模型 ID', { exact: true }).fill('fixture-text');
    await page.getByLabel('显示名称（可选）', { exact: true }).fill('我的测试模型');
    const created = page.waitForResponse((response) => response.url().endsWith('/api/v1/model-profiles') && response.request().method() === 'POST');
    await page.getByRole('button', { name: '添加模型配置', exact: true }).click();
    const createdResponse = await created; expect(createdResponse.status()).toBe(201);
    const profile = await createdResponse.json() as { id: string };
    expect(JSON.parse(createdResponse.request().postData()!)).toEqual({ provider: 'deepseek', model: 'fixture-text', label: '我的测试模型' });
    const input = page.getByLabel('模型 API Key', { exact: true });
    await expect(input).toBeVisible(); await expect(input).toBeFocused(); await expect(input).toHaveValue('');
    await expect(page.getByLabel('选择凭据目标', { exact: true })).toHaveValue(profile.id);
    await page.screenshot({ path: screenshot('add-model') });
    await input.fill('synthetic-model-test-key');
    const submitted = page.waitForRequest((request) => request.url().endsWith('/api/v1/secret-submissions'));
    await page.getByRole('button', { name: '加密保存 Key', exact: true }).click();
    expect((await submitted).postData()).not.toContain('synthetic-model-test-key');
    await expect(page.getByRole('region', { name: '模型 Key 设置' })).toContainText('凭据已加密保存到服务端');
    await expect(input).toHaveValue(''); expect(fixture.calls).toHaveLength(0);
    await page.keyboard.press('Escape');
    await openSettings(page);
    await selectChatSetting(page, 'model', profile.id);
    await page.getByRole('textbox', { name: '消息', exact: true }).fill('你好'); await expect(page.getByRole('textbox', { name: '消息', exact: true })).toHaveValue('你好'); await page.getByRole('button', { name: '发送消息', exact: true }).click();
    await expect(page.getByTestId('assistant-message')).toContainText('本地 HTTP 替身回复');
    expect(fixture.calls).toHaveLength(1);
    await fixture.backend.stop(); await fixture.backend.start();
    const catalog = await fixture.backend.request<{ items: { id: string; ready: boolean }[] }>('/chat/models');
    expect(catalog.items).toHaveLength(1); expect(catalog.items[0]).toMatchObject({ id: profile.id, ready: true });
    expect(errors).toEqual([]);
  } finally { await app?.close(); await fixture.cleanup(); rmSync(directory, { recursive: true, force: true }); }
});

test('客户端输入 Key：Web Crypto 密文提交、清空、状态刷新、重开与服务端模型调用', async () => {
  const fixture = await startModelBackend({ withKey: false }); const directory = mkdtempSync(join(tmpdir(), 'tavo-secret-client-')); let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory, fixture.backend); const page = await app.firstWindow();
    expect(await page.evaluate(() => isSecureContext && !!crypto.subtle)).toBe(true);
    await openSettings(page);
    const picker = page.getByRole('combobox', { name: '选择模型（切换会开始新对话）' });
    await expect(picker.locator('option[value="local-test"]')).toBeDisabled();
    await openSettings(page);
    const panel = page.getByRole('region', { name: '模型 Key 设置' });
    await page.getByLabel('选择凭据目标', { exact: true }).selectOption('local-test');
    const input = page.getByLabel('模型 API Key', { exact: true });
    await expect(input).toHaveAttribute('type', 'password');
    await input.fill('synthetic-model-test-key');
    await expect(input).toHaveValue('synthetic-model-test-key');
    const pending = page.waitForRequest((request) => request.url().endsWith('/api/v1/secret-submissions'));
    const accepted = page.waitForResponse((response) => response.url().endsWith('/api/v1/secret-submissions'));
    await page.getByRole('button', { name: '加密保存 Key', exact: true }).click();
    const submitted = (await pending).postData()!;
    expect(submitted).not.toContain('synthetic-model-test-key'); expect(JSON.parse(submitted).envelope.split('.')).toHaveLength(5);
    const acceptedResponse = await accepted;
    expect({ status: acceptedResponse.status(), body: await acceptedResponse.json() }).toMatchObject({ status: 200, body: { resourceId: 'local-test', ready: true, credentialRevision: 1 } });
    await expect(input).toHaveValue(''); await expect(panel).toContainText('凭据已加密保存到服务端');
    await expect(panel).toContainText('凭据版本 1'); expect(fixture.calls).toHaveLength(0);
    await page.screenshot({ path: screenshot('secret-settings') });
    await input.fill('synthetic-unsaved-key'); await page.keyboard.press('Escape');
    await openSettings(page);
    await page.getByLabel('选择凭据目标', { exact: true }).selectOption('local-test'); await expect(input).toHaveValue('');
    await input.fill('synthetic-failed-key');
    await page.route('**/api/v1/secret-submissions', (route) => route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: { code: 'SECRET_CHALLENGE_INVALID' } }) }));
    await page.getByRole('button', { name: '加密保存 Key', exact: true }).click();
    await expect(panel).toContainText('请重新输入'); await expect(input).toHaveValue('');
    await page.unroute('**/api/v1/secret-submissions');
    await page.getByRole('button', { name: '关闭设置', exact: true }).click();
    await openSettings(page);
    await expect(picker.locator('option[value="local-test"]')).toBeEnabled(); await selectChatSetting(page, 'model', 'local-test');
    await page.getByRole('textbox', { name: '消息', exact: true }).fill('你好'); await page.getByRole('button', { name: '发送消息', exact: true }).click();
    await expect(page.getByTestId('assistant-message')).toContainText('本地 HTTP 替身回复');
    expect(readFileSync(join(fixture.backend.directory, 'tavo.sqlite')).includes(Buffer.from('synthetic-model-test-key'))).toBe(false);
    await fixture.backend.stop(); await fixture.backend.start();
    const catalog = await fixture.backend.request<{ items: { id: string; ready: boolean; credentialRevision: number }[] }>('/chat/models');
    expect(catalog.items.find((model) => model.id === 'local-test')).toMatchObject({ ready: true, credentialRevision: 1 });
  } finally { await app?.close(); await fixture.cleanup(); rmSync(directory, { recursive: true, force: true }); }
});

test('服务端模型：选择、真实 HTTP、错误重试、停止与切换清空，不向 Renderer 返回 Key', async () => {
  const fixture = await startModelBackend(); const directory = mkdtempSync(join(tmpdir(), 'tavo-model-client-')); let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory, fixture.backend); const page = await app.firstWindow();
    await openSettings(page);
    const picker = page.getByRole('combobox', { name: '选择模型（切换会开始新对话）' });
    await expect(picker.locator('option[value="local-test"]')).toHaveCount(1);
    await expect(picker.locator('option[value="no-key"]')).toBeDisabled();
    await selectChatSetting(page, 'model', 'local-test');
    const input = page.getByRole('textbox', { name: '消息', exact: true });
    await input.fill('你好'); await input.press('Enter');
    await expect(page.getByTestId('assistant-message')).toContainText('本地 HTTP 替身回复');
    expect(fixture.calls).toHaveLength(1); expect(fixture.calls[0]!.messages.at(-1)?.content).toBe('你好');
    await expect(page.getByRole('button', { name: '停止回复', exact: true })).toHaveCount(0);
    fixture.failNext(); await input.fill('短暂限流'); await input.press('Enter');
    await expect(page.getByTestId('user-message').last()).toHaveText('短暂限流');
    await expect.poll(() => fixture.calls.length).toBe(3);
    await expect(page.getByTestId('assistant-message').last()).toContainText('本地 HTTP 替身回复');
    expect(fixture.calls).toHaveLength(3);
    fixture.failNext(4); await input.fill('测试失败'); await input.press('Enter');
    await expect(page.getByRole('alert')).toContainText('限流或配额不足');
    await page.getByRole('button', { name: '重试', exact: true }).click();
    await expect(page.getByTestId('assistant-message').last()).toContainText('本地 HTTP 替身回复');
    await expect(page.getByRole('button', { name: '停止回复', exact: true })).toHaveCount(0);
    fixture.setDelay(1500); await input.fill('停止测试'); await input.press('Enter');
    await expect.poll(() => fixture.calls.length).toBe(9);
    await page.getByRole('button', { name: '停止回复', exact: true }).click();
    await expect(page.getByTestId('assistant-message').last()).toContainText('已停止回复');
    await page.screenshot({ path: screenshot('model') });
    await selectChatSetting(page, 'model', 'mock'); await expect(page.getByTestId('user-message')).toHaveCount(0);
    expect(await page.evaluate(async () => JSON.stringify(await (await fetch(new URL('/api/v1/chat/models', (await window.desktop.profile()).backendUrl))).json()))).not.toContain('synthetic-model-test-key');
    await page.waitForTimeout(1700); await expect(page.getByTestId('assistant-message')).toHaveCount(0);
    expect(await page.evaluate(() => document.body.textContent)).not.toContain('synthetic-private-upstream');
  } finally { await app?.close(); await fixture.cleanup(); rmSync(directory, { recursive: true, force: true }); }
});

test('单页对话：欢迎、键盘/输入法、新对话、窄窗口与隔离', async () => {
  const backend = await startBackend(); const directory = mkdtempSync(join(tmpdir(), 'tavo-chat-')); let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory, backend); const page = await app.firstWindow();
    const errors: string[] = []; page.on('pageerror', (error) => errors.push(error.message));
    await expect(page.getByRole('heading', { name: '今天，想聊点什么？' })).toBeVisible();
    await expect(page.getByTestId('backend-state')).toHaveText('后端已连接');
    expect(await page.getByRole('navigation').count()).toBe(0);
    const isolation = await page.evaluate(() => ({ node: typeof Reflect.get(window, 'require'), process: typeof Reflect.get(window, 'process'), legacy: typeof Reflect.get(window, 'tavo'), api: Object.keys(window.desktop).sort() }));
    expect(isolation).toEqual({ node: 'undefined', process: 'undefined', legacy: 'undefined', api: ['configure', 'notify', 'preferences', 'profile', 'reconnect', 'selectFile'] });
    const csp = await page.evaluate(async () => (await fetch(location.href)).headers.get('content-security-policy'));
    expect(csp).toContain("script-src 'self';"); expect(csp).toContain(`connect-src 'self' ${backend.origin};`); expect(csp).not.toContain('unsafe-inline');
    await page.screenshot({ path: screenshot('welcome') });
    const input = page.getByRole('textbox', { name: '消息', exact: true });
    await expect(page.getByRole('button', { name: '发送消息', exact: true })).toBeDisabled();
    await page.getByRole('button', { name: '记忆和上下文有什么区别？' }).click();
    await expect(page.getByTestId('assistant-message')).toContainText('记忆像一份');
    await expect(input).toBeFocused();
    await page.screenshot({ path: screenshot('conversation') });
    await input.fill('第一行'); await input.press('Shift+Enter'); await input.press('a');
    await expect(input).toHaveValue('第一行\na');
    await input.dispatchEvent('compositionstart'); await input.press('Enter');
    await expect(page.getByTestId('user-message')).toHaveCount(1);
    await expect(page.getByRole('button', { name: '发送消息', exact: true })).toBeEnabled();
    await input.dispatchEvent('compositionend'); await input.press('Enter');
    await expect(page.getByTestId('user-message')).toHaveCount(2);
    await expect(page.getByTestId('assistant-message').last()).toContainText('收到你的消息了');
    await input.fill('<img src=x onerror="alert(1)">');
    await expect(page.getByRole('button', { name: '发送消息', exact: true })).toBeEnabled();
    await input.press('Enter');
    await expect(page.getByTestId('user-message').last()).toHaveText('<img src=x onerror="alert(1)">');
    expect(await page.getByTestId('user-message').locator('img').count()).toBe(0);
    await page.getByRole('button', { name: '新对话', exact: true }).click();
    await expect(input).toBeFocused(); await page.waitForTimeout(1100);
    await expect(page.getByTestId('user-message')).toHaveCount(0); await expect(page.getByTestId('assistant-message')).toHaveCount(0);
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]!.setSize(620, 540));
    await expect(page.getByRole('button', { name: '发送消息', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight)).toBe(true);
    await page.screenshot({ path: screenshot('narrow') });
    expect(errors).toEqual([]);
  } finally { await app?.close(); await backend.cleanup(); rmSync(directory, { recursive: true, force: true }); }
});

test('连接设置：认证文档、错误提示、离线对话、重连与弹窗焦点', async () => {
  const backend = await startBackend(); const directory = mkdtempSync(join(tmpdir(), 'tavo-chat-connection-')); let app: ElectronApplication | undefined;
  try {
    const browser = await chromium.launch({ channel: process.env.TAVO_BROWSER_CHANNEL });
    try {
      const context = await browser.newContext({ httpCredentials: backend.docsCredentials() }); const docs = await context.newPage(); await docs.goto(backend.origin + '/docs');
      await expect(docs.locator('.swagger-ui .info .title')).toContainText('Tavo API'); await expect(docs.locator('.opblock')).not.toHaveCount(0);
    } finally { await browser.close(); }
    app = await launchApp(directory, backend); const page = await app.firstWindow();
    await expect(page.getByTestId('backend-state')).toHaveText('后端已连接');
    await openSettings(page);
    const dialog = page.getByRole('dialog', { name: '设置', exact: true }); await expect(dialog).toBeVisible();
    await expect(page.getByLabel('后端地址', { exact: true })).toHaveValue(backend.origin);
    await page.getByLabel('后端地址', { exact: true }).fill('http://example.com');
    await page.getByRole('button', { name: '保存连接', exact: true }).click();
    await expect(dialog.getByRole('status')).toContainText('后端配置失败');
    await page.keyboard.press('Escape'); await expect(dialog).not.toBeVisible(); await expect(page.getByRole('button', { name: '打开设置', exact: true })).toBeFocused();
    await backend.stop(); await expect(page.getByTestId('backend-state')).toHaveText('后端未连接', { timeout: 15_000 });
    await page.getByRole('textbox', { name: '消息', exact: true }).fill('你好'); await expect(page.getByRole('textbox', { name: '消息', exact: true })).toHaveValue('你好'); await page.getByRole('button', { name: '发送消息', exact: true }).click();
    await expect(page.getByTestId('user-message')).toHaveText('你好');
    await expect(page.getByTestId('assistant-message')).toContainText('你好，我是 Tavo');
    await openSettings(page); await page.getByRole('button', { name: '重新连接', exact: true }).click();
    await expect(page.getByRole('dialog').getByRole('status')).toContainText('无法连接或认证后端');
    await backend.start(); await page.getByRole('button', { name: '重新连接', exact: true }).click();
    await expect(page.getByTestId('settings-backend-state')).toHaveText('后端已连接');
    await page.screenshot({ path: screenshot('settings') }); await page.getByRole('button', { name: '关闭设置', exact: true }).click();
    await expect(page.getByTestId('user-message')).toHaveText('你好');
  } finally { await app?.close(); await backend.cleanup(); rmSync(directory, { recursive: true, force: true }); }
});

test('持久会话与工具审核：批准、拒绝、停止重试、窗口和服务端重启恢复', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'tavo-session-lifecycle-'));
  const backend = await startBackend(); let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory, backend); let page = await app.firstWindow();
    const send = async (text: string) => {
      const input = page.getByRole('textbox', { name: '消息', exact: true });
      await input.fill(text); await page.getByRole('button', { name: '发送消息', exact: true }).click();
    };
    await send('计算 12 / 3');
    await expect(page.getByTestId('assistant-message')).toContainText('计算结果：4');
    await page.locator('.tool-timeline > summary').click();
    await expect(page.locator('.tool-card')).toContainText('calculate');
    await page.getByRole('button', { name: '新对话', exact: true }).click();
    const legacy = await backend.request<{id:string;revision:number}>('/sessions', { method: 'POST', body: JSON.stringify({profileId:'mock',reviewMode:'ask'}) });
    await backend.request(`/sessions/${legacy.id}/messages`, {method:'POST',body:JSON.stringify({clientMessageId:'legacy-review',content:'计算 8 * 7',expectedRevision:legacy.revision})});
    await page.getByRole('button', { name: '会话列表', exact: true }).click();
    await page.getByRole('button', { name: /计算 8 \* 7/ }).click();
    await openSettings(page);
    await expect(page.getByLabel('命令权限（切换会开始新对话）')).toHaveValue('ask');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: '批准工具调用 1', exact: true })).toBeVisible();
    await page.screenshot({ path: screenshot('review') });
    await app.close(); app = undefined; await backend.stop(); await backend.start();
    app = await launchApp(directory, backend); page = await app.firstWindow();
    await expect(page.getByTestId('user-message')).toHaveText('计算 8 * 7');
    await page.getByRole('button', { name: '批准工具调用 1', exact: true }).click();
    await expect(page.getByTestId('assistant-message')).toContainText('56');
    await send('现在几点');
    await expect(page.getByRole('button', { name: '拒绝工具调用 1', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '拒绝工具调用 1', exact: true }).click();
    await expect(page.getByTestId('assistant-message').last()).toContainText('未获准');
    await send('计算 10 + 2');
    await expect(page.getByRole('button', { name: '批准工具调用 1', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '停止回复', exact: true }).click();
    await expect(page.getByTestId('assistant-message').last()).toContainText('已停止回复');
    await page.getByRole('button', { name: '重新生成', exact: true }).click();
    await expect(page.getByRole('button', { name: '批准工具调用 1', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '批准工具调用 1', exact: true }).click();
    await expect(page.getByTestId('assistant-message').last()).toContainText('12');
    await expect(page.getByTestId('user-message')).toHaveCount(3);
    await page.getByRole('button', { name: '会话列表', exact: true }).click();
    await page.getByRole('button', { name: /计算 12 \/ 3/ }).click();
    await expect(page.getByTestId('user-message')).toHaveText('计算 12 / 3');
    await expect(page.getByTestId('assistant-message')).toContainText('计算结果：4');
    await page.screenshot({ path: screenshot('restored') });
  } finally { await app?.close(); await backend.cleanup(); rmSync(directory, { recursive: true, force: true }); }
});

test('沙箱 Bash：默认启用、自审查自动执行、文件跨重启保留与逐次确认', async () => {
  test.skip(process.env.TAVO_TEST_DOCKER !== '1', 'Requires explicit real Docker suite and prepared sandbox image');
  const backend = await startBackend({ bash: true });
  const directory = mkdtempSync(join(tmpdir(), 'tavo-bash-desktop-'));
  let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory, backend); let page = await app.firstWindow();
    const send = async (text:string) => { await page.getByRole('textbox',{name:'消息',exact:true}).fill(text); await page.getByRole('button',{name:'发送消息',exact:true}).click(); };
    await expect(page.getByLabel('启用沙箱 Bash（会开始新对话）')).toHaveCount(0);
    await send('bash: printf desktop-ok > note.txt; cat note.txt');
    await expect(page.getByTestId('assistant-message').last()).toContainText('已完成：desktop-ok');
    await page.locator('.tool-timeline > summary').click();
    await expect(page.locator('.tool-timeline')).toContainText('规则审核 · 通过');
    await page.screenshot({path:screenshot('bash')});
    await app.close(); app=undefined; await backend.stop(); await backend.start();
    app=await launchApp(directory,backend); page=await app.firstWindow();
    await expect(page.getByTestId('user-message')).toHaveText('bash: printf desktop-ok > note.txt; cat note.txt');
    await expect(page.getByLabel('启用沙箱 Bash（会开始新对话）')).toHaveCount(0);
    await send('bash: cat note.txt');
    await expect(page.getByTestId('assistant-message').last()).toContainText('已完成：desktop-ok');
    await selectChatSetting(page, 'permission', 'auto_review');
    await send('bash: printf automatic-ok');
    await expect(page.getByTestId('assistant-message').last()).toContainText('已完成：automatic-ok', { timeout: 20_000 });
    await selectChatSetting(page, 'permission', 'request_approval');
    await send('bash: printf needs-confirmation');
    await expect(page.getByRole('button',{name:'批准工具调用 1',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'拒绝工具调用 1',exact:true}).click();
    await expect(page.getByTestId('assistant-message').last()).toContainText('未获准');
  } finally { await app?.close(); await backend.cleanup(); rmSync(directory,{recursive:true,force:true}); }
});

test('沙箱策略：专用 API 凭据加密提交与具体工作区变更批准', async () => {
  test.skip(process.env.TAVO_TEST_DOCKER !== '1', 'Requires real Docker sandbox');
  const pythonPrelude = `from dataclasses import replace
from app.core.config import Settings
from app.core.sandbox_config import NetworkTarget
original_settings=Settings.from_env.__func__
def fixture_settings(cls):
 s=original_settings(cls)
 target=NetworkTarget(id='fixture-api',hostname='api.example.test',paths=['/v1'],capability='api_test',credential=True)
 policy=s.sandbox.model_copy(update={'delete_approval_files':2,'network':s.sandbox.network.model_copy(update={'targets':[target]})})
 return replace(s,sandbox=policy)
Settings.from_env=classmethod(fixture_settings)`;
  const backend = await startBackend({ bash: true, pythonPrelude });
  const directory = mkdtempSync(join(tmpdir(), 'tavo-sandbox-policy-desktop-'));
  let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory, backend); const page = await app.firstWindow();
    await expect(page.getByTestId('backend-state')).toHaveText('后端已连接');
    await openSettings(page);
    await page.getByLabel('授权 API 目标', { exact: true }).selectOption('fixture-api');
    const input = page.getByLabel('API Token', { exact: true });
    const synthetic = 'synthetic-desktop-sandbox-credential';
    await input.fill(synthetic);
    const submission = page.waitForResponse(r => r.url().endsWith('/secret-submissions') && r.request().method() === 'POST');
    await page.getByRole('region', { name: '沙箱 API 凭据' }).getByRole('button', { name: '加密保存 Key', exact: true }).click();
    const response = await submission;
    expect(response.status()).toBe(200);
    expect(response.request().postData()).not.toContain(synthetic);
    await expect(input).toHaveValue('');
    await page.keyboard.press('Escape');
    await selectChatSetting(page, 'permission', 'request_approval');
    const send = async (text: string) => { await page.getByRole('textbox', { name: '消息', exact: true }).fill(text); await page.getByRole('button', { name: '发送消息', exact: true }).click(); };
    await send('bash: python -c "from pathlib import Path; [Path(str(i)).write_text(\'old\') for i in range(3)]"');
    await page.getByRole('button', { name: '批准工具调用 1', exact: true }).click();
    await expect(page.getByTestId('assistant-message').last()).toContainText('已完成');
    await send('bash: python -c "from pathlib import Path; [p.unlink() for p in Path(\'.\').iterdir() if p.is_file()]"');
    await page.getByRole('button', { name: '批准工具调用 1', exact: true }).click();
    await expect(page.getByText('工作区大范围变更；批准只提交此版本，不重跑脚本。', { exact: true })).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('.tool-output').filter({ hasText: '工作区大范围变更' })).toContainText('deletedPaths');
    await page.screenshot({ path: screenshot('sandbox-policy-approval') });
    await page.getByRole('button', { name: '批准工具调用 1', exact: true }).click();
    await expect(page.getByRole('button', { name: '批准工具调用 1', exact: true })).toHaveCount(0);
    await expect(page.getByTestId('assistant-message').last()).toContainText('已完成');
  } finally { await app?.close(); await backend.cleanup(); rmSync(directory, { recursive: true, force: true }); }
});

test('宿主机 Bash：默认可用、自动选择、三档审批与执行位置审计', async () => {
  const project = mkdtempSync(join(tmpdir(), 'tavo-host-project-'));
  const directory = mkdtempSync(join(tmpdir(), 'tavo-host-desktop-'));
  const pythonPrelude = `from dataclasses import replace
from app.core.config import Settings
original_settings=Settings.from_env.__func__
def fixture_settings(cls):
 s=original_settings(cls)
 host=s.sandbox.host.model_copy(update={'default_cwd':${JSON.stringify(project)}})
 return replace(s,sandbox=s.sandbox.model_copy(update={'host':host}))
Settings.from_env=classmethod(fixture_settings)`;
  const backend = await startBackend({ pythonPrelude });
  let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory, backend); const page = await app.firstWindow();
    await expect(page.getByTestId('backend-state')).toHaveText('后端已连接');
    await openSettings(page);
    const permission = page.getByLabel('命令权限（切换会开始新对话）');
    await expect(permission).toHaveValue('full_access');
    await page.keyboard.press('Escape');
    const send = async (text:string) => { await page.getByRole('textbox',{name:'消息',exact:true}).fill(text); await page.getByRole('button',{name:'发送消息',exact:true}).click(); };
    await send('host: printf desktop-host > note; cat note');
    await expect(page.getByTestId('assistant-message').last()).toContainText('已完成：desktop-host');
    expect(readFileSync(join(project,'note'),'utf8')).toBe('desktop-host');
    await page.locator('.tool-timeline > summary').click();
    await expect(page.locator('.tool-timeline')).toContainText('宿主机 Bash');
    await expect(page.locator('.tool-timeline')).toContainText('无容器隔离');
    await selectChatSetting(page, 'permission', 'auto_review');
    await send('host: cat note');
    await expect(page.getByTestId('assistant-message').last()).toContainText('已完成：desktop-host');
    await page.locator('.tool-timeline > summary').click();
    await expect(page.locator('.tool-timeline')).toContainText('Mock 复核');
    await selectChatSetting(page, 'permission', 'request_approval');
    await send('host: printf approved > approved');
    await expect(page.getByRole('button',{name:'批准工具调用 1',exact:true})).toBeVisible();
    expect(existsSync(join(project,'approved'))).toBe(false);
    await page.screenshot({path:screenshot('host-approval')});
    await page.getByRole('button',{name:'批准工具调用 1',exact:true}).click();
    await expect(page.getByTestId('assistant-message').last()).toContainText('已完成：');
    expect(readFileSync(join(project,'approved'),'utf8')).toBe('approved');
    await send('host: printf rejected > rejected');
    await page.getByRole('button',{name:'拒绝工具调用 1',exact:true}).click();
    await expect(page.getByTestId('assistant-message').last()).toContainText('未获准');
    expect(existsSync(join(project,'rejected'))).toBe(false);
  } finally { await app?.close(); await backend.cleanup(); rmSync(project,{recursive:true,force:true}); rmSync(directory,{recursive:true,force:true}); }
});
