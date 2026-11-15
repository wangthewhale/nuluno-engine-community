# Prepare a community release for review

The `release-readiness` workflow runs manually or for an existing matching `community-v<version>` tag. It **does not create a tag, GitHub Release, PyPI upload or deployment**. It needs no repository secret, private methods archive, platform credential or paid model. Its repository permission is `contents: read`.

It exports only reviewed Git-tracked blob bytes from the exact checked-out commit, rejects links, private/generated paths, commercial catalogs, unexpected skill folders and common credential patterns, then builds a wheel from that isolated snapshot. Dirty/untracked local files are never imported into the source artifact. The scan is a safeguard, not a complete privacy/security audit. Inspect source and artifacts before any public release.

The expected manual version and any tag must match `pyproject.toml`. Wheel checks enforce the community namespace, its single console entry and two starter folders. The outputs are the wheel, a full public source ZIP (including contribution tools), `release-manifest.json` and `SHA256SUMS`. These checksums provide integrity, not a signed publisher identity.

After core, proposal-inspector and official MCP checks pass, the workflow stores these files as a temporary Actions artifact for maintainer review. Artifact upload follows [GitHub's official artifact workflow](https://docs.github.com/en/actions/tutorials/store-and-share-data); repository permissions follow [GitHub's workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax). There is no write permission to create a release.

Local preparation from a Git checkout:

```sh
.venv/bin/python scripts/release_artifacts.py --expected-version 0.13.0a2 --output dist/release-readiness
```

The output directory must be empty. The source commit is always `HEAD`; commit reviewed changes before expecting them in an artifact. A maintainer separately verifies the commit, source inventory, checksums, version and prerelease status, then deliberately publishes through the intended channel. Keep the commercial library outside this repository and these artifacts.
