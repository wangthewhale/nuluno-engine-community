# Source-bound campaign drafts (unreleased development slice)

This is more than a source receipt: it produces an offer, a sales brief, audience
hypotheses, ad drafts and actual HTML landing drafts. The same original product
quotations serve human and buyer-AI views. It is **not** a hosted autonomous CRO
service or an accepted sales improvement. It does not publish ads or pages.

## One-command owner trial

With Node 24/npm and Python 3.9+ installed, from this reviewed source checkout:

```sh
node scripts/try-campaign.mjs --url https://www.sqlite.org/index.html --out my-campaign
```

Open `my-campaign/starter/landing-control.html` and `landing-candidate.html`.
This installs an isolated consumer and makes a real scoped read, then produces
drafts through the standard MCP tools below. Without a model/candidate, the
copy frames are clearly marked starter templates, not inferred audiences or
AI learning. The unknowns and exact source quotations still come from the real
product. Source reads are limited to the one exact public URL. No private
credentials, personal package configuration or existing install are reused.

Run the same command again: it opens the original persistent state in a new
service process and returns the original receipts/artifact without another GET.
An external agent can read `starter/research.json` and write the candidate
format below, then add `--candidate ./reviewed.json --revision reviewed` to
save an explicitly different draft using the same source evidence. Never
change a payload under its old revision. To revoke, add `--revoke`; a later
ordinary invocation must fail with native 401 proof and exit 3. No new key is
created after denial. All state remains under the ignored private output;
dependency consumers are removed after successful invocations.

## Existing remote or local operator gateway

Use a running community gateway built from this source, an existing operator
scope and run, Node 24, and the gateway's installed official MCP dependencies.
Existing alpha.2 release assets do **not** include these new tools. Set
`ENGINE_MCP_URL`, `ENGINE_EXTERNAL_AGENT_TOKEN` and `ENGINE_RUN_ID` privately.
Expose only `campaign_research`, `campaign_build`, `campaign_read`, and
`campaign_events` (plus `run_create` during owner bootstrap). Set an exact public
HTTPS URL allowlist and read quota; do not use a wildcard host or a private URL.

First collect current product evidence:

```sh
node examples/campaign/campaign.mjs \
  --sources '["https://your-product.example/"]' \
  --request first-campaign --out ./first-research
```

The extractor scans the **whole bounded visible text** (up to the existing
40,000-codepoint source limit), selecting exact quotations instead of returning
only a 2,500-byte prefix. Quotation offsets are Unicode codepoints; in JavaScript
use `Array.from(text).slice(start,end).join('')`, and hash the original visible
text as UTF-8. Raw response bytes have a separate SHA-256. Categories are
heuristics for review, not semantic truth. Missing prices/terms remain unknown.

Give `research.json` to a human or model to create a candidate JSON file:

```json
{
  "selected_fact_ids": ["fact-COPY_AN_ACTUAL_RESEARCH_ID"],
  "angles": [{"id":"evaluation","audience":"Technical buyers","motive":"Reduce uncertainty","hypothesis":"Evaluate the product using published details","fact_ids":["fact-COPY_AN_ACTUAL_RESEARCH_ID"]}],
  "variants": [
    {"id":"control","angle_id":"evaluation","headline":"Start with published product details","subheadline":"Review the original sources before choosing.","ad_headline":"Make an informed evaluation","cta_label":"View the product"},
    {"id":"candidate","angle_id":"evaluation","headline":"Know what to verify before purchasing","subheadline":"Keep missing conditions visible.","ad_headline":"Check product conditions first","cta_label":"Read the source"}
  ],
  "experiment":{"control_variant":"control","candidate_variant":"candidate","primary_metric":"qualified_conversion","assignment_unit":"visitor","min_sample_per_arm":100}
}
```

Replace placeholder IDs with real IDs; the placeholder cannot validate. Reuse
the original `--request` and sources, add `--candidate ./reviewed.json`, and use
a new output directory. Research reuses its original receipts without another
GET. Changed payload under an existing ID is rejected, rather than silently
minting another operation.

Creative copy stays an unvalidated messaging hypothesis. Numbers, prices and
guarantee-like claims require an entire verbatim quotation from referenced
facts; an ID alone cannot certify them. Sections contain only original quoted
statements. All HTML is escaped, contains no scripts, and its CTA goes only to
the original product. No private code or paid method library is needed.

The experiment is a preregistered draft, **awaiting native evidence**. A host
may serve these HTML files and add its own same-origin tracking script to the
`data-nuluno-*` markers. It must bind sticky assignment before exposures, verify
qualified conversions in an owner backend, deduplicate events, record refunds
and complete costs, and run existing commercial comparison/adoption gates.
Client clicks, a submitted `paid=true`, page renders or these drafts cannot
prove a sale or authorize adopting a winner. Min sample is a declared floor,
not a validated statistical power analysis. Without that adapter, the output
honestly says `measurement_adapter_connected:false` and cost/lift stay unknown.

For a reviewed revision of the same source, `campaign_research` also accepts
`receipt_ids` (one original receipt per URL). It verifies the original run,
requested URL and content digest before re-extracting; this makes no new GET.
An optional `fragments` array selects at most 24 exact, bounded quotations:
`{source_index:0,kind:"feature",start:120,end:170}`. Indices are codepoints into
that original receipt's visible text. A model or person chooses the fragment;
the engine verifies it and labels the category `caller_selected_requires_review`.
This is useful for short public quotations, not a way to submit invented facts.
Use a new research request ID for changed selections; all old receipts and
research artifacts stay unchanged. Never revive revoked access to revise copy:
the owner can work with previously delivered files, or explicitly authorize a
different product job. Existing-key denial remains denial.

No runtime model calls are made. A caller's model can propose copy, but this is
not autonomous method learning or model-weight training. A stopped gateway can
be restarted against the original persistent volume. Original artifacts and
cursor-readable activity remain; expired/revoked operator access still fails
at the gateway. Never put the bearer key in a URL, HTML, or public receipt.
