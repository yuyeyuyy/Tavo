/* global fetch */
import assert from 'node:assert/strict';
import { startBackend, pause } from './backend-harness.mjs';

const backend = await startBackend();
const post = (path, body) => backend.request(path, { method: 'POST', ...(body ? { body: JSON.stringify(body) } : {}) });
async function waitRun(session, run, status) {
  for (let i = 0; i < 100; i++) {
    const value = await backend.request(`/sessions/${session}/runs/${run}`);
    if (value.status === status) return value;
    if (['failed', 'interrupted', 'cancelled'].includes(value.status)) throw new Error(`Run ended: ${value.status}`);
    await pause(100);
  }
  throw new Error('Session run timeout');
}
try {
  assert.equal((await fetch(backend.origin + '/api/v1/health')).status, 401);
  assert.equal((await backend.request('/system/status')).mode, 'single-owner');
  const s = await post('/sessions', { profileId: 'mock', reviewMode: 'ask' });
  const accepted = await post(`/sessions/${s.id}/messages`, { clientMessageId: 'smoke-1', content: '计算 12 / 3', expectedRevision: s.revision });
  const replay = await post(`/sessions/${s.id}/messages`, { clientMessageId: 'smoke-1', content: '计算 12 / 3', expectedRevision: s.revision });
  assert.equal(replay.run.id, accepted.run.id);
  const waiting = await waitRun(s.id, accepted.run.id, 'waiting_review');
  assert.equal(waiting.toolCalls[0].decisions[0].action, 'ask');
  await backend.stop(); await backend.start();
  const restored = await backend.request(`/sessions/${s.id}/runs/${accepted.run.id}`);
  assert.equal(restored.status, 'waiting_review');
  const call = restored.toolCalls[0];
  await post(`/sessions/${s.id}/tool-calls/${call.id}/decision`, { action: 'approve', expectedRevision: call.reviewRevision });
  const finished = await waitRun(s.id, accepted.run.id, 'succeeded');
  assert.equal(finished.toolCalls[0].status, 'succeeded'); assert.equal(finished.rounds, 2);
  const messages = await backend.request(`/sessions/${s.id}/messages`);
  assert.deepEqual(messages.items.map((m) => m.kind), ['user', 'tool_call', 'tool_result', 'assistant']);
  assert.deepEqual(messages.items[2].result.output, { result: '4' });
  const history = await backend.request('/sessions'); assert.equal(history.items[0].id, s.id);
  for (const path of ['/accounts', '/snapshot', '/events/stream', '/settings']) {
    const response = await fetch(backend.origin + '/api/v1' + path, { headers: { Authorization: `Bearer ${backend.session()}` } });
    assert.equal(response.status, 410); assert.equal((await response.json()).error.code, 'FEATURE_REMOVED');
  }
  console.log('Independent backend passed: authentication, durable Session/messages, idempotent enqueue, review across process restart, approved tool loop and retired routes.');
} finally { await backend.cleanup(); }
