/** One command from a reviewed source checkout; no account, model or cloud bill.
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

const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const ROOT=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const {values}=parseArgs({options:{out:{type:'string'},url:{type:'string'},'status-id':{type:'string'}}});
const pyproject=existsSync(join(ROOT,'pyproject.toml'))?readFileSync(join(ROOT,'pyproject.toml'),'utf8'):'';
const pkg=existsSync(join(ROOT,'package.json'))?JSON.parse(readFileSync(join(ROOT,'package.json'),'utf8')):null;
const product=/name\s*=\s*"nuluno-engine-community"/.test(pyproject)?'engine':pkg?.name==='@nuluno/access-community'?'access':null;
if(!product)throw Error('reviewed_community_source_required');
const output=resolve(values.out??('nuluno-'+product+'-trial'));
const summaryPath=join(output,'trial-summary.json');
if(existsSync(summaryPath)){
 const saved=JSON.parse(readFileSync(summaryPath,'utf8'));
 assert.equal(saved.schema,'nuluno-one-command-public-trial-v1');assert.equal(saved.product,product);assert.equal(saved.passed,true);
 console.log(JSON.stringify({schema:'nuluno-recorded-trial-replay-v1',product,state:'replayed_recorded_trial',historicalPassed:true,executedCurrentTrial:false,newProviderActions:0,originalTrial:saved}));
 process.exit(0);
}
if(existsSync(output))throw Error('existing_output_preserved_resume_original_helper_do_not_reset');
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
 assert.ok(Number(process.versions.node.split('.')[0])>=24,'Node24_required_for_one_command_trial');
 for(const name of readdirSync(ROOT).sort())if(allowedFolders.has(name)||allowedTop.has(name))copy(name);
 const sourceSnapshotSha256=sha(JSON.stringify(inventory));
 let checkoutCommit=null,checkoutClean=null;
 try{checkoutCommit=run('git',['rev-parse','HEAD'],{cwd:ROOT,timeout:10000}).trim();checkoutClean=run('git',['status','--porcelain','--untracked-files=all'],{cwd:ROOT,timeout:10000}).trim()==='';}catch{}
 console.log('[1/5] Install this reviewed source in a separate private temporary consumer.');
 const npm=npmEntry();
 if(product==='engine'){
  stage='install_engine';run(pythonEntry(),['-m','venv',join(source,'.trial-python')]);
  const python=join(source,'.trial-python',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  run(python,['-m','pip','install','.']);run(process.execPath,[npm,'ci','--omit=dev','--ignore-scripts','--no-audit','--no-fund'],{cwd:join(source,'deploy/mcp-gateway')});env.ENGINE_PYTHON=python;
 }else{stage='install_access';run(process.execPath,[npm,'ci','--ignore-scripts','--no-audit','--no-fund']);run(process.execPath,[npm,'run','build']);}
 const helper=product==='engine'?'examples/source-brief/brief.mjs':'examples/public-status/read-once.mjs';
 const args=[join(source,helper),'--out',output];
 if(product==='engine'&&values.url)args.push('--url',values.url);
 if(product==='access'&&values['status-id'])args.push('--status-id',values['status-id']);
 const definitions=[['read','[2/5] Make one real public task.',[],0],['resume','[3/5] Start a new process and reuse the original task/receipt.',[],0],['revoke','[4/5] Revoke the original key.', ['--revoke'],0],['denial','[5/5] Start another process and require native revoked-key denial.',[],3]];
 const steps=[];
 for(const [name,message,extra,expected]of definitions){stage=name;console.log(message);const result=JSON.parse(run(process.execPath,[...args,...extra],{expected,timeout:90000}));assert.equal(result.passed,true);steps.push({stage:name,expectedExit:expected,result});}
 const [a,b,c,d]=steps.map(x=>x.result);
 if(product==='engine'){assert.equal(a.receiptId,b.receiptId);assert.equal(b.reusedOriginalReceipt,true);assert.equal(b.providerRequestsReserved,1);assert.equal(d.httpStatus,401);}
 else{assert.equal(a.taskId,b.taskId);assert.equal(a.receiptHash,b.receiptHash);assert.equal(b.resumedOriginalTask,true);assert.equal(b.providerCalls,1);assert.equal(d.agentDenied,true);}
 assert.equal(d.newKeyCreated,false);assert.equal(d.readSucceeded,false);assert.equal(d.afterFreshServiceProcess,true);
 const summary={schema:'nuluno-one-command-public-trial-v1',product,passed:true,observedAt:new Date().toISOString(),packageVersion:a.engineVersion??a.packageVersion,sourceSnapshotSha256,sourceFiles:inventory.length,baseCheckoutCommit:checkoutCommit,checkoutClean,bootstrapSha256:sha(readFileSync(fileURLToPath(import.meta.url))),helperSha256:sha(readFileSync(join(source,helper))),steps,modelCalls:0,externalProviderWrites:0,dependencySetupNetworkCalls:null,completeCost:null,privateAccountUsed:false,existingInstallOrStateReplaced:false,temporaryPrivateConsumerRemovedAfterSuccess:true};
 writeFileSync(summaryPath,JSON.stringify(summary,null,2)+'\n',{mode:0o600,flag:'wx'});success=true;
 console.log('PASS: real artifact, original-task reuse and native revoked-key denial.');
 console.log('Open '+(product==='engine'?'brief.md':'status.md')+' in your selected output folder.');
 console.log(JSON.stringify(summary));
}catch{
 console.error('Trial failed at '+stage+'. Original state/output was preserved. No fixture, replacement key or replacement task was substituted. Inspect the actual prerequisites, public source and retained original state before trying again.');process.exitCode=1;
}finally{if(success)rmSync(temporary,{recursive:true,force:true});}
