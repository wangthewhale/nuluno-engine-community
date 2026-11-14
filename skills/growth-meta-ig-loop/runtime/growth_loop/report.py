import base64
import html
from pathlib import Path

from .files import atomic_json, redact, safe_path


def write_report(store, run_id, output):
    status = store.status(run_id)
    profile = store.config(run_id)
    escape = lambda value: html.escape(str(value))
    mode = profile.get("mode", "prepare")
    label = {"prepare": "本機草稿，未公開、未花費", "fixture": "受控HTTP測試，不是真實投放或銷售", "live": "真實連接；各步驟依實際證據列示"}[mode]
    rows = []
    for task in status["tasks"]:
        outcome = task.get("outcome") or {}
        reason = outcome.get("reason") or outcome.get("decision") or ""
        rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % tuple(escape(v) for v in (task["cycle"], task["kind"], task["state"], task["attempts"], reason)))
    figures = []
    outcomes = []
    instagram = store.artifact(run_id, 1, "instagram")
    meta = store.artifact(run_id, 1, "meta_ads")
    bio = store.artifact(run_id, 0, "instagram_bio")
    for title, record in (("IG 實際發布", instagram), ("Meta 實際狀態", meta), ("IG 公開簡介", bio)):
        if not record:
            continue
        import json
        outcomes.append("<h2>%s</h2>" % escape(title))
        permalink = record.get("readback", {}).get("permalink")
        if isinstance(permalink, str) and permalink.startswith("https://www.instagram.com/"):
            outcomes.append('<p><a href="%s">開啟真實貼文</a></p>' % escape(permalink))
        outcomes.append("<pre>%s</pre>" % escape(json.dumps(redact(record), ensure_ascii=False, indent=2)))
        proof = record.get("screenshot_path")
        if proof and Path(proof).is_file():
            encoded = base64.b64encode(Path(proof).read_bytes()).decode("ascii")
            outcomes.append('<img alt="%s讀回證據" style="max-width:100%%" src="data:image/png;base64,%s">' % (escape(title), encoded))
    records = store.db.execute("SELECT cycle,data FROM artifacts WHERE run_id=? AND kind='creative' ORDER BY cycle", (run_id,))
    import json
    for row in records:
        data = json.loads(row["data"])
        path = Path(data["asset"]["path"])
        if path.exists():
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            figures.append('<figure><img alt="本輪實際製作素材" src="data:image/jpeg;base64,%s"><figcaption>第%s輪 · %s</figcaption></figure>' % (encoded, escape(row["cycle"]), escape(data["headline"])))
    if profile.get("workflow"):
        outcomes.append("<h2>流程與實際交付</h2><p>流程 %s；下列是已保存成果。受控資料、草稿、對外動作與市場成交分開。</p>" % escape(profile["workflow"]["id"]))
        seen_assets = set()
        for row in store.db.execute("SELECT cycle,kind,data FROM artifacts WHERE run_id=? AND kind LIKE 'flow_output.%' ORDER BY created,rowid", (run_id,)):
            result = json.loads(row["data"])
            data = result.get("data", {})
            outcomes.append("<details><summary>第%s輪 · %s · %s</summary><pre>%s</pre></details>" % (escape(row["cycle"]), escape(result.get("skill_id", row["kind"])), escape(result.get("completion_level", result.get("execution_level"))), escape(json.dumps(redact(result), ensure_ascii=False, indent=2))))
            if result.get("skill_id") == "S12":
                for asset in data.get("assets", []):
                    if asset.get("sha256") in seen_assets: continue
                    try: path = safe_path(store.root, asset["path"])
                    except (ValueError, KeyError): continue
                    if path.is_file():
                        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                        figures.append('<figure><img alt="流程實際圖檔" src="data:image/jpeg;base64,%s"><figcaption>第%s輪實際圖檔</figcaption></figure>' % (encoded, escape(row["cycle"])))
                        seen_assets.add(asset.get("sha256"))
        outcomes.append("<h2>對外動作讀回</h2>")
        for row in store.db.execute("SELECT request,state,result FROM operations WHERE task_key LIKE ? ORDER BY updated", (run_id + ":%",)):
            action, result = json.loads(row[0]), json.loads(row[2]) if row[2] else None
            outcomes.append("<details><summary>%s · %s</summary><pre>%s</pre></details>" % (escape(action.get("action")), escape(row[1]), escape(json.dumps(redact(result), ensure_ascii=False, indent=2))))
    text = """<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>行銷流程驗收</title><style>body{font:16px/1.7 system-ui;margin:32px auto;padding:0 24px;max-width:1100px;color:#142437;background:#fafbf9}h1{font-size:30px}table{width:100%%;border-collapse:collapse}td,th{border-bottom:1px solid #ddd;text-align:left;padding:10px;overflow-wrap:anywhere}figure{display:inline-block;margin:16px;max-width:310px;vertical-align:top}img{width:100%%;border-radius:12px}.state{padding:14px;border-radius:10px;background:#e6efeb}.table{overflow:auto}</style><h1>%s · 流程驗收</h1><p class="state">%s</p><p>run %s · 素材、草稿、實際發佈與付款分開記錄。</p><section>%s</section><div class="table"><table><thead><tr><th>輪次</th><th>工作</th><th>狀態</th><th>嘗試</th><th>原因／下一步</th></tr></thead><tbody>%s</tbody></table></div></html>""" % (escape(profile["brand"]["name"]), escape(label), escape(run_id), "".join(figures), "".join(rows))
    text = text.replace("</html>", "<section>" + "".join(outcomes) + "</section><style>pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f2;padding:16px;border-radius:10px;font-size:13px}</style></html>")
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    atomic_json(path.with_suffix(".json"), redact(status))
    return {"html": str(path.resolve()), "json": str(path.with_suffix(".json").resolve()), "mode": mode, "claim": label}
