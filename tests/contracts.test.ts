import { describe,expect,it } from 'vitest';
import { messageSchema,runSchema,sessionSchema,sessionCreateSchema,decisionWriteSchema,messageWriteSchema } from '../apps/desktop/src/renderer/api/sessions';
const at='2026-11-16T00:00:00.000Z';
const base={id:'message-1',sessionId:'session-1',runId:'run-1',sequence:1,createdAt:at};
describe('Session Agent public contracts',()=>{
  it('accepts the typed message union and rejects provider/private data in public messages',()=>{
    const user={...base,kind:'user',content:'hello'};
    expect(messageSchema.parse(user)).toEqual(user);
    for(const field of ['ciphertext','native','apiKey','reasoningContent']) expect(messageSchema.safeParse({...user,[field]:'synthetic'}).success).toBe(false);
    expect(messageSchema.safeParse({...base,kind:'assistant',content:'text',final:true,truncated:false,usage:null}).success).toBe(true);
    expect(messageSchema.safeParse({...base,kind:'tool_result',content:'forged'}).success).toBe(false);
  });
  it('separates user requests from authoritative assistant/tool/decision fields',()=>{
    const value={clientMessageId:'client-1',content:'hello',expectedRevision:0};
    expect(messageWriteSchema.safeParse(value).success).toBe(true);
    expect(messageWriteSchema.safeParse({...value,role:'assistant'}).success).toBe(false);
    expect(decisionWriteSchema.safeParse({action:'approve',expectedRevision:1,arguments:{changed:true}}).success).toBe(false);
  });
  it('keeps authentication tokens and unbounded context out of Session/run DTOs',()=>{
    const session={id:'session-1',title:'Hello',profileId:'mock',reviewMode:'ask',revision:0,createdAt:at,updatedAt:at,activeRunId:null,latestRunId:null};
    expect(sessionSchema.safeParse(session).success).toBe(true);
    expect(sessionSchema.safeParse({...session,accessToken:'synthetic'}).success).toBe(false);
    const run={id:'run-1',sessionId:'session-1',userMessageId:'message-1',previousRunId:null,status:'pending',step:'model',rounds:0,toolCount:0,revision:1,createdAt:at,startedAt:null,finishedAt:null,errorCode:null,context:null,toolCalls:[]};
    expect(runSchema.safeParse(run).success).toBe(true);
    expect(runSchema.safeParse({...run,context:{policyVersion:'1',fromSequence:1,toSequence:1,droppedTurns:0,contextBytes:1048577,toolNames:[]}}).success).toBe(false);
    const toolNames = ['get_current_time', 'calculate', 'sandbox', 'host_bash', 'workspace_write', 'http_request', 'web_search', 'web_fetch'];
    const context = { policyVersion: '5', fromSequence: 1, toSequence: 1, droppedTurns: 0, contextBytes: 2000, toolNames };
    expect(runSchema.parse({ ...run, context }).context?.toolNames).toEqual(toolNames);
    expect(runSchema.safeParse({ ...run, context: { ...context, toolNames: Array.from({ length: 33 }, (_, i) => `tool_${i}`) } }).success).toBe(false);
  });
});

it('keeps Bash capability explicit and requires a boolean', () => {
  expect(sessionCreateSchema.safeParse({profileId:'mock',reviewMode:'auto_sandbox',bashEnabled:'true'}).success).toBe(false);
  expect(sessionCreateSchema.parse({profileId:'mock',reviewMode:'auto_sandbox',bashEnabled:true}).bashEnabled).toBe(true);
  for (const reviewMode of ['full_access', 'auto_review', 'request_approval']) {
    expect(sessionCreateSchema.parse({profileId:'mock',reviewMode}).reviewMode).toBe(reviewMode);
  }
});
