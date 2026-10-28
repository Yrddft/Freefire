# Free Fire Match Records Bot

A Python Discord bot for submitting Free Fire match records, reviewing them in a
separate channel, and publishing approved records with their screenshots.

## What it does

- `/record` accepts a match type, up to four Discord members, and a PNG, JPG,
  JPEG, or WEBP screenshot. Empty player slots appear as **Not Found**.
- Submissions go to the configured approval channel. The bot pings the review
  role and adds **Approve** and **Reject** buttons.
- Only members with role ID `1564884044205006849` can approve or reject.
- Approved records are posted to the official records channel in an embed with
  the screenshot. Rejections require a reason and the bot attempts to DM it to
  the submitter.
- `/recent`, `/stats`, and `/my-records` show approved match history, player
  stats, and submission status.
- `/record-edit` edits pending records and can clear a player slot back to
  **Not Found**. Admins can also edit approved records. `/record-delete` removes
  a submitter's pending record; admins can also remove reviewed records.
- `/setup` and `/approval-setup` let server admins choose the two channels.

The bot stores records and channel settings in PostgreSQL. It stores Discord
message IDs and re-uploads the screenshot when a match is approved, so the
approved post has its own image attachment.

## Render deployment

The repository-root `render.yaml` is a Render Blueprint. It defines a
continuously running Python background worker and a Render Postgres database in
the same region. Render supplies the database connection string to the worker
as `DATABASE_URL`.

1. Push this repository to GitHub or another Git provider supported by Render.
2. In Render, create a new Blueprint using this repository and review the
   services and pricing before deploying.
3. Add `DISCORD_BOT_TOKEN` when Render prompts for the secret, or set it in the
   worker's Environment settings. The token saved in Replit is not copied to
   Render automatically.
4. Invite the bot to your server using the `bot` and `applications.commands`
   OAuth scopes. Grant it **View Channels**, **Send Messages**, **Embed Links**,
   **Attach Files**, and **Read Message History** in both configured channels.
   To notify a non-mentionable approval role, also grant **Mention @everyone,
   @here, and All Roles**, or make the approval role mentionable.
5. In Discord, run `/setup` in the official records channel and
   `/approval-setup` in the review channel.

The bot does not need Message Content Intent. Slash commands may take a little
time to appear globally. For development, set `DEV_GUILD_ID` to sync commands to
one server immediately.

The Blueprint creates a dedicated database. If you already have a Render
Postgres database, remove the `databases` block and the `fromDatabase` mapping
from `render.yaml`, then set the worker's `DATABASE_URL` to that database's
internal connection string in Render. Keep the worker and database in the same
region.

### Render plan note

Render background workers do not have a Free plan. The Blueprint uses the
smallest listed always-on worker plan and a small Postgres plan; both are paid.
Render's Free Postgres databases expire after 30 days, so they are not suitable
for long-term match history. Review the current Render pricing before deploying.

## Local development

1. Create a local `.env` from `.env.example` and fill in the bot token and a
   PostgreSQL URL. Do not commit `.env`.
2. Install dependencies with `python -m pip install -r requirements.txt`.
3. Run `python -m freefire_bot` from this directory.

The bot creates its tables and indexes on startup. Keep `DATABASE_URL` pointed
at the Render database in production; do not paste credentials into source code
or chat.

