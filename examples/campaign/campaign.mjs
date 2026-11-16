/** Public research -> caller-reviewed campaign artifacts through standard MCP.
 * Existing scoped run/key required. No cookie, model API or platform write.
 */
import {mkdir,readFile,writeFile,lstat} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {parseArgs} from 'node:util';
import {connectClient,unwrap} from '../../deploy/mcp-gateway/compatibility.mjs';

const {values}=parseArgs({options:{sources:{type:'string'},candidate:{type:'string'},request:{type:'string'},out:{type:'string'}}});
if(!values.sources||!values.request||!values.out)throw Error('Pass --sources JSON_ARRAY --request STABLE_ID --out NEW_DIRECTORY, optionally --candidate REVIEWED_JSON');
const urls=JSON.parse(values.sources),directory=resolve(values.out);
await mkdir(directory,{mode:0o700});
if((await lstat(directory)).isSymbolicLink())throw Error('output_symlink_denied');
await writeFile(join(directory,'.gitignore'),'*\n',{flag:'wx',mode:0o600});
const client=await connectClient(process.env.ENGINE_MCP_URL,process.env.ENGINE_EXTERNAL_AGENT_TOKEN);
try{
 const tools=(await client.listTools()).tools;
 for(const name of ['campaign_research','campaign_read','campaign_events'])if(!tools.some(t=>t.name===name))throw Error('campaign_tools_not_exposed');
 const run_id=process.env.ENGINE_RUN_ID,sourceRequest=values.request+'-research';
 const research=unwrap(await client.callTool({name:'campaign_research',arguments:{run_id,request_id:sourceRequest,urls}}));
 await writeFile(join(directory,'research.json'),JSON.stringify(research,null,2)+'\n',{flag:'wx',mode:0o600});
 let campaign;
 if(values.candidate){
  const body=JSON.parse(await readFile(resolve(values.candidate),'utf8'));
  campaign=unwrap(await client.callTool({name:'campaign_build',arguments:{run_id,request_id:values.request+'-build',source_request_id:sourceRequest,body}}));
  const saved=unwrap(await client.callTool({name:'campaign_read',arguments:{run_id,artifact_id:campaign.id}}));
  if(saved.artifact_digest!==campaign.artifact_digest)throw Error('original_artifact_readback_mismatch');
  await writeFile(join(directory,'campaign.json'),JSON.stringify(saved,null,2)+'\n',{flag:'wx',mode:0o600});
  await writeFile(join(directory,'sales-brief.md'),saved.outputs.sales_brief_markdown,{flag:'wx',mode:0o600});
  for(const [id,page]of Object.entries(saved.outputs.landing_html_by_variant)){
   if(!/^[A-Za-z0-9_.-]{1,128}$/.test(id))throw Error('invalid_variant_filename');
   await writeFile(join(directory,'landing-'+id+'.html'),page,{flag:'wx',mode:0o600});
  }
 }
 const activity=unwrap(await client.callTool({name:'campaign_events',arguments:{run_id,after:0,limit:100}}));
 await writeFile(join(directory,'activity.json'),JSON.stringify(activity,null,2)+'\n',{flag:'wx',mode:0o600});
 process.stdout.write(JSON.stringify({state:campaign?.state??research.state,artifact_id:campaign?.id??research.id,full_cost:null,sales_lift_verified:false,external_writes:0,model_calls_by_engine:0})+'\n');
}finally{await client.close();}
