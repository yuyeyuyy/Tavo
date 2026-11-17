import { afterEach,describe,expect,it,vi } from 'vitest';
import { AppError } from '@tavo/shared';
import type { APIClient } from '../apps/desktop/src/renderer/api/client';
import { sessionSchema,runSchema } from '../apps/desktop/src/renderer/api/sessions';
import { createSessionStore } from '../apps/desktop/src/renderer/chat/session-store';
const at='2026-11-16T00:00:00.000Z';
const session=(id:string)=>sessionSchema.parse({id,title:id,profileId:'mock',reviewMode:'auto_readonly',revision:0,createdAt:at,updatedAt:at,activeRunId:null,latestRunId:null});
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(yes=>{resolve=yes;});return {resolve,promise};}
const stops:(()=>void)[]=[];
afterEach(()=>{for(const stop of stops)stop();stops.length=0;vi.useRealTimers();});
describe('persistent session request ownership',()=>{
  it('rejects an old load even when its API ignores abort',async()=>{
    const a=deferred<ReturnType<typeof session>>();
    const client={getSession:vi.fn((id:string)=>id==='a'?a.promise:Promise.resolve(session('b'))),sessionMessages:vi.fn(async()=>({items:[],hasMore:false,nextSequence:null}))};
    const store=createSessionStore(client as unknown as APIClient);stops.push(store.getState().stopWatching);
    const old=store.getState().load('a');await store.getState().load('b');a.resolve(session('a'));await old;
    expect(store.getState().session?.id).toBe('b');expect(store.getState().messages).toEqual([]);
  });
  it('does not post a message after new conversation supersedes session creation or duplicate the pending send',async()=>{
    const created=deferred<ReturnType<typeof session>>();const client={createSession:vi.fn(()=>created.promise),sendMessage:vi.fn()};
    const store=createSessionStore(client as unknown as APIClient);stops.push(store.getState().stopWatching);
    const old=store.getState().send('hello',{profileId:'mock',reviewMode:'ask'});
    expect(await store.getState().send('duplicate',{profileId:'mock',reviewMode:'ask'})).toBe(false);
    store.getState().reset();created.resolve(session('a'));expect(await old).toBe(false);
    expect(client.sendMessage).not.toHaveBeenCalled();expect(store.getState().session).toBeNull();
  });
  it('uses the durable cancel endpoint and refreshes terminal state',async()=>{
    let run=runSchema.parse({id:'run-1',sessionId:'s',userMessageId:'m',previousRunId:null,status:'running',step:'model',rounds:1,toolCount:0,revision:1,createdAt:at,startedAt:at,finishedAt:null,errorCode:null,context:null,toolCalls:[]});
    const client={getSession:vi.fn(async()=>({...session('s'),latestRunId:'run-1',activeRunId:run.status==='running'?'run-1':null})),sessionRun:vi.fn(async()=>run),sessionMessages:vi.fn(async()=>({items:[],hasMore:false,nextSequence:null})),cancelRun:vi.fn(async()=>{run={...run,status:'cancelled',errorCode:'CANCELLED',finishedAt:at};return run;})};
    const store=createSessionStore(client as unknown as APIClient);stops.push(store.getState().stopWatching);
    await store.getState().load('s');await store.getState().cancel();
    expect(client.cancelRun).toHaveBeenCalledWith('s','run-1');expect(store.getState().run?.status).toBe('cancelled');
  });
  it('retains a mutation error after refreshing public state instead of silently resending',async()=>{
    const client={getSession:vi.fn(async()=>session('s')),sessionMessages:vi.fn(async()=>({items:[],hasMore:false,nextSequence:null})),sendMessage:vi.fn(async()=>{throw new AppError('REVISION_CONFLICT');})};
    const store=createSessionStore(client as unknown as APIClient);stops.push(store.getState().stopWatching);await store.getState().load('s');
    expect(await store.getState().send('hello',{profileId:'mock',reviewMode:'auto_readonly'})).toBe(false);
    expect(store.getState().error).toBeTruthy();expect(client.sendMessage).toHaveBeenCalledTimes(1);
  });
});
