/* global fetch, AbortController */
import assert from 'node:assert/strict';
import { startModelBackend } from './model-harness.mjs';
const fixture = await startModelBackend(); const { backend } = fixture;
try {
  const catalog = await backend.request('/chat/models');
  assert.equal(catalog.items[0].ready, true); assert.equal(catalog.items[1].ready, false);
  const payload = { profileId: 'local-test', messages: [{ role: 'user', content: 'hello' }] };
  const answer = await backend.request('/chat/completions', { method: 'POST', body: JSON.stringify(payload) });
  assert.equal(answer.provider, 'deepseek'); assert(answer.message.content.includes('本地 HTTP 替身'));
  fixture.failNext();
  assert((await backend.request('/chat/completions', { method: 'POST', body: JSON.stringify(payload) })).message.content);
  assert.equal(fixture.calls.length, 3);
  fixture.failNext(4);
  const failed = await fetch(backend.origin + '/api/v1/chat/completions', { method: 'POST', headers: { Authorization: `Bearer ${backend.session()}`, 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  assert.equal(failed.status, 429); assert.equal((await failed.json()).error.code, 'MODEL_RATE_LIMITED');
  fixture.setDelay(1200);
  const controller = new AbortController();
  const pending = fetch(backend.origin + '/api/v1/chat/completions', { method: 'POST', headers: { Authorization: `Bearer ${backend.session()}`, 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal: controller.signal }).catch(() => null);
  for (let i = 0; i < 30 && fixture.calls.length < 8; i++) await new Promise((done) => setTimeout(done, 30));
  controller.abort(); await pending; fixture.setDelay(0);
  await new Promise((done) => setTimeout(done, 200));
  assert((await backend.request('/chat/completions', { method: 'POST', body: JSON.stringify(payload) })).message.content);
  await backend.stop(); await backend.start();
  assert.equal((await backend.request('/chat/models')).items[0].ready, true);
  assert((await backend.request('/chat/completions', { method: 'POST', body: JSON.stringify(payload) })).message.content);
  const post = (path, body) => backend.request(path, { method: 'POST', body: JSON.stringify(body) });
  const s = await post('/sessions', { profileId: 'local-test', reviewMode: 'auto_readonly' });
  const accepted = await post(`/sessions/${s.id}/messages`, { clientMessageId: 'loopback-agent', content: '计算工具闭环', expectedRevision: s.revision });
  let run;
  for (let i = 0; i < 80; i++) {
    run = await backend.request(`/sessions/${s.id}/runs/${accepted.run.id}`);
    if (!['pending', 'running'].includes(run.status)) break;
    await new Promise((done) => setTimeout(done, 100));
  }
  assert.equal(run.status, 'succeeded'); assert.equal(run.rounds, 2);
  assert.equal(run.toolCalls[0].name, 'calculate'); assert.equal(run.toolCalls[0].status, 'succeeded');
  console.log('Real loopback model smoke passed: encrypted server key, authenticated catalog/chat, provider error, disconnect, retry, restart and durable two-round Agent tool loop. No vendor requests.');
} finally { await fixture.cleanup(); }
