# Nuluno Engine Community

**Keep an agent's work, evidence and next steps when its process stops.**

I built this because my agents could finish individual tool calls and still lose
the larger job: repeat research, forget what was tried, or report a test as a
business result. Engine keeps durable work, original source receipts and
versioned method checks behind an MCP interface that you host.

The Apache-2.0 community core includes the complete S01 public-product-research
starter. Bring your own model and platform permissions for broader work.

## First run: make something you can use

Requires **Python 3.9+ and Node 20+**. No Nuluno account, model API key or social
login is needed for this public-read example. Run from a new source checkout:

```sh
git clone https://github.com/wangthewhale/nuluno-engine-community.git
cd nuluno-engine-community
python3 -m venv .venv
.venv/bin/python -m pip install .
npm --prefix deploy/mcp-gateway ci --omit=dev --ignore-scripts
ENGINE_PYTHON=.venv/bin/python node examples/source-brief/brief.mjs \
  --url https://www.python.org/ --out my-source-brief
```

Open **`my-source-brief/brief.md`**. It contains a real bounded source excerpt,
the original URL, body hash and receipt ID. The source remains untrusted data;
the excerpt can omit prices or conditions. The proof JSON records actual
execution with unknown complete costs left unknown.

Run the last command again. A new service process opens the same saved run and
returns its original receipt. It preserves the one-read quota instead of making
a new task. Private local state stays inside the output folder and is ignored
by Git. [Commands and recovery details](examples/source-brief/README.md).

## Three things to try

| Situation | Useful action | What you can verify |
|---|---|---|
| Collect evidence before writing about a product | Create a source brief for a public HTTPS page | URL, original observation and body/receipt hashes |
| Continue research after restarting an agent | Rerun with the same output folder | Same run/request/receipt and one reserved provider request |
| Stop a worker that should no longer read | Add `--revoke` to the same command | Its original key receives 401; a later run remains denied |

The [version-bound alpha.2 Linux receipt](docs/evidence/first-use-0.13.0a2-20261115.json) records the exact source commit, a real public GET, original-receipt reuse across separate service processes, original-key revocation and a later fresh process exiting with code 3. The [preceding preflight](docs/evidence/first-use-preflight-20261115.json) remains unchanged. These are operator-run technical checks, not external human adoption or sales improvement. Linux core CI also exercised an actual persistent container replacement.

## What the core contains

- Persistent tasks, scoped worker authority, source evidence and cost unknowns.
- Standard MCP through an independently hosted gateway.
- Bounded artifact proposals, validation, adoption and rollback mechanisms.
- An operator-controlled installer for separately licensed, data-only methods.

The full **99-goal / 134-graph / 188-folder** commercial library is separate from
this free starter. Installation provides no platform credentials or running
model worker. The current community version is **0.13.0a2**, using the isolated
`nuluno_engine_community.growth_loop` namespace; existing commercial releases
and their customer rights remain separate.

## Learning includes rejecting a bad method

We froze three AI-authored method proposals and tested them on nine real public
sources. V3 retained 7/9 required facts versus its prefix baseline's 2/9, but
missed two necessary facts. Every proposal was rejected before adoption.
[All failures, criteria and replay instructions are public](examples/method-learning/README.md).

That experiment does not establish unattended method generation, model-weight
training, sales lift or a complete-cost advantage. A useful next contribution
is a reproducible failure and a new method with independently evaluated evidence.

## Help improve the first task

If installation fails, open a [first-run issue](https://github.com/wangthewhale/nuluno-engine-community/issues/new/choose)
with your runtime versions, failed step and sanitized receipt. Keep keys, state
folders and customer data private. There is no automatic telemetry or background
daemon in this example.

[Technical guide](docs/technical-guide.md) · [S01 procedure](docs/S01-starter.md) ·
[Deployment](docs/deployment.md) · [Method packs](docs/method-packs.md) ·
[Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [License](LICENSE)

## Client identity and authorization

The SDK identifies its actual Nuluno Community version. A confirmed Cloudflare 403 / Error 1010 is reported as `transport_client_identity_rejected` with `requiresOwnerAuthorization: false`; it is a transport identity problem. Structured invalid-key, revoked-key and scope-denial responses remain authorization failures. This classification never renews a key, expands a grant or retries a possibly dispatched POST.
