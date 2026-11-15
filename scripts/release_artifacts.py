"""Prepare reviewed community artifacts from HEAD, without publishing anything.

Only Git-tracked blob bytes from the exact commit are exported. The wheel is
built from that isolated snapshot with a minimal, credential-free environment.
Checksums provide integrity, not a signed publisher identity or a security audit.
"""
import argparse
import configparser
import email.parser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'nuluno-engine-community'
VERSION = re.compile(r'\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?')
ALLOWED_DIRS = {'.github','community','community_namespace','deploy','docs','examples','scripts','skills','tests'}
ALLOWED_TOP = {'README.md','LICENSE','NOTICE','THIRD_PARTY.md','pyproject.toml','setup.py','MANIFEST.in','community.json','skill-catalog.json','CONTRIBUTING.md','SECURITY.md','.gitignore','.dockerignore'}
FORBIDDEN_PARTS = {'.git','.venv','.private','node_modules','__pycache__','private','credentials','commercial-artifacts'}
FORBIDDEN_FILES = {'credentials.json','authorization.json','service.json','goal-catalog.json','channel-catalog.json'}
SECRET_PATTERNS = [re.compile(rb'sk_(?:live|test)_[A-Za-z0-9]{16,}'),re.compile(rb'gh[pousr]_[A-Za-z0-9]{32,}'),
                   re.compile(rb'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{32,}-----END (?:RSA |EC )?PRIVATE KEY-----')]


def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT)


def safe_path(name):
    parts=PurePosixPath(name).parts
    if not parts or name.startswith('/') or '\\' in name or any(p in ('.','..') for p in parts):raise ValueError('unsafe_source_path')
    if any(p in FORBIDDEN_PARTS or p.endswith('.egg-info') for p in parts):raise ValueError('private_or_generated_source_path')
    if Path(name).name in FORBIDDEN_FILES or Path(name).name.startswith('.env') or '.sqlite' in name or name.endswith(('.log','.zip','.whl')):raise ValueError('restricted_source_file')
    if parts[0] not in ALLOWED_DIRS and name not in ALLOWED_TOP:raise ValueError('unreviewed_source_root')
    if parts[0]=='examples' and not (len(parts)>=3 and parts[1]=='method-learning' or name in ('examples/source-brief/README.md','examples/source-brief/brief.mjs')):raise ValueError('unreviewed_example_folder')
    if parts[0]=='skills' and (len(parts)<2 or parts[1] not in {'growth-meta-ig-loop','growth-read-existing-offer'}):raise ValueError('commercial_skill_folder_forbidden')
    if '/references/graphs/' in name:raise ValueError('commercial_graph_library_forbidden')
    return name


def read_snapshot():
    commit=git('rev-parse','HEAD').decode().strip()
    files={}
    total=0
    for entry in git('ls-tree','-r','-z','HEAD').split(b'\0'):
        if not entry:continue
        header,name=entry.split(b'\t',1);mode,kind,sha=header.split()
        if kind!=b'blob' or mode not in (b'100644',b'100755'):raise ValueError('source_links_and_submodules_forbidden')
        name=safe_path(name.decode('utf-8'));data=git('cat-file','blob',sha.decode())
        total+=len(data)
        if len(data)>8*1024*1024 or total>64*1024*1024:raise ValueError('source_size_limit')
        if any(p.search(data) for p in SECRET_PATTERNS):raise ValueError('possible_credential_in_tracked_source')
        files[name]=data
    if not files or len(files)>5000:raise ValueError('invalid_source_inventory')
    blueprint=json.loads(files['skills/growth-meta-ig-loop/references/blueprint.json'])
    if [s['id'] for s in blueprint['skills']]!=['S01'] or blueprint['workflows']:raise ValueError('community_starter_boundary_changed')
    return commit,files


def version_of(files,expected='',tag=None):
    text=files['pyproject.toml'].decode();project=text.split('[project]',1)[1].split('\n[',1)[0]
    name=re.search(r'^name\s*=\s*"([^"]+)"',project,re.M).group(1)
    version=re.search(r'^version\s*=\s*"([^"]+)"',project,re.M).group(1)
    if name!='nuluno-engine-community' or not VERSION.fullmatch(version):raise ValueError('invalid_community_version')
    for filename in ('community_namespace/__init__.py','skills/growth-meta-ig-loop/runtime/growth_loop/__init__.py'):
        declared=re.search(r'^__version__\s*=\s*[\'\"]([^\'\"]+)',files[filename].decode(),re.M)
        if not declared or declared.group(1)!=version:raise ValueError('runtime_version_does_not_match_distribution')
    if json.loads(files['community.json'])['version']!=version:raise ValueError('resource_version_does_not_match_distribution')
    if expected and expected!=version:raise ValueError('requested_version_does_not_match_source')
    if tag and tag!='community-v'+version:raise ValueError('tag_does_not_match_source_version')
    return version


def verify_wheel(path,version):
    with zipfile.ZipFile(path) as wheel:
        names=wheel.namelist()
        for name in names:
            if not (name.startswith('nuluno_engine_community/') or name.startswith('nuluno_engine_community-'+version+'.dist-info/')):raise ValueError('wheel_namespace_boundary_changed')
            if any(p in FORBIDDEN_PARTS for p in PurePosixPath(name).parts) or Path(name).name in FORBIDDEN_FILES or '/references/graphs/' in name:raise ValueError('restricted_wheel_payload')
        metadata=email.parser.Parser().parsestr(wheel.read('nuluno_engine_community-'+version+'.dist-info/METADATA').decode())
        if metadata['Name']!='nuluno-engine-community' or metadata['Version']!=version:raise ValueError('wheel_identity_mismatch')
        parser=configparser.ConfigParser();parser.read_string(wheel.read('nuluno_engine_community-'+version+'.dist-info/entry_points.txt').decode())
        if dict(parser['console_scripts'])!={'nuluno-engine-community':'nuluno_engine_community.growth_loop.cli:main'}:raise ValueError('legacy_console_script_overwrite')
        skills=[name for name in names if name.endswith('/SKILL.md')]
        if len(skills)!=2:raise ValueError('wheel_starter_count_changed')


def prepare(output,expected=''):
    commit,files=read_snapshot()
    tag=os.environ.get('GITHUB_REF_NAME') if os.environ.get('GITHUB_REF_TYPE')=='tag' else None
    version=version_of(files,expected,tag)
    output=Path(output).absolute()
    if output.exists() and any(output.iterdir()):raise ValueError('output_directory_must_be_empty')
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nuluno-release-snapshot-') as temporary:
        snapshot=Path(temporary)/'source';snapshot.mkdir()
        for name,data in files.items():
            destination=snapshot/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(data)
        wheels=Path(temporary)/'wheels';wheels.mkdir()
        env={k:os.environ[k] for k in ('PATH','SYSTEMROOT','LANG','LC_ALL','TMPDIR','SSL_CERT_FILE','SSL_CERT_DIR') if k in os.environ}
        env.update(PIP_CONFIG_FILE=os.devnull,PIP_NO_INPUT='1',PIP_DISABLE_PIP_VERSION_CHECK='1',SOURCE_DATE_EPOCH=git('show','-s','--format=%ct','HEAD').decode().strip())
        subprocess.run([sys.executable,'-m','pip','wheel','--no-deps','--wheel-dir',str(wheels),'.'],cwd=snapshot,env=env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        produced=list(wheels.glob('*.whl'))
        if len(produced)!=1:raise ValueError('one_community_wheel_required')
        verify_wheel(produced[0],version)
        wheel_path=output/produced[0].name;wheel_path.write_bytes(produced[0].read_bytes())
    source_path=output/(PREFIX+'-'+version+'-source.zip')
    with zipfile.ZipFile(source_path,'x',zipfile.ZIP_DEFLATED) as zipped:
        for name,data in sorted(files.items()):
            info=zipfile.ZipInfo(PREFIX+'-'+version+'/'+name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16;zipped.writestr(info,data)
    artifacts={p.name:{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in (wheel_path,source_path)}
    manifest={'schema':'nuluno-community-release-readiness-v1','version':version,'source_commit':commit,'source_file_count':len(files),
              'artifacts':artifacts,'commercial_library_included':False,'namespace':'nuluno_engine_community.growth_loop',
              'published_release':False,'published_to_pypi':False,'signed_attestation':False,'model_calls':0,'platform_writes':0}
    manifest_path=output/'release-manifest.json';manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    checksums={**artifacts,manifest_path.name:{'sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest()}}
    (output/'SHA256SUMS').write_text(''.join(v['sha256']+'  '+name+'\n' for name,v in sorted(checksums.items())))
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--expected-version',default='');parser.add_argument('--output',required=True);args=parser.parse_args()
    try:print(json.dumps(prepare(args.output,args.expected_version),indent=2))
    except (ValueError,KeyError,subprocess.CalledProcessError) as error:
        print(json.dumps({'prepared':False,'error':str(error) if isinstance(error,ValueError) else 'release_preparation_failed'}),file=sys.stderr);sys.exit(2)
