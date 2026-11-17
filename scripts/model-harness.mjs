/* global Buffer */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { startBackend } from './backend-harness.mjs';

// A real loopback HTTP provider with synthetic credentials, never a vendor call.
export async function startModelBackend({ withKey = true, origins, empty = false } = {}) {
  let failures = 0, delay = 0;
  const calls = [];
  const server = createServer(async (request, response) => {
    let data = ''; for await (const chunk of request) data += chunk;
    assert.equal(request.headers.authorization, 'Bearer synthetic-model-test-key');
    const value = JSON.parse(data); calls.push(value);
    assert(!data.includes('synthetic-model-test-key'));
    if (failures > 0) { failures--; response.writeHead(429, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ private: 'synthetic-private-upstream' })); return; }
    if (delay) await new Promise((done) => setTimeout(done, delay));
    response.writeHead(200, { 'Content-Type': 'application/json' });
    if (value.messages?.[0]?.content?.startsWith("You are Tavo's tool safety reviewer")) {
      assert(!value.tools?.length);
      const payload = JSON.parse(value.messages.at(-1).content);
      response.end(JSON.stringify({ choices: [{ finish_reason: 'stop', message: { role: 'assistant', content: JSON.stringify({decisions:payload.calls.map(c=>({toolCallId:c.toolCallId,action:'allow',reasonCode:'GOAL_ALIGNED',summary:'Loopback model review'}))}) } }] })); return;
    }
    if (value.tools?.length && value.messages.some((m) => m.role === 'user' && m.content === '计算工具闭环')) {
      const result = value.messages.at(-1);
      if (result.role === 'tool') {
        assert.equal(result.tool_call_id, 'loopback-call');
        assert.deepEqual(JSON.parse(result.content).output, { result: '4' });
      } else {
        response.end(JSON.stringify({ choices: [{ finish_reason: 'tool_calls', message: { role: 'assistant', content: null, tool_calls: [{ id: 'loopback-call', type: 'function', function: { name: 'calculate', arguments: JSON.stringify({ operation: 'divide', left: '12', right: '3' }) } }] } }] })); return;
      }
    }
    response.end(JSON.stringify({ choices: [{ finish_reason: 'stop', message: { role: 'assistant', content: '本地 HTTP 替身回复；未调用真实厂商。' } }], usage: { prompt_tokens: 8, completion_tokens: 12 } }));
  });
  await new Promise((done) => server.listen(0, '127.0.0.1', done));
  const port = server.address().port;
  const endpoint = `http://127.0.0.1:${port}/v1`;
  const backend = await startBackend({ origins,
    ...(!empty ? { models: { defaultProfileId: 'local-test', profiles: [{ id: 'local-test', provider: 'deepseek', label: '本地协议测试', model: 'fixture-text', enabled: true, baseUrl: endpoint }, { id: 'no-key', provider: 'openai', label: '缺少 Key', model: 'fixture-text', enabled: true }] } } : {}),
    ...(empty ? { pythonPrelude: `from app.llm.config import PRESETS\nPRESETS['deepseek'] = ('completions', '${endpoint}')` } : {}),
  });
  try {
    // Synthetic fixture only: the real UI and hidden CLI use the same persistent directory.
    if (withKey && !empty) {
      const result = spawnSync(resolve('backend/.venv/bin/python'), ['-c', 'from pathlib import Path; import sys; from pydantic import SecretStr; from app.core.config import Settings; from app.llm.keys import ModelKeys; from app.services.model_profiles import ModelProfileService; from app.core.security import SecretStore; from app.repositories.database import Database; d=Path(sys.argv[1]); s=Settings(data_dir=d,keyring_file=d/"master.keys",client_token_file=d/"client.token"); db=Database(s.database); store=SecretStore(db,s); models=ModelProfileService(db,store,s.models_file); ModelKeys(store).put(models.profile("local-test"),SecretStr("synthetic-model-test-key")); db.close()', backend.directory], { cwd: resolve('backend') });
      assert.equal(result.status, 0);
    }
    assert(!readFileSync(join(backend.directory, 'tavo.sqlite')).includes(Buffer.from('synthetic-model-test-key')));
    return { backend, calls, failNext: (count = 1) => { failures = count; }, setDelay: (value) => { delay = value; }, async cleanup() { await backend.cleanup(); await new Promise((done) => server.close(done)); } };
  } catch (error) { await backend.cleanup(); server.close(); throw error; }
}
