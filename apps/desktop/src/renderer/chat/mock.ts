import type { ChatTransport } from './types';
function wait(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(new Error('CANCELLED')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, 900);
    signal.addEventListener('abort', abort, { once: true }); if (signal.aborted) abort();
  });
}
export const mockTransport: ChatTransport = {
  async reply(messages, signal) {
    await wait(signal);
    const text = messages.at(-1)?.content ?? '';
    if (/记忆|memory/i.test(text)) return '记忆像一份可以跨对话使用的笔记，上下文则是这一轮实际翻开的几页。\n\n当前离线演示不会自动保存长期记忆。连接后端后，会话历史会持久化，Context 只选择本轮需要的历史。';
    if (/工具|tool/i.test(text)) return '我可以协助分析问题、编写代码和验证结果。这里是离线 Mock 演示，请连接后端后开始实际任务。';
    if (/tavo|你好|你能|什么/i.test(text)) return '你好，我是 Tavo。这里是离线对话演示。\n\n目前是本地 Mock 演示，你可以体验发送消息、停止回复和开始新对话。连接后端后可选择模型，继续处理任务。';
    return '收到你的消息了。\n\n当前使用本地 Mock 回复，还不能真正回答这个问题。对话没有发送给外部模型。你可以试着问我“记忆和上下文有什么区别”。';
  },
};
