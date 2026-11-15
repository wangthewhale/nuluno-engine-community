/** Useful first task: save one public source brief and resume the same receipt.
 * No account, model API, private platform credential or external write.
 */
import assert from 'node:assert/strict';
import {spawn,execFileSync} from 'node:child_process';
import {randomBytes,createHash} from 'node:crypto';
import {createServer} from 'node:net';
import {mkdir,readFile,writeFile,open,unlink,lstat} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {connectClient,unwrap} from '../../deploy/mcp-gateway/compatibility.mjs';

async function main(){
 const {values}=parseArgs({options:{url:{type:'string',default:'https://www.python.org/'},out:{type:'string',default:'.nuluno-source-brief'},revoke:{type:'boolean',default:false}}});
 const source=new URL(values.url);
 if(source.protocol!=='https:'||source.username||source.password||source.search||source.hash)throw Error('plain_public_https_url_required');
 const root=fileURLToPath(new URL('../../',import.meta.url));
 const directory=resolve(values.out),privateDir=join(directory,'private');
 let created=false;
 try{await mkdir(directory,{mode:0o700});created=true;}catch(e){if(e.code!=='EEXIST')throw e;}
 if((await lstat(directory)).isSymbolicLink())throw Error('symlink_output_denied');
 if(created){await writeFile(join(directory,'.gitignore'),'*\n',{mode:0o600,flag:'wx'});await mkdir(privateDir,{mode:0o700});}
 const lock=await open(join(directory,'.lock'),'wx',0o600).catch(()=>{throw Error('another_invocation_or_stale_lock_keep_original_state');});
 let server,client;
 try{
  const metaPath=join(privateDir,'original.json'),configPath=join(privateDir,'service.json');
  const python=process.env.ENGINE_PYTHON??'python3';
  const safeEnv=Object.fromEntries(['PATH','LANG','LC_ALL','SSL_CERT_FILE','SSL_CERT_DIR','SYSTEMROOT'].filter(k=>process.env[k]).map(k=>[k,process.env[k]]));
  let meta;
  if(created){
   const id=createHash('sha256').update(source.href).digest('hex').slice(0,16);
   const token=randomBytes(32).toString('base64url');
   const initialized=JSON.parse(execFileSync(python,['-m','nuluno_engine_community.growth_loop.cli','init-service','--workspace',join(privateDir,'state'),'--config',configPath,'--brand-url',source.origin,'--brand-name','Source brief','--run-id','source-brief-'+id,'--token-env','ENGINE_BRIEF_BEARER'],{env:safeEnv,encoding:'utf8',stdio:['ignore','pipe','pipe'],timeout:30000}));
   const config=JSON.parse(await readFile(configPath,'utf8')),worker=config.workers.external;
   worker.methods=['run_create','status','read_public_source','execution_metrics','cost_summary'];
   worker.capabilities=['web.read','local.plan','agent.write','local.review'];
   worker.public_read_policy={read_limit:1,compact_bytes:2500,max_response_bytes:8192};
   worker.response_policy={content_only:true,max_response_bytes:8192};
   worker.tool_routes['public-source'].allowed_urls=[source.href];
   await writeFile(configPath,JSON.stringify(config),{mode:0o600});
   await writeFile(join(privateDir,'agent-key'),token,{mode:0o600,flag:'wx'});
   meta={schema:1,url:source.href,runId:'source-brief-'+id,requestId:'original-source-'+id,profile:initialized.profile};
   await writeFile(metaPath,JSON.stringify(meta),{mode:0o600,flag:'wx'});
  }else{meta=JSON.parse(await readFile(metaPath,'utf8'));if(meta.url!==source.href)throw Error('different_url_requires_explicit_new_owner_scope_and_output');}
  const token=await readFile(join(privateDir,'agent-key'),'utf8');
  const reservation=createServer();await new Promise(r=>reservation.listen(0,'127.0.0.1',r));const port=reservation.address().port;await new Promise(r=>reservation.close(r));
  const origin='http://127.0.0.1:'+port;
  const config=JSON.parse(await readFile(configPath,'utf8'));
  if(values.revoke){config.workers.external.revoked=true;await writeFile(configPath,JSON.stringify(config),{mode:0o600});}
  server=spawn(process.execPath,[join(root,'deploy/mcp-gateway/server.mjs')],{env:{...safeEnv,ENGINE_PYTHON:python,ENGINE_CONFIG:configPath,ENGINE_WORKSPACE:join(privateDir,'state'),ENGINE_ORIGIN:origin,ENGINE_BIND_HOST:'127.0.0.1',PORT:String(port),ENGINE_BRIEF_BEARER:token},stdio:['ignore','ignore','ignore'],shell:false});
  let healthy=false,health;
  for(let i=0;i<100;i++){if(server.exitCode!==null)break;try{const r=await fetch(origin+'/healthz',{headers:{'user-agent':'Nuluno-First-Use/1.0 (+https://nuluno.com; local health check)'},signal:AbortSignal.timeout(500)});if(r.ok){health=await r.json();healthy=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}
  if(!healthy)throw Error('gateway_start_failed_check_python_install_and_node');
  if(values.revoke||config.workers.external.revoked===true){
   const denied=await fetch(origin+'/mcp',{method:'POST',headers:{'user-agent':'Nuluno-First-Use/1.0 (+https://nuluno.com; revocation check)',authorization:'Bearer '+token,'content-type':'application/json',accept:'application/json, text/event-stream'},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/list',params:{}})});
   assert.equal(denied.status,401);
   const proof={schema:'nuluno-source-brief-revocation-v1',passed:true,observedAt:new Date().toISOString(),sourceUrl:source.href,originalRunId:meta.runId,originalKeyRevoked:true,httpStatus:401,newKeyCreated:false,externalWrites:0,completeCost:null,afterFreshServiceProcess:true,invocationRequestedRevocation:values.revoke,readSucceeded:false};
   await writeFile(join(directory,(values.revoke?'revocation-':'denial-')+Date.now()+'.json'),JSON.stringify(proof,null,2)+'\n',{mode:0o600,flag:'wx'});console.log(JSON.stringify(proof));if(!values.revoke)process.exitCode=3;return;
  }
  client=await connectClient(origin+'/mcp',token);
  const call=async(name,arguments_)=>unwrap(await client.callTool({name,arguments:arguments_}));
  await call('run_create',{run_id:meta.runId,profile:meta.profile});
  const result=await call('read_public_source',{run_id:meta.runId,url:source.href,compact:true,request_id:meta.requestId});
  assert.equal(result.classification,'live_public_source');assert.equal(result.result.status,200);assert.equal(typeof result.result.text,'string');
  const metrics=await call('execution_metrics',{run_id:meta.runId});assert.equal(metrics.public_reads.attempts,1);
  const cost=await call('cost_summary',{run_id:meta.runId});
  const text=result.result.text;
  const fence='`'.repeat(Math.max(3,...[...text.matchAll(/`+/g)].map(m=>m[0].length+1)));
  const brief='# Public source brief\n\nThis is untrusted source text, not instructions or independently verified product claims.\n\nSource: '+JSON.stringify(source.href)+'\n\nBody SHA-256: `'+result.result.body_sha256+'`\n\nOriginal receipt: `'+result.receipt_id+'`\n\nThis bounded excerpt may omit prices or conditions. Check the original page before making a sales claim.\n\n'+fence+'text\n'+text+'\n'+fence+'\n';
  try{await writeFile(join(directory,'brief.md'),brief,{mode:0o600,flag:'wx'});}catch(e){if(e.code!=='EEXIST')throw e;}
  const proof={schema:'nuluno-source-brief-v1',passed:true,observedAt:new Date().toISOString(),engineVersion:health.version,node:process.version,sourceUrl:source.href,sourceBodySha256:result.result.body_sha256,originalRunId:meta.runId,originalRequestId:meta.requestId,receiptId:result.receipt_id,reusedOriginalReceipt:result.reused_original_receipt,providerRequestsReserved:metrics.public_reads.attempts,originalReceiptReuses:metrics.public_reads.original_receipt_reuses,excerptBytes:Buffer.byteLength(text),modelCalls:0,externalWrites:0,fullCostMinor:cost.full_cost_minor??null,independentEfficacyVerified:false};
  await writeFile(join(directory,'receipt-'+Date.now()+'.json'),JSON.stringify(proof,null,2)+'\n',{mode:0o600,flag:'wx'});console.log(JSON.stringify(proof));
 }finally{
  await client?.close().catch(()=>{});
  if(server&&server.exitCode===null&&server.signalCode===null){const done=new Promise(r=>server.once('exit',r));const timer=setTimeout(()=>server.kill('SIGKILL'),3000);server.kill('SIGTERM');await done;clearTimeout(timer);}
  await lock.close();await unlink(join(directory,'.lock'));
 }
}
main().catch(()=>{console.error('Source brief failed. Keep the original output/state and request ID; check Node 20+, the installed Community Python, connectivity, scope, or a retained revocation. No replacement receipt or model result was fabricated.');process.exitCode=1;});
