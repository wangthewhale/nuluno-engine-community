"""Executable capability/measurement contracts shared by all goal definitions."""
import json
from pathlib import Path

from .resources import skill_root
REF = skill_root() / "references"

# These are requirements, not claims that a provider exposes these metrics.
OPERATIONS = {
    "S01": ("offer.read", ["brand_url", "existing_product_pages"], ["actual_source_text", "fact_ids", "offer_limits", "unknown_checkout_state"]),
    "S02": ("audience.reason_from_sources", ["product_facts", "actual_buyer_voice"], ["sourced_segments", "hypotheses", "unknowns"]),
    "S04": ("workflow.plan", ["current_product", "buyer_goal", "existing_tools", "available_budget_and_time"], ["selected_goal", "fixed_target_and_window", "dependency_graph", "missing_prerequisites"]),
    "S05": ("identity.read", ["brand_url", "selected_account_or_official_access_entry"], ["subject_id", "brand_match", "actual_scopes", "observed_at"]),
    "S03": ("source.research", ["query", "source_window", "sample_selection", "readable_original_urls"], ["original_url", "author", "published_at_or_unknown", "actual_quote_or_empty", "visible_metrics_only"]),
    "S06": ("search.research", ["product_facts", "market", "query_window"], ["actual_queries", "source_urls", "selection_rule", "limitations"]),
    "S07": ("source.research", ["research_question", "original_source_urls"], ["source_url", "observed_fact", "checked_at", "unknowns"]),
    "S08": ("prospect.research", ["buyer_definition", "source_query", "recency", "exclusion_rules"], ["original_url", "identity", "observed_need", "inferred_fit_separately", "exclusions"]),
    "S09": ("buying_team.research", ["actual_company_domain", "current_roles", "original_conversation"], ["verified_people_or_unknown", "role_evidence", "decision_authority_or_hypothesis", "open_questions"]),
    "S10": ("content.plan", ["verified_product_facts", "audience_evidence", "single_test_variable"], ["hypothesis", "variants", "fact_ids", "acceptance_rule"]),
    "S11": ("content.write", ["verified_product_facts", "audience_evidence", "native_format", "exact_cta"], ["exact_copy", "fact_ids", "claim_limits"]),
    "S12": ("media.image", ["approved_copy", "brand_assets", "native_dimensions"], ["actual_file", "mime_type", "dimensions", "sha256", "visual_readback"]),
    "S13": ("media.video", ["script", "owned_media", "audio_rights", "native_video_requirements"], ["actual_file", "decoded_duration", "dimensions", "sha256", "playback_readback"]),
    "S14": ("content.publish", ["account_ref", "exact_copy", "ordered_media", "destination", "scoped_approval"], ["original_operation_id", "platform_object_id", "account_ref", "published_state", "exact_content_readback", "permalink"]),
    "S15": ("listing.submit", ["actual_platform", "owned_listing", "exact_fields", "scoped_approval"], ["submission_id", "review_state", "public_url_if_public", "readback"]),
    "S16": ("resource.capture_and_deliver", ["existing_resource", "existing_external_form", "source_and_consent_scope"], ["real_submission", "stored_contact", "delivered_resource", "separate_marketing_consent"]),
    "S17": ("followup.schedule", ["original_contact", "buyer_event", "due_time", "purpose", "suppression_state"], ["durable_followup_id", "original_trigger", "cancel_on_reply_purchase_or_optout", "no_send_claim"]),
    "S18": ("message.send_or_reply", ["account_ref", "actual_transport", "original_recipient_or_thread", "contact_eligibility", "exact_text", "scoped_approval"], ["message_id", "actual_recipient", "reply_parent_if_reply", "delivery_or_pending_state", "readback"]),
    "S19": ("buyer_question.answer", ["actual_inbound_question", "verified_product_facts", "existing_terms"], ["answer_with_fact_ids", "unsupported_questions", "buyer_stage", "draft_or_actual_sent_receipt"]),
    "S20": ("community.run", ["authorized_community", "member_eligibility", "actual_program_task"], ["actual_task_id", "participation_records", "uncompleted_external_events"]),
    "S21": ("event.create", ["actual_event_provider", "host", "time_zone", "capacity", "existing_offer"], ["event_id", "event_url", "host_confirmed_or_pending", "attendance_source"]),
    "S22": ("proof.request", ["actual_customer", "experience_source", "permitted_use", "actual_transport"], ["original_customer_response", "permission_scope", "unmodified_claim", "source"]),
    "S23": ("partner.qualify", ["existing_offer", "partner_audience", "original_sources"], ["fit_evidence", "contact_route", "actual_terms_or_unknown"]),
    "S24": ("partner.deliver", ["existing_selling_kit", "recipient", "actual_transport", "scope"], ["actual_delivery_id", "recipient", "files_or_links", "readback"]),
    "S25": ("sales.advance", ["actual_buyer_question", "existing_product", "existing_price_and_terms"], ["buyer_stage", "usable_demo_or_reply", "buyer_decision_or_pending"]),
    "S26": ("product.demonstrate_existing", ["actual_buyer_need", "working_existing_feature", "permitted_product_media"], ["usable_demo", "demonstrated_or_prepared_state", "original_feedback_or_pending", "limitations"]),
    "S27": ("business_case.calculate", ["buyer_baseline", "same_currency_and_period", "existing_price", "assumption_sources"], ["reproducible_calculation", "unknown_inputs", "scenario_not_received_income", "proposal_version"]),
    "S28": ("pilot.monitor_existing", ["agreed_existing_offer", "buyer_confirmed_success_criteria", "real_usage_source"], ["actual_milestone_result", "met_not_met_or_unknown", "buyer_evidence", "pending_external_event"]),
    "S29": ("procurement.coordinate", ["actual_buyer_process", "existing_terms", "current_document_versions"], ["document_delivery", "actual_approval_state", "owner_decisions", "received_vs_unpaid_amounts"]),
    "S30": ("ads.plan", ["actual_account", "currency", "existing_conversion_source", "exact_caps", "one_test_variable"], ["paused_test_manifest", "daily_and_total_limits", "observation_window", "stop_conditions"]),
    "S31": ("ads.change", ["selected_ad_account", "brand_identity", "exact_object_ids", "exact_payload", "currency", "daily_and_total_caps", "scoped_approval"], ["original_object_ids", "effective_status", "native_budget_readback", "review_or_delivery_state"]),
    "S32": ("ads.review", ["actual_delivery_state", "whole_campaign_spend", "comparable_window", "conversion_maturity"], ["hold_adjust_pause_or_wait", "source_based_reason", "scoped_change_order", "no_automatic_activation"]),
    "S33": ("outcome.read", ["goal_id", "account_ref", "baseline_or_current", "fixed_window", "component_sources"], ["goal_observation", "original_metric_fields", "source_read_time", "missing_components", "economic_ids_if_required"]),
    "S34": ("strategy.review", ["goal_evaluation", "buyer_evidence", "prior_variants", "remaining_scope"], ["iterate_or_wait_or_stop", "one_changed_variable", "specific_change", "hypothesis", "restart_node"]),
}
ECONOMIC_COMPONENTS = {'external_purchases','net_attributed_receipts_minor','net_after_all_costs_minor','repeat_buyers','paid_contracts'}
CLOSED_QUERY_COMPONENTS={'eligible_requests','qualified_replies','qualified_conversations'}


def required_capabilities(parent,profile,empty_query=False):
    from .contracts import CAPABILITIES
    caps=list(CAPABILITIES[parent])
    if parent=='S33' and profile.get('workflow',{}).get('id')=='goal':
        from .goals import goal_definition
        components=set(goal_definition(profile['goal']['id'])['metric']['components'])
        if not components & ECONOMIC_COMPONENTS:caps=[c for c in caps if c!='payments.read']
        if empty_query and components.issubset(CLOSED_QUERY_COMPONENTS):
            caps=['local.calculate' if c=='analytics.read' else c for c in caps]
    return caps


def skill_execution(spec):
    from .contracts import ALLOWED_ACTIONS
    parent = spec.get("parent_skill_id", spec["id"])
    operation, inputs, outputs = OPERATIONS.get(parent, (
        "planning.prepare", ["current_brand_facts", "actual_prior_outputs"], ["usable_artifact", "sources", "unknowns"]))
    actions=spec.get('allowed_actions',sorted(ALLOWED_ACTIONS.get(parent,[])))
    return {"schema": "growth-execution-contract-v1", "operation": operation,
        "channel": spec.get("channel", "local"), "required_inputs": inputs,
        "native_output_proof": outputs, "tool_registration_is_not_execution": True,
        "write_actions": actions,
        "dispatch": "operator_bound_tool_route_or_authenticated_host_tool; never a tool inferred from prose",
        "identity_gate": parent == "S05", "needs_native_readback": bool(actions),
        "capability_scope":"S33 only requires payments.read for an economic goal; a verified closed empty query may use local.calculate, while exact goal source/metric checks remain mandatory" if parent=='S33' else "selected parent and channel capabilities",
        "uncertain_write": "preserve original operation; reconcile without resend",
        "human_only": ["identity_verification", "provider_terms", "new_account_or_permission_consent", "new_financial_commitment"],
        "completion": "check this operation's concrete fields and every applicable procedure step; do not blanket-attest a whole SOP"}


def measurement_contract(goal):
    key = goal["metric"]["key"]
    economic = bool(set(goal['metric']['components']) & ECONOMIC_COMPONENTS)
    sample = "account_snapshots" if key in ("net_followers", "net_subscribers") else (goal['metric']['components'][1] if len(goal['metric']['components'])>1 else 'verified_outcome_records')
    qualified=any(c.startswith('qualified_') for c in goal['metric']['components'])
    return {"schema": "growth-measurement-contract-v1", "goal_id": goal["id"],
        "metric": goal["metric"], "sample_unit": sample,
        "baseline": "captured before intervention; immutable even when its denominator is zero",
        "threshold": "evaluate the complete current window; a recorded zero-denominator baseline is not an invented zero rate",
        "change_target": "requires comparable, defined baseline and current values; relative change also needs a nonzero baseline",
        "component_sources": [{"component": c, "required": True,
            "source_kind": "verified_external_payment_ledger" if c in ECONOMIC_COMPONENTS else "native_report_or_explicitly_bound_verified_source",
            "proof_requirements": component_requirements(c,goal),
            "missing": "null/wait; never model-estimated or substituted with a different metric"} for c in goal["metric"]["components"]],
        "qualified_outcomes": "needs the brand's explicit qualification rule and original buyer evidence, not any comment",
        "source_read_time_required_live": True, "cross_goal_metric_substitution": False,
        "qualification_rule_required_live":qualified,
        "qualified_count_proof":"pinned qualification rule hash and deduplicated component_records with original IDs/account/source/event time" if qualified else None,
        "snapshot_target":"followers/subscribers are account totals; threshold compares net change against the pinned baseline; one snapshot per period" if sample=='account_snapshots' else None,
        "count_unit":"positive verified canonical payment IDs; not distinct people or all-time sales" if 'external_purchases' in goal['metric']['components'] else None,
        "external_customer_classification_required": economic,
        "refund_adjusted": "退款後" in goal["title"] or "refund" in key,
        "native_metric_availability": "verify per platform/account; e.g. average watch percentage does not prove completed-view counts",
        "transport_limit": "a source gateway does not itself create a provider integration"}


def component_requirements(component,goal):
    if component in ECONOMIC_COMPONENTS:
        result=['original settled payment IDs','positive received amount','external versus owner/test classification','exact run/channel/account attribution','period coverage']
        if component=='repeat_buyers':result+=['stable anonymous customer ID','verified prior payment and current payment; deduplicate people']
        if component=='paid_contracts':result+=['original contract/invoice reference','paid-in-full evidence; deduplicate contracts, not installments']
        if component=='net_after_all_costs_minor':result+=['all eight cost categories including verified zeros','future delivery reserve coverage']
        if '退款後' in goal['title'] or 'refund' in goal['metric']['key']:result+=['complete original refund feed and original parent payments']
        return result
    if component.startswith('qualified_'):return ['brand-specific preregistered qualification rule','original unit IDs and buyer/analytics evidence','same unit deduplication','no model-generated total without records']
    if component in ('completed_views','video_starts'):return ['actual native completed/start counts for the same videos and window','average watch percentage/time is not a completed-view count','if unavailable, this exact goal waits or user chooses a different goal']
    if component in ('followers','subscribers'):return ['account total at two fixed timestamps','same account and source','one snapshot sample; current minus pinned baseline']
    if component=='attributed_sessions':return ['existing web analytics or verified tracked-link source','exact campaign/channel attribution','session deduplication and conversion delay','no new product tracking is assumed']
    if component=='delivered':return ['provider message IDs and delivered events','exclude bounces, pending and rejected','native acceptance alone is not delivery']
    if component.endswith('_minor'):return ['original report or ledger','exact currency and integer minor units','same account/window; API and ad costs kept separate']
    if component in ('confirmed_meetings','attendees','registrations','completed_participants','reactivated_buyers'):return ['original calendar/event/conversation/member records','actual confirmation, attendance or completion rather than invitation/reaction','deduplicated eligible cohort']
    if component.startswith('eligible_') or component in ('reviewed_people','reviewed_roles','reviewed_makers','read_requests','reviewed_queries','referred_people'):
        return ['fixed cohort/selection rule','original reviewed unit IDs','same source window and deduplication unit as numerator']
    return ['exact native report field or explicitly verified source field','source read time and original account/object IDs','same window and denominator; unavailable fields stay null']


def execution_catalog():
    from .channels import channel_catalog
    from .goals import goal_catalog
    from .contracts import definition
    channels = channel_catalog()
    work = [{"id": "S%02d" % n, "parent_skill_id": "S%02d" % n, "channel": "local",
             "title": definition("S%02d" % n)["responsibility"]} for n in range(1, 35)] + channels["channel_skills"]
    return {"schema": "growth-execution-catalog-v1", "version": "0.6.0",
        "work": [{"id": s["id"], **skill_execution(s)} for s in work],
        "goals": [{"id": g["id"], "title": g["title"], "channel": g["channel"],
            "measurement": measurement_contract(g)} for g in goal_catalog()["goals"]],
        "counts_are_not_connected_tools": True}


def readiness(profile, config, traces=(), selected_goal=None, connected=(), authorization=None, now=None):
    """All-goal inventory, with code, tool, data and owner gates kept distinct."""
    from .channels import channel_skill
    from .goals import compile_goal, goal_catalog
    catalog = goal_catalog()["goals"]
    reports = []
    routes = config.get("tool_routes", {})
    for goal in catalog:
        if selected_goal and selected_goal != goal["id"]:
            continue
        setting = profile.get("goal", {}) if profile.get("goal", {}).get("id") == goal["id"] else {}
        graph = compile_goal(goal, setting)
        missing, configured, accounts,approvals = [], [], set(),[]
        for node in graph["nodes"]:
            ident = node.get("skill")
            if not ident:
                continue
            spec = channel_skill(ident) if not ident.startswith("S") else {"id": ident, "channel": "local", "parent_skill_id": ident}
            operation = skill_execution(spec)
            # Reasoning still needs the host model, actual files need media
            # tools, and research still needs original sources.
            applicable = [(key, route) for key, route in routes.items() if route.get("enabled") is True
                and ident in route.get("skill_ids", []) and profile.get("brand", {}).get("url") in route.get("brand_urls", [])]
            row = {"node": node["id"], "skill_id": ident, "operation": operation["operation"], "channel": spec["channel"]}
            if applicable:
                configured.append({**row, "routes": [x[0] for x in applicable], "actual_execution_verified": any(t.get("skill_id") == ident and t.get("route_id") in {x[0] for x in applicable} and t.get("source") == "live_tool" for t in traces)})
            else:
                missing.append(row)
            if spec["channel"] not in ("local", "audience"):
                accounts.add(spec["channel"])
            actions=spec.get('allowed_actions',[])
            if actions:
                from .policy import Policy
                from .errors import WorkflowError
                import time
                allowed=False
                for action in actions:
                    try:
                        Policy(profile,authorization or {},lambda:now if now is not None else time.time()).require(action,profile.get('bindings',{}).get(spec['channel'],{}).get('account_ref'),
                            public=action in ('publish_content','update_content','publish_listing','update_listing'),spending=action=='activate_ads')
                        allowed=True;break
                    except (WorkflowError,ValueError,TypeError):pass
                if not allowed:approvals.append({'node':node['id'],'channel':spec['channel'],'action_alternatives':actions,'reason':'existing exact scope not verified; no automatic new grant'})
        sources = config.get("data_sources", {})
        source_gaps = [c for c in goal["metric"]["components"] if not any(
            s.get("enabled") is True and s.get("brand_url") == profile.get("brand", {}).get("url")
            and goal["id"] in s.get("goal_ids", []) and c in s.get("components", []) for s in sources.values())]
        owner = [{"channel": ch,"account_ref":profile.get('bindings',{}).get(ch,{}).get('account_ref'), "reason": "selected brand account and current management readback required"}
            for ch in sorted(accounts) if not profile.get("bindings", {}).get(ch, {}).get("account_ref") or not any(a.get('channel')==ch and a.get('account_ref')==profile.get('bindings',{}).get(ch,{}).get('account_ref') and a.get('state')=='connected' for a in connected)]
        reports.append({"goal_id": goal["id"], "title": goal["title"], "graph_nodes": len(graph["nodes"]),
            "definition_present": True, "configured_routes": configured, "missing_tool_routes": missing,
            "missing_data_components": source_gaps, "owner_gates": owner,"authorization_gates":approvals,
            "measurement_contract":measurement_contract(goal),
            "declared_sources_are_not_verified_feeds":True,
            "needs_goal_settings": not all(k in setting for k in ("target", "minimum_sample", "observation_seconds")),
            "needs_execution_goal": goal["channel"] == "audience" and not setting.get("execution_goal_id"),
            "state": "tools_missing" if missing else "data_source_pending" if source_gaps else "account_pending" if owner else "scope_pending" if approvals else "configured_requires_live_acceptance",
            "live_end_to_end_accepted": False, "market_outcome_verified": False})
    return {"version": "0.6.0", "goals": reports, "goal_count": len(reports),
        "scope": "operator-configured routes and declared sources; live execution and goal results require separate receipts",
        "writes_performed": False}
