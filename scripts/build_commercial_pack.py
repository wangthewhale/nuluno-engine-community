"""Produce a data-only commercial pack from the immutable licensed 0.12 ZIP.

This builder is Apache-2.0 code. Its output is separately licensed commercial
data, not part of the community distribution or permission to redistribute it.
"""
import argparse
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

SOURCE_SHA256 = '4010ada568c238fa63f01e12c1646d1f2537f3491c70f164b9ab5328ccfaf0c1'


def build(source_zip, destination):
    source_zip, destination = Path(source_zip), Path(destination)
    source_data=source_zip.read_bytes()
    if hashlib.sha256(source_data).hexdigest() != SOURCE_SHA256: raise ValueError('immutable_source_archive_mismatch')
    if destination.exists(): raise ValueError('refuse_overwrite_commercial_pack')
    files = {}
    with zipfile.ZipFile(io.BytesIO(source_data)) as original:
        for item in original.infolist():
            parts = item.filename.split('/',1)
            if len(parts)!=2: continue
            rel=parts[1]; segments=Path(rel).parts
            if rel == 'LICENSE.txt': name=rel
            elif rel in ('skill-catalog.json','workflow-catalog.json'):name='library/'+rel
            elif len(segments)>=3 and segments[0]=='skills' and 'runtime' not in segments and 'assets' not in segments and 'agents' not in segments:
                tail=segments[2:]
                if tail not in (('SKILL.md',),('.growth-pack.json',)) and not (tail[0]=='references' and Path(rel).suffix in ('.json','.md','.txt')):continue
                name='library/'+rel
            else:continue
            files[name]=original.read(item)
    manifest={'schema':'nuluno-method-pack-v1','pack_id':'nuluno-sales-methods','pack_version':'0.12.0',
              'kind':'commercial_data_only','requires':{'kernel_contract':'nuluno-method-resources-v1','starter_skill_id':'S01'},
              'source_archive_sha256':SOURCE_SHA256,'license':'commercial-end-user',
              'files':{n:{'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)} for n,b in sorted(files.items())}}
    files['pack.json']=(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED) as packed:
        for name,data in sorted(files.items()):
            entry=zipfile.ZipInfo(name,(2026,11,14,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED;entry.external_attr=0o100644<<16
            packed.writestr(entry,data)
    return {'pack_id':manifest['pack_id'],'pack_version':manifest['pack_version'],'files':len(files),
            'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'runtime_code_included':False,'license':'commercial-end-user'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source-zip',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    print(json.dumps(build(args.source_zip,args.output),indent=2))
