# A source brief that survives a restarted process

From the repository root, after the README installation commands:

```sh
ENGINE_PYTHON=.venv/bin/python node examples/source-brief/brief.mjs \
  --url https://www.python.org/ --out my-source-brief
```

You receive `brief.md` and an immutable `receipt-TIMESTAMP.json`. The brief is a
bounded original text excerpt for source-grounded research, with a URL and body
hash. It may contain navigation or omit important facts; verify the original
page before writing product, price or outcome claims. Source content stays
inside a fence and never becomes code or an agent instruction.

Run the exact command again. It opens the original SQLite state in a new
service process and reuses the same run/request/receipt. `reusedOriginalReceipt`
becomes true while `providerRequestsReserved` remains 1. This is an explicit
operator restart, not a measured natural-crash recovery rate.

Revoke this example's original worker key:

```sh
ENGINE_PYTHON=.venv/bin/python node examples/source-brief/brief.mjs \
  --url https://www.python.org/ --out my-source-brief --revoke
```

The proof records 401. A later normal invocation is denied too; it does not
mint a new key. The owner must explicitly authorize a separate workspace for
new work. Existing local output remains available to its owner.

Private key/config/state files are under `private/`; the output folder has a
Git ignore rule. Treat the whole folder as private and do not attach it to an
issue. Share only a reviewed receipt. Do not delete state simply to retry an
uncertain source read. Different source URLs require a deliberately new output
folder and owner scope. Only one driver should use an output folder at a time;
after an abrupt driver kill, inspect the retained `.lock` and remove it only
after confirming no original driver is running. The underlying saved task and
receipt must be retained.

On errors, check Node 20+, the selected Community Python installation, public
network access, exact scope and retained revocation. No network failure is
replaced by a fixture. No model call, platform write or private login occurs.
Complete costs remain unknown rather than zero.
