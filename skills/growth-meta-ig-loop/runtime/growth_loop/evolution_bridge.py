"""Stateless single-JSON bridge for existing durable hosts (stdin -> stdout).

Never uses ephemeral SQLite to claim persistence. Host owns stored artifacts,
leases, source authenticity, prospective assignment, permissions and adoption.
"""
import json
import os
import sys
from .artifact_kernel import SCHEMA, validate_artifact, run_program, graph_next, evaluate_transfer_trial, make_artifact, validate_case


def handle(request):
    op = request.get('operation')
    if op == 'contract':
        from .artifact_kernel import OPS
        return {'schema':SCHEMA,'version':'0.10.0','stateless':True,'operations':['make','validate','execute','graph_next','transfer_evaluate','proposal_contract','validate_case'],
                'program_operations':OPS,'arbitrary_code_supported':False,'external_executor':'host_original_lease_and_capability_path',
                'persistence':'host_owned','model_weights_updated':False}
    if op == 'proposal_contract':
        from .artifact_policy import proposal_contract
        return proposal_contract()
    if op == 'make': return make_artifact(request['kind'],request['body'],request['contract'],request.get('parent_id'),request['hypothesis'],request['source_refs'])
    if op == 'transfer_evaluate': return evaluate_transfer_trial(request['plan'],request['rows'],request['now'])
    artifact, contract = request['artifact'], request['contract']
    checked = validate_artifact(artifact,contract)
    if op == 'validate': return checked
    if op == 'validate_case': return validate_case(artifact,contract,request['case'])
    if op == 'execute':
        if artifact['kind'] != 'program': raise ValueError('use_graph_next_for_graphs')
        return {'schema':SCHEMA,**run_program(artifact['body'],request['arguments'],contract),'id':artifact['id'],
                'state':'executed_pure_program','external_execution_authorized':False,'economic_adoption_proven':False}
    if op == 'graph_next':
        if artifact['kind'] != 'graph': raise ValueError('graph_artifact_required')
        return {'schema':SCHEMA,**graph_next(artifact['body'],request['arguments'],contract,request.get('host_receipts')),'id':artifact['id']}
    raise ValueError('unsupported_evolution_operation')


def main():
    # Resource limits contain malformed/high-cost data. They are defense in
    # depth; generated values remain interpreted JSON, never native code.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU,(2,2))
        resource.setrlimit(resource.RLIMIT_FSIZE,(2*1024*1024,2*1024*1024))
        resource.setrlimit(resource.RLIMIT_NOFILE,(32,32))
    except (ImportError,ValueError,OSError):
        pass
    try:
        raw = sys.stdin.buffer.read(1024*1024+1)
        if len(raw)>1024*1024: raise ValueError('bridge_request_too_large')
        request=json.loads(raw,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('finite_json_required')))
        result={'result':handle(request)}
    except Exception as exc:
        result={'error':{'kind':'evolution_validation_rejected','message':str(exc)[:600]}}
    sys.stdout.write(json.dumps(result,ensure_ascii=False,allow_nan=False)+'\n')


if __name__=='__main__':main()
