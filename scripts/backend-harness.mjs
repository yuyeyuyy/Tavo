/* global fetch, AbortSignal */
import { spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, realpathSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { createServer } from 'node:net';
import { createHash } from 'node:crypto';

export const pause = (ms) => new Promise((done) => setTimeout(done, ms));
export async function freePort() {
  const server = createServer(); await new Promise((done) => server.listen(0, '127.0.0.1', done));
  const port = server.address().port; await new Promise((done) => server.close(done)); return port;
}
export async function startBackend({ directory = mkdtempSync(join(tmpdir(), 'tavo-backend-')), origins, models, pythonPrelude, bash = false } = {}) {
  const python = resolve('backend/.venv/bin/python'); const cwd = resolve('backend');
  if (models) writeFileSync(join(directory, 'models.json'), JSON.stringify(models));
  // Test-owned injection only; the production CLI has no endpoint override from Renderer.
  const entry = pythonPrelude ? ['-c', `${pythonPrelude}\nfrom app.cli import main\nmain()`] : ['-m', 'app.cli'];
  const execute = (args) => {
    const result = spawnSync(python, [...entry, ...args, '--data-dir', directory], { cwd, encoding: 'utf8' });
    if (result.status !== 0) throw new Error('Backend initialization/import failed; inspect sanitized CLI locally.');
  };
  execute(['init']);
  const owner = readFileSync(join(directory, 'client.token'), 'utf8').trim();
  const port = await freePort(); const origin = `http://127.0.0.1:${port}`; let child; let session;
  async function request(path, options = {}) {
    const response = await fetch(origin + '/api/v1' + path, { ...options, headers: { Authorization: `Bearer ${session}`, ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers }, signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error(`Backend API ${response.status}`);
    return response.status === 204 ? undefined : response.json();
  }
  async function start() {
    let diagnostic = '';
    child = spawn(python, [...entry, 'serve', '--data-dir', directory, '--port', String(port)], { cwd, env: { ...process.env, TAVO_BASH_ENABLED: bash ? '1' : '0', ...(origins ? { TAVO_CORS_ORIGINS: origins.join(',') } : {}) }, stdio: ['ignore', 'ignore', 'pipe'] });
    child.stderr.on('data', chunk => { diagnostic = (diagnostic + chunk.toString()).slice(-8192); });
    for (let i = 0; i < 100; i++) {
      try {
        const response = await fetch(origin + '/api/v1/auth/session', { method: 'POST', headers: { Authorization: `Bearer ${owner}` }, signal: AbortSignal.timeout(500) });
        if (response.ok) { session = (await response.json()).accessToken; return; }
      } catch { /* Wait for the independent listener. */ }
      if ((child.exitCode !== null || child.signalCode !== null)) {
        const codes = diagnostic.match(/\b(?:BASH|DOCKER|SANDBOX)_[A-Z_]+\b/g) ?? [];
        throw new Error(`Independent backend exited during startup (${[...new Set(codes)].join(',') || 'no public error code'}).`);
      }
      await pause(100);
    }
    throw new Error('Independent backend startup timeout.');
  }
  async function stop() {
    if (child && (child.exitCode === null && child.signalCode === null)) {
      child.kill('SIGTERM');
      // Egress sealing, container removal and volume cleanup need time to finish.
      for (let i = 0; i < 200 && (child.exitCode === null && child.signalCode === null); i++) await pause(100);
      if ((child.exitCode === null && child.signalCode === null)) { child.kill('SIGKILL'); await new Promise((done) => child.once('exit', done)); }
    }
  }
  await start();
  return { directory, origin, tokenFile: join(directory, 'client.token'), request, start, stop,
    session: () => session,
    docsCredentials: () => ({ username: 'owner', password: owner }),
    async cleanup() { await stop();
      if (bash) {
        const instance = createHash('sha256').update(realpathSync(join(directory,'tavo.sqlite'))).digest('hex').slice(0,16);
        const list = (type, filter, format) => {
          const r=spawnSync('docker',[type,'ls','--filter',filter,'--format',format],{encoding:'utf8'});
          if(r.status!==0) throw new Error('Cannot confirm test sandbox cleanup');
          return r.stdout.trim().split('\n').filter(Boolean);
        };
        for(const id of list('container','label=tavo.instance='+instance,'{{.ID}}')) {
          if(!/^[a-f0-9]+$/.test(id)) throw new Error('Invalid fixture container identity');
          if(spawnSync('docker',['rm','-f',id]).status!==0) throw new Error('Fixture container cleanup failed');
        }
        for(const name of list('volume','label=tavo.instance='+instance,'{{.Name}}')) {
          if(!new RegExp('^(?:tavo-(?:bash|work)-'+instance+'-[a-f0-9]{24}|tavo-egress-'+instance+'-[a-f0-9]{32})$').test(name)) throw new Error('Invalid fixture volume identity');
          if(spawnSync('docker',['volume','rm',name]).status!==0) throw new Error('Fixture volume cleanup failed');
        }
      }
      rmSync(directory, { recursive: true, force: true }); },
  };
}
