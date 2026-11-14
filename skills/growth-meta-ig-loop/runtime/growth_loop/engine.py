import time
from pathlib import Path

from .errors import UncertainWrite, Waiting, WorkflowError
from .files import atomic_json, read_json
from .media import render_variant, verify_image, normalized_image
from .review import review
from .creative_agent import next_variant


class Engine:
    def __init__(self, store, provider_factory, tokens=None):
        self.store, self.provider_factory, self.tokens = store, provider_factory, tokens

    def seed(self, run_id):
        profile = self.store.config(run_id)
        for channel in profile.get("channels", ["meta_ads", "instagram"]):
            self.store.enqueue(run_id, 0, "accounts." + channel, {"channel": channel})
        self.seed_cycle(run_id, 1, profile.get("channels", ["meta_ads", "instagram"]))

    def seed_cycle(self, run_id, cycle, channels):
        creative = self.store.enqueue(run_id, cycle, "creative")
        self.store.enqueue(run_id, cycle, "draft", depends=[creative])
        for channel in channels:
            account = "%s:0:accounts.%s" % (run_id, channel)
            publication = self.store.enqueue(run_id, cycle, "publish." + channel, {"channel": channel}, [creative, account])
            self.schedule_observation(run_id, cycle, channel, publication, 1)

    def schedule_observation(self, run_id, cycle, channel, publication, index):
        profile = self.store.config(run_id)
        delay = profile.get("schedule", {}).get("interval_seconds", 43200)
        observed = self.store.enqueue(run_id, cycle, "observe.%s.%s" % (channel, index),
                                      {"channel": channel, "index": index}, [publication], self.store.clock() + delay)
        self.store.enqueue(run_id, cycle, "review.%s.%s" % (channel, index),
                           {"channel": channel, "index": index}, [observed])

    def run_tick(self, run_id, limit=40):
        if self.store.config(run_id).get("workflow"):
            from .workflows import WorkflowRunner
            WorkflowRunner(self.store).wake_due(run_id)
        grant_path = self.store.root / "authorization.json"
        if grant_path.exists():
            grant = read_json(grant_path)
            if grant.get("pause_on_expiry") and grant.get("expires_at", 0) <= self.store.clock():
                if self.store.config(run_id).get("workflow"):
                    from .workflows import WorkflowRunner
                    WorkflowRunner(self.store).queue_expiry_stops(run_id, grant)
                rows = self.store.db.execute("SELECT cycle FROM artifacts WHERE run_id=? AND kind='meta_ads' ORDER BY cycle DESC LIMIT 1", (run_id,)).fetchall()
                if rows:
                    self.store.enqueue(run_id, rows[0][0], "pause.meta_ads.expired", {"channel": "meta_ads", "reason": "authorization_expired"})
        processed = []
        for _ in range(limit):
            task = self.store.claim(run_id)
            if task is None:
                break
            profile = self.store.config(run_id)
            try:
                from .continuity import before
                before(self.store,task)
                output = self.execute(task, profile)
                self.store.finish(task, "done", output)
            except WorkflowError as exc:
                kind = exc.kind
                if kind == "auth_expired":
                    try:
                        if not self.tokens:
                            raise Waiting("需要正常登入授權；原進度已保留。")
                        if task["attempts"] > 2:
                            raise Waiting("正常更新後仍無有效授權；需要本人重新授權。")
                        self.tokens.refresh()
                        self.store.finish(task, "retry", {"kind": "auth_refreshed", "reason": "已正常更新憑證，從原任務接續。"}, self.store.clock())
                    except WorkflowError as refresh_error:
                        self.store.finish(task, refresh_error.kind, {"reason": str(refresh_error), "details": refresh_error.details})
                elif kind in ("transient", "uncertain", "readback_mismatch"):
                    cap = profile.get("recovery", {}).get("max_attempts", 5)
                    if task["attempts"] >= cap:
                        self.store.finish(task, "waiting_owner", {"reason": "已達有界恢復次數；原進度與未確認寫入保留。", "kind": kind, "details": exc.details})
                    else:
                        delay = exc.retry_after if exc.retry_after is not None else min(300, 2 ** task["attempts"] * 5)
                        self.store.finish(task, "retry", {"kind": kind, "reason": str(exc), "details": exc.details}, self.store.clock() + max(1, delay))
                elif kind == "waiting_time":
                    self.store.finish(task, "retry", {"reason": str(exc), "details": exc.details}, exc.details["resume_at"])
                elif kind.startswith("waiting_"):
                    self.store.finish(task, kind, {"reason": str(exc), "details": exc.details})
                else:
                    self.store.finish(task, "failed", {"kind": kind, "reason": str(exc), "details": exc.details})
            except Exception as exc:
                # A programming error must not be disguised as a successful run.
                self.store.finish(task, "failed", {"kind": "runtime_error", "reason": type(exc).__name__})
            from .continuity import observe_failure
            observe_failure(self.store,task)
            processed.append(self.store.task(task["key"]))
        return {"processed": len(processed), "status": self.store.status(run_id)}

    def execute(self, task, profile):
        kind = task["kind"].split(".")[0]
        run_id, cycle = task["run_id"], task["cycle"]
        directory = self.store.root / "artifacts" / run_id / ("cycle-%02d" % cycle)
        if kind == "flow":
            from .workflows import WorkflowRunner
            return WorkflowRunner(self.store).execute(task, profile)
        if kind == "creative":
            agent_result = None
            if cycle > 1 and profile.get("creative", {}).get("refresh") == "agent":
                agent_result = next_variant(self.store, task, profile)
                profile = {**profile, "creative": {**profile["creative"], "variants": [agent_result["variant"]]}}
            result = render_variant(profile, cycle, directory)
            if agent_result:
                result["hypothesis"] = agent_result["hypothesis"]
                result["changed_variable"] = agent_result["changed_variable"]
            self.store.artifact(run_id, cycle, "creative", result)
            return {"artifact": result, "next": "draft and selected channel publication tasks"}
        if kind == "draft":
            creative = self.store.artifact(run_id, cycle, "creative")
            result = {"brand": profile["brand"]["name"], "url": profile["brand"]["url"], "cycle": cycle,
                      "creative": creative, "accounts": profile.get("accounts", {}),
                      "budget": profile.get("budget", {}), "mode": profile.get("mode", "prepare"),
                      "delivery_status": "LOCAL_REVIEWABLE_DRAFT_NOT_PUBLISHED"}
            atomic_json(directory / "publication-draft.json", result)
            self.store.artifact(run_id, cycle, "draft", result)
            return {"path": str(directory / "publication-draft.json"), "publication": False}
        provider = self.provider_factory(profile)
        channel = task["data"].get("channel")
        if kind == "accounts":
            result = provider.account_ready(task)
            self.store.artifact(run_id, 0, "accounts_" + channel, result)
            return result
        if kind == "publish":
            creative = self.store.artifact(run_id, cycle, "creative")
            try:
                current = verify_image(creative["asset"]["path"], (1080, 1350))
                if current["sha256"] != creative["asset"]["sha256"]:
                    raise WorkflowError("素材檔與已保存版本不符。", "invalid_asset")
            except WorkflowError as exc:
                if exc.kind != "invalid_asset":
                    raise
                old = creative["asset"]
                try:
                    fixed = normalized_image(old["path"], directory / "creative-repaired.jpg")
                    creative["asset"] = fixed
                except WorkflowError:
                    creative = render_variant(profile, cycle, directory)
                creative.setdefault("repair_history", []).append({"old_sha256": old.get("sha256"), "reason": str(exc), "at": self.store.clock()})
                # A changed local file cannot silently keep referencing an old
                # remote image. The configured uploader must publish the repair.
                creative["public_image_url"] = None
                self.store.artifact(run_id, cycle, "creative", creative)
                self.store.event(run_id, task["key"], "asset_repaired", {"new_sha256": creative["asset"]["sha256"]})
            result = provider.publish_ads(task, creative) if channel == "meta_ads" else provider.publish_instagram(task, creative)
            result["published_at"] = self.store.clock()
            self.store.artifact(run_id, cycle, channel, result)
            return result
        if kind == "observe":
            publication = self.store.artifact(run_id, cycle, channel)
            result = provider.observe(task, {channel: publication})
            result["age_seconds"] = self.store.clock() - publication["published_at"]
            self.store.artifact(run_id, cycle, task["kind"], result)
            return result
        if kind == "review":
            index = task["data"]["index"]
            observation = self.store.artifact(run_id, cycle, "observe.%s.%s" % (channel, index))
            decision = review(channel, observation, profile, cycle)
            publication_key = "%s:%s:publish.%s" % (run_id, cycle, channel)
            if decision["decision"] == "new_creative":
                maximum = profile.get("schedule", {}).get("max_cycles", 2)
                if cycle < maximum:
                    self.seed_cycle(run_id, cycle + 1, [channel])
                    decision["next_cycle_enqueued"] = cycle + 1
                else:
                    decision["stopped_at_configured_cycle_limit"] = True
            elif decision["decision"] == "pause":
                self.store.enqueue(run_id, cycle, "pause." + channel, {"channel": channel}, [publication_key])
            elif decision["decision"] == "repair_delivery":
                raise Waiting("需要處理平台具體審核／配送問題，保留所有素材與現有廣告ID。", "waiting_owner", decision)
            elif decision["decision"] == "complete_zero_spend":
                decision["stopped_at_zero_spend_scope"] = True
            else:
                maximum = profile.get("schedule", {}).get("max_observations_per_cycle", 60)
                if index < maximum:
                    self.schedule_observation(run_id, cycle, channel, publication_key, index + 1)
                    decision["next_observation_enqueued"] = index + 1
                else:
                    decision["stopped_at_configured_observation_limit"] = True
            self.store.artifact(run_id, cycle, task["kind"], decision)
            return decision
        if kind == "pause":
            return provider.pause_ads(task, self.store.artifact(run_id, cycle, channel))
        raise WorkflowError("Unknown task kind", "invalid_task")
