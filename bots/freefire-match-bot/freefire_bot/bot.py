from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .config import Settings
from .presentation import PLAYER_LABELS, PLAYER_SLOTS, build_record_embed
from .storage import Storage
from .views import ApprovalView

logger = logging.getLogger("freefire_bot")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024
ALLOWED_SCREENSHOT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MATCH_TYPE_CHOICES = [
    app_commands.Choice(name="Guild vs Guild", value="Guild vs Guild"),
    app_commands.Choice(name="Guild vs Random", value="Guild vs Random"),
]


class MatchCommandTree(app_commands.CommandTree):
    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        logger.error("Slash command failed (%s).", type(error).__name__)
        message = "That command could not be completed. Please try again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            logger.warning("Could not send a slash-command error response.")


class MatchBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            command_prefix="!",
            intents=discord.Intents.default(),
            tree_cls=MatchCommandTree,
        )
        self.settings = settings
        self.storage = Storage(settings.database_url)

    async def setup_hook(self) -> None:
        await self.storage.connect()
        await self.storage.ensure_schema()
        self.add_view(ApprovalView(self))
        await self.add_cog(MatchCommands(self))

        if self.settings.dev_guild_id:
            dev_guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=dev_guild)
            synced = await self.tree.sync(guild=dev_guild)
            logger.info("Synced %s application commands to the development guild.", len(synced))
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s global application commands.", len(synced))

    async def close(self) -> None:
        await self.storage.close()
        await super().close()

    async def resolve_text_channel(self, channel_id: int) -> discord.TextChannel:
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise TypeError("The configured channel is not a server text channel.")
        return channel


class MatchCommands(commands.Cog):
    def __init__(self, bot: MatchBot) -> None:
        self.bot = bot

    @staticmethod
    def _guild_id(interaction: discord.Interaction) -> int | None:
        return interaction.guild_id

    @staticmethod
    def _is_admin(interaction: discord.Interaction) -> bool:
        permissions = getattr(interaction.user, "guild_permissions", None)
        return bool(
            permissions
            and (permissions.administrator or permissions.manage_guild)
        )

    @staticmethod
    async def _reply(
        interaction: discord.Interaction, content: str, *, ephemeral: bool = True
    ) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)

    @staticmethod
    def _validate_screenshot(attachment: discord.Attachment) -> str:
        extension = Path(attachment.filename).suffix.lower()
        if extension not in ALLOWED_SCREENSHOT_EXTENSIONS:
            raise ValueError("Upload a PNG, JPG, JPEG, or WEBP match screenshot.")
        if attachment.size > MAX_SCREENSHOT_BYTES:
            raise ValueError("The screenshot must be 10 MB or smaller.")
        if attachment.content_type and not attachment.content_type.startswith("image/"):
            raise ValueError("The uploaded file must be an image.")
        return f"match-screenshot{extension}"

    async def _load_screenshot(
        self, attachment: discord.Attachment
    ) -> tuple[str, bytes]:
        filename = self._validate_screenshot(attachment)
        data = await attachment.read()
        if len(data) > MAX_SCREENSHOT_BYTES:
            raise ValueError("The screenshot must be 10 MB or smaller.")
        return filename, data

    @app_commands.command(
        name="record",
        description="Submit a Free Fire match and screenshot for approval.",
    )
    @app_commands.describe(
        match_type="Choose the kind of official match.",
        screenshot="Attach the Free Fire match-result screenshot.",
        first="Player in the first slot; leave empty to show Not Found.",
        second="Player in the second slot; leave empty to show Not Found.",
        third="Player in the third slot; leave empty to show Not Found.",
        fourth="Player in the fourth slot; leave empty to show Not Found.",
    )
    @app_commands.choices(match_type=MATCH_TYPE_CHOICES)
    async def record(
        self,
        interaction: discord.Interaction,
        match_type: str,
        screenshot: discord.Attachment,
        first: discord.Member | None = None,
        second: discord.Member | None = None,
        third: discord.Member | None = None,
        fourth: discord.Member | None = None,
    ) -> None:
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await self._reply(interaction, "Use this command inside your server.")
            return

        settings = await self.bot.storage.get_guild_settings(guild_id)
        if not settings or not settings.get("approval_channel_id"):
            await self._reply(
                interaction,
                "An admin needs to set the approval channel with /approval-setup first.",
            )
            return
        if not settings.get("record_channel_id"):
            await self._reply(
                interaction,
                "An admin needs to set the official records channel with /setup first.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            filename, screenshot_bytes = await self._load_screenshot(screenshot)
        except (ValueError, discord.HTTPException) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        players: dict[str, tuple[int, str] | None] = {}
        for slot, member in zip(
            PLAYER_SLOTS, (first, second, third, fourth), strict=True
        ):
            players[slot] = (
                (member.id, member.display_name) if member is not None else None
            )

        record = await self.bot.storage.create_record(
            guild_id=guild_id,
            submitter_id=interaction.user.id,
            match_type=match_type,
            players=players,
            screenshot_filename=filename,
        )

        approval_message: discord.Message | None = None
        try:
            approval_channel = await self.bot.resolve_text_channel(
                settings["approval_channel_id"]
            )
            approval_message = await approval_channel.send(
                content=f"<@&{self.bot.settings.approval_role_id}> "
                f"New match record #{record['id']} needs review.",
                embed=build_record_embed(record, status_label="Pending approval"),
                file=discord.File(io.BytesIO(screenshot_bytes), filename=filename),
                view=ApprovalView(self.bot),
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=True,
                    replied_user=False,
                ),
            )
            await self.bot.storage.attach_approval_message(
                record["id"], approval_channel.id, approval_message.id
            )
        except Exception as exc:
            if approval_message is not None:
                try:
                    await approval_message.delete()
                except discord.HTTPException:
                    logger.warning(
                        "Could not remove failed approval post for record %s.",
                        record["id"],
                    )
            await self.bot.storage.delete_record(record["id"])
            logger.error(
                "Could not submit record %s (%s).",
                record["id"],
                type(exc).__name__,
            )
            await interaction.followup.send(
                "I couldn't send this record to the approval channel. "
                "Please check the bot's channel permissions and try again.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Match record #{record['id']} was submitted for approval.",
            ephemeral=True,
        )

    @app_commands.command(
        name="recent", description="Show the latest approved match records."
    )
    @app_commands.describe(limit="How many approved records to show (1–10).")
    async def recent(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 10] = 5,
    ) -> None:
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await self._reply(interaction, "Use this command inside your server.")
            return

        records = await self.bot.storage.list_recent(guild_id, limit)
        if not records:
            await self._reply(interaction, "There are no approved match records yet.")
            return

        lines = []
        for record in records:
            when = int(record["created_at"].timestamp())
            player_entries = []
            for slot in PLAYER_SLOTS:
                player_id = record[f"{slot}_player_id"]
                player_name = f"<@{player_id}>" if player_id else "Not Found"
                player_entries.append(f"{PLAYER_LABELS[slot]}: {player_name}")
            players = ", ".join(player_entries)
            lines.append(
                f"**#{record['id']} · {record['match_type']}** · <t:{when}:R>\n{players}"
            )
        embed = discord.Embed(
            title="Recent approved match records",
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="stats", description="Show approved match stats for a player."
    )
    async def stats(
        self, interaction: discord.Interaction, player: discord.Member
    ) -> None:
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await self._reply(interaction, "Use this command inside your server.")
            return

        stats = await self.bot.storage.get_stats(guild_id, player.id)
        embed = discord.Embed(
            title=f"Match stats · {player.display_name}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Approved matches", value=str(stats["games"]), inline=False)
        for slot in PLAYER_SLOTS:
            embed.add_field(
                name=PLAYER_LABELS[slot],
                value=str(stats[f"{slot}_count"]),
                inline=True,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="my-records", description="Check your recent submission statuses."
    )
    async def my_records(self, interaction: discord.Interaction) -> None:
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await self._reply(interaction, "Use this command inside your server.")
            return

        records = await self.bot.storage.list_user_records(
            guild_id, interaction.user.id, limit=10
        )
        if not records:
            await self._reply(interaction, "You haven't submitted any match records.")
            return

        lines = []
        for record in records:
            status = record["status"].replace("_", " ").title()
            line = (
                f"**#{record['id']} · {record['match_type']}** — {status} "
                f"(<t:{int(record['created_at'].timestamp())}:R>)"
            )
            if record["rejection_reason"]:
                line += f"\nReason: {record['rejection_reason'][:160]}"
            lines.append(line)
        embed = discord.Embed(
            title="Your recent match records",
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="record-edit",
        description="Edit a pending record or, as an admin, an approved record.",
    )
    @app_commands.describe(
        record_id="The number shown in the record's embed.",
        match_type="Change the match type.",
        screenshot="Replace the screenshot.",
        first="Set the first player.",
        second="Set the second player.",
        third="Set the third player.",
        fourth="Set the fourth player.",
        clear_first="Clear the first slot to Not Found.",
        clear_second="Clear the second slot to Not Found.",
        clear_third="Clear the third slot to Not Found.",
        clear_fourth="Clear the fourth slot to Not Found.",
    )
    @app_commands.choices(match_type=MATCH_TYPE_CHOICES)
    async def record_edit(
        self,
        interaction: discord.Interaction,
        record_id: int,
        match_type: str | None = None,
        screenshot: discord.Attachment | None = None,
        first: discord.Member | None = None,
        second: discord.Member | None = None,
        third: discord.Member | None = None,
        fourth: discord.Member | None = None,
        clear_first: bool = False,
        clear_second: bool = False,
        clear_third: bool = False,
        clear_fourth: bool = False,
    ) -> None:
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await self._reply(interaction, "Use this command inside your server.")
            return

        record = await self.bot.storage.get_record(record_id)
        if not record or record["guild_id"] != guild_id:
            await self._reply(interaction, f"Record #{record_id} was not found.")
            return
        is_admin = self._is_admin(interaction)
        is_pending = record["status"] == "pending"
        is_approved = record["status"] == "approved"
        if not is_pending and not is_approved:
            await self._reply(
                interaction,
                "Only pending or approved records can be edited.",
            )
            return
        if is_pending and record["submitter_id"] != interaction.user.id and not is_admin:
            await self._reply(
                interaction, "You can only edit your own pending records."
            )
            return
        if is_approved and not is_admin:
            await self._reply(
                interaction, "Only server admins can edit approved records."
            )
            return

        updates: dict[str, Any] = {}
        if match_type is not None:
            updates["match_type"] = match_type
        for slot, member, clear_slot in zip(
            PLAYER_SLOTS,
            (first, second, third, fourth),
            (clear_first, clear_second, clear_third, clear_fourth),
            strict=True,
        ):
            if member is not None and clear_slot:
                await self._reply(
                    interaction,
                    f"Choose a player or clear {PLAYER_LABELS[slot]}, not both.",
                )
                return
            if clear_slot:
                updates[f"{slot}_player_id"] = None
                updates[f"{slot}_player_name"] = None
            elif member is not None:
                updates[f"{slot}_player_id"] = member.id
                updates[f"{slot}_player_name"] = member.display_name

        if not updates and screenshot is None:
            await self._reply(interaction, "Choose at least one field to change.")
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        screenshot_bytes: bytes | None = None
        if screenshot is not None:
            try:
                filename, screenshot_bytes = await self._load_screenshot(screenshot)
            except (ValueError, discord.HTTPException) as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            updates["screenshot_filename"] = filename

        message_channel_id = (
            record["record_channel_id"]
            if is_approved
            else record["approval_channel_id"]
        )
        message_id = (
            record["record_message_id"]
            if is_approved
            else record["approval_message_id"]
        )
        if not message_channel_id or not message_id:
            await interaction.followup.send(
                "The Discord post for this record is missing, so it could not be edited.",
                ephemeral=True,
            )
            return

        try:
            message_channel = await self.bot.resolve_text_channel(message_channel_id)
            record_message = await message_channel.fetch_message(message_id)
        except (discord.HTTPException, TypeError):
            await interaction.followup.send(
                "I couldn't load the Discord post. The record was not changed.",
                ephemeral=True,
            )
            return

        updated_record = await self.bot.storage.update_record(
            record_id,
            updates,
            expected_status=record["status"],
        )
        if updated_record is None:
            await interaction.followup.send(
                "This record changed status while you were editing it. Please retry.",
                ephemeral=True,
            )
            return

        try:
            status_label = "Approved" if is_approved else "Pending approval"
            embed = build_record_embed(
                updated_record,
                status_label=status_label,
                public=is_approved,
            )
            if screenshot_bytes is not None:
                await record_message.edit(
                    embed=embed,
                    attachments=[
                        discord.File(
                            io.BytesIO(screenshot_bytes),
                            filename=updated_record["screenshot_filename"],
                        )
                    ],
                )
            else:
                await record_message.edit(embed=embed)
        except (discord.HTTPException, TypeError):
            logger.warning("Could not refresh Discord post for record %s.", record_id)
            if screenshot_bytes is not None:
                try:
                    await self.bot.storage.update_record(
                        record_id,
                        {"screenshot_filename": record["screenshot_filename"]},
                        expected_status=record["status"],
                    )
                except Exception as exc:
                    logger.error(
                        "Could not restore screenshot metadata for record %s (%s).",
                        record_id,
                        type(exc).__name__,
                    )
            await interaction.followup.send(
                "The record details were saved, but I couldn't refresh its Discord "
                "post. If you replaced the screenshot, that image change was reverted. "
                "Check the bot's channel permissions and retry.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Record #{record_id} was updated.", ephemeral=True
        )

    @app_commands.command(
        name="record-delete", description="Delete one of your pending records."
    )
    @app_commands.describe(record_id="The number shown in the record's embed.")
    async def record_delete(
        self, interaction: discord.Interaction, record_id: int
    ) -> None:
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await self._reply(interaction, "Use this command inside your server.")
            return

        record = await self.bot.storage.get_record(record_id)
        if not record or record["guild_id"] != guild_id:
            await self._reply(interaction, f"Record #{record_id} was not found.")
            return

        is_admin = self._is_admin(interaction)
        is_submitter = record["submitter_id"] == interaction.user.id
        if not is_admin and not (is_submitter and record["status"] == "pending"):
            await self._reply(
                interaction,
                "You can delete your own pending records. Only admins can delete "
                "records that have already been reviewed.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if record["approval_channel_id"] and record["approval_message_id"]:
                await self._delete_message(
                    record["approval_channel_id"], record["approval_message_id"]
                )
            if record["record_channel_id"] and record["record_message_id"]:
                await self._delete_message(
                    record["record_channel_id"], record["record_message_id"]
                )
        except discord.HTTPException:
            await interaction.followup.send(
                "I couldn't remove a Discord post, so the database record was kept. "
                "Check the bot's channel permissions and try again.",
                ephemeral=True,
            )
            return

        await self.bot.storage.delete_record(record_id)
        await interaction.followup.send(
            f"Match record #{record_id} was deleted.", ephemeral=True
        )

    async def _delete_message(self, channel_id: int, message_id: int) -> None:
        channel = await self.bot.resolve_text_channel(channel_id)
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            return
        await message.delete()

    @app_commands.command(
        name="setup",
        description="Choose the channel for approved match records (admin only).",
    )
    async def setup(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await self._reply(interaction, "Use this command inside your server.")
            return
        if not self._is_admin(interaction):
            await self._reply(interaction, "Only server admins can configure channels.")
            return

        await self.bot.storage.set_channel(
            guild_id, "record_channel_id", channel.id
        )
        await interaction.response.send_message(
            f"Approved match records will be posted in {channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="approval-setup",
        description="Choose the channel for pending approvals (admin only).",
    )
    async def approval_setup(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await self._reply(interaction, "Use this command inside your server.")
            return
        if not self._is_admin(interaction):
            await self._reply(interaction, "Only server admins can configure channels.")
            return

        await self.bot.storage.set_channel(
            guild_id, "approval_channel_id", channel.id
        )
        await interaction.response.send_message(
            f"Pending records will be sent to {channel.mention}. "
            f"Only members with role ID {self.bot.settings.approval_role_id} "
            "can approve or reject them.",
            ephemeral=True,
        )

    @app_commands.command(name="help", description="Show the match-record bot commands.")
    async def help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Free Fire match-record commands",
            description=(
                "`/record` — submit a match and screenshot for approval.\n"
                "`/recent` — view recent approved records.\n"
                "`/stats <player>` — view stats from approved records.\n"
                "`/my-records` — check your recent submission statuses.\n"
                "`/record-edit <record_id>` — edit your pending record; admins can edit approved records.\n"
                "`/record-delete <record_id>` — delete your pending record.\n"
                "`/setup <channel>` — admin: choose the approved-records channel.\n"
                "`/approval-setup <channel>` — admin: choose the review channel."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def run() -> None:
    settings = Settings.from_environment()
    bot = MatchBot(settings)
    async with bot:
        await bot.start(settings.discord_bot_token)

