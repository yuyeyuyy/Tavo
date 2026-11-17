/* global fetch, AbortSignal */
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
  // API queues a durable run while the independent Worker is stopped.
  compose(['stop', 'worker']);
  const s = await request('/sessions', 'POST', { profileId: 'mock', reviewMode: 'ask' });
  const accepted = await request(`/sessions/${s.id}/messages`, 'POST', { clientMessageId: 'linux-agent', content: '计算 12 / 3', expectedRevision: s.revision });
  const path = `/sessions/${s.id}/runs/${accepted.run.id}`;
  await pause(500); assert.equal((await request(path)).status, 'pending');
  compose(['start', 'worker']);
  await waitFor(async () => (await request(path)).status === 'waiting_review');
  compose(['restart', 'worker']);
  const waiting = await request(path); const call = waiting.toolCalls[0];
  assert.equal(waiting.status, 'waiting_review');
  await request(`/sessions/${s.id}/tool-calls/${call.id}/decision`, 'POST', { action: 'approve', expectedRevision: call.reviewRevision });
  await waitFor(async () => (await request(path)).status === 'succeeded');
  const finished = await request(path); assert.equal(finished.rounds, 2);
  assert.equal(finished.toolCalls[0].status, 'succeeded');
  const page = await request(`/sessions/${s.id}/messages`);
  assert.deepEqual(page.items.map(m => m.kind), ['user', 'tool_call', 'tool_result', 'assistant']);
  assert.deepEqual(page.items[2].result.output, { result: '4' });
  const protectedStorage = compose(['exec', '-T', 'api', 'python', '-c', 'import os,pathlib,sqlite3; p=pathlib.Path(os.environ["TAVO_KEYRING_FILE"]); c=sqlite3.connect("/data/tavo.sqlite"); rows=c.execute("select ciphertext from assistant_model_turns").fetchall(); assert p.stat().st_mode & 0o777 == 0o600; assert len(rows)==2 and all(b"provider_id" not in r[0] for r in rows); print("ok")']);
  assert.equal(protectedStorage, 'ok');
  compose(['restart', 'api']);
  await waitFor(async () => { try { return (await request('/health')).status === 'ok'; } catch { return false; } });
  assert.equal((await request(`/sessions/${s.id}`)).latestRunId, accepted.run.id);
  assert.equal((await request(path)).status, 'succeeded');
  const retired = await fetch(origin + '/api/v1/accounts', { headers: { Authorization: `Bearer ${token}` } }); assert.equal(retired.status, 410);
  console.log('Linux Compose passed: non-root/no Node, authenticated REST, separate Worker queue, review across Worker restart, encrypted continuation, tools and API restart persistence.');
} finally {
  // This unique project and its volumes were created solely by this script.
  compose(['down', '--volumes', '--remove-orphans']);
}
