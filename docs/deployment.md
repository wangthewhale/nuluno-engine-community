# Independent community deployment

Install the community package and gateway dependencies as shown in README. Create a private durable directory and initialize a read-only, zero-spend service:

```sh
.venv/bin/nuluno-engine-community init-service \
  --workspace /absolute/private/state --config /absolute/private/service.json \
  --brand-url https://your-existing-product.example \
  --brand-name 'Your product' --run-id first-product
```

Use an actual HTTPS product URL. Initialization never overwrites existing policy. Set a unique 32+ character `ENGINE_EXTERNAL_AGENT_TOKEN` using your secret manager. Then set `ENGINE_PYTHON`, `ENGINE_CONFIG`, `ENGINE_WORKSPACE`, `ENGINE_ORIGIN` and start `node deploy/mcp-gateway/server.mjs`. The endpoint is `/mcp`, with the operator's Bearer token. Do not put keys in URLs or commit them.

Configure only required worker `methods`, exact source URLs and quotas. An external agent can call `run_create` with the saved profile, read public sources, acquire/heartbeat/complete the S01 task, and save/read evidence using the original run. Research completion is not a sale. The community starter does not imply that the commercial goal library or a private platform connection is installed.

The optional paid methods directory is selected by host environment `NULUNO_METHOD_PACK_DIR`. See method-packs.md. It is independent of the run database and cannot supply platform authority. Restart long-lived Python processes after changing a library. A Node gateway launches separate kernel invocations and sees the current valid selection.

`deploy/Dockerfile.mcp` builds the community runtime and official SDK gateway, running as UID 10001 with `/var/lib/nuluno` as durable storage. Initialize config/state **inside the mounted volume as that UID** using `--entrypoint nuluno-engine-community`, then start the gateway with the same volume and secret. Bind development ports to loopback. A real public service requires HTTPS, an explicit origin/Host policy, a configured proxy, backups and monitoring. A container build or temporary CI process is not a managed cloud service or capacity acceptance.

Nuluno Access and Nuluno.com are independent products. No Nuluno.com cookie or Mission is required. Access account operations require their own connection and scoped key; installing a methods pack does not connect Access, grant inbox/social access, authorize writes or start paid model calls.
