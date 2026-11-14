# Propose a method another developer can inspect

A proposal contains its executable bounded program, applicable conditions,
limitations, license, file hashes and explicitly classified evidence. It carries
no tenant identity, account credential, permission grant or automatic adoption.

Run from this directory with Python 3.9 or later:

```sh
python3 method_pack.py examples/normalize-query
```

After installing the corresponding Engine package, validate its actual bounded
program grammar too:

```sh
python3 method_pack.py examples/normalize-query --with-engine-kernel
```

The example only normalizes public query text. Its evidence is illustrative.
It does not demonstrate autonomous learning, a new customer or sales uplift.

To contribute, copy the example folder, replace the method and evidence, and
recompute the listed SHA-256 values. Run the checker before opening a pull
request. Say exactly what you ran, what failed and what remains unknown. Keep
private data, credentials, personal contact lists and platform grants out of the
proposal. The checker detects some credential patterns; it is not a complete
privacy or security audit. The source-rights field is a declaration, not proof.

The checker refuses unlisted files, symlinks, path traversal, oversized input,
duplicate JSON keys, mismatched hashes and requested permissions. It never
executes contributor code. Host-side use must construct a new Engine contract
with the destination's own identities, source references, permitted operations
and evaluation. The Engine's existing artifact validation and adoption gates
still apply. A valid package remains a proposal.

Graph and external-tool proposals require a separately reviewed host contract;
this first interchange format intentionally accepts only pure bounded programs.
This is a local contribution format, not a hosted public marketplace. These new community tools and the example method are Apache-2.0 licensed. Paid Nuluno libraries and connectors retain their separate licenses.
