/** Persistent owner-scoped public campaign trial. No account/model/platform write. */
import {execFileSync} from 'node:child_process';
import {randomBytes,createHash} from 'node:crypto';
import {constants} from 'node:fs';
import {createServer} from 'node:net';
import {mkdir,readFile,writeFile,open,unlink,lstat,rename,readdir} from 'node:fs/promises';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {createEngineServer} from '../../deploy/mcp-gateway/server.mjs';
import {connectClient,unwrap} from '../../deploy/mcp-gateway/compatibility.mjs';
import {preflightCampaign} from '../../scripts/campaign-preflight.mjs';

const sha=value=>createHash('sha256').update(value).digest('hex');
const {values}=parseArgs({options:{url:{type:'string',default:'https://engine.nuluno.com/?lang=en'},out:{type:'string',default:'my-campaign'},candidate:{type:'string'},revision:{type:'string',default:'starter'},revoke:{type:'boolean',default:false}}});
const {url,out,revisionPath}=preflightCampaign({url:values.url,out:values.out,revision:values.revision});
const root=fileURLToPath(new URL('../../',import.meta.url)),privateDir=join(out,'private');
async function entry(path){try{return await lstat(path);}catch(e){if(e.code==='ENOENT')return null;throw e;}}
async function directory(path,label,optional=false){const s=await entry(path);if(!s&&optional)return false;if(!s||s.isSymbolicLink()||!s.isDirectory())throw Error(label+'_directory_required_no_symlink');return true;}
async function regular(path,optional=false){const s=await entry(path);if(!s&&optional)return false;if(!s||s.isSymbolicLink()||!s.isFile())throw Error('regular_file_required_no_symlink');return true;}
async function readRegular(path){await regular(path);const handle=await open(path,constants.O_RDONLY|(constants.O_NOFOLLOW??0));try{if(!(await handle.stat()).isFile())throw Error('regular_file_required');return await handle.readFile('utf8');}finally{await handle.close();}}
async function save(path,data,exclusive=false){
 await directory(dirname(path),'output_parent');await regular(path,true);
 if(exclusive){const h=await open(path,constants.O_CREAT|constants.O_EXCL|constants.O_WRONLY|(constants.O_NOFOLLOW??0),0o600);try{await h.writeFile(data);}finally{await h.close();}return;}
 const temp=join(dirname(path),'.campaign-write-'+randomBytes(12).toString('hex'));
 const h=await open(temp,constants.O_CREAT|constants.O_EXCL|constants.O_WRONLY|(constants.O_NOFOLLOW??0),0o600);
 try{await h.writeFile(data);await h.close();await regular(path,true);await rename(temp,path);}finally{await h.close().catch(()=>{});await unlink(temp).catch(e=>{if(e.code!=='ENOENT')throw e;});}
}
const python=process.env.ENGINE_PYTHON??'python3';
const safeEnv=Object.fromEntries(['PATH','LANG','LC_ALL','SSL_CERT_FILE','SSL_CERT_DIR','SYSTEMROOT','TMPDIR','TEMP','TMP'].filter(k=>process.env[k]).map(k=>[k,process.env[k]]));
let created=false;try{await mkdir(out,{mode:0o700});created=true;}catch(e){if(e.code!=='EEXIST')throw e;}
await directory(out,'output');
if(created){await mkdir(privateDir,{mode:0o700});await save(join(out,'.gitignore'),'*\n',true);}
await directory(privateDir,'private');
if(await directory(revisionPath,'revision',true))for(const name of await readdir(revisionPath))await regular(join(revisionPath,name));
for(const name of ['service.json','agent-key','original.json'])await regular(join(privateDir,name),true);
if(await directory(join(privateDir,'state'),'state',true))for(const name of ['workflow.sqlite3','workflow.sqlite3-wal','workflow.sqlite3-shm','initial-profile.json'])await regular(join(privateDir,'state',name),true);
const lock=await open(join(out,'.lock'),'wx',0o600).catch(()=>{throw Error('another_or_stale_invocation_preserve_original_state');});
const config=join(privateDir,'service.json'),workspace=join(privateDir,'state'),metaPath=join(privateDir,'original.json');
let server,client;
try{
 let meta;
 if(created){
  const id=sha(url.href).slice(0,16),token=randomBytes(32).toString('base64url');
  const init=JSON.parse(execFileSync(python,['-m','nuluno_engine_community.growth_loop.cli','init-service','--workspace',workspace,'--config',config,'--brand-url',url.origin+'/','--run-id','campaign-'+id,'--token-env','ENGINE_CAMPAIGN_TRIAL_TOKEN'],{env:safeEnv,encoding:'utf8',stdio:['ignore','pipe','pipe']}));
  const settings=JSON.parse(await readRegular(config)),w=settings.workers.external;
  w.methods=['run_create','campaign_research','campaign_build','campaign_read','campaign_events'];
  w.public_read_policy={read_limit:1,compact_bytes:2500,max_response_bytes:65536};w.response_policy={content_only:true,max_response_bytes:65536};w.tool_routes['public-source'].allowed_urls=[url.href];
  await save(config,JSON.stringify(settings));await save(join(privateDir,'agent-key'),token,true);
  meta={url:url.href,runId:'campaign-'+id,researchRequest:'original-source-'+id,profile:init.profile};
  await save(metaPath,JSON.stringify(meta),true);
 }else{meta=JSON.parse(await readRegular(metaPath));if(meta.url!==url.href)throw Error('existing_scope_different_url_preserved');}
 const token=await readRegular(join(privateDir,'agent-key'));
 const port=await new Promise(r=>{const s=createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});});
 const origin='http://127.0.0.1:'+port;
 server=await createEngineServer({python,workspace,config,origin,env:{...safeEnv,ENGINE_CAMPAIGN_TRIAL_TOKEN:token}});await new Promise(r=>server.listen(port,'127.0.0.1',r));
 const settings=JSON.parse(await readRegular(config));
 if(values.revoke){settings.workers.external.revoked=true;await save(config,JSON.stringify(settings));}
 if(settings.workers.external.revoked===true){
  const denied=await fetch(origin+'/mcp',{method:'POST',headers:{Authorization:'Bearer '+token,'content-type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/list',params:{}})});
  if(denied.status!==401)throw Error('native_revoked_key_denial_required');
  const proof={schema:'nuluno-campaign-trial-v1',phase:values.revoke?'revocation':'denial',passed:true,run_id:meta.runId,url:meta.url,native_denial_status:401,new_key_created:false,read_succeeded:false,after_fresh_service_process:true,external_writes:0,model_calls_by_engine:0,full_cost:null};
  await save(join(out,values.revoke?'revocation.json':'denial.json'),JSON.stringify(proof,null,2)+'\n');process.stdout.write(JSON.stringify(proof)+'\n');if(!values.revoke)process.exitCode=3;
 }else{
  client=await connectClient(origin+'/mcp',token);const call=async(name,args)=>unwrap(await client.callTool({name,arguments:{run_id:meta.runId,...args}}));
  await call('run_create',{profile:meta.profile});
  const research=await call('campaign_research',{request_id:meta.researchRequest,urls:[meta.url]});
  let body,originLabel;
  if(values.candidate){body=JSON.parse(await readRegular(resolve(values.candidate)));originLabel='caller_supplied_candidate_not_autonomous_learning';}
  else{
   const selected=[];for(const kind of ['product','price','delivery','limitation','feature','demo']){const f=research.fact_candidates.find(f=>f.kind===kind);if(f&&selected.length<4)selected.push(f.id);}
   if(!selected.length)throw Error('no_original_facts_do_not_invent_a_campaign');
   body={selected_fact_ids:selected,angles:[{id:'evaluate',audience:'Potential buyers evaluating this product',motive:'Understand the published offer before choosing',hypothesis:'Test whether source-based product evaluation helps a buyer take the next step',fact_ids:selected},{id:'fit',audience:'Potential buyers checking whether this product fits',motive:'Keep conditions and unknown information visible',hypothesis:'Test whether clearer conditions help a buyer decide what to verify next',fact_ids:selected}],variants:[{id:'control',angle_id:'evaluate',headline:'Start with the product’s published details',subheadline:'Review the original statements and choose what to check next.',ad_headline:'A clearer starting point for product evaluation',cta_label:'Review the original product'},{id:'candidate',angle_id:'fit',headline:'Know the conditions before you choose',subheadline:'Keep missing information visible while you evaluate the product.',ad_headline:'Check the offer against your requirements',cta_label:'Check the published offer'}],experiment:{control_variant:'control',candidate_variant:'candidate',primary_metric:'qualified_conversion',assignment_unit:'visitor',min_sample_per_arm:100}};
   originLabel='starter_templates_not_inferred_audience_not_AI_learning';
  }
  const built=await call('campaign_build',{request_id:'campaign-'+values.revision,source_request_id:meta.researchRequest,body});
  const saved=await call('campaign_read',{artifact_id:built.id});if(saved.artifact_digest!==built.artifact_digest)throw Error('original_campaign_readback_mismatch');
  const activity=await call('campaign_events',{after:0,limit:100});
  const revision=revisionPath;try{await mkdir(revision,{mode:0o700});}catch(e){if(e.code!=='EEXIST')throw e;}await directory(revision,'revision');
  for(const [name,data]of [['research.json',research],['candidate.json',body],['campaign.json',saved],['activity.json',activity]])await save(join(revision,name),JSON.stringify(data,null,2)+'\n');
  await save(join(revision,'sales-brief.md'),saved.outputs.sales_brief_markdown);
  for(const [id,page]of Object.entries(saved.outputs.landing_html_by_variant)){
   if(!/^[A-Za-z0-9_.-]{1,128}$/.test(id))throw Error('invalid_variant_filename');
   await save(join(revision,'landing-'+id+'.html'),page.replace('<aside>','<aside>'+ (values.candidate?'Caller-proposed creative draft. ':'Starter messaging frames only; no audience inference or AI learning. ')));
  }
  const proof={schema:'nuluno-campaign-trial-v1',phase:'campaign',passed:true,source_only_development_feature:true,run_id:meta.runId,url:meta.url,artifact_id:saved.id,artifact_digest:saved.artifact_digest,original_source_receipt:research.sources[0].receipt_id,reused_original_source:research.reused_original_artifact,reused_original_campaign:built.reused_original_artifact,candidate_origin:originLabel,provider_read_quota:1,external_writes:0,model_calls_by_engine:0,full_cost:null,causal_lift:null,sales_lift_verified:false,measurement_adapter_connected:false,source_receipts:saved.sources};
  await save(join(revision,'proof.json'),JSON.stringify(proof,null,2)+'\n');process.stdout.write(JSON.stringify(proof)+'\n');
 }
}finally{await client?.close().catch(()=>{});if(server)await new Promise(r=>server.close(r));await lock.close();await unlink(join(out,'.lock'));}
