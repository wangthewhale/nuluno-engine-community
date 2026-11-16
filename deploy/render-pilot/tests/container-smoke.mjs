/** Disposable Linux container acceptance. No Render service or private key.
 * The only Engine source GET is the owner's public product page. Native SDK
 * calls use loopback HTTP behind the same fixed canonical Host policy that a
 * TLS-terminating proxy uses. This does not test actual Render TLS or billing.
 */
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {randomBytes,randomUUID} from 'node:crypto';
import {createServer} from 'node:net';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join,dirname} from 'node:path';
import {canonicalHttpFetch} from './canonical-http.mjs';

const root=fileURLToPath(new URL('../',import.meta.url)),evidence=join(root,'evidence','docker-smoke.json');
const image=process.env.ENGINE_PILOT_SMOKE_IMAGE??'nuluno-engine-render-pilot:ci';
if(!/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,200}$/.test(image))throw Error('reviewed_image_name_required');
const id='nuluno-render-pilot-ci-'+randomBytes(8).toString('hex'),volume=id,container=id;
const token=randomBytes(32).toString('base64url');
const product='https://engine.nuluno.com/',source='https://engine.nuluno.com/?lang=en';
const canonicalHost='pilot.ci.invalid',canonicalOrigin='https://'+canonicalHost;
const runtimeEnv={...process.env,ENGINE_PILOT_BEARER:token};
const report={schema:'nuluno-render-pilot-linux-acceptance-v1',status:'running',observedAt:new Date().toISOString(),
 deployment_source_commit:process.env.GITHUB_SHA??null,engine_source_commit:'63cf40a044ad47739b84eceaf6fb2e298694ffe3',
 disposable_config:true,single_owner:true,single_product:true,multi_tenant:false,model_worker:false,
 render_service_created:false,render_tls_tested:false,new_subscription:false,external_platform_writes:0,model_calls:0,
 full_cost:null,source_url:source,source_gets:null,quota_limit:12,container_memory_limit_mb:512,container_cpu_limit:0.5,steps:[]};
report.source_count_basis='Original public_read_requests.attempts. Failed attempts are not claimed successful HTTP; a passed read requires native200 and no redirect. Dependency/setup downloads are separate.';
mkdirSync(dirname(evidence),{recursive:true});
const persist=()=>writeFileSync(evidence,JSON.stringify(report,null,2)+'\n',{mode:0o600});persist();
const docker=args=>execFileSync('docker',args,{env:runtimeEnv,encoding:'utf8',stdio:['ignore','pipe','pipe'],timeout:180000,maxBuffer:2*1024*1024});
const runIn=(command,args,{stdin,rootUser=false}={})=>execFileSync('docker',['exec',...(rootUser?[]:['--user','10001:10001']),'--workdir','/opt/engine/deploy/mcp-gateway','-i',container,command,...args],{env:runtimeEnv,input:stdin,encoding:'utf8',stdio:['pipe','pipe','pipe'],timeout:120000,maxBuffer:2*1024*1024});
const python=code=>JSON.parse(runIn('python',['-c',code]));
const port=await new Promise(resolve=>{const server=createServer();server.listen(0,'127.0.0.1',()=>{const port=server.address().port;server.close(()=>resolve(port));});});
const local='http://127.0.0.1:'+port;
let stage='volume_create',started=false;
function mark(name,result){report.steps.push({name,...result});persist();}
function start(restore={}){
 const args=['run','-d','--name',container,'--memory','512m','--cpus','0.5','--pids-limit','128','--publish','127.0.0.1:'+port+':10000','--volume',volume+':/state',
  '--env','ENGINE_PILOT_BEARER','--env','ENGINE_PUBLIC_ORIGIN='+canonicalOrigin,'--env','ENGINE_PRODUCT_URL='+product,
  '--env','ENGINE_SOURCE_URLS_JSON='+JSON.stringify([source]),'--env','ENGINE_PUBLIC_READ_LIMIT=12','--env','ENGINE_RUN_ID=pilot-product'];
 if(restore.archive)args.push('--env','ENGINE_RESTORE_ARCHIVE='+restore.archive,'--env','ENGINE_RESTORE_REQUEST_ID='+restore.id);
 docker([...args,image]);started=true;
}
function remove(){if(started){docker(['rm','--force',container]);started=false;}}
async function healthy(){
 for(let count=0;count<120;count++){
  try{const response=await canonicalHttpFetch(local+'/healthz',{headers:{Host:canonicalHost}});report.lastHealthStatus=response.status;if(response.ok){const value=await response.json();assert.equal(value.version,'0.13.0a2');return value;}}catch(error){report.lastHealthFailureKind=error?.name??'Error';}
  await new Promise(done=>setTimeout(done,500));
 }
 throw Error('actual_container_health_not_ready');
}
const inspect=()=>python(`import json,os,sqlite3,hashlib
from pathlib import Path
r=Path('/state');d=sqlite3.connect(str(r/'workspace/workflow.sqlite3'));d.row_factory=sqlite3.Row
names={x[0] for x in d.execute("select name from sqlite_master where type='table'")}
reads=[dict(x) for x in d.execute('select run_id,id,state,attempts from public_read_requests')] if 'public_read_requests' in names else []
c=json.loads((r/'service.json').read_text());w=c['workers']['pilot-owner']
uid=next(line.split()[1] for line in Path('/proc/1/status').read_text().splitlines() if line.startswith('Uid:'))
print(json.dumps({'pid1_uid':int(uid),'config_uid':(r/'service.json').stat().st_uid,'config_mode':oct((r/'service.json').stat().st_mode&0o777),'mount_mode':oct(r.stat().st_mode&0o777),'run_count':d.execute('select count(*) from runs').fetchone()[0],'tasks':[list(x) for x in d.execute('select key,state,attempts from tasks order by key')],'reads':reads,'source_gets':sum(x['attempts'] for x in reads),'read_limit':w['public_read_policy']['read_limit'],'owner_fingerprint':hashlib.sha256((r/'owner.json').read_bytes()).hexdigest(),'revoked':w.get('revoked',False),'original_token_env':w['token_env'],'source_urls':w['tool_routes']['public-source']['allowed_urls']}))`);
function mcp(name,args,expectError=false){
 // Bare imports resolve from the fixed installed gateway package inside the
 // actual image. No unofficial RPC client, fixture server or fake readback.
 const program=`import {Client,StreamableHTTPClientTransport} from '@modelcontextprotocol/client';
 import {request as httpRequest} from 'node:http';
 ${canonicalHttpFetch.toString()}
 const client=new Client({name:'nuluno-render-pilot-linux-check',version:'1.0.0'});
 try{
 await client.connect(new StreamableHTTPClientTransport(new URL('http://127.0.0.1:10000/mcp'),{fetch:canonicalHttpFetch,requestInit:{headers:{Host:'${canonicalHost}',Authorization:'Bearer '+process.env.ENGINE_PILOT_BEARER}}}));
 const tools=(await client.listTools()).tools;
 if(tools.some(t=>['run_create','tool_execute','economic_receipt_ingest'].includes(t.name)))throw Error('unscoped_tool_exposed');
 const result=await client.callTool({name:${JSON.stringify(name)},arguments:${JSON.stringify(args)}});
 if(${JSON.stringify(expectError)}){if(!result.isError)throw Error('outside_run_not_rejected');process.stdout.write(JSON.stringify({rejected:true,official_client:true}));}
 else{
 if(result.isError)throw Error('actual_mcp_tool_rejected');
 const data=result.structuredContent??JSON.parse(result.content.find(p=>p.type==='text').text);
 process.stdout.write(JSON.stringify({data,tool_names:tools.map(t=>t.name),official_client:true,result_bytes:Buffer.byteLength(JSON.stringify(result))}));
 }
 }finally{await client.close();}`;
 return JSON.parse(runIn('node',['--input-type=module','-'],{stdin:program}));
}
async function denied(){
 const response=await canonicalHttpFetch(local+'/mcp',{method:'POST',headers:{Host:canonicalHost,Authorization:'Bearer '+token,'content-type':'application/json','accept':'application/json, text/event-stream'},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'original-denied-key',version:'1'}}})});
 assert.equal(response.status,401);return response.status;
}
try{
 docker(['volume','create',volume]);stage='first_boot';start();await healthy();const first=inspect();
 assert.equal(first.pid1_uid,10001);assert.equal(first.config_uid,10001);assert.equal(first.config_mode,'0o600');assert.equal(first.mount_mode,'0o700');assert.equal(first.run_count,1);assert.equal(first.source_gets,0);
 assert.deepEqual(first.source_urls,[source]);assert.equal(first.original_token_env,'ENGINE_PILOT_BEARER');
 assert.equal((await canonicalHttpFetch(local+'/healthz')).status,403,'CI must not weaken canonical Host policy');
 assert.equal((await canonicalHttpFetch(local+'/healthz',{headers:{Host:canonicalHost,Origin:'https://outside.ci.invalid'}})).status,403);
 assert.equal(mcp('status',{run_id:'outside-authorized-run'},true).rejected,true);
 mark(stage,{actual_linux_image_boot:true,pid1_uid:10001,state_volume:true,strict_host_and_origin:true,outside_run_denied:true});
 stage='actual_scoped_contract';
 const discovered=mcp('contract',{run_id:'pilot-product'}),contract=discovered.data;assert.equal(contract.schema,'growth-host-contract-v1');assert.deepEqual(contract.library_inventory.installed_work_skill_ids,['S01']);assert.equal(contract.library_inventory.channel_catalog_installed,false);assert.equal(contract.worker_scope.allow_evolution,false);assert.equal(contract.worker_scope.allow_goal_continuity,false);assert.ok(discovered.result_bytes<=16000);const next=contract.next_actions.find(item=>item.tool==='cost_summary');assert.ok(next);const costs=mcp(next.tool,next.arguments);assert.equal(costs.data.full_cost_minor,null);assert.equal(inspect().source_gets,0);mark(stage,{official_client:true,contract_bytes:discovered.result_bytes,original_cap:16000,installed_skill_ids:['S01'],suggested_cost_read:true,full_cost:null,source_gets:0});
 stage='one_real_public_source';
 const read=mcp('campaign_research',{run_id:'pilot-product',request_id:'cloud-pilot-original-read',urls:[source]});
 const receipt=read.data.sources[0].receipt_id;assert.equal(read.data.reused_original_artifact,false);
 const afterRead=inspect();assert.equal(afterRead.source_gets,1);assert.equal(afterRead.read_limit,12);
 const native=python(`import sqlite3,json
d=sqlite3.connect('/state/workspace/workflow.sqlite3');r=json.loads(d.execute('select data from artifacts where run_id=? and kind=?',('pilot-product',${JSON.stringify('public_source.')}+${JSON.stringify(receipt)})).fetchone()[0]);v=r['result'];print(json.dumps({'url':v['url'],'status':v['status'],'body_sha256':v['body_sha256'],'receipt_id':${JSON.stringify(receipt)},'classification':r['classification'],'source_http_requests':1+len(v.get('redirect_chain',[]))}))`);
 assert.equal(native.status,200);assert.equal(native.url,source);assert.equal(native.classification,'live_public_source');assert.equal(native.source_http_requests,1);
 report.source_gets=1;report.source_receipt=native;mark(stage,{official_client:true,source_http_status:200,receipt_id:receipt,source_gets:1,quota_limit:12});
 stage='same_volume_replacement';remove();start();await healthy();
 const resumed=mcp('campaign_research',{run_id:'pilot-product',request_id:'cloud-pilot-original-read',urls:[source]}).data;
 assert.equal(resumed.reused_original_artifact,true);assert.equal(resumed.sources[0].receipt_id,receipt);
 const resumedState=inspect();assert.equal(resumedState.owner_fingerprint,first.owner_fingerprint);assert.deepEqual(resumedState.tasks,first.tasks);assert.equal(resumedState.source_gets,1);assert.equal(resumedState.read_limit,12);
 mark(stage,{original_owner_unchanged:true,original_tasks_unchanged:true,original_receipt_reused:true,source_gets:1,quota_limit:12});
 stage='consistent_online_backup';
 const backup=JSON.parse(runIn('python',['/opt/pilot/pilot.py','backup'],{rootUser:true}));
 assert.equal(backup.sqlite_online_backup,true);assert.equal(backup.raw_wal_copied,false);
 const backupPermissions=python(`import os,json
s=os.stat('/state/backups/'+${JSON.stringify(backup.backup)});print(json.dumps({'uid':s.st_uid,'mode':oct(s.st_mode&0o777)}))`);
 assert.equal(backupPermissions.uid,10001);assert.equal(backupPermissions.mode,'0o600');
 assert.equal(inspect().config_uid,10001);mark(stage,{sqlite_online_backup:true,raw_wal_copied:false,archive_sha256:backup.sha256,root_shell_drops_to_owner_uid:true});
 stage='revoke_original_key';const revoke=JSON.parse(runIn('python',['/opt/pilot/pilot.py','revoke'],{rootUser:true}));assert.equal(revoke.new_key_created,false);await denied();
 const revoked=inspect();assert.equal(revoked.revoked,true);assert.equal(revoked.config_uid,10001);assert.equal(revoked.source_gets,1);
 mark(stage,{native_denial_status:401,new_key_created:false,source_gets:1});
 stage='revoked_volume_replacement';remove();start();await healthy();await denied();assert.equal(inspect().source_gets,1);
 mark(stage,{native_denial_status:401,source_gets:1,revocation_survives_replacement:true});
 stage='old_backup_restores_locked';const restore={archive:backup.backup,id:randomUUID()};remove();start(restore);await healthy();await denied();
 const restored=inspect();assert.equal(restored.revoked,true);assert.equal(restored.owner_fingerprint,first.owner_fingerprint);assert.equal(restored.source_gets,1);assert.equal(restored.read_limit,12);
 python(`import sqlite3,json
d=sqlite3.connect('/state/workspace/workflow.sqlite3');d.execute('create table ci_after_restore(id primary key,value)');d.execute('insert into ci_after_restore values(1,?)',('preserved',));d.commit();print(json.dumps({'marker_created':True}))`);
 mark(stage,{native_denial_status:401,restored_old_active_config_still_revoked:true,original_receipt_preserved:true,source_gets:1});
 stage='restore_request_not_repeated';remove();start(restore);await healthy();await denied();
 const marker=python(`import sqlite3,json
d=sqlite3.connect('/state/workspace/workflow.sqlite3');print(json.dumps({'marker':d.execute('select value from ci_after_restore where id=1').fetchone()[0]}))`);
 assert.equal(marker.marker,'preserved');assert.equal(inspect().source_gets,1);
 mark(stage,{post_restore_marker_preserved:true,native_denial_status:401,source_gets:1,no_every_boot_restore_or_quota_reset:true});
 report.status='passed';report.actual_linux_container_replacement=true;report.original_key_remains_denied=true;report.no_fixture_substitution=true;persist();
 console.log(JSON.stringify(report));
}catch(error){
 try{const state=inspect();report.source_gets=state.source_gets;report.quota_limit=state.read_limit;}catch{}
 try{report.container_failure_state=JSON.parse(docker(['inspect','--format','{"status":{{json .State.Status}},"exit_code":{{.State.ExitCode}},"oom_killed":{{.State.OOMKilled}}}',container]));}catch{}
 report.status='failed';report.failedStage=stage;report.failureKind=/^[A-Za-z]+$/.test(error?.name??'')?error.name:'Error';report.resultNotProven=true;persist();
 console.error('Render pilot Linux acceptance failed at '+stage+'. No source/key/fixture substitution was made; inspect the actual job stage.');process.exitCode=1;
}finally{
 try{docker(['rm','--force',container]);started=false;}catch{}
 try{docker(['volume','rm',volume]);}catch{}
}
