"""One resource layout for the editable source, skill ZIP and installed wheel."""
from pathlib import Path


def bundle_root():
    source = Path(__file__).resolve().parents[4]
    if (source / 'community.json').is_file() and (source / 'skills/growth-meta-ig-loop/SKILL.md').is_file():
        return source
    return Path(__file__).resolve().parent / '_bundle'


def skill_root():
    from .method_packs import active_library
    library, _ = active_library(bundle_root())
    return library / 'skills/growth-meta-ig-loop'


def library_status():
    import json
    from .method_packs import active_library
    library, active = active_library(bundle_root())
    root = library / 'skills/growth-meta-ig-loop/references'
    def count(name, key):
        path = root / name
        return len(json.loads(path.read_text())[key]) if path.is_file() else 0
    folders = [p for p in (library / 'skills').iterdir() if p.is_dir() and (p/'SKILL.md').is_file() and (p/'.growth-pack.json').is_file()]
    return {'edition': 'community', 'starter_work_skills': ['S01'], 'active_pack': active,
            'work_skills': count('blueprint.json','skills') + count('channel-catalog.json','channel_skills'),
            'skill_folders': len(folders), 'goals': count('goal-catalog.json','goals'),
            'graph_templates': len(list((root/'graphs').glob('*.json'))),
            'channel_skills': count('channel-catalog.json','channel_skills'),
            'goal_library_installed': (root/'goal-catalog.json').is_file(),
            'commercial_library_included_in_community_distribution': False}


def reference(name):
    return skill_root() / 'references' / name
