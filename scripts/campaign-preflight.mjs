/** Pure local/syntactic checks. No directory creation, package install or IO to a network. */
import {lstatSync,readdirSync} from 'node:fs';
import {resolve,join,dirname} from 'node:path';

const credentialKey=/(?:token|password|passwd|secret|apikey|session|credential|authorization|verification|signature|jwt|code$|^key$|^auth$|^sig$|^ticket$|^otp$|^cfchltk$)/;
export function publicCampaignURL(value){
 let url;try{url=new URL(value);}catch{throw Error('public_https_product_url_required');}
 if(url.protocol!=='https:'||url.username||url.password||url.hash||!url.hostname)throw Error('public_https_product_url_required');
 for(const key of url.searchParams.keys())if(credentialKey.test(key.toLowerCase().replace(/[^a-z0-9]/g,'')))throw Error('public_url_credentials_query_forbidden');
 return url;
}
function stat(path){try{return lstatSync(path);}catch(e){if(e.code==='ENOENT')return null;throw e;}}
function directory(path,label){const s=stat(path);if(s&&(s.isSymbolicLink()||!s.isDirectory()))throw Error(label+'_directory_required_no_symlink');return !!s;}
function regular(path){const s=stat(path);if(s&&(s.isSymbolicLink()||!s.isFile()))throw Error('regular_file_required_no_symlink');}
export function preflightCampaign({url,out,revision='starter'}){
 const source=publicCampaignURL(url);
 if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(revision)||revision==='private')throw Error('bounded_explicit_revision_required');
 const output=resolve(out),revisionPath=resolve(output,revision),privateDir=join(output,'private');
 if(dirname(revisionPath)!==output)throw Error('revision_must_be_direct_child');
 if(directory(output,'output')){
  const hasPrivate=directory(privateDir,'private');
  if(hasPrivate){
   for(const name of ['service.json','agent-key','original.json'])regular(join(privateDir,name));
   if(directory(join(privateDir,'state'),'state'))for(const name of ['workflow.sqlite3','workflow.sqlite3-wal','workflow.sqlite3-shm','initial-profile.json'])regular(join(privateDir,'state',name));
  }
  if(directory(revisionPath,'revision'))for(const name of readdirSync(revisionPath))regular(join(revisionPath,name));
  for(const name of ['revocation.json','denial.json'])regular(join(output,name));
  if(!hasPrivate||['service.json','agent-key','original.json'].some(name=>!stat(join(privateDir,name))))throw Error('existing_output_missing_original_private_state_preserved');
 }
 return {url:source,out:output,revisionPath};
}
