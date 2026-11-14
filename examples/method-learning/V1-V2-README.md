# Real failed methods, preserved results

This demo exposes the failure of a 2,500-byte prefix research method and two
AI-authored attempts to improve it. The same public source, model-call count
(zero at runtime), complete representation budget, and independent source-bound
claims are used for both methods. It does **not** demonstrate sales improvement,
lower complete costs, weight training, or unattended method generation.

Both proposed methods were rejected. They were never manually adopted.

| Method | New source cases | Baseline claims retained | Candidate claims retained | Decision |
|---|---:|---:|---:|---|
| V1 | 3/3 | 0/2, 1/2, 3/3 | 2/2, 2/2, 2/3 | Reject: lost a required free-tier fact |
| V2 | 3/3 | 1/2, 2/2, 3/3 | 1/2, 2/2, 3/3 | Reject: incomplete free-tier evidence; no strict improvement |

All source spans and byte budgets passed. Failed claims and neutral cases remain
in the denominator. V1's sources became explicitly seen training for V2; they
were not reused as new held-out evidence. V2 was frozen before the evaluator
revealed its three new URLs or independently authored their claim sets.

`methods/v2/evidence/community-wrapper-receipt.json` records execution against
the installed `nuluno-engine-community 0.13.0a1` namespace and unchanged kernel
hashes. The source gate rejected the candidate **before native adoption**. A
separate, labelled known-input compatibility check proves fresh-process SQLite
resume only; it does not turn this failed new-source experiment into a success.

## Install and inspect

Use Python 3.9+ and the Community package. These commands pin the initial public
release commit; they do not require the original paid method library:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install 'git+https://github.com/wangthewhale/nuluno-engine-community.git@794d4cd4680c97c4a35ace107cde9d98a133426f'
```

Review the four frozen method files under each `methods/v*/` directory. The
candidate is a fixed, pure, source-span selector. Engine only executes the
bounded JSON `filter_equal → map_pick → concat` program. No arbitrary Python
program is installed as an Engine op. The outer orchestration and layout
wrappers are separately hashed and leave those frozen method files unchanged.

## Replay a historical result without redistributing web pages

Raw HTML, extracted full text and SQLite files are intentionally omitted.
`fetch_replay.py` issues only public HTTPS GETs to the recorded final source
URLs, checks public DNS/IPs, and requires both original body and extracted text
hashes to match before using the historical source envelope and oracle. It
records the new GET separately. A changed or unavailable source stays in the
denominator and prevents historical adoption; it is never silently substituted.

Run from this demo directory:

```sh
python3 fetch_replay.py --method v2 --out replay-v2
NULUNO_DEMO_KERNEL=$(.venv/bin/python -c 'from pathlib import Path; import nuluno_engine_community.growth_loop as g; print(Path(g.__file__).parent)')
python3 methods/v2/community_wrapper.py \
  --python .venv/bin/python --kernel-root "$NULUNO_DEMO_KERNEL" \
  --registration methods/v2/evidence/heldout-registration.json \
  --manifest replay-v2/private/manifest.json --out replay-v2/community
```

The corresponding V1 command substitutes `v1` and a fresh output folder. If a
provider has changed its page, the published receipts/hashes remain historical
evidence; new GETs cannot reproduce that old snapshot. To evaluate a new method,
freeze it first, have a separate evaluator register previously unseen products
and prepare new source-bound claims, and preserve every failure. Do not edit a
method or weaken its criteria after inspecting new-source results.

Unknown complete costs remain `null`. No private inbox access, account creation,
outreach, social posting, payments or external writes occur in this demo.
