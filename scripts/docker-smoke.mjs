/* global fetch, AbortSignal, TextDecoder */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { freePort, pause } from './backend-harness.mjs';

const project = `tavo-test-${process.pid}`;
const port = await freePort();
const origin = `http://127.0.0.1:${port}`;
const env = { ...process.env, TAVO_HTTP_PORT: String(port) };
function compose(args) {
  const result = spawnSync('docker', ['compose', '-p', project, ...args], { env, encoding: 'utf8' });
  if (result.status !== 0) throw new Error(`Isolated Compose operation failed: ${args[0]}`);
  return result.stdout.trim();
}
let token;
async function request(path, method = 'GET', body) {
  const response = await fetch(origin + '/api/v1' + path, { method, headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(5000) });
  assert(response.ok, `API ${response.status}`); return response.status === 204 ? undefined : response.json();
}
async function waitFor(check) {
  for (let i = 0; i < 150; i++) { const value = await check(); if (value) return value; await pause(100); }
  throw new Error('Headless business result timeout');
}
try {
  compose(['up', '--no-build', '-d']);
  const platform = JSON.parse(compose(['exec', '-T', 'api', 'python', '-c', 'import sys,os,shutil,json; print(json.dumps([sys.platform,os.geteuid(),shutil.which("node")]))']));
  assert.deepEqual(platform, ['linux', 10001, null]);
  // Captured privately in this process; never sent to logs, arguments, URLs or test reports.
  const owner = compose(['exec', '-T', 'api', 'python', '-c', 'import os,pathlib; print(pathlib.Path(os.environ["TAVO_CLIENT_TOKEN_FILE"]).read_text().strip())']);
  await waitFor(async () => {
    try {
      const response = await fetch(origin + '/api/v1/auth/session', { method: 'POST', headers: { Authorization: `Bearer ${owner}` }, signal: AbortSignal.timeout(500) });
      if (!response.ok) return false;
      token = (await response.json()).accessToken; return true;
    } catch { return false; }
  });
  assert.equal((await fetch(origin + '/api/v1/health')).status, 401);
  assert.equal((await request('/health')).status, 'ok');
  assert.equal((await request('/system/status')).mode, 'single-owner');
  // Prove that the API creates a durable pending task without its own worker.
  compose(['stop', 'worker']);
  const account = await request('/accounts', 'POST', { connectorId: 'mock', externalAccountId: 'linux', displayName: 'Linux Mock' });
  const job = await request(`/accounts/${account.id}/sync`, 'POST');
  await pause(500); assert.equal((await request(`/sync-tasks/${job.id}`)).status, 'pending');
  compose(['start', 'worker']);
  await waitFor(async () => (await request(`/sync-tasks/${job.id}`)).status === 'succeeded');
  const first = await request('/snapshot'); assert.equal(first.data[0].balance, '8.50');
  const event = first.businessEvents.find((row) => row.eventType === 'data.changed'); assert(event);
  const run = await request('/agent/analyze', 'POST', { eventId: event.eventId });
  await waitFor(async () => (await request(`/agent/runs/${run.id}`)).status === 'succeeded');
  assert.equal((await request(`/agent/runs/${run.id}`)).result.severity, 'warning');
  const response = await fetch(origin + '/api/v1/events/stream?after=0', { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(5000) });
  assert.equal(response.status, 200); const reader = response.body.getReader();
  let stream = ''; const decoder = new TextDecoder();
  while (!stream.includes('event: business')) { const chunk = await reader.read(); assert(!chunk.done); stream += decoder.decode(chunk.value); }
  await reader.cancel(); assert(stream.includes('id: 1'));
  const protectedStorage = compose(['exec', '-T', 'api', 'python', '-c', 'import os,pathlib,sqlite3; p=pathlib.Path(os.environ["TAVO_KEYRING_FILE"]); c=sqlite3.connect("/data/tavo.sqlite"); r=c.execute("select ciphertext from secrets").fetchall(); assert p.stat().st_mode & 0o777 == 0o600; assert len(r)==1 and b"mock-only:" not in r[0][0]; print("ok")']);
  assert.equal(protectedStorage, 'ok');
  compose(['restart', 'api']);
  await waitFor(async () => { try { return (await request('/health')).status === 'ok'; } catch { return false; } });
  assert.equal((await request('/snapshot')).jobs[0].id, job.id);
  console.log('Linux arm64 Compose passed: non-root/no Node, authenticated REST, separate Worker queue/restart, encrypted credentials, SSE, Python Agent and API restart persistence.');
} finally {
  // This unique project and its volumes were created solely by this script.
  compose(['down', '--volumes', '--remove-orphans']);
}
