from __future__ import annotations

from datetime import datetime, timezone
import unittest

import discord

from freefire_bot.bot import MatchBot, MatchCommands
from freefire_bot.config import Settings
from freefire_bot.presentation import build_record_embed
from freefire_bot.storage import _database_connection_options
from freefire_bot.views import ApprovalView


class DatabaseUrlTests(unittest.TestCase):
    def test_render_postgres_url_is_normalized(self) -> None:
        dsn, ssl = _database_connection_options(
            "postgres://bot:secret@db.internal:5432/matches"
        )
        self.assertEqual(
            dsn, "postgresql://bot:secret@db.internal:5432/matches"
        )
        self.assertIsNone(ssl)

    def test_external_postgres_url_requires_ssl_by_default(self) -> None:
        _, ssl = _database_connection_options(
            "postgres://bot:secret@db.example.com:5432/matches"
        )
        self.assertEqual(ssl, "require")

    def test_external_ssl_mode_is_passed_to_asyncpg(self) -> None:
        dsn, ssl = _database_connection_options(
            "postgres://bot:secret@db.example:5432/matches"
            "?sslmode=require&application_name=freefire-bot"
        )
        self.assertEqual(ssl, "require")
        self.assertEqual(
            dsn,
            "postgresql://bot:secret@db.example:5432/matches"
            "?application_name=freefire-bot",
        )


class EmbedTests(unittest.TestCase):
    def test_embed_lists_four_slots_and_missing_players(self) -> None:
        record = {
            "id": 17,
            "match_type": "Guild vs Guild",
            "first_player_id": 123,
            "second_player_id": None,
            "third_player_id": None,
            "fourth_player_id": 456,
            "submitter_id": 789,
            "screenshot_filename": "match-screenshot.png",
            "created_at": datetime.now(timezone.utc),
            "status": "pending",
        }
        embed = build_record_embed(record, status_label="Pending approval")

        self.assertIn("**First:** <@123>", embed.description)
        self.assertIn("**Second:** Not Found", embed.description)
        self.assertIn("**Third:** Not Found", embed.description)
        self.assertIn("**Fourth:** <@456>", embed.description)
        self.assertEqual(
            embed.image.url, "attachment://match-screenshot.png"
        )
        public_embed = build_record_embed(record, status_label="Approved", public=True)
        self.assertIsNone(public_embed.title)
        self.assertTrue(public_embed.description.startswith("**Players:**"))


class SlashCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_commands_register_without_leaderboard(self) -> None:
        settings = Settings(
            discord_bot_token="test-token",
            database_url="postgresql://user:password@localhost/database",
            approval_role_id=1564884044205006849,
            dev_guild_id=None,
        )
        bot = MatchBot(settings)
        try:
            await bot.add_cog(MatchCommands(bot))
            command_names = {command.name for command in bot.tree.get_commands()}
            self.assertEqual(
                command_names,
                {
                    "record",
                    "recent",
                    "stats",
                    "my-records",
                    "record-edit",
                    "record-delete",
                    "setup",
                    "approval-setup",
                    "help",
                },
            )
            self.assertNotIn("leaderboard", command_names)
            for command in bot.tree.get_commands():
                payload = command.to_dict(bot.tree)
                self.assertEqual(payload["name"], command.name)
            approval_view = ApprovalView(bot)
            self.assertIsNone(approval_view.timeout)
            self.assertEqual(
                {item.custom_id for item in approval_view.children},
                {"freefire-record:approve", "freefire-record:reject"},
            )
        finally:
            await bot.close()


if __name__ == "__main__":
    unittest.main()

