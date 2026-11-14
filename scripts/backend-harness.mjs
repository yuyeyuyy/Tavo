/* global fetch, AbortSignal */
import { spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { createServer } from 'node:net';

export const pause = (ms) => new Promise((done) => setTimeout(done, ms));
export async function freePort() {
  const server = createServer(); await new Promise((done) => server.listen(0, '127.0.0.1', done));
  const port = server.address().port; await new Promise((done) => server.close(done)); return port;
}
export async function startBackend({ directory = mkdtempSync(join(tmpdir(), 'tavo-backend-')), source, origins } = {}) {
  const python = resolve('backend/.venv/bin/python'); const cwd = resolve('backend');
  const execute = (args) => {
    const result = spawnSync(python, ['-m', 'app.cli', ...args, '--data-dir', directory], { cwd, encoding: 'utf8' });
    if (result.status !== 0) throw new Error('Backend initialization/import failed; inspect sanitized CLI locally.');
  };
  execute(['init']); if (source) execute(['import-legacy', '--source', source]);
  const owner = readFileSync(join(directory, 'client.token'), 'utf8').trim();
  const port = await freePort(); const origin = `http://127.0.0.1:${port}`; let child; let session;
  async function request(path, options = {}) {
    const response = await fetch(origin + '/api/v1' + path, { ...options, headers: { Authorization: `Bearer ${session}`, ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers }, signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error(`Backend API ${response.status}`);
    return response.status === 204 ? undefined : response.json();
  }
  async function start() {
    child = spawn(python, ['-m', 'app.cli', 'serve', '--data-dir', directory, '--port', String(port)], { cwd, env: { ...process.env, ...(origins ? { TAVO_CORS_ORIGINS: origins.join(',') } : {}) }, stdio: ['ignore', 'ignore', 'pipe'] });
    child.stderr.on('data', () => {}); // Do not dump process output containing application context.
    for (let i = 0; i < 100; i++) {
      try {
        const response = await fetch(origin + '/api/v1/auth/session', { method: 'POST', headers: { Authorization: `Bearer ${owner}` }, signal: AbortSignal.timeout(500) });
        if (response.ok) { session = (await response.json()).accessToken; return; }
      } catch { /* Wait for the independent listener. */ }
      if ((child.exitCode !== null || child.signalCode !== null)) throw new Error('Independent backend exited during startup.');
      await pause(100);
    }
    throw new Error('Independent backend startup timeout.');
  }
  async function stop() {
    if (child && (child.exitCode === null && child.signalCode === null)) {
      child.kill('SIGTERM');
      for (let i = 0; i < 50 && (child.exitCode === null && child.signalCode === null); i++) await pause(100);
      if ((child.exitCode === null && child.signalCode === null)) { child.kill('SIGKILL'); await new Promise((done) => child.once('exit', done)); }
    }
  }
  await start();
  return { directory, origin, tokenFile: join(directory, 'client.token'), request, start, stop,
    session: () => session,
    docsCredentials: () => ({ username: 'owner', password: owner }),
    async cleanup() { await stop(); rmSync(directory, { recursive: true, force: true }); },
  };
}
