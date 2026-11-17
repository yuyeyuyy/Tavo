import assert from 'node:assert/strict';
import { startBackend, pause } from './backend-harness.mjs';
const backend=await startBackend({bash:true});
const post=(path,value)=>backend.request(path,{method:'POST',body:JSON.stringify(value)});
try {
  assert.equal((await backend.request('/agent-runtime')).bash.available,true);
  const s=await post('/sessions',{profileId:'mock'});
  async function execute(content,id) {
    const current=await backend.request(`/sessions/${s.id}`);
    const a=await post(`/sessions/${s.id}/messages`,{content,clientMessageId:id,expectedRevision:current.revision});
    let run;
    for(let i=0;i<100;i++) {run=await backend.request(`/sessions/${s.id}/runs/${a.run.id}`);if(!['pending','running'].includes(run.status)) break;await pause(100);}
    assert.equal(run.context.policyVersion,'5');assert.equal(run.status,'succeeded');assert.ok(run.toolCalls[0].decisions.every(d=>d.source==='policy'));assert.equal(run.toolCalls[0].status,'succeeded');
    const messages=await backend.request(`/sessions/${s.id}/messages`);
    return messages.items.filter(m=>m.kind==='tool_result').at(-1).result.output;
  }
  assert.equal((await execute('bash: printf smoke-ok > note; cat note','first')).stdout,'smoke-ok');
  await backend.stop();await backend.start();
  assert.equal((await execute('bash: cat note','second')).stdout,'smoke-ok');
  console.log('Independent Bash sandbox passed: default Bash capability, full access without fabricated approval, real command/file persistence, backend restart and owned resource cleanup.');
} finally {await backend.cleanup();}
