"""Private SDK gateway bridge; no Nuluno application, cookies or Mission.

The Node gateway authenticates the worker; this bridge reloads the operator's
policy for every call. Tool input never selects config files or a worker.
"""
import argparse
import json
import sys
from . import __version__
from .files import canonical, read_json, redact
from .service import METHODS, Service, input_schema, load_worker
from .store import Store
from .errors import WorkflowError


def metadata():
    return {'version': __version__, 'methods': {name: input_schema(name) for name in METHODS}}


def invoke(workspace, config_path, worker, method, arguments):
    settings = load_worker(read_json(config_path), worker)
    if settings.get('revoked') is True:
        raise WorkflowError('This worker is revoked.', 'scope_mismatch')
    store = Store(workspace)
    try:
        return Service(store, worker, settings).run(method, arguments)
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--metadata', action='store_true')
    parser.add_argument('--workspace')
    parser.add_argument('--config')
    parser.add_argument('--worker')
    parser.add_argument('--method')
    args = parser.parse_args()
    if args.metadata:
        print(canonical(metadata())); return 0
    try:
        if not all((args.workspace, args.config, args.worker, args.method)):
            raise ValueError('Missing private operator binding')
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('Request too large')
        value = json.loads(raw)
        result = invoke(args.workspace, args.config, args.worker, args.method, value)
        print(canonical({'result': result})); return 0
    except (ValueError, KeyError, TypeError, WorkflowError) as exc:
        print(canonical({'error': {'kind': getattr(exc, 'kind', 'invalid_request'),
            'message': str(exc), 'details': redact(getattr(exc, 'details', {})),
            'retry_after': getattr(exc, 'retry_after', None)}})); return 0
    except Exception:
        print(canonical({'error': {'kind': 'internal_error',
            'message': 'Execution failed; inspect the saved original task before retrying.'}})); return 1


if __name__ == '__main__':
    raise SystemExit(main())
