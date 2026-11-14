# Nuluno Engine Community — 0.13.0a1 preview

An **Apache-2.0 executable core**, with a complete S01 product-research starter: persistent tasks, scoped MCP, source receipts, original-request reuse, recovery and bounded artifact learning. This is a prerelease community distribution, not a replacement for the sold Engine 0.12.0 package. Its installed Python namespace is `nuluno_engine_community.growth_loop`, so it does not overwrite the commercial `growth_loop` module. Use a dedicated virtual environment for a clean trial.

The free starter contains one work method (S01) and its orchestrator. It does **not** contain the commercial 99-goal / 134-graph / 188-folder sales methods library. Those are separate data under their commercial license. Core execution and technical learning are present and usable without purchasing that library; no SDK-only placeholder is substituted for them.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
npm --prefix deploy/mcp-gateway ci --omit=dev --ignore-scripts
.venv/bin/nuluno-engine-community doctor
ENGINE_PYTHON="$PWD/.venv/bin/python" node deploy/mcp-gateway/public-read-demo.mjs
```

The demo performs a real unauthenticated public GET, reuses the original receipt, replaces its gateway process and checks the same saved run/note, rejects another read beyond its quota, and revokes the original key. It creates private temporary state and removes it on exit. There are no model calls, private inbox access, social writes or advertising spend. Network failures fail the run; fixtures are never substituted.

Python 3.9+ and Node 20+ are declared requirements; actual runtime combinations are recorded in receipts. You provide models, a host/worker and platform permissions for broader tasks. Installation alone does not create an autonomous daemon or bypass consent/MFA/CAPTCHA. Missing costs remain unknown, not zero. Technical method adoption is not verified sales improvement.

## Separately acquired commercial methods

Obtain the data-only pack and its SHA-256 from a delivery record you trust. The installer checks integrity, identity, file paths, data schema and complete skill/graph dependencies. A self-authored manifest does not prove payment or authenticity. The operator must have the legal right to use the pack.

```sh
.venv/bin/nuluno-engine-community method-pack-install \
  --archive /path/to/licensed-methods.zip \
  --sha256 THE_SHA256_FROM_YOUR_DELIVERY_RECORD \
  --directory /absolute/operator-owned/method-packs
NULUNO_METHOD_PACK_DIR=/absolute/operator-owned/method-packs \
  .venv/bin/nuluno-engine-community doctor
```

Set the same `NULUNO_METHOD_PACK_DIR` on the MCP gateway/worker process. The pack is data, never executable code or authority configuration. Installation does not change an existing run, grant platform access, start actions or relicense commercial content. New Python invocations load the atomically selected valid library; restart long-lived Python clients after a pack change.

See [the full S01 starter](docs/S01-starter.md), [independent deployment](docs/deployment.md), [pack format and trust](docs/method-packs.md), [security](SECURITY.md), [contributing](CONTRIBUTING.md) and [third-party notices](THIRD_PARTY.md).
