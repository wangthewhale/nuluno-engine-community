"""Data-only learned input programs. No commands, expressions or new authority."""
import copy
from urllib.parse import urlparse
from .files import digest
from .errors import WorkflowError

OPERATIONS = ('strip', 'collapse_whitespace', 'encode_url_path_spaces', 'strip_url_fragment')
FORBIDDEN = {'account', 'account_ref', 'account_id', 'authorization', 'token', 'api_key', 'password', 'operation_key', 'budget', 'cost_minor', 'recipient', 'email', 'phone'}


def _parts(path):
    if not isinstance(path,str) or not path.startswith('/'):
        raise ValueError('學習欄位需JSON Pointer')
    return [p.replace('~1','/').replace('~0','~') for p in path.split('/')[1:]]


def _permitted(path,route):
    try:
        parts=_parts(path)
        if path not in route.get('learning_fields',[]) or any(p.lower() in FORBIDDEN for p in parts):
            return False
        if 'account_pointer' in route:
            protected=_parts(route['account_pointer']) if route['account_pointer'] else []
            common=min(len(parts),len(protected))
            if parts[:common]==protected[:common]:
                return False
        return True
    except (ValueError,TypeError):
        return False


def validate_program(program, route):
    if not isinstance(program, list) or len(program) > 8:
        raise ValueError('程序需0至8個資料轉換，不接受程式或命令')
    if program and route.get('mode') != 'read':
        raise WorkflowError('學習程序不能改寫外部寫入；原核准與對帳保持不變。', 'scope_mismatch')
    for step in program:
        if not isinstance(step, dict) or set(step) != {'op', 'pointer'} or step['op'] not in OPERATIONS:
            raise ValueError('未知的程序操作')
        path = step['pointer']
        if not _permitted(path,route):
            raise WorkflowError('程序欄位不在操作者明確開放的唯讀範圍。', 'scope_mismatch')
    return program


def apply_program(arguments, program, route):
    validate_program(program, route)
    output = copy.deepcopy(arguments)
    for step in program:
        parts = _parts(step['pointer'])
        cursor = output
        for part in parts[:-1]:
            cursor = cursor[int(part)] if isinstance(cursor, list) else cursor[part]
        key = int(parts[-1]) if isinstance(cursor, list) else parts[-1]
        value = cursor[key]
        if not isinstance(value, str):
            raise ValueError('學習欄位需字串；不轉換身分或數值')
        parsed=urlparse(value.strip())
        actual_url=parsed.scheme in ('http','https') and bool(parsed.netloc)
        is_url=actual_url or 'url' in parts[-1].lower()
        if step['op'] in ('encode_url_path_spaces','strip_url_fragment') and not actual_url:
            raise WorkflowError('URL程序只能處理原HTTP網址。','scope_mismatch')
        if step['op']=='collapse_whitespace' and is_url:
            raise WorkflowError('不能合併網址內空白並改變來源資源。','scope_mismatch')
        if step['op'] == 'strip':
            updated = value.strip()
        elif step['op'] == 'collapse_whitespace':
            updated = ' '.join(value.split())
        elif step['op'] == 'encode_url_path_spaces':
            updated = parsed._replace(path=parsed.path.replace(' ', '%20')).geturl()
        else:
            # Fragments identify an in-document location and are never sent in
            # an HTTP request. This preserves the exact server resource.
            updated = parsed._replace(fragment='').geturl()
        if is_url:
            before, after = urlparse(value.strip()), urlparse(updated)
            if (before.scheme, before.netloc, before.query) != (after.scheme, after.netloc, after.query) or step['op'] != 'strip_url_fragment' and before.fragment != after.fragment or step['op'] == 'strip_url_fragment' and before.path != after.path:
                raise WorkflowError('學習程序不能更換來源主機、權限、查詢或目標。', 'scope_mismatch')
        cursor[key] = updated
    return output


def selected_input_examples(arguments, route):
    """Only explicitly enabled public learning fields enter a proposal prompt."""
    from .tooling import pointer
    from .files import redact
    result = {}
    for path in route.get('learning_fields', []):
        if not _permitted(path,route):
            continue
        try:
            value = pointer(arguments, path)
            if isinstance(value, str) and len(value) <= 1000:
                result[path] = redact(value)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return result
