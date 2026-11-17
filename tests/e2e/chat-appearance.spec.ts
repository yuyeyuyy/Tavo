import { expect, test, type ElectronApplication } from '@playwright/test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startBackend } from '../../scripts/backend-harness.mjs';
import { launchApp, openSettings, screenshot } from './chat-helpers';

test('外观：系统跟随、独立持久化、设置焦点与存储不可用降级', async () => {
  const backend = await startBackend(); const directory = mkdtempSync(join(tmpdir(), 'tavo-appearance-')); let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory, backend); let page = await app.firstWindow();
    let root = page.locator('html'); const input = page.getByRole('textbox', { name: '消息', exact: true });
    await expect(page.getByTestId('backend-state')).toHaveText('后端已连接');
    await expect(root).toHaveAttribute('data-theme-preference', 'system');
    await page.emulateMedia({ colorScheme: 'light' }); await expect(root).toHaveAttribute('data-theme', 'light');
    await page.screenshot({ path: screenshot('light-welcome') });
    await page.emulateMedia({ colorScheme: 'dark' }); await expect(root).toHaveAttribute('data-theme', 'dark');
    await input.fill('你好'); await input.press('Enter');
    await expect(page.getByTestId('assistant-message')).toContainText('服务端 Mock');
    const before = await backend.request<{ items: { id: string; revision: number }[] }>('/sessions');
    await input.fill('尚未发送的草稿');
    expect(await page.locator('.composer').count()).toBe(1);
    expect(await page.locator('.composer select, .composer-hint, .chat-footer, .bash-notice').count()).toBe(0);
    expect(await page.getByRole('combobox').count()).toBe(0);
    await openSettings(page);
    await expect(page.getByLabel('选择模型（切换会开始新对话）')).toBeVisible();
    await expect(page.getByLabel('命令权限（切换会开始新对话）')).toHaveValue('full_access');
    await page.getByRole('radio', { name: '浅色', exact: true }).check();
    await expect(root).toHaveAttribute('data-theme', 'light');
    await expect(page.getByRole('button', { name: '保存连接', exact: true })).toHaveCSS('color', 'rgb(255, 255, 255)');
    await page.emulateMedia({ colorScheme: 'dark' }); await expect(root).toHaveAttribute('data-theme', 'light');
    await page.screenshot({ path: screenshot('light-settings') });
    await page.getByRole('radio', { name: '深色', exact: true }).check();
    await expect(root).toHaveAttribute('data-theme', 'dark');
    await page.screenshot({ path: screenshot('dark-settings') });
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: '打开设置', exact: true })).toBeFocused();
    await expect(input).toHaveValue('尚未发送的草稿');
    await expect(page.getByTestId('user-message')).toHaveText('你好');
    expect(await backend.request('/sessions')).toEqual(before);
    await app.close(); app = await launchApp(directory, backend); page = await app.firstWindow(); root = page.locator('html');
    await expect(root).toHaveAttribute('data-theme-preference', 'dark');
    await expect(page.getByTestId('user-message')).toHaveText('你好');
    await page.emulateMedia({ colorScheme: 'light' }); await expect(root).toHaveAttribute('data-theme', 'dark');
    await page.evaluate(() => localStorage.setItem('tavo.theme', 'invalid'));
    await page.reload(); await expect(root).toHaveAttribute('data-theme-preference', 'system');
    await expect(root).toHaveAttribute('data-theme', 'light');
    await page.addInitScript(() => Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new Error('Storage blocked'); } }));
    await page.reload(); await expect(root).toHaveAttribute('data-theme-preference', 'system');
    await openSettings(page); await page.getByRole('radio', { name: '深色', exact: true }).check();
    await expect(root).toHaveAttribute('data-theme', 'dark'); await page.keyboard.press('Escape');
    await expect(page.getByTestId('user-message')).toHaveText('你好');
  } finally { await app?.close(); await backend.cleanup(); rmSync(directory, { recursive: true, force: true }); }
});

const markdown = [
  '# 开发记录', '', '这是 **粗体**、~~删除线~~和 `inline_code`。', '中文第二行保持换行。', '',
  '> 引用：脚本保存在任务工作区。', '', '- 第一项', '  - 嵌套项目', '- 第二项', '',
  '- [x] 已验证', '- [ ] 待处理', '',
  '```python', 'print("hello Tavo")', `# ${'long-code-'.repeat(35)}`, '```', '',
  '| 项目 | 状态 |', '| :--- | ---: |', '| Python | 通过 |', `| ${'宽表格内容'.repeat(22)} | 结果 |`, '',
  '[官方文档](https://example.com/docs?q=tavo#intro)', '',
  '[危险脚本](javascript:alert%281%29) [本地文件](file:///etc/passwd) [数据](data:text/html,test) [自定义协议](tavo://app/index.html)', '',
  '![远程图片](https://example.com/tracking.png)', '',
  '<script>globalThis.markdownExecuted=true</script>', '<img src="https://example.com/raw.png" onerror="globalThis.markdownExecuted=true">', '<iframe src="https://example.com"></iframe>', '',
  '脚注示例[^note]。', '', '[^note]: 注释内容。', '',
  '```text', '不完整围栏也应显示为代码',
].join('\n');

test('Markdown：格式、长内容、纯文本存储、外链点击与不可信内容隔离', async () => {
  const pythonPrelude = `from app.agent.mock import MockAssistantModel
from app.llm.turns import AssistantTurn
original_turn = MockAssistantModel.turn
async def markdown_turn(self, profile, history, specs, credential=None):
    if history[-1].get("role") == "user" and history[-1].get("content", "").startswith("markdown-fixture"):
        text = ${JSON.stringify(markdown)}
        return AssistantTurn(text=text, native={"role": "assistant", "content": text})
    return await original_turn(self, profile, history, specs, credential)
MockAssistantModel.turn = markdown_turn`;
  const backend = await startBackend({ pythonPrelude }); const directory = mkdtempSync(join(tmpdir(), 'tavo-markdown-')); let app: ElectronApplication | undefined;
  try {
    app = await launchApp(directory, backend); const page = await app.firstWindow();
    const errors: string[] = [], externalRequests: string[] = [];
    page.on('pageerror', error => errors.push(error.message)); page.on('request', request => { if (request.url().startsWith('https://')) externalRequests.push(request.url()); });
    await app.evaluate(({ shell }) => {
      const urls: string[] = []; Reflect.set(globalThis, 'testExternalUrls', urls);
      shell.openExternal = async url => { urls.push(url); };
    });
    await expect(page.getByTestId('backend-state')).toHaveText('后端已连接');
    await page.emulateMedia({ colorScheme: 'light' });
    const input = page.getByRole('textbox', { name: '消息', exact: true });
    await input.fill('markdown-fixture'); await input.press('Enter');
    const message = page.getByTestId('assistant-message').last();
    await expect(message.getByRole('heading', { name: '开发记录' })).toBeVisible();
    await expect(message.locator('strong')).toHaveText('粗体'); await expect(message.locator('del')).toHaveText('删除线');
    await expect(message.locator('ul ul li')).toHaveText('嵌套项目'); await expect(message.locator('blockquote')).toContainText('引用');
    await expect(message.getByRole('checkbox', { name: '已完成事项' })).toBeChecked();
    await expect(message.getByRole('checkbox', { name: '未完成事项' })).toBeDisabled();
    await expect(message.locator('th').last()).toHaveCSS('text-align', 'right');
    await expect(message.locator('pre').last()).toContainText('不完整围栏');
    expect(await message.locator('script, img, iframe, [style], [onerror]').count()).toBe(0);
    expect(await page.evaluate(() => Reflect.get(globalThis, 'markdownExecuted'))).toBeUndefined();
    expect(await message.getByRole('link', { name: /危险脚本|本地文件|数据|自定义协议/ }).count()).toBe(0);
    const link = message.getByRole('link', { name: '官方文档', exact: true });
    await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    await link.evaluate(node => (node as HTMLAnchorElement).click());
    expect(await app.evaluate(() => Reflect.get(globalThis, 'testExternalUrls'))).toEqual([]);
    const originalUrl = page.url(); await link.click();
    await expect.poll(() => app!.evaluate(() => Reflect.get(globalThis, 'testExternalUrls'))).toEqual(['https://example.com/docs?q=tavo#intro']);
    expect(page.url()).toBe(originalUrl); expect(await app.windows()).toHaveLength(1);
    await page.evaluate(() => { window.open('file:///etc/passwd'); window.open('javascript:alert(1)'); window.open('https://user:pass@example.com'); });
    expect(await app.evaluate(() => Reflect.get(globalThis, 'testExternalUrls'))).toEqual(['https://example.com/docs?q=tavo#intro']);
    const footnote = message.locator('a[href^="#md-"]').first(); await footnote.click(); expect(page.url()).toBe(originalUrl);
    expect(externalRequests).toEqual([]);
    const sessions = await backend.request<{ items: { id: string }[] }>('/sessions');
    const stored = await backend.request<{ items: { kind: string; content: string }[] }>(`/sessions/${sessions.items[0]!.id}/messages`);
    expect(stored.items.find(item => item.kind === 'assistant')?.content).toBe(markdown);
    await message.getByRole('heading', { name: '开发记录' }).scrollIntoViewIfNeeded(); await page.screenshot({ path: screenshot('light-markdown') });
    await page.emulateMedia({ colorScheme: 'dark' }); await page.screenshot({ path: screenshot('dark-markdown') });
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]!.setSize(620, 540));
    await expect.poll(() => page.evaluate(() => ({ width: innerWidth, height: innerHeight, overflowX: document.documentElement.scrollWidth > innerWidth, overflowY: document.documentElement.scrollHeight > innerHeight }))).toEqual({ width: 620, height: 540, overflowX: false, overflowY: false });
    await message.getByRole('heading', { name: '开发记录' }).scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight)).toBe(true);
    expect(await message.locator('pre').first().evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
    expect(await message.locator('.markdown-table').evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
    await page.screenshot({ path: screenshot('narrow-markdown') });
    await input.fill('markdown-fixture 第二条'); await input.press('Enter');
    await expect(page.getByTestId('assistant-message')).toHaveCount(2);
    await expect(page.getByTestId('assistant-message').last().getByRole('heading', { name: '开发记录' })).toBeVisible();
    const ids = await page.locator('.markdown-message [id]').evaluateAll(nodes => nodes.map(node => node.id));
    expect(ids.length).toBeGreaterThanOrEqual(6);
    expect(new Set(ids).size).toBe(ids.length);
    const backref = page.getByTestId('assistant-message').last().locator('a[href^="#md-"]').last();
    const referenceId = (await backref.getAttribute('href'))!.slice(1);
    await expect(page.locator(`[id="${referenceId}"]`)).toHaveCount(1);
    await backref.click(); expect(page.url()).toBe(originalUrl);
    expect(errors).toEqual([]);
  } finally { await app?.close(); await backend.cleanup(); rmSync(directory, { recursive: true, force: true }); }
});
