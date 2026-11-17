import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startBackend, pause } from './backend-harness.mjs';

const project = mkdtempSync(join(tmpdir(), 'tavo-host-smoke-'));
const pythonPrelude = `from dataclasses import replace
from app.core.config import Settings
original=Settings.from_env.__func__
def fixture(cls):
 s=original(cls)
 host=s.sandbox.host.model_copy(update={'default_cwd':${JSON.stringify(project)}})
 return replace(s,sandbox=s.sandbox.model_copy(update={'host':host}))
Settings.from_env=classmethod(fixture)`;
let backend;
try {
  backend = await startBackend({ pythonPrelude });
  const post=(path,value)=>backend.request(path,{method:'POST',body:JSON.stringify(value)});
  const runtime=await backend.request('/agent-runtime');
  assert.equal(runtime.bash.enabled,false); assert.equal(runtime.hostBash.available,true);
  const s=await post('/sessions',{profileId:'mock'});
  assert.equal(s.executionScope,'host_and_sandbox');
  const execute=async(content,id)=>{
    const current=await backend.request(`/sessions/${s.id}`);
    const a=await post(`/sessions/${s.id}/messages`,{content,clientMessageId:id,expectedRevision:current.revision});
    let run;
    for(let i=0;i<150;i++) {run=await backend.request(`/sessions/${s.id}/runs/${a.run.id}`);if(!['pending','running'].includes(run.status))break;await pause(100);}
    assert.equal(run.status,'succeeded'); assert.equal(run.context.policyVersion,'5');
    assert.equal(run.toolCalls[0].name,'host_bash'); assert.equal(run.toolCalls[0].attempts,1);
    assert.ok(run.toolCalls[0].decisions.every(d=>d.source==='policy'));
    const messages=await backend.request(`/sessions/${s.id}/messages`);
    return messages.items.filter(m=>m.kind==='tool_result').at(-1).result.output;
  };
  const first=await execute('host: printf host-smoke > note; cat note','first');
  assert.equal(first.stdout,'host-smoke'); assert.equal(first.executionTarget,'host'); assert.equal(first.sandboxId,null);
  const system=await execute('host: uname -s; id -u; ps -p $$ -o comm=; printf temporary-log > task.log; tail -n 1 task.log','system');
  assert.match(system.stdout,/Darwin|Linux/); assert.match(system.stdout,/temporary-log/);
  if(process.env.TAVO_TEST_DOCKER==='1') {
    const docker=await execute("host: docker version --format '{{.Server.Version}}'",'docker-status');
    assert.match(docker.stdout,/\d+\.\d+/);
  }
  await backend.stop(); await backend.start();
  const second=await execute('host: cat note','after-restart');
  assert.equal(second.stdout,'host-smoke'); assert.equal(readFileSync(join(project,'note'),'utf8'),'host-smoke');
  console.log('Independent host passed: sandbox disabled, default dual capability, own system/process/temp-log commands, optional read-only Docker status, policy approval, exit metadata and backend restart. No user project or remote deployment.');
} finally { await backend?.cleanup(); rmSync(project,{recursive:true,force:true}); }
