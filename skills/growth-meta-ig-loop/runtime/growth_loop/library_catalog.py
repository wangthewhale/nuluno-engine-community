"""Bounded resource discovery; never inject the whole paid library into MCP."""
import json
from .resources import skill_root, library_status
from .contracts import blueprint


def page(params):
    kind, after, limit = params.get('kind','work'), params.get('after',0), params.get('limit',10)
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError('catalog_page_bounds_required')
    ref = skill_root()/'references'
    common = blueprint()['skills']
    channel_path = ref/'channel-catalog.json'
    channels = json.loads(channel_path.read_text())['channel_skills'] if channel_path.is_file() else []
    work = common + channels
    if kind == 'work':
        rows = [{'id':s['id'],'name':s.get('name',s.get('slug')),'parent_skill_id':s.get('parent_skill_id'),
                 'step_count':len(s.get('steps',[]))} for s in work]
    elif kind == 'goals':
        path=ref/'goal-catalog.json'
        if not path.is_file(): return {'library':library_status(),'kind':kind,'installed':False,'items':None,'next_after':None}
        rows=[{'id':g['id'],'title':g['title'],'channel':g['channel'],'metric':g['metric']['key']} for g in json.loads(path.read_text())['goals']]
    elif kind == 'graphs':
        rows=[{'id':p.stem} for p in sorted((ref/'graphs').glob('*.json'))]
    elif kind == 'skill':
        selected=next((s for s in work if s['id']==params.get('skill_id')),None)
        if not selected: raise ValueError('skill_not_installed')
        steps=selected['steps']; chunk=steps[after:after+min(limit,2)]
        return {'library':library_status(),'kind':kind,'skill_id':selected['id'],
                'purpose':selected.get('responsibility',selected.get('purpose')),
                'inputs':selected.get('inputs'), 'outputs':selected.get('outputs'),
                'steps':chunk,'total_steps':len(steps),'next_after':after+len(chunk) if after+len(chunk)<len(steps) else None}
    else: raise ValueError('unknown_catalog_kind')
    chunk=rows[after:after+limit]
    return {'library':library_status(),'kind':kind,'installed':True,'items':chunk,'total':len(rows),
            'next_after':after+len(chunk) if after+len(chunk)<len(rows) else None}
