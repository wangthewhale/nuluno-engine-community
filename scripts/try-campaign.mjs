/** One command: public URL to source-bound campaign drafts; no model or cloud bill.
 * Copies only source into a private temporary consumer; existing installs/state
 * are never replaced. Installs locked dependencies, then calls the real helper.
 */
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {existsSync,lstatSync,mkdirSync,mkdtempSync,readFileSync,readdirSync,writeFileSync,chmodSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname,join,resolve,relative,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {preflightCampaign} from './campaign-preflight.mjs';

const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const ROOT=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const {values}=parseArgs({options:{out:{type:'string'},url:{type:'string',default:'https://engine.nuluno.com/?lang=en'},candidate:{type:'string'},revision:{type:'string',default:'starter'},revoke:{type:'boolean',default:false}}});
const pyproject=existsSync(join(ROOT,'pyproject.toml'))?readFileSync(join(ROOT,'pyproject.toml'),'utf8'):'';
const pkg=existsSync(join(ROOT,'package.json'))?JSON.parse(readFileSync(join(ROOT,'package.json'),'utf8')):null;
const product=/name\s*=\s*"nuluno-engine-community"/.test(pyproject)?'engine':pkg?.name==='@nuluno/access-community'?'access':null;
if(!product)throw Error('reviewed_community_source_required');
const output=resolve(values.out??'my-campaign');
if(product!=='engine')throw Error('engine_community_source_required');
preflightCampaign({url:values.url,out:output,revision:values.revision});
const temporary=mkdtempSync(join(tmpdir(),'nuluno-private-public-trial-'));chmodSync(temporary,0o700);
const source=join(temporary,'source');mkdirSync(source,{mode:0o700});
const userConfig=join(temporary,'empty-user-config'),globalConfig=join(temporary,'empty-global-config');
for(const p of [userConfig,globalConfig])writeFileSync(p,'',{mode:0o600,flag:'wx'});
const env={PATH:process.env.PATH??'',LANG:process.env.LANG??'en_US.UTF-8',GIT_CONFIG_NOSYSTEM:'1',GIT_CONFIG_GLOBAL:userConfig,GIT_TERMINAL_PROMPT:'0',PIP_CONFIG_FILE:process.platform==='win32'?'NUL':'/dev/null',PIP_NO_INPUT:'1',PIP_DISABLE_PIP_VERSION_CHECK:'1',PIP_CACHE_DIR:join(temporary,'pip-cache'),NPM_CONFIG_USERCONFIG:userConfig,NPM_CONFIG_GLOBALCONFIG:globalConfig,NPM_CONFIG_CACHE:join(temporary,'npm-cache')};
for(const key of ['SSL_CERT_FILE','SSL_CERT_DIR','SYSTEMROOT','WINDIR','TMPDIR','TEMP','TMP'])if(process.env[key])env[key]=process.env[key];
const allowedFolders=new Set(['.github','community','community_namespace','deploy','docs','examples','scripts','skills','src','tests']);
const allowedTop=new Set(['README.md','LICENSE','LICENSE.txt','NOTICE','THIRD_PARTY.md','CONTRIBUTING.md','SECURITY.md','pyproject.toml','setup.py','MANIFEST.in','community.json','skill-catalog.json','package.json','package-lock.json','tsconfig.json','tsconfig.build.json','Dockerfile','compose.yaml','SETUP.md']);
const ignoredNames=new Set(['.git','.venv','node_modules','__pycache__','private','.private','dist','build','credentials.json','authorization.json','service.json']);
let copiedBytes=0,stage='source_snapshot',success=false;
const inventory=[];
function copy(path){
 const full=join(ROOT,path),stat=lstatSync(full);
 if(full===output||full.startsWith(output+sep))return;
 if(ignoredNames.has(path.split(sep).at(-1))||path.split(sep).some(x=>x.startsWith('.env')||x.endsWith('.egg-info'))||/\.(?:sqlite3?|db|log|whl|zip|tgz)$/i.test(path))return;
 if(stat.isSymbolicLink())throw Error('source_symlink_not_allowed_in_trial_snapshot');
 const target=join(source,path);
 if(stat.isDirectory()){mkdirSync(target,{recursive:true,mode:0o700});for(const name of readdirSync(full).sort())copy(join(path,name));return;}
 if(!stat.isFile())throw Error('source_special_file_not_allowed');
 copiedBytes+=stat.size;if(stat.size>8*1024*1024||copiedBytes>64*1024*1024)throw Error('bounded_source_snapshot_exceeded');
 const bytes=readFileSync(full);writeFileSync(target,bytes,{mode:0o600,flag:'wx'});inventory.push([path.split(sep).join('/'),sha(bytes)]);
}
function run(command,args,{cwd=source,expected=0,timeout=180000}={}){
 try{const stdout=execFileSync(command,args,{cwd,env,encoding:'utf8',stdio:['ignore','pipe','pipe'],timeout,maxBuffer:2*1024*1024});if(expected!==0)throw Error('unexpected_success_exit');return stdout;}
 catch(error){
  if(error.status===expected)return error.stdout??'';
  writeFileSync(join(temporary,'failed-step.stderr'),String(error.stderr??'step_failed'),{mode:0o600});throw Error('trial_step_failed');
 }
}
function npmEntry(){
 const base=dirname(process.execPath);
 for(const p of [join(base,'../lib/node_modules/npm/bin/npm-cli.js'),join(base,'node_modules/npm/bin/npm-cli.js'),join(base,'npm')])if(existsSync(p))return p;
 throw Error('npm_with_node_required');
}
function pythonEntry(){
 for(const command of ['python3','python']){
  try{const text=run(command,['-c','import sys;assert sys.version_info>=(3,9);print(sys.executable)'],{timeout:10000});return text.trim();}catch{}
 }
 throw Error('python39_or_newer_required');
}
try{
 assert.ok(Number(process.versions.node.split('.')[0])>=24,'Node24_required_for_campaign_trial');
 for(const name of readdirSync(ROOT).sort())if(allowedFolders.has(name)||allowedTop.has(name))copy(name);
 const sourceSnapshotSha256=sha(JSON.stringify(inventory));
 stage='install_engine';console.error('[1/3] Install reviewed source in an isolated private consumer. Existing installation/state is preserved.');
 run(pythonEntry(),['-m','venv',join(source,'.trial-python')]);
 const python=join(source,'.trial-python',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 run(python,['-m','pip','install','.']);
 run(process.execPath,[npmEntry(),'ci','--omit=dev','--ignore-scripts','--no-audit','--no-fund'],{cwd:join(source,'deploy/mcp-gateway')});env.ENGINE_PYTHON=python;
 stage='original_campaign';console.error('[2/3] Execute the original scoped campaign request through standard MCP.');
 const args=[join(source,'examples/campaign/first-campaign.mjs'),'--url',values.url,'--out',output,'--revision',values.revision];
 if(values.candidate)args.push('--candidate',resolve(values.candidate));if(values.revoke)args.push('--revoke');
 let stdout,exit=0;
 try{stdout=execFileSync(process.execPath,args,{cwd:source,env,encoding:'utf8',stdio:['ignore','pipe','pipe'],timeout:120000,maxBuffer:2*1024*1024});}
 catch(e){if(e.status===3){stdout=e.stdout;exit=3;}else{writeFileSync(join(temporary,'failed-step.stderr'),String(e.stderr??'campaign_failed'),{mode:0o600});throw Error('original_campaign_failed');}}
 const proof=JSON.parse(String(stdout).trim().split('\n').at(-1));assert.equal(proof.passed,true);
 if(exit===3){assert.equal(proof.native_denial_status,401);assert.equal(proof.new_key_created,false);assert.equal(proof.read_succeeded,false);process.exitCode=3;}
 console.error('[3/3] Saved public facts, draft pages and original proof; costs and growth effects remain unknown.');
 console.log(JSON.stringify({...proof,bootstrap_sha256:sha(readFileSync(fileURLToPath(import.meta.url))),source_snapshot_sha256:sourceSnapshotSha256,helper_sha256:sha(readFileSync(join(source,'examples/campaign/first-campaign.mjs'))),dependency_setup_network_calls:null,existing_install_or_state_replaced:false}));
 success=true;
}catch{
 console.error('Campaign trial failed at '+stage+'. Original state remains. No fixture, replacement key, new scope or success result was substituted. Retain the output and original IDs; check the actual prerequisites or source.');process.exitCode=1;
}finally{if(success)rmSync(temporary,{recursive:true,force:true});}
