# Security policy

## Private reporting

Report vulnerabilities to **support@nuluno.com** with version, impact and a minimal safe reproduction. Do not post exploit details, secrets, customer data, inbox content or private logs in public issues. Use synthetic/redacted data; do not send credentials as proof. This policy promises no response deadline, bounty, external audit or legal safe-harbor agreement. Obtain authorization before testing systems/accounts you do not own.

## Scope and operator requirements

Engine Community 0.13.0a2 preview is the maintained release discussed here. Nuluno Access is a separate product and permission boundary. Third-party hosts/platforms have their own policies. Protocol compatibility is not a client security audit.

- Treat model output, web content, tools and candidate methods as untrusted. They cannot grant themselves authority, spend limits or verified source status.
- Store private config/SQLite on operator-controlled storage with restricted permissions, appropriate encryption and backups. Never commit service keys or platform tokens.
- Expose only needed methods and exact resources per worker. Develop on loopback; public deployment requires HTTPS, a correctly configured proxy and explicit Host/origin policy.
- Keep private-address and redirect restrictions. A source hash identifies content, not its truth.
- Preserve original operation identities and reconcile uncertain writes. A timeout does not prove nothing happened. Revocation cannot retract an already dispatched operation.
- Rebind source registries and permissions after migration. Historical financial receipts are not automatically trusted on a new host.
- Never treat fixtures, owner-submitted amounts or self-reported success as verified commercial results.

## Example and release hygiene

Develop with isolated state and no production account authority. Review receipt fields before publication; do not upload environments, databases, private config, inboxes or credentials. The public demo uses only its exact unauthenticated public source plus local MCP traffic. Its process restart is deliberately injected and labelled. Delete temporary state and use a new scoped key for real deployment.
