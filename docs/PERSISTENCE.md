# State persistence design

GitHub-hosted runners are disposable. The current market-research workflow uploads
one SQLite snapshot as an artifact, but does not carry state into the next run.
Do not treat artifacts as a live shared database.

Planned solution: a persistent hosted PostgreSQL database with migrations,
idempotent observation IDs, encrypted secrets, and separate paper-order and
research journals. Select a provider only after verifying its current free-tier
limits and sleep policies. Avoid committing a database or secrets to a public repo.

Until persistent storage is configured, research runs are independent and
**no unattended order execution is enabled**. For backtests, historical input
datasets should be versioned with provenance and timestamp validation.
