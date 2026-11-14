/* global fetch, AbortController, TextDecoder, Buffer */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { startBackend } from './backend-harness.mjs';

const backend = await startBackend();
const controllers = [];
async function nextEvent(after) {
  const controller = new AbortController(); controllers.push(controller);
  const response = await fetch(`${backend.origin}/api/v1/events/stream?after=${after}`, { headers: { Authorization: `Bearer ${backend.session()}`, 'Last-Event-ID': String(after) }, signal: controller.signal });
  assert.equal(response.status, 200); const reader = response.body.getReader(); let buffer = '';
  const decoder = new TextDecoder();
  for (;;) {
    const chunk = await reader.read(); assert.equal(chunk.done, false);
    buffer += decoder.decode(chunk.value, { stream: true });
    const match = buffer.match(/id: (\d+)\nevent: business\ndata: ([^\n]+)\n\n/);
    if (match) { const event = JSON.parse(match[2]); assert.equal(event.sequence, Number(match[1])); controller.abort(); return event; }
  }
}
try {
  assert.equal((await fetch(backend.origin + '/api/v1/health')).status, 401);
  const account = await backend.request('/accounts', { method: 'POST', body: JSON.stringify({ connectorId: 'mock', externalAccountId: 'standalone', displayName: 'Headless Mock' }) });
  const connected = await nextEvent(0); assert.equal(connected.eventType, 'account.connected');
  const job = await backend.request(`/accounts/${account.id}/sync`, { method: 'POST' });
  let completed;
  for (let i = 0; i < 60; i++) {
    completed = await backend.request(`/sync-tasks/${job.id}`);
    if (completed.status === 'succeeded') break;
    await new Promise((done) => setTimeout(done, 100));
  }
  assert.equal(completed.status, 'succeeded');
  const later = await nextEvent(connected.sequence); assert(later.sequence > connected.sequence);
  const before = await backend.request('/snapshot'); const changed = before.businessEvents.find((event) => event.eventType === 'data.changed'); assert(changed);
  const run = await backend.request('/agent/analyze', { method: 'POST', body: JSON.stringify({ eventId: changed.eventId }) });
  let result;
  for (let i = 0; i < 60; i++) {
    result = await backend.request(`/agent/runs/${run.id}`); if (result.status === 'succeeded') break;
    await new Promise((done) => setTimeout(done, 100));
  }
  assert.equal(result.status, 'succeeded'); assert.equal(result.result.severity, 'warning');
  await backend.stop(); await backend.start();
  const restored = await backend.request('/snapshot'); assert.equal(restored.data.length, 1); assert.equal(restored.jobs[0].id, job.id);
  const replay = await nextEvent(later.sequence); assert(replay.sequence > later.sequence);
  assert(!JSON.stringify(restored).includes('mock-only:'));
  assert(!readFileSync(backend.directory + '/tavo.sqlite').includes(Buffer.from('mock-only:')));
  console.log('Independent backend: auth, REST, durable sync, SSE replay/reconnect, Python Agent and process restart passed.');
} finally { for (const controller of controllers) controller.abort(); await backend.cleanup(); }
