"""Independent controlled execution tests, never sales evidence."""
import copy,json,os,sys,tempfile,threading,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/growth-meta-ig-loop/runtime'))
from growth_loop.artifact_kernel import make_artifact,validate_artifact,graph_next,digest,evaluate_transfer_trial
from growth_loop.artifact_evolution import ArtifactEvolution,isolated
from growth_loop.service import Service,http_server,input_schema,METHODS
from growth_loop.mcp_protocol import MCPServer
from growth_loop.sdk import GrowthClient
from growth_loop.store import Store

def program(op='identity',extra=False):
    steps=[{'id':'one','op':op,'args':{'value':{'ref':'input','path':'/text'}}}]
    if extra:steps.append({'id':'two','op':'identity','args':{'value':{'ref':'one'}}})
    return {'steps':steps,'output':{'text':{'ref':'two' if extra else 'one'}}}

def contract():
    return {'scope':{'tenant_id':'tenant','brand_id':'https://product.example','product_id':'product'},'context':{'model':'same-model','initial_tools_digest':'same-tools','permission_digest':'same-permission','budget_digest':'same-budget','outcome_definition':'correct_parse'},'source_refs':['original:failure:1']}

class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name,lambda:1000)
        self.store.create_run({'brand':{'url':'https://product.example'},'product_id':'product','mode':'prepare'},'run')
        self.c=contract();self.base=make_artifact('program',program(),self.c,None,'Original',['original:failure:1'])
        cases=[{'id':str(i),'input':{'text':t},'expected':{'text':t.strip()}} for i,t in enumerate([' first ',' second ',' third '])]
        self.settings={'enabled':True,'contracts':{'run':self.c},'baselines':{'run':{self.base['id']:self.base}},'original_heads':{'run':{'program':self.base['id']}},'validation_suites':{'hidden':{'scope':self.c['scope'],'source_ref':'host:heldout','independent':True,'hidden_from_model':True,'fixture':True,'minimum_cases':3,'cases':cases,'case_ids':[c['id'] for c in cases],'training_case_ids':['train']}}}
        self.cfg={'runs':['run'],'allow_evolution':True,'artifact_evolution':self.settings};self.engine=ArtifactEvolution(self.store,self.cfg);self.api=Service(self.store,'host',self.cfg)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def candidate(self,body=None):return self.engine.propose('run',{'kind':'program','body':body or program('strip'),'hypothesis':'Trim public whitespace','source_refs':['original:failure:1']})['artifact']
    def test_propose_validate_adopt_execute_restart_rollback(self):
        a=self.candidate()
        with self.assertRaises(ValueError):self.engine.execute('run',a['id'],{'text':' untouched '})
        r=self.engine.validate('run',a['id'],'hidden');self.assertTrue(r['all_cases_pass']);self.assertTrue(r['execution_improvement_verified']);self.assertFalse(r['economic_adoption_proven'])
        self.engine.adopt('run',a['id'],r['id']);self.store.close();self.store=Store(self.tmp.name,lambda:1001);self.engine=ArtifactEvolution(self.store,self.cfg)
        out=self.engine.execute('run',a['id'],{'text':' entirely new '});self.assertEqual(out['output'],{'text':'entirely new'});self.assertEqual(out['external_effects'],0)
        self.engine.rollback('run',a['id'],'independent regression');self.assertEqual(self.engine.head('run','program')['id'],self.base['id'])
        with self.assertRaises(ValueError):self.engine.execute('run',a['id'],{'text':' again '})
    def test_original_tick_adopts_and_future_failure_rolls_back(self):
        a=self.candidate();self.settings.update(autonomous=True,autonomous_suites={'program':'hidden'},rollback_on_failure=True)
        r=self.api.run('artifact_tick',{'run_id':'run'});self.assertEqual(r['state'],'adopted_execution')
        with self.assertRaises(ValueError):self.engine.execute('run',a['id'],{'text':42})
        self.assertEqual(self.engine.head('run','program')['id'],self.base['id'])

    def test_first_new_pure_capability_is_not_baseline_superiority(self):
        self.settings['original_heads']={'run':{}}
        a=self.candidate();r=self.engine.validate('run',a['id'],'hidden')
        self.assertTrue(r['new_pure_capability_verified']);self.assertFalse(r['execution_improvement_verified'])
        self.engine.adopt('run',a['id'],r['id']);self.assertEqual(self.engine.execute('run',a['id'],{'text':' new '})['output'],{'text':'new'})

    def test_bridge_stdout_can_be_redirected_to_regular_file(self):
        import subprocess
        with tempfile.TemporaryFile() as output:
            result=subprocess.run([sys.executable,'-m','growth_loop.evolution_bridge'],input=b'{"operation":"contract"}',stdout=output,stderr=subprocess.PIPE,env={**os.environ,'PYTHONPATH':str(ROOT/'skills/growth-meta-ig-loop/runtime')})
            output.seek(0);value=json.load(output)
        self.assertEqual(result.returncode,0);self.assertEqual(value['result']['version'],'0.10.0')

    def test_model_cannot_submit_expected_or_adoption(self):
        with self.assertRaises(ValueError):self.engine.propose('run',{'kind':'program','body':program('strip'),'hypothesis':'x','source_refs':['original:failure:1'],'expected':{},'state':'adopted'})
        a=self.candidate()
        with self.assertRaises(ValueError):self.engine.adopt('run',a['id'],'model-says-pass')
    def test_context_change_invalidates_validation(self):
        a=self.candidate();r=self.engine.validate('run',a['id'],'hidden');self.c['context']['permission_digest']='changed'
        with self.assertRaises(ValueError):self.engine.adopt('run',a['id'],r['id'])
    def test_hidden_cases_cannot_overlap_or_be_replaced(self):
        a=self.candidate();suite=self.settings['validation_suites']['hidden'];suite['training_case_ids']=['0']
        with self.assertRaises(ValueError):self.engine.validate('run',a['id'],'hidden')
        suite['training_case_ids']=[];r=self.engine.validate('run',a['id'],'hidden');suite['cases'][0]['expected']={'text':'replacement'}
        with self.assertRaises(ValueError):self.engine.adopt('run',a['id'],r['id'])
    def test_no_improvement_does_not_adopt(self):
        a=self.candidate(program('identity',True));r=self.engine.validate('run',a['id'],'hidden');self.assertFalse(r['execution_improvement_verified'])
        with self.assertRaises(ValueError):self.engine.adopt('run',a['id'],r['id'])
    def test_native_code_tamper_and_identity_changes_rejected(self):
        for op in ('exec','eval','shell','http','import','open'):
            with self.assertRaises(ValueError):self.candidate(program(op))
        c={**self.c,'argument_transform':True,'mutable_pointers':['/text']};a=make_artifact('program',program('strip'),c,None,'x',['original:failure:1'])
        with self.assertRaises(ValueError):isolated({'operation':'execute','artifact':a,'contract':c,'arguments':{'text':' x ','account_ref':'owner'}})
        with self.assertRaises(ValueError):validate_artifact({**a,'id':'fake'},c)
    def test_graph_node_removal_is_real_and_equivalent(self):
        first={'id':'parse','depends_on':[],'program':{'steps':[{'id':'v','op':'strip','args':{'value':{'ref':'input','path':'/input/text'}}}],'output':{'text':{'ref':'v'}}}}
        second={'id':'finish','depends_on':['parse'],'program':{'steps':[{'id':'v','op':'identity','args':{'value':{'ref':'input','path':'/dependencies/parse'}}}],'output':{'ref':'v'}}}
        a=make_artifact('graph',{'nodes':[first,second],'output':{'ref':'finish'}},self.c,None,'original',['original:failure:1']);b=make_artifact('graph',{'nodes':[{**first,'id':'finish'}],'output':{'ref':'finish'}},self.c,a['id'],'remove identity',['original:failure:1'])
        r1=isolated({'operation':'graph_next','artifact':a,'contract':self.c,'arguments':{'text':' new '}});r2=isolated({'operation':'graph_next','artifact':b,'contract':self.c,'arguments':{'text':' new '}})
        self.assertEqual(r1['output'],r2['output']);self.assertLess(r2['steps'],r1['steps']);self.assertEqual(len(b['body']['nodes']),1)
    def test_original_graph_controls_write_limit_and_receipts_preserved(self):
        nodes=[{'id':'authorize','executor_id':'auth','arguments':{},'depends_on':[]},{'id':'write','executor_id':'publish','arguments':{'text':{'ref':'input','path':'/text'}},'depends_on':['authorize']},{'id':'reconcile','executor_id':'readback','arguments':{},'depends_on':['write']},{'id':'measure','executor_id':'metrics','arguments':{},'depends_on':['reconcile']}]
        c={**self.c,'executors':{'auth':{'effect':'control'},'publish':{'effect':'write'},'readback':{'effect':'reconcile'},'metrics':{'effect':'measure'}},'protected_nodes':{n['id']:n for n in nodes},'protected_precedence':[['authorize','write'],['write','reconcile'],['reconcile','measure']],'maximum_writes':1,'required_before_write':['authorize'],'required_after_write':{'write':['reconcile','measure']}}
        body={'nodes':nodes,'output':{'ref':'measure'}};a=make_artifact('graph',body,c,None,'x',['original:failure:1']);first=isolated({'operation':'graph_next','artifact':a,'contract':c,'arguments':{'text':'same'}})
        self.assertEqual(first['state'],'requires_host_executor');self.assertFalse(first['external_execution_authorized'])
        receipts={'authorize':{'state':'succeeded','source_ref':'original:receipt','binding_digest':first['binding_digest'],'output':{}}};second=graph_next(body,{'text':'same'},c,receipts);self.assertEqual(second['node_id'],'write')
        for change in ('control','measurement','extra'):
            bad=copy.deepcopy(body)
            if change=='control':bad['nodes'][1]['depends_on']=[]
            elif change=='measurement':bad['nodes']=bad['nodes'][:-1]
            else:bad['nodes'].append({'id':'extra','executor_id':'publish','arguments':{},'depends_on':['authorize']})
            with self.assertRaises(ValueError):make_artifact('graph',bad,c,None,'x',['original:failure:1'])
        with self.assertRaises(ValueError):graph_next(body,{'text':'changed'},c,{'authorize':{**receipts['authorize'],'binding_digest':'forged'}})
    def test_transfer_is_complete_denominator_and_fixture_never_real_lift(self):
        plan={'schema':'nuluno-evolution-v1','source_product_id':'old','target_product_id':'new','training_product_ids':['old'],'assignment_source_ref':'backend:assignment','comparison_context':self.c['context'],'registered_at':1,'start_at':2,'end_at':3,'complete_denominator':True,'minimum_per_arm':30,'fixture':True,'currency':'USD'}
        plan['assigned_unit_ids']=[f'{arm}-{i}' for arm in ('baseline','candidate') for i in range(1000)];rows=[]
        for arm in ('baseline','candidate'):
            for i in range(1000):rows.append({'arm':arm,'unit_id':f'{arm}-{i}','owner_test':False,'external_unit':True,'fixture':True,'target_product_id':'new','context_digest':digest(plan['comparison_context']),'assignment_digest':digest(plan),'source_ref':'original:'+f'{arm}-{i}','source_class':'product_backend','raw_sha256':'a'*64,'currency':'USD','occurred_at':2.5,'settled_at':4,'qualified':i<(200 if arm=='baseline' else 900),'costs':{k:1 for k in ('ads','platform_payment','commission','partner_share','delivery','people','software','other')}})
        r=evaluate_transfer_trial(plan,rows,5);self.assertEqual(r['decision'],'eligible');self.assertTrue(r['controlled_result']);self.assertFalse(r['cross_product_lift_proven'])
        with self.assertRaises(ValueError):evaluate_transfer_trial(plan,rows[:-1],5)
        plan['target_product_id']='old'
        with self.assertRaises(ValueError):evaluate_transfer_trial(plan,[],5)
    def test_http_v1_and_mcp_share_scope(self):
        os.environ['CONTROLLED_ENGINE_TOKEN']='isolated-token';server=http_server(self.tmp.name,{'workers':{'host':{**self.cfg,'token_env':'CONTROLLED_ENGINE_TOKEN'}}},port=0);t=threading.Thread(target=server.serve_forever,daemon=True);t.start();url='http://127.0.0.1:'+str(server.server_port)
        try:
            self.assertEqual(json.load(urlopen(url+'/v1/manifest'))['api_version'],'v1');client=GrowthClient(url,'host','CONTROLLED_ENGINE_TOKEN');r=client.artifact_contract('run');self.assertEqual(r['contract']['scope'],self.c['scope'])
            with self.assertRaises(Exception):client.artifact_contract('other')
            mcp=MCPServer(lambda n,p:self.api.run(n,p),METHODS,input_schema);meta={'io.modelcontextprotocol/protocolVersion':'2026-07-28','io.modelcontextprotocol/clientCapabilities':{}}
            reply=mcp.process({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'_meta':meta,'name':'artifact_contract','arguments':{'run_id':'run'}}});self.assertEqual(reply['result']['structuredContent']['contract']['scope'],r['contract']['scope'])
        finally:server.shutdown();server.server_close();t.join(3);os.environ.pop('CONTROLLED_ENGINE_TOKEN',None)
    def test_configured_model_adapter_generates_candidate_not_own_tests(self):
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));seen.append(json.loads(request['input']));proposal={'kind':'program','body':program('strip'),'hypothesis':'Original failure showed whitespace','source_refs':['original:failure:1']};data=json.dumps({'id':'controlled-model','output':[{'content':[{'type':'output_text','text':json.dumps(proposal)}]}],'usage':{'input_tokens':20,'output_tokens':20}}).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        self.settings['model']={'endpoint':'http://127.0.0.1:'+str(server.server_port)+'/model','fixture_only':True,'model':'controlled','input_usd_per_million':0,'output_usd_per_million':0,'budget_usd':0};self.settings['sources']={'original:failure:1':{'public':True,'input':{'text':' before '},'observed':'wrong public whitespace'}}
        try:
            r=self.api.run('artifact_generate',{'run_id':'run','body':{'kind':'program','source_refs':['original:failure:1']}});self.assertEqual(r['state'],'candidate');self.assertFalse(r['economic_adoption_proven']);self.assertEqual(self.engine.head('run','program')['id'],self.base['id']);self.assertNotIn('cases',seen[0]);self.assertEqual(seen[0]['independent_cases'],'withheld_by_host')
        finally:server.shutdown();server.server_close();t.join(3)

if __name__=='__main__':unittest.main()
