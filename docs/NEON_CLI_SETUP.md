# Neon CLI setup, project misty-shadow-43891652

Scope: Project $10K paper-trading research. Target **existing** Neon
project `misty-shadow-43891652`, requested Neon branch **production**.

## Implemented in GitHub

The repository contains:

- `neon.ts` exactly as requested:
  ```ts
  import { defineConfig } from "@neon/config/v1";

  export default defineConfig({});
  ```
- `package.json` with `@neon/config` and `@neon/env` dependencies.
- `.gitignore` excludes `.neon`, `.env.local`, `.env.*.local`
  and `node_modules/`.

These files were **committed to GitHub**. They have **not** been installed
or run on the owner's local PC. No Neon authentication, project linking or
deployment has been successfully completed through ChatGPT.

## Local setup, in the cloned horsesdontstop directory

Use Node.js **22.20 or newer** for `neon skills`. Run in a local
terminal where you can authorize the browser login:

```powershell
git pull
npm i -g neon@latest
neon auth
neon skills -y
neon link --project-id misty-shadow-43891652 --branch production -y
npm install
neon config status
neon config plan
```

`neon auth` is the documented CLI login command. Linking validates
the project and branch, then writes **local** `.neon` context and may pull
database secrets to `.env.local`; do not commit or paste those files.

### MCP

The supplied command `neon mcp -y` defaults to an account-wide MCP
configuration, may mint a broadly scoped API key, and enables write
actions. Consider a project-scoped OAuth configuration instead:

```powershell
neon mcp --project --oauth --project-id misty-shadow-43891652 -y
```

Confirm your installed CLI help supports the flags:
`neon mcp --help`. Using the official Neon ChatGPT integration is an
alternative and may avoid configuring local MCP entirely.

### Deploy

`neon deploy` is equivalent to `neon config apply`, and it can modify
existing Neon services on the **production** branch. Review
`neon config plan` first; apply ONLY if it lists expected safe actions,
and the account owner approves those actions:

```powershell
neon deploy
```

The empty `defineConfig({})` declares **no additional services**. It
does NOT deploy a Python trading bot, create journal tables, install a
persistent always-on worker, enable order submission, or guarantee that
the paper-trading journal is connected.

## Next milestone after successful linking

- Apply and verify a **dedicated, least-privilege** PostgreSQL journal
  schema on a safe database using `bot/paper_store.py` and a migration
  routine. Review access rights first, particularly for the
  `production` branch. Do not blindly run application migrations as a
  side effect of `neon deploy`.
- Store the credential securely as `PAPER_JOURNAL_DSN` in a GitHub
  Actions secret or worker secret store, never in the repo.
- Confirm TLS PostgreSQL connectivity, idempotent transactional
  reservations, journal health and order reconciliation, with dry-run
  tests and a failure-safe watchdog.
- PAPER orders stay **OFF** until those requirements and explicit
  owner authorization have been satisfied.

## Notes for other AI agents

The current cloud model working directory is not the owner's PC
checkout. GitHub connector edits are to repository files, not
Neon project state. The owner requested CLI installation, skills,
MCP, link and deploy, but authorization and an authenticated Neon
deployment have not occurred yet. Never say deployment succeeded based
only on the existence of `neon.ts`.
