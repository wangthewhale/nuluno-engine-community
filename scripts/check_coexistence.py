"""Verify installed namespaces/entry points without printing machine paths."""
import argparse
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path


def check(require_commercial=False):
    os.environ.pop('NULUNO_METHOD_PACK_DIR',None)
    community=importlib.import_module('nuluno_engine_community.growth_loop')
    resources=importlib.import_module('nuluno_engine_community.growth_loop.resources')
    starter=resources.library_status()
    assert community.__version__=='0.13.0a2' and starter['work_skills']==1 and starter['active_pack'] is None
    entrypoints={e.name for e in importlib.metadata.distribution('nuluno-engine-community').entry_points if e.group=='console_scripts'}
    assert entrypoints=={'nuluno-engine-community'}
    try:
        commercial_distribution=importlib.metadata.distribution('nuluno-engine')
        original_present=True
    except importlib.metadata.PackageNotFoundError:
        commercial_distribution=None;original_present=False
    original_version=None
    if original_present:
        original=importlib.import_module('growth_loop')
        original_version=original.__version__
        assert original_version=='0.12.0'
        assert Path(original.__file__).resolve()!=Path(community.__file__).resolve()
        from growth_loop.goals import goal_catalog
        assert len(goal_catalog()['goals'])==99
        old_entries={e.name for e in importlib.metadata.distribution('nuluno-engine').entry_points if e.group=='console_scripts'}
        assert {'nuluno-engine','growth-loop'} <= old_entries
    if require_commercial and not original_present:raise ValueError('original_commercial_wheel_required_for_this_check')
    return {'community_distribution':'nuluno-engine-community','community_version':community.__version__,
            'community_namespace':'nuluno_engine_community.growth_loop','community_console_scripts':sorted(entrypoints),
            'community_starter_work_skills':starter['work_skills'], 'commercial_library_in_community':False,
            'original_commercial_distribution_tested':original_present,'original_version':original_version,
            'original_namespace_preserved':True if original_present else None,
            'original_console_scripts_preserved':True if original_present else None,
            'model_calls':0,'network_calls':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--require-commercial',action='store_true');parser.add_argument('--output');args=parser.parse_args()
    receipt=check(args.require_commercial);body=json.dumps(receipt,indent=2)+'\n'
    if args.output:
        with Path(args.output).open('x') as out:out.write(body)
    print(body,end='')
