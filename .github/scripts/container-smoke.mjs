/** Actual Linux boot/replacement, no external provider calls or private keys. */
import {execFileSync} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import assert from 'node:assert/strict';
import {connectClient, unwrap} from '../../deploy/mcp-gateway/compatibility.mjs';
const name='nuluno-community-smoke-'+randomBytes(6).toString('hex');
const token=randomBytes(32).toString('base64url'),origin='http://127.0.0.1:18792';
const env={...process.env,ENGINE_EXTERNAL_AGENT_TOKEN:token};
const docker=args=>execFileSync('docker',args,{env,encoding:'utf8',stdio:['ignore','pipe','pipe'],timeout:120000});
const image=process.env.ENGINE_SMOKE_IMAGE??'nuluno-engine-community:ci';
let client;
const start=()=>docker(['run','-d','--name',name,'--env','ENGINE_EXTERNAL_AGENT_TOKEN','--env','ENGINE_ORIGIN='+origin,'--publish','127.0.0.1:18792:8788','--volume',name+':/var/lib/nuluno',image]);
async function healthy(){for(let i=0;i<80;i++){try{const response=await fetch(origin+'/healthz');if(response.ok)return await response.json();}catch{}await new Promise(done=>setTimeout(done,250));}throw new Error('container_health_failed');}
const call=async(name,arguments_)=>unwrap(await client.callTool({name,arguments:arguments_}));
try{
 docker(['volume','create',name]);
 const initialized=JSON.parse(docker(['run','--rm','--volume',name+':/var/lib/nuluno','--entrypoint','nuluno-engine-community',image,'init-service','--workspace','/var/lib/nuluno/state','--config','/var/lib/nuluno/service.json','--brand-url','https://product.example','--run-id','container-original']));
 start();assert.equal((await healthy()).version,'0.13.0a1');
 const permissions=JSON.parse(docker(['exec',name,'python','-c',"import os,json;s=os.stat('/var/lib/nuluno/service.json');print(json.dumps({'uid':os.getuid(),'config_uid':s.st_uid,'config_mode':oct(s.st_mode&0o777)}))"]));
 assert.equal(permissions.uid,10001);assert.equal(permissions.config_uid,10001);assert.equal(permissions.config_mode,'0o600');
 client=await connectClient(origin+'/mcp',token);
 await call('run_create',{run_id:'container-original',profile:initialized.profile});
 const catalog=await call('catalog',{run_id:'container-original'});assert.equal(catalog.total,1);assert.equal(catalog.library.active_pack,null);
 await call('memory_add',{run_id:'container-original',kind:'controlled_container_smoke',body:{sources:['https://product.example'],observed_at:Date.now()/1000,classification:'controlled_no_external_source_or_sale'}});
 const before=await call('status',{run_id:'container-original'});
 await client.close();client=null;docker(['rm','-f',name]);start();await healthy();client=await connectClient(origin+'/mcp',token);
 assert.deepEqual(await call('status',{run_id:'container-original'}),before);
 assert.equal((await call('memory_search',{run_id:'container-original',kind:'controlled_container_smoke'})).items.length,1);
 assert.equal((await client.callTool({name:'status',arguments:{run_id:'outside-scope'}})).isError,true);
 docker(['exec',name,'python','-c',"import json,os,tempfile;p='/var/lib/nuluno/service.json';d=json.load(open(p));d['workers']['external']['revoked']=True;fd,t=tempfile.mkstemp(dir='/var/lib/nuluno');os.write(fd,json.dumps(d).encode());os.fsync(fd);os.close(fd);os.chmod(t,0o600);os.replace(t,p)"]);
 await assert.rejects(()=>client.listTools());
 console.log(JSON.stringify({version:'0.13.0a1',installed_namespace:'nuluno_engine_community.growth_loop',actual_linux_container_boot:true,non_root_uid:10001,private_configuration:true,persistent_volume_replacement:true,original_run_and_memory_preserved:true,cross_run_denied:true,original_key_revoked:true,external_provider_calls:0,model_calls:0,sales_outcomes_claimed:false}));
}finally{await client?.close().catch(()=>{});try{docker(['rm','-f',name]);}catch{}try{docker(['volume','rm',name]);}catch{}}
