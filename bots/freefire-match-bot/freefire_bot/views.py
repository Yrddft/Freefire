from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

import discord

from .presentation import build_record_embed

if TYPE_CHECKING:
    from .bot import MatchBot

logger = logging.getLogger(__name__)


def member_has_approval_role(interaction: discord.Interaction, role_id: int) -> bool:
    if interaction.guild_id is None:
        return False
    roles = getattr(interaction.user, "roles", ())
    return any(getattr(role, "id", None) == role_id for role in roles)


class ApprovalView(discord.ui.View):
    """Persistent buttons for match review posts."""

    def __init__(self, bot: "MatchBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Approve",
        style=discord.ButtonStyle.success,
        custom_id="freefire-record:approve",
    )
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        del button
        if not member_has_approval_role(
            interaction, self.bot.settings.approval_role_id
        ):
            await interaction.response.send_message(
                "You need the configured match-approval role to review records.",
                ephemeral=True,
            )
            return
        if interaction.message is None or interaction.guild_id is None:
            await interaction.response.send_message(
                "This approval post is unavailable.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            pending_record = await self.bot.storage.get_record_by_approval_message(
                interaction.message.id
            )
        except Exception as exc:
            logger.error(
                "Could not load a match record for approval (%s).",
                type(exc).__name__,
            )
            await interaction.followup.send(
                "I couldn't reach the records database. Please try again shortly.",
                ephemeral=True,
            )
            return
        if (
            pending_record is None
            or pending_record["guild_id"] != interaction.guild_id
            or pending_record["status"] != "pending"
        ):
            await interaction.followup.send(
                "This record has already been reviewed or is no longer available.",
                ephemeral=True,
            )
            return

        try:
            record = await self.bot.storage.claim_review(
                interaction.message.id, "approving", interaction.user.id
            )
        except Exception as exc:
            logger.error(
                "Could not claim match record for approval (%s).",
                type(exc).__name__,
            )
            await interaction.followup.send(
                "I couldn't reach the records database. Please try again shortly.",
                ephemeral=True,
            )
            return
        if record is None:
            await interaction.followup.send(
                "This record has already been reviewed or is no longer available.",
                ephemeral=True,
            )
            return

        posted_message: discord.Message | None = None
        try:
            settings = await self.bot.storage.get_guild_settings(record["guild_id"])
            if not settings or not settings.get("record_channel_id"):
                raise RuntimeError("The official records channel has not been set.")
            record_channel = await self.bot.resolve_text_channel(
                settings["record_channel_id"]
            )
            if not interaction.message.attachments:
                raise RuntimeError("The screenshot is missing from the approval post.")

            screenshot_bytes = await interaction.message.attachments[0].read()
            screenshot = discord.File(
                io.BytesIO(screenshot_bytes),
                filename=record["screenshot_filename"],
            )
            posted_message = await record_channel.send(
                embed=build_record_embed(record, status_label="Approved", public=True),
                file=screenshot,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            completed = await self.bot.storage.complete_approval(
                record["id"],
                record_channel.id,
                posted_message.id,
                interaction.user.id,
            )
            if not completed:
                await posted_message.delete()
                posted_message = None
                raise RuntimeError("The record could not be finalized in the database.")
        except Exception as exc:
            if posted_message is not None:
                try:
                    await posted_message.delete()
                except discord.HTTPException:
                    logger.warning(
                        "Could not remove an unfinalized public post for record %s.",
                        record["id"],
                    )
            await self.bot.storage.release_review(record["id"])
            logger.exception("Approval failed for record %s.", record["id"])
            await interaction.followup.send(
                "I couldn't finish approving this record. It remains pending; "
                "please check the configured records channel and try again.",
                ephemeral=True,
            )
            return

        try:
            final_record = await self.bot.storage.get_record(record["id"])
            await interaction.message.edit(
                content=f"Approved by <@{interaction.user.id}>.",
                embed=build_record_embed(
                    final_record or record,
                    status_label="Approved",
                    reviewer_id=interaction.user.id,
                ),
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.warning("Could not update approval post for record %s.", record["id"])

        await interaction.followup.send(
            f"Record #{record['id']} approved and posted to the records channel.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Reject",
        style=discord.ButtonStyle.danger,
        custom_id="freefire-record:reject",
    )
    async def reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        del button
        if not member_has_approval_role(
            interaction, self.bot.settings.approval_role_id
        ):
            await interaction.response.send_message(
                "You need the configured match-approval role to review records.",
                ephemeral=True,
            )
            return
        if interaction.message is None:
            await interaction.response.send_message(
                "This approval post is unavailable.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            RejectReasonModal(self.bot, interaction.message.id)
        )


class RejectReasonModal(discord.ui.Modal, title="Reject match record"):
    reason = discord.ui.TextInput(
        label="Reason for rejection",
        placeholder="Tell the submitter what needs to be corrected.",
        style=discord.TextStyle.paragraph,
        min_length=3,
        max_length=400,
        required=True,
    )

    def __init__(self, bot: "MatchBot", approval_message_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.approval_message_id = approval_message_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        role_id = self.bot.settings.approval_role_id
        if not member_has_approval_role(interaction, role_id):
            await interaction.response.send_message(
                "You need the configured match-approval role to review records.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            pending_record = await self.bot.storage.get_record_by_approval_message(
                self.approval_message_id
            )
        except Exception as exc:
            logger.error(
                "Could not load a match record for rejection (%s).",
                type(exc).__name__,
            )
            await interaction.followup.send(
                "I couldn't reach the records database. Please try again shortly.",
                ephemeral=True,
            )
            return
        if (
            interaction.guild_id is None
            or pending_record is None
            or pending_record["guild_id"] != interaction.guild_id
            or pending_record["status"] != "pending"
        ):
            await interaction.followup.send(
                "This record has already been reviewed or is no longer available.",
                ephemeral=True,
            )
            return

        try:
            record = await self.bot.storage.claim_review(
                self.approval_message_id, "rejecting", interaction.user.id
            )
        except Exception as exc:
            logger.error(
                "Could not claim match record for rejection (%s).",
                type(exc).__name__,
            )
            await interaction.followup.send(
                "I couldn't reach the records database. Please try again shortly.",
                ephemeral=True,
            )
            return
        if record is None:
            await interaction.followup.send(
                "This record has already been reviewed or is no longer available.",
                ephemeral=True,
            )
            return

        reason = str(self.reason.value).strip()
        if len(reason) < 3:
            await self.bot.storage.release_review(record["id"])
            await interaction.followup.send(
                "Please enter a rejection reason with at least three characters.",
                ephemeral=True,
            )
            return

        try:
            completed = await self.bot.storage.complete_rejection(
                record["id"], interaction.user.id, reason
            )
        except Exception as exc:
            await self.bot.storage.release_review(record["id"])
            logger.error(
                "Could not save rejection for record %s (%s).",
                record["id"],
                type(exc).__name__,
            )
            await interaction.followup.send(
                "I couldn't save the rejection. The record remains pending; try again.",
                ephemeral=True,
            )
            return
        if not completed:
            await self.bot.storage.release_review(record["id"])
            await interaction.followup.send(
                "The rejection could not be saved. Please try again.",
                ephemeral=True,
            )
            return

        record = await self.bot.storage.get_record(record["id"]) or record
        try:
            approval_channel = await self.bot.resolve_text_channel(
                record["approval_channel_id"]
            )
            approval_message = await approval_channel.fetch_message(
                record["approval_message_id"]
            )
            await approval_message.edit(
                content=f"Rejected by <@{interaction.user.id}>.",
                embed=build_record_embed(
                    record,
                    status_label="Rejected",
                    reviewer_id=interaction.user.id,
                    rejection_reason=reason,
                ),
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.HTTPException, TypeError):
            logger.warning("Could not update approval post for record %s.", record["id"])

        submitter_note = (
            f"Your Free Fire match record #{record['id']} was rejected.\n"
            f"Reason: {reason}"
        )
        try:
            submitter = self.bot.get_user(record["submitter_id"])
            if submitter is None:
                submitter = await self.bot.fetch_user(record["submitter_id"])
            await submitter.send(submitter_note)
            notice = "The submitter was notified."
        except discord.HTTPException:
            notice = "I couldn't DM the submitter; they can see the reason with /my-records."

        await interaction.followup.send(
            f"Record #{record['id']} rejected. {notice}", ephemeral=True
        )

