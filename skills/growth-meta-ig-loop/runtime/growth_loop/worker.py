"""A persistent worker that delegates model/tool work to an operator-owned command."""
import json
import os
import subprocess
import time
import urllib.request


def rpc(url, worker, secret, method, params):
    body = json.dumps({'method': method, 'params': params}).encode()
    req = urllib.request.Request(url.rstrip('/') + '/rpc', data=body,
        headers={'Content-Type': 'application/json', 'X-Growth-Worker': worker, 'Authorization': 'Bearer ' + secret})
    with urllib.request.urlopen(req, timeout=60) as response:
        value = json.load(response)
    if 'error' in value: raise RuntimeError(value['error'])
    return value['result']


def work(url, worker, token_env, run_id, command, interval=15, max_polls=0):
    """Host command handles one leased request. It may call action APIs using
    the lease. Output is a workflow receipt, never an unvalidated done flag.
    A child that exits or loses a lease cannot report completion afterward.
    """
    secret = os.environ[token_env]
    polls = 0
    while max_polls == 0 or polls < max_polls:
        polls += 1
        job = rpc(url, worker, secret, 'acquire', {'run_id': run_id})
        if not job.get('request'):
            time.sleep(max(1, interval)); continue
        request = job['request']
        ownership = {'run_id': run_id, 'task_key': request['task_key'], 'lease': job['lease'], 'request_sha256': request['request_sha256']}
        # Temporary files avoid a pipe buffer deadlock for large contexts/results.
        import tempfile
        with tempfile.TemporaryFile() as inp, tempfile.TemporaryFile() as out:
            inp.write(json.dumps(job).encode()); inp.seek(0)
            child = subprocess.Popen(command, stdin=inp, stdout=out)
            try:
                deadline = time.monotonic() + 3600
                while child.poll() is None:
                    if time.monotonic() > deadline:
                        raise TimeoutError('單筆宿主工作超過一小時')
                    try: child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        rpc(url, worker, secret, 'heartbeat', ownership)
                if child.returncode != 0:
                    raise RuntimeError('宿主失敗，保留租約到期續接與不明操作')
                out.seek(0); receipt = json.load(out)
                rpc(url, worker, secret, 'complete', {**ownership, 'receipt': receipt})
            finally:
                if child.poll() is None:
                    child.terminate()
                    try: child.wait(timeout=5)
                    except subprocess.TimeoutExpired: child.kill(); child.wait()
