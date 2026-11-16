/** Official SDK Streamable HTTP adapter over the unchanged portable kernel. */
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {timingSafeEqual} from 'node:crypto';
import {McpServer,createMcpHandler} from '@modelcontextprotocol/server';
import {toNodeHandler} from '@modelcontextprotocol/node';
import {z} from 'zod';
import {scopedHostContract,scopeMethodNames} from './contract-view.mjs';

const MAX_BYTES=2*1024*1024;
const READ_ONLY=new Set(['campaign_research','campaign_read','campaign_events','negotiate','status','read_public_source','delivery_report','execution_metrics','cost_summary','trust_status','learning_status','commercial_rank','catalog','goal_summary','graph','memory_search','preflight','contract','execution_catalog','goal_readiness','capability_inventory','capability_readiness','experience_search','artifact_contract','pursuit_status','evolution_status']);
function same(a,b){const x=Buffer.from(a),y=Buffer.from(b);return x.length===y.length&&timingSafeEqual(x,y);}
function json(res,status,value){const body=JSON.stringify(value);res.writeHead(status,{'content-type':'application/json','cache-control':'no-store','content-length':Buffer.byteLength(body)});res.end(body);}
function safeOrigin(origin){const u=new URL(origin);if(u.username||u.password||u.search||u.hash||u.pathname!=='/'||!['https:','http:'].includes(u.protocol))throw new Error('Set ENGINE_ORIGIN to a plain HTTPS origin');if(u.protocol==='http:'&&!['127.0.0.1','localhost','[::1]'].includes(u.hostname))throw new Error('Non-loopback origin requires HTTPS');return u;}
export function pythonCall(options,args,input){
 return new Promise((resolveResult,reject)=>{
  const child=spawn(options.python,['-m','nuluno_engine_community.growth_loop.gateway',...args],{stdio:['pipe','pipe','ignore'],shell:false,env:options.env??process.env});
  let output='',length=0,settled=false;
  const timer=setTimeout(()=>{child.kill('SIGTERM');if(!settled){settled=true;reject(new Error('engine_timeout_reconcile_original_task'));}},options.timeoutMs??120000);
  const finish=()=>clearTimeout(timer);
  child.on('error',()=>{finish();if(!settled){settled=true;reject(new Error('engine_process_unavailable'));}});
  child.stdout.on('data',chunk=>{length+=chunk.length;if(length>16*MAX_BYTES){child.kill();finish();if(!settled){settled=true;reject(new Error('engine_response_too_large'));}}else output+=chunk;});
  child.on('close',()=>{finish();if(settled)return;settled=true;try{resolveResult(JSON.parse(output));}catch{reject(new Error('engine_response_unavailable_reconcile_original_task'));}});
  child.stdin.on('error',()=>{});child.stdin.end(input===undefined?'':JSON.stringify(input));
 });
}
export async function authenticate(configPath,authorization,env=process.env){
 if(typeof authorization!=='string'||!/^Bearer [A-Za-z0-9_-]{32,256}$/.test(authorization))throw new Error('unauthorized');
 const config=JSON.parse(await readFile(configPath,'utf8')),token=authorization.slice(7),matches=[];
 for(const [worker,settings]of Object.entries(config.workers??{})){
  const secret=env[settings.token_env];
  if(settings.revoked!==true&&typeof secret==='string'&&secret.length>=32&&same(token,secret))matches.push({worker,settings});
 }
 if(matches.length!==1)throw new Error('unauthorized');return matches[0];
}
function toolSchema(schema){const fields={};const types={string:()=>z.string(),object:()=>z.record(z.string(),z.unknown()),array:()=>z.array(z.unknown()),integer:()=>z.number().int(),number:()=>z.number().finite(),boolean:()=>z.boolean()};for(const [name,field]of Object.entries(schema.properties)){let value=types[field.type]();if(!schema.required.includes(name))value=value.optional();fields[name]=value;}return z.object(fields).strict();}
function toolResult(value,isError,settings){
 const result={content:[{type:'text',text:JSON.stringify(value)}],isError};
 if(settings.response_policy?.content_only!==true)result.structuredContent=value;
 const cap=settings.response_policy?.max_response_bytes;
 if(cap!==undefined){
  if(!Number.isInteger(cap)||cap<2048||cap>65536)throw new Error('invalid_response_byte_policy');
  if(Buffer.byteLength(JSON.stringify(result))>cap-256)return {content:[{type:'text',text:JSON.stringify({error:{kind:'response_byte_budget_exceeded',message:'Original progress is saved. Query a narrower result; do not replay an unknown write.'}})}],isError:true};
 }
 return result;
}
export async function createEngineServer(options){
 const origin=safeOrigin(options.origin),metadata=await pythonCall(options,['--metadata']);
 const resource=new URL('/mcp',origin).href;
 const makeMcp=(binding,authorization)=>{
  const server=new McpServer({name:'nuluno-engine',version:metadata.version},{instructions:'Independent Nuluno Engine. Use only the tools exposed by this operator scope. When workflow tools are exposed, preserve the original run, lease and evidence. Public research and a saved report are not a completed sale. Resume or reconcile original uncertain writes. Platform grants, including independent Nuluno Access, remain separate.'});
  for(const name of scopeMethodNames(metadata.methods,binding.settings)){
   const schema=metadata.methods[name];
   server.registerTool(name,{description:READ_ONLY.has(name)?'Read scoped Engine '+name.replaceAll('_',' ')+'.':'Run scoped Engine '+name.replaceAll('_',' ')+'. Preserve original task, lease, evidence and authority.',inputSchema:toolSchema(schema),annotations:{readOnlyHint:READ_ONLY.has(name),destructiveHint:false,openWorldHint:['campaign_research','read_public_source','tool_probe','tool_execute','tool_reconcile','source_retry','capability_discover','artifact_dispatch','artifact_generate','evolution_tick'].includes(name)}},async arguments_=>{
    try{
     const current=await authenticate(options.config,authorization,options.env);
     if(current.worker!==binding.worker)throw new Error('unauthorized');
     const result=await pythonCall(options,['--workspace',options.workspace,'--config',options.config,'--worker',current.worker,'--method',name],arguments_);
     const value=result.error?result:name==='contract'?scopedHostContract(result.result,current.settings,arguments_.run_id):result.result;
     return toolResult(value,!!result.error,current.settings);
    }catch(error){return {content:[{type:'text',text:JSON.stringify({error:{kind:'gateway_error',message:error.message}})}],isError:true};}
   });
  }
  return server;
 };
 const server=createServer(async(req,res)=>{
  try{
   const url=new URL(req.url,origin);
   if(req.headers.host!==origin.host)return json(res,403,{error:'host_not_allowed'});
   if(req.headers.origin&&req.headers.origin!==origin.origin)return json(res,403,{error:'origin_not_allowed'});
   if(req.method==='GET'&&url.pathname==='/healthz')return json(res,200,{product:'Nuluno Engine',version:metadata.version,status:'ready',transport:'streamable-http',nuluno_com_required:false});
   if(req.method==='GET'&&url.pathname==='/.well-known/nuluno-engine.json')return json(res,200,{name:'Nuluno Engine',version:metadata.version,mcp:resource,authentication:'operator-issued scoped Bearer token',persistent_state:'SQLite on operator-owned durable volume',nuluno_com_required:false,access:'optional independent Nuluno Access endpoint',sales_outcomes_guaranteed:false});
   if(url.pathname!=='/mcp'||url.search)return json(res,404,{error:'not_found'});
   let binding;
   try{binding=await authenticate(options.config,req.headers.authorization,options.env);}catch{res.setHeader('www-authenticate','Bearer realm="nuluno-engine"');return json(res,401,{error:'unauthorized'});}
   const handler=createMcpHandler(()=>makeMcp(binding,req.headers.authorization),{responseMode:'json',legacy:'stateless',maxRequestBodySize:MAX_BYTES});
   return await toNodeHandler(handler)(req,res);
  }catch{return json(res,500,{error:'gateway_error_original_progress_preserved'});}
 });
 return server;
}
export async function main(){
 const port=Number(process.env.PORT??8788),host=process.env.ENGINE_BIND_HOST??'127.0.0.1';
 const options={python:process.env.ENGINE_PYTHON??'python3',workspace:resolve(process.env.ENGINE_WORKSPACE??'./.state'),config:resolve(process.env.ENGINE_CONFIG??'./service.json'),origin:process.env.ENGINE_ORIGIN??`http://127.0.0.1:${port}`};
 const server=await createEngineServer(options);server.listen(port,host,()=>process.stdout.write(JSON.stringify({product:'Nuluno Engine',mcp:new URL('/mcp',options.origin).href,persistent_state_required:true})+'\n'));
 for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>server.close(()=>process.exit(0)));
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href)main().catch(()=>{process.stderr.write('Engine gateway startup failed; verify private operator configuration.\n');process.exitCode=1;});
