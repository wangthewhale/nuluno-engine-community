/** Fixed public preview only. Default is offline verification, never publication. */
import {createHash} from 'node:crypto';
import {readFileSync,readdirSync,lstatSync} from 'node:fs';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';

export const CONFIG=JSON.parse(readFileSync(new URL('../.github/preview-release.json',import.meta.url),'utf8'));
const fail=code=>{throw new Error(code);};
const sha=data=>createHash('sha256').update(data).digest('hex');
const same=(a,b)=>JSON.stringify([...a].sort())===JSON.stringify([...b].sort());
function validConfig(c){
 if(!['wangthewhale/nuluno-engine-community','wangthewhale/nuluno-access-community'].includes(c.repository)||!/^[a-f0-9]{40}$/.test(c.sourceCommit)||typeof c.version!=='string'||typeof c.tag!=='string'||!c.files||Object.keys(c.files).length!==4)fail('invalid_fixed_config');
 for(const [name,v]of Object.entries(c.files))if(!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(name)||!/^[a-f0-9]{64}$/.test(v.sha256)||!Number.isSafeInteger(v.bytes)||v.bytes<1||v.bytes>32*1024*1024)fail('invalid_fixed_asset');
 if(!c.files['release-manifest.json']||!c.files.SHA256SUMS)fail('missing_fixed_manifest');
}
export function verifyLocalArtifacts(directory,config=CONFIG){
 validConfig(config);const root=resolve(directory),names=readdirSync(root);
 if(!same(names,Object.keys(config.files)))fail('artifact_inventory_mismatch');
 const files=Object.entries(config.files).map(([name,expected])=>{
  const path=join(root,name),s=lstatSync(path);if(!s.isFile()||s.isSymbolicLink())fail('artifact_not_regular_file');
  if(s.size!==expected.bytes)fail('artifact_size_mismatch');
  const data=readFileSync(path);if(data.length!==expected.bytes||sha(data)!==expected.sha256)fail('artifact_sha256_mismatch');
  return {name,sha256:expected.sha256,bytes:expected.bytes,data};
 });
 let manifest;try{manifest=JSON.parse(files.find(f=>f.name==='release-manifest.json').data.toString('utf8'));}catch{fail('invalid_release_manifest');}
 if(manifest.schema!==config.manifest.schema||manifest.version!==config.version||manifest[config.manifest.sourceField]!==config.sourceCommit||manifest[config.manifest.commercialField]!==false)fail('manifest_lineage_mismatch');
 for(const [k,v]of Object.entries(config.manifest.expected??{}))if(manifest[k]!==v)fail('manifest_identity_mismatch');
 const payloadNames=Object.keys(config.files).filter(n=>!['release-manifest.json','SHA256SUMS'].includes(n));
 if(!manifest.artifacts||!same(Object.keys(manifest.artifacts),payloadNames))fail('manifest_asset_inventory_mismatch');
 for(const name of payloadNames)if(manifest.artifacts[name].sha256!==config.files[name].sha256||manifest.artifacts[name].bytes!==config.files[name].bytes)fail('manifest_asset_mismatch');
 const lines=files.find(f=>f.name==='SHA256SUMS').data.toString('utf8').trimEnd().split('\n'),sums=new Map();
 for(const line of lines){const m=/^([a-f0-9]{64})  ([A-Za-z0-9][A-Za-z0-9_.-]*)$/.exec(line);if(!m||sums.has(m[2]))fail('invalid_checksum_manifest');sums.set(m[2],m[1]);}
 if(!same([...sums.keys()],Object.keys(config.files).filter(n=>n!=='SHA256SUMS')))fail('checksum_inventory_mismatch');
 for(const [name,digest]of sums)if(config.files[name].sha256!==digest)fail('checksum_mismatch');
 return {files,manifest};
}
export function validateExistingState({tagCommit,release,assets=[]},config=CONFIG){
 validConfig(config);
 if(tagCommit!==null&&tagCommit!==config.sourceCommit)fail('existing_tag_source_mismatch');
 if(!release){if(assets.length)fail('assets_without_release');return {createRelease:true,missingAssets:Object.keys(config.files),skipAssets:[]};}
 if(tagCommit!==config.sourceCommit)fail('existing_release_tag_missing');
 if(!Number.isSafeInteger(release.id)||release.id<1||release.tag_name!==config.tag||release.target_commitish!==config.sourceCommit||release.draft!==false||release.prerelease!==true||release.name!==config.title||release.body!==config.notes)fail('existing_release_mismatch');
 const seen=new Set();
 for(const asset of assets){
  if(!asset||typeof asset.name!=='string'||seen.has(asset.name)||!Object.hasOwn(config.files,asset.name))fail('existing_asset_inventory_mismatch');seen.add(asset.name);
  const expected=config.files[asset.name];
  if(!Number.isSafeInteger(asset.id)||asset.id<1||asset.size!==expected.bytes||asset.digest!=='sha256:'+expected.sha256)fail('existing_asset_digest_mismatch');
 }
 return {createRelease:false,missingAssets:Object.keys(config.files).filter(n=>!seen.has(n)),skipAssets:[...seen]};
}
async function github(path,token,{method='GET',body,allow404=false,upload=false}={}){
 const url=(upload?'https://uploads.github.com':'https://api.github.com')+path;
 const response=await fetch(url,{method,redirect:'error',signal:AbortSignal.timeout(30000),headers:{authorization:'Bearer '+token,accept:'application/vnd.github+json','x-github-api-version':'2026-03-10',...(body===undefined?{}:{'content-type':upload?'application/octet-stream':'application/json'})},...(body===undefined?{}:{body:upload?body:JSON.stringify(body)})});
 if(allow404&&response.status===404){await response.body?.cancel();return null;}
 if(!response.ok){await response.body?.cancel();fail('github_'+method.toLowerCase()+'_failed_'+response.status);}
 const raw=await response.text();if(Buffer.byteLength(raw)>2*1024*1024)fail('github_response_too_large');
 try{return JSON.parse(raw);}catch{fail('github_invalid_response');}
}
async function tagCommit(token){
 const base='/repos/'+CONFIG.repository;let value=await github(base+'/git/ref/tags/'+encodeURIComponent(CONFIG.tag),token,{allow404:true});
 if(!value)return null;let object=value.object;
 for(let i=0;i<4;i++){
  if(!object||!/^[a-f0-9]{40}$/.test(object.sha))fail('invalid_tag_object');
  if(object.type==='commit')return object.sha;
  if(object.type!=='tag')fail('unsupported_tag_object');
  object=(await github(base+'/git/tags/'+object.sha,token)).object;
 }
 fail('tag_peel_limit');
}
async function existing(token){
 const base='/repos/'+CONFIG.repository,commit=await tagCommit(token),release=await github(base+'/releases/tags/'+encodeURIComponent(CONFIG.tag),token,{allow404:true});
 const assets=release?await github(base+'/releases/'+release.id+'/assets?per_page=100',token):[];
 if(!Array.isArray(assets))fail('invalid_asset_list');
 return {tagCommit:commit,release,assets};
}
export async function publishPreview(directory,{publish=false}={}){
 const verified=verifyLocalArtifacts(directory); // All bytes retained before any REST write.
 const report={repository:CONFIG.repository,tag:CONFIG.tag,sourceCommit:CONFIG.sourceCommit,version:CONFIG.version,files:verified.files.map(({name,sha256,bytes})=>({name,sha256,bytes}))};
 if(!publish)return {...report,mode:'offline_dry_run',published:false,restRequests:0};
 if(process.env.GITHUB_EVENT_NAME!=='workflow_dispatch'||process.env.GITHUB_REF!=='refs/heads/main'||process.env.GITHUB_REPOSITORY!==CONFIG.repository||process.env.GITHUB_REPOSITORY_OWNER!=='wangthewhale')fail('manual_main_owner_workflow_required');
 const token=process.env.GITHUB_TOKEN;if(!token||token.length<20)fail('ephemeral_github_token_required');
 const base='/repos/'+CONFIG.repository;
 const run=await github(base+'/actions/runs/'+CONFIG.artifactRunId,token),artifact=await github(base+'/actions/artifacts/'+CONFIG.artifactId,token);
 if(run.id!==CONFIG.artifactRunId||run.head_sha!==CONFIG.sourceCommit||run.status!=='completed'||run.conclusion!=='success'||run.repository?.full_name!==CONFIG.repository||artifact.id!==CONFIG.artifactId||artifact.name!==CONFIG.artifactName||artifact.expired!==false||artifact.workflow_run?.id!==CONFIG.artifactRunId||artifact.workflow_run?.head_sha!==CONFIG.sourceCommit)fail('readiness_artifact_lineage_mismatch');
 let state=await existing(token),plan=validateExistingState(state),release=state.release;
 if(plan.createRelease){
  // A new tag is created by GitHub at this exact SHA, never at current main.
  release=await github(base+'/releases',token,{method:'POST',body:{tag_name:CONFIG.tag,target_commitish:CONFIG.sourceCommit,name:CONFIG.title,body:CONFIG.notes,draft:false,prerelease:true,generate_release_notes:false,make_latest:'false'}});
  state={tagCommit:await tagCommit(token),release,assets:[]};plan=validateExistingState(state);
 }
 for(const name of plan.missingAssets){
  const file=verified.files.find(f=>f.name===name);
  const asset=await github('/repos/'+CONFIG.repository+'/releases/'+release.id+'/assets?name='+encodeURIComponent(name),token,{method:'POST',body:file.data,upload:true});
  validateExistingState({tagCommit:CONFIG.sourceCommit,release,assets:[asset]});
 }
 const finalState=await existing(token),finalPlan=validateExistingState(finalState);if(finalPlan.missingAssets.length)fail('release_asset_readback_incomplete');
 return {...report,mode:'manual_preview_publish',published:true,releaseId:finalState.release.id,releaseUrl:'https://github.com/'+CONFIG.repository+'/releases/tag/'+CONFIG.tag,uploadedAssets:plan.missingAssets,skippedMatchingAssets:plan.skipAssets};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 try{const {values}=parseArgs({options:{directory:{type:'string'},publish:{type:'boolean'},'dry-run':{type:'boolean'}}});if(!values.directory||values.publish&&values['dry-run'])fail('directory_and_one_mode_required');process.stdout.write(JSON.stringify(await publishPreview(values.directory,{publish:values.publish===true}),null,2)+'\n');}
 catch(error){const code=typeof error?.message==='string'&&/^[a-z0-9_]+$/.test(error.message)?error.message:'preview_publish_failed';process.stderr.write(code+'\n');process.exitCode=1;}
}
