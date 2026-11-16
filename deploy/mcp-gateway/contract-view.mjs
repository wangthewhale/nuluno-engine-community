/** Scoped, byte-bounded discovery over the original native host contract.
 * No state, token, key, quota or execution permission is created or changed.
 * Full schemas remain available through standard authenticated MCP tools/list.
 */
const FIELDS=['schema','version','engine_version','product','model_neutral','autonomous_evolution','interfaces','capabilities','methods','work_cycle','executable_tool_dispatch','source_ingress','adaptive_execution','inventory_fields','trust','readiness_states','continuous_execution','universal_agent_certification','library_inventory'];
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
export function scopeMethodNames(methods,settings){
 if(!object(methods)||!object(settings)||settings.methods!==undefined&&(!Array.isArray(settings.methods)||settings.methods.some(name=>typeof name!=='string')))throw Error('invalid_method_scope_policy');
 const allowed=settings.methods===undefined?null:new Set(settings.methods);
 return Object.keys(methods).filter(name=>!allowed||allowed.has(name));
}
function envelope(value,settings){
 const result={content:[{type:'text',text:JSON.stringify(value)}],isError:false};
 if(settings.response_policy?.content_only!==true)result.structuredContent=value;
 return result;
}
export function scopedHostContract(value,settings,runId){
 if(!object(value)||value.schema!=='growth-host-contract-v1'||!object(value.methods)||!Array.isArray(value.capabilities))throw Error('native_contract_shape_not_supported');
 const configured=settings.response_policy?.max_response_bytes;
 if(configured!==undefined&&(!Number.isInteger(configured)||configured<2048||configured>65536))throw Error('invalid_response_byte_policy');
 const budget=(configured??65536)-256, fits=result=>Buffer.byteLength(JSON.stringify(envelope(result,settings)))<=budget;
 const names=scopeMethodNames(value.methods,settings), capSet=new Set(Array.isArray(settings.capabilities)?settings.capabilities:[]);
 const capabilities=value.capabilities.filter(cap=>typeof cap==='string'&&capSet.has(cap));
 const scope={authority:'authenticated_worker_settings',documented_features_are_not_grants:true,methods_limited:settings.methods!==undefined,capabilities_declared:Array.isArray(settings.capabilities),allow_evolution:settings.allow_evolution===true,allow_goal_continuity:settings.allow_goal_continuity===true};
 const discovery={protocol_method:'tools/list',same_worker_authentication:true,only_authorized_tools:true,provides_full_input_schemas:true};
 const next=typeof runId==='string'?['cost_summary','learning_status','status'].filter(name=>names.includes(name)).map(tool=>({tool,arguments:{run_id:runId},read_only:true,requires_new_authority:false})):[];
 const projection={schema:'nuluno-scoped-host-contract-view-v1',scope_filtered:true,method_count:names.length,capability_count:capabilities.length,method_schemas_included:true,static_documentation_complete:true};
 let result=Object.fromEntries(FIELDS.filter(key=>Object.hasOwn(value,key)).map(key=>[key,value[key]]));
 result={...result,methods:Object.fromEntries(names.map(name=>[name,value.methods[name]])),capabilities,worker_scope:scope,contract_view:projection,schema_discovery:discovery,next_actions:next};
 if(fits(result))return result;
 // The original API accepted only run_id. Do not ask callers to supply an
 // unavailable selector: MCP already has a real, scoped schema discovery call.
 result={...result,methods:{},method_names:names,contract_view:{...projection,method_schemas_included:false}};
 if(fits(result))return result;
 // Very small configured budgets retain explicit identity, authority and a
 // usable standard next operation; omitted documentation is never a claim of
 // complete contract delivery. This does not raise the operator's byte cap.
 result={schema:value.schema,version:value.version,engine_version:value.engine_version,product:value.product,model_neutral:value.model_neutral,methods:{},method_names:[...names],capabilities:[...capabilities],worker_scope:scope,contract_view:{...projection,method_schemas_included:false,static_documentation_complete:false,method_names_omitted:0,capability_names_omitted:0},schema_discovery:discovery,next_actions:next};
 while(!fits(result)&&result.method_names.length){result.method_names.pop();result.contract_view.method_names_omitted++;}
 while(!fits(result)&&result.capabilities.length){result.capabilities.pop();result.contract_view.capability_names_omitted++;}
 while(!fits(result)&&result.next_actions.length)result.next_actions.pop();
 if(!fits(result))throw Error('contract_identity_exceeds_configured_budget_use_mcp_tools_list');
 return result;
}
