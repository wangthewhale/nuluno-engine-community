/** CLI negatives reject filesystem redirection before an Engine/source call. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,symlinkSync,existsSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {publicCampaignURL} from '../scripts/campaign-preflight.mjs';

const helper=fileURLToPath(new URL('../scripts/try-campaign.mjs',import.meta.url));
function attempt(out,revision='starter'){
 const r=spawnSync(process.execPath,[helper,'--url','https://www.sqlite.org/index.html','--out',out,'--revision',revision],{encoding:'utf8',timeout:10000,env:{PATH:process.env.PATH??''}});
 assert.equal(r.status,1);assert.equal(r.stdout,'');assert.equal(existsSync(join(out,'private/state')),false,'Rejected before runtime or source initialization');return r.stderr;
}
function fixture(){const root=mkdtempSync(join(tmpdir(),'campaign-path-check-')),out=join(root,'out'),external=join(root,'external');mkdirSync(out);mkdirSync(external);mkdirSync(join(out,'private'));writeFileSync(join(external,'canary'),'original');return {root,out,external};}
test('dot/traversal revisions cannot write the output parent',()=>{
 const f=fixture();try{for(const revision of ['.','..','../escape','.hidden','/absolute'])assert.match(attempt(f.out,revision),/bounded_explicit_revision_required/);assert.equal(existsSync(join(f.root,'research.json')),false);assert.equal(readFileSync(join(f.external,'canary'),'utf8'),'original');}finally{rmSync(f.root,{recursive:true,force:true});}
});
test('existing revision directory or output file symlinks cannot redirect writes',()=>{
 const f=fixture();try{
  symlinkSync(f.external,join(f.out,'starter'),'dir');assert.match(attempt(f.out),/revision_directory_required_no_symlink/);assert.equal(readFileSync(join(f.external,'canary'),'utf8'),'original');
  rmSync(join(f.out,'starter'));mkdirSync(join(f.out,'starter'));symlinkSync(join(f.external,'canary'),join(f.out,'starter','campaign.json'));
  assert.match(attempt(f.out),/regular_file_required_no_symlink/);assert.equal(readFileSync(join(f.external,'canary'),'utf8'),'original');
 }finally{rmSync(f.root,{recursive:true,force:true});}
});
test('private directory and config/key/metadata symlinks fail before reading them',()=>{
 for(const name of ['directory','service.json','agent-key','original.json']){
  const f=fixture();try{
   if(name==='directory'){rmSync(join(f.out,'private'),{recursive:true});symlinkSync(f.external,join(f.out,'private'),'dir');}
   else symlinkSync(join(f.external,'canary'),join(f.out,'private',name));
   assert.match(attempt(f.out),name==='directory'?/private_directory_required_no_symlink/:/regular_file_required_no_symlink/);
   assert.equal(readFileSync(join(f.external,'canary'),'utf8'),'original');
  }finally{rmSync(f.root,{recursive:true,force:true});}
 }
});
test('bootstrap rejects credential-like public URL queries before dependency install; lang is allowed',()=>{
 const f=fixture();try{
  for(const key of ['token','api_key','code','oauth_code','session_id','password','client_secret','access-token','x-amz-signature','to%6ben']){
   const r=spawnSync(process.execPath,[helper,'--url','https://www.sqlite.org/?'+key+'=CONTROLLED_DO_NOT_ECHO','--out',f.out],{encoding:'utf8',timeout:5000,env:{PATH:process.env.PATH??''}});
   assert.equal(r.status,1);assert.match(r.stderr,/public_url_credentials_query_forbidden/);assert.doesNotMatch(r.stderr,/CONTROLLED_DO_NOT_ECHO|Install reviewed|pip|npm/);assert.equal(r.stdout,'');
  }
  assert.equal(publicCampaignURL('https://engine.nuluno.com/?lang=en').search,'?lang=en');
 }finally{rmSync(f.root,{recursive:true,force:true});}
});
