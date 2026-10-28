# Free Fire Match Records Bot

A Python Discord bot that collects Free Fire match records, sends them to a
review channel, and publishes approved matches with their screenshots.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- `cd bots/freefire-match-bot && python -m unittest discover -s tests -v` — run bot unit tests
- Deploy the bot as a Render background worker using the repository-root `render.yaml`; do not run it as a persistent Replit workflow
- Set `DISCORD_BOT_TOKEN` as a secret in Render; Render supplies `DATABASE_URL` and `APPROVAL_ROLE_ID` is defined in `render.yaml`

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)
- Bot: Python 3.13, discord.py, asyncpg, and Render Postgres

## Where things live

- `bots/freefire-match-bot/freefire_bot/` — Discord commands, embeds, approval controls, and PostgreSQL access
- `bots/freefire-match-bot/README.md` — bot setup, permissions, and Render deployment steps
- `render.yaml` — Render background worker and Postgres Blueprint

## Architecture decisions

- Records stay pending until a member with the configured approval role approves them.
- PostgreSQL stores record data and Discord message references; approval copies the screenshot into the final post.
- Render is the bot's production host. Set `DISCORD_BOT_TOKEN` in Render and let the Blueprint provide its Postgres `DATABASE_URL`.

## Product

Server members submit match type, player mentions, and a result screenshot. Reviewers
approve or reject submissions in a designated channel. Only approved records appear
in the official records channel; player statistics use those approved records.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

_Populate as you build — sharp edges, "always run X before Y" rules._

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
