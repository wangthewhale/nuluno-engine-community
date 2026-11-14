# Methods packs and trust boundaries

The community wheel contains executable Python/MCP code plus the full S01 starter. The paid methods ZIP contains data only: skill procedures/contracts, common/channel/goal catalogs and graph definitions. It contains no runtime, executable scripts, account credentials, authorization files, worker config or model settings.

`method-pack-install` requires the SHA-256 of the **whole archive**, obtained independently from an operator-trusted purchase/delivery record. It then checks the fixed pack identity/schema, declared file list, sizes/hashes, safe paths, duplicate/symlink/encrypted entries, expansion bounds, immutable S01 compatibility and the complete goal/skill/graph dependency closure. It rejects unknown schema/authority manifest fields, missing graph dependencies and malformed catalogs. This validates integrity and admissible content, not a payment or license signature.

Validated releases are installed under `releases/<archive SHA>` in an operator-owned directory. `active.json` is replaced atomically only after validation; failure leaves the previous selection intact. Resources are verified again on loading. Run SQLite, task IDs, receipts, model configuration, worker methods, accounts and budgets live elsewhere and are never overwritten by a pack. A library description is not permission to use a tool.

The host sets `NULUNO_METHOD_PACK_DIR`. Model/tool inputs cannot change it. The same installed community kernel reads the selected library. Long-lived Python processes must restart after activation; the gateway's separate kernel invocations see the current selection. Existing S01 definitions/contracts must remain byte/content-compatible, preserving the starter's queued work.

The commercial builder is source code, not the commercial library. It only derives a data pack from the exact immutable, previously licensed release archive. Do not commit its output into the Apache community repository. Installing a commercial pack does not make its content Apache-licensed. See `NOTICE` and the pack's own license.


## Bounded discovery

The community catalog API is paginated: `catalog({run_id,kind:"work",after:0,limit:10})` lists actual installed work IDs; `kind:"goals"` or `"graphs"` lists the corresponding installed resources; `kind:"skill",skill_id:"S01"` returns the actual procedure, at most two steps at a time. Follow `next_after`. Uninstalled goal definitions return `installed:false, items:null`, rather than an empty catalog file pretending to be a library. Loading the complete licensed pack changes the real counts to 186 work methods, 99 goals, 134 graphs and 188 folders without changing the host's authority.
