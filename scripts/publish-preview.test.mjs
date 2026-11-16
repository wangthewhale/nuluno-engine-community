// Offline fixed-preview validation only. Never invokes the publisher or GitHub.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,rmSync,symlinkSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {CONFIG,verifyLocalArtifacts,validateExistingState} from './publish-preview.mjs';

const originalFetch=globalThis.fetch;let networkAttempts=0;
globalThis.fetch=async()=>{networkAttempts++;throw Error('preview_validator_test_network_forbidden');};
after(()=>{globalThis.fetch=originalFetch;assert.equal(networkAttempts,0);});
const hash=data=>createHash('sha256').update(data).digest('hex');
function fixture({manifest:mutateManifest,checksums:mutateChecksums}={}){
 const root=mkdtempSync(join(tmpdir(),'nuluno-preview-validator-')),directory=join(root,'artifacts');mkdirSync(directory);
 const config=structuredClone(CONFIG),payloads={'payload-a.tgz':Buffer.from('archive-a\n'),'payload-b.zip':Buffer.from('source-b\n')};
 const metadata=Object.fromEntries(Object.entries(payloads).map(([name,data])=>[name,{sha256:hash(data),bytes:data.length}]));
 const manifest={schema:config.manifest.schema,version:config.version,[config.manifest.sourceField]:config.sourceCommit,[config.manifest.commercialField]:false,...config.manifest.expected,artifacts:structuredClone(metadata)};
 mutateManifest?.(manifest);
 const data={...payloads,'release-manifest.json':Buffer.from(JSON.stringify(manifest)+'\n')};
 let lines=Object.entries(data).map(([name,bytes])=>hash(bytes)+'  '+name);
 if(mutateChecksums)lines=mutateChecksums(lines);
 data.SHA256SUMS=Buffer.from(lines.join('\n')+'\n');
 config.files=Object.fromEntries(Object.entries(data).map(([name,bytes])=>[name,{sha256:hash(bytes),bytes:bytes.length}]));
 for(const [name,bytes]of Object.entries(data))writeFileSync(join(directory,name),bytes);
 return {root,directory,config,data,manifest,cleanup:()=>rmSync(root,{recursive:true,force:true})};
}
function withFixture(fn,options){const f=fixture(options);try{return fn(f);}finally{f.cleanup();}}
const release=config=>({tag_name:config.tag,target_commitish:config.sourceCommit,draft:false,prerelease:true,name:config.title,body:config.notes,id:1});
const assets=config=>Object.entries(config.files).map(([name,v],index)=>({name,id:index+1,size:v.bytes,digest:'sha256:'+v.sha256}));
const sorted=value=>[...value].sort();

test('four pinned regular files validate with exact manifest and retained upload bytes',()=>withFixture(f=>{
 const result=verifyLocalArtifacts(f.directory,f.config);
 assert.deepEqual(result.manifest,f.manifest);assert.equal(result.files.length,4);
 assert.deepEqual(sorted(result.files.map(file=>file.name)),sorted(Object.keys(f.config.files)));
 for(const file of result.files){assert.ok(Buffer.isBuffer(file.data));assert.equal(file.bytes,f.config.files[file.name].bytes);assert.equal(file.sha256,f.config.files[file.name].sha256);assert.deepEqual(file.data,f.data[file.name]);}
 // Upload bytes are snapshotted in memory before any possible later file edit.
 const first=result.files[0],original=Buffer.from(first.data);writeFileSync(join(f.directory,first.name),'changed after verification');assert.deepEqual(first.data,original);
}));

test('artifact inventory rejects added, missing and directory entries',()=>{
 withFixture(f=>{writeFileSync(join(f.directory,'unexpected.txt'),'extra');assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/artifact_inventory_mismatch/);});
 withFixture(f=>{rmSync(join(f.directory,'payload-a.tgz'));assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/artifact_inventory_mismatch/);});
 withFixture(f=>{rmSync(join(f.directory,'payload-a.tgz'));mkdirSync(join(f.directory,'payload-a.tgz'));assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/artifact_not_regular_file/);});
});

test('a symlink is refused even when it points to identical pinned bytes',()=>withFixture(f=>{
 const target=join(f.root,'outside.bin');writeFileSync(target,f.data['payload-a.tgz']);rmSync(join(f.directory,'payload-a.tgz'));symlinkSync(target,join(f.directory,'payload-a.tgz'));
 assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/artifact_not_regular_file/);
}));

test('payload size changes and same-size byte tampering are both refused',()=>{
 withFixture(f=>{writeFileSync(join(f.directory,'payload-a.tgz'),Buffer.concat([f.data['payload-a.tgz'],Buffer.from('x')]));assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/artifact_size_mismatch/);});
 withFixture(f=>{const bytes=Buffer.from(f.data['payload-a.tgz']);bytes[0]^=1;writeFileSync(join(f.directory,'payload-a.tgz'),bytes);assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/artifact_sha256_mismatch/);});
});

test('manifest schema/version/source/commercial lineage is checked after hashes are valid',()=>{
 const mutations=[m=>{m.schema='wrong-schema';},m=>{m.version='different-version';},m=>{m[CONFIG.manifest.sourceField]='0'.repeat(40);},m=>{m[CONFIG.manifest.commercialField]=true;},m=>{delete m[CONFIG.manifest.commercialField];}];
 for(const manifest of mutations)withFixture(f=>assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/manifest_lineage_mismatch/),{manifest});
 for(const key of Object.keys(CONFIG.manifest.expected??{}))withFixture(f=>assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/manifest_identity_mismatch/),{manifest:m=>{m[key]='wrong-product-identity';}});
});

test('manifest payload inventory and metadata cannot disagree with fixed files',()=>{
 withFixture(f=>assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/manifest_asset_inventory_mismatch/),{manifest:m=>{delete m.artifacts['payload-a.tgz'];}});
 withFixture(f=>assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/manifest_asset_inventory_mismatch/),{manifest:m=>{m.artifacts['extra.tgz']={sha256:'0'.repeat(64),bytes:1};}});
 for(const field of ['sha256','bytes'])withFixture(f=>assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/manifest_asset_mismatch/),{manifest:m=>{m.artifacts['payload-a.tgz'][field]=field==='sha256'?'0'.repeat(64):1;}});
});

test('checksum list rejects duplicate, missing, extra, malformed and false digests',()=>{
 for(const checksums of [lines=>[...lines,lines[0]],lines=>[lines[0].replace('  ',' '),...lines.slice(1)]])withFixture(f=>assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/invalid_checksum_manifest/),{checksums});
 for(const checksums of [lines=>lines.slice(1),lines=>[...lines,'0'.repeat(64)+'  extra.bin']])withFixture(f=>assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/checksum_inventory_mismatch/),{checksums});
 withFixture(f=>assert.throws(()=>verifyLocalArtifacts(f.directory,f.config),/checksum_mismatch/),{checksums:lines=>['0'.repeat(64)+lines[0].slice(64),...lines.slice(1)]});
});

test('missing release plans only the four pinned assets; an existing exact tag may be reused',()=>withFixture(f=>{
 for(const tagCommit of [null,f.config.sourceCommit])assert.deepEqual(validateExistingState({tagCommit,release:null,assets:[]},f.config),{createRelease:true,missingAssets:Object.keys(f.config.files),skipAssets:[]});
 assert.throws(()=>validateExistingState({tagCommit:'0'.repeat(40),release:null,assets:[]},f.config),/existing_tag_source_mismatch/);
 assert.throws(()=>validateExistingState({tagCommit:null,release:null,assets:assets(f.config)},f.config),/assets_without_release/);
}));

test('matching existing release/assets are skipped without changing the input state',()=>withFixture(f=>{
 const state={tagCommit:f.config.sourceCommit,release:release(f.config),assets:assets(f.config)},before=JSON.stringify(state);
 const plan=validateExistingState(state,f.config);assert.equal(plan.createRelease,false);assert.deepEqual(plan.missingAssets,[]);assert.deepEqual(sorted(plan.skipAssets),sorted(Object.keys(f.config.files)));assert.equal(JSON.stringify(state),before);
 const partial=validateExistingState({...state,assets:[state.assets[0]]},f.config);assert.equal(partial.createRelease,false);assert.deepEqual(partial.skipAssets,[state.assets[0].name]);assert.deepEqual(sorted(partial.missingAssets),sorted(Object.keys(f.config.files).filter(name=>name!==state.assets[0].name)));
}));

test('existing release requires the exact tag/source/title/body and preview flags',()=>withFixture(f=>{
 const good=release(f.config),base={tagCommit:f.config.sourceCommit,release:good,assets:[]};
 assert.throws(()=>validateExistingState({...base,tagCommit:null},f.config),/existing_release_tag_missing/);
 assert.throws(()=>validateExistingState({...base,tagCommit:'0'.repeat(40)},f.config),/existing_tag_source_mismatch/);
 const patches=[{id:0},{id:'1'},{tag_name:'wrong-tag'},{target_commitish:'0'.repeat(40)},{draft:true},{prerelease:false},{name:'wrong-title'},{body:good.body+'changed'}];
 for(const patch of patches)assert.throws(()=>validateExistingState({...base,release:{...good,...patch}},f.config),/existing_release_mismatch/);
}));

test('existing assets reject duplicate/extra names and absent or mismatched digest metadata',()=>withFixture(f=>{
 const good=assets(f.config),base={tagCommit:f.config.sourceCommit,release:release(f.config)};
 assert.throws(()=>validateExistingState({...base,assets:[good[0],{...good[0],id:99}]},f.config),/existing_asset_inventory_mismatch/);
 assert.throws(()=>validateExistingState({...base,assets:[{...good[0],name:'unexpected.bin'}]},f.config),/existing_asset_inventory_mismatch/);
 for(const patch of [{id:0},{id:'1'},{size:good[0].size+1},{digest:'sha256:'+'0'.repeat(64)},{digest:undefined},{digest:null},{digest:good[0].digest.replace('sha256:','SHA256:')}])assert.throws(()=>validateExistingState({...base,assets:[{...good[0],...patch}]},f.config),/existing_asset_digest_mismatch/);
}));

test('GitHub form CRLF normalization preserves exact text and every newline',()=>withFixture(f=>{
 const good=release(f.config),base={tagCommit:f.config.sourceCommit,assets:[]};
 const crlf=good.body.replace(/\n/g,'\r\n');
 assert.equal(validateExistingState({...base,release:{...good,body:crlf}},f.config).createRelease,false);
 const changed=[crlf.replace('Apache','apache'),crlf+'\r\n',' '+crlf,crlf+' ',crlf.replace(/\r\n/,'\r'),crlf.replace('Apache','Ａpache')];
 for(const body of changed)assert.throws(()=>validateExistingState({...base,release:{...good,body}},f.config),/existing_release_mismatch/);
}));
