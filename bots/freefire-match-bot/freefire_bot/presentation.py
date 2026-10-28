from __future__ import annotations

import discord

PLAYER_SLOTS = ("first", "second", "third", "fourth")
PLAYER_LABELS = {
    "first": "First",
    "second": "Second",
    "third": "Third",
    "fourth": "Fourth",
}


def player_text(record: dict, slot: str) -> str:
    player_id = record.get(f"{slot}_player_id")
    if player_id:
        return f"<@{player_id}>"
    return "Not Found"


def build_record_embed(
    record: dict,
    *,
    status_label: str | None = None,
    reviewer_id: int | None = None,
    rejection_reason: str | None = None,
    public: bool = False,
) -> discord.Embed:
    """Build the match post and its review-state variants."""
    record_id = record["id"]
    match_type = record["match_type"]
    players_section = "\n".join(
        f"**{PLAYER_LABELS[slot]}:** {player_text(record, slot)}"
        for slot in PLAYER_SLOTS
    )
    description = (
        f"**Players:**\n{players_section}"
        if public
        else f"**Match type:** {match_type}\n\n**Players:**\n{players_section}"
    )

    status = status_label
    if status is None:
        status = str(record.get("status", "pending")).replace("_", " ").title()

    if status.lower() in {"pending", "pending approval", "approving", "rejecting"}:
        color = discord.Color.gold()
        title = f"Match record #{record_id} · Pending approval"
    elif status.lower() == "rejected":
        color = discord.Color.red()
        title = f"Match record #{record_id} · Rejected"
    else:
        color = discord.Color.blurple()
        title = f"Match record #{record_id}"

    embed = discord.Embed(
        title=None if public else title,
        description=description,
        color=color,
        timestamp=record.get("created_at"),
    )

    if not public:
        embed.add_field(
            name="Submitted by",
            value=f"<@{record['submitter_id']}>",
            inline=True,
        )
        if reviewer_id:
            embed.add_field(
                name="Reviewed by",
                value=f"<@{reviewer_id}>",
                inline=True,
            )
        if status_label:
            embed.add_field(name="Status", value=status_label, inline=True)
        if rejection_reason:
            embed.add_field(
                name="Rejection reason",
                value=rejection_reason[:1024],
                inline=False,
            )

    footer = (
        f"Free Fire match records · {match_type} · #{record_id}"
        if public
        else "Free Fire match records"
    )
    embed.set_footer(text=footer)
    filename = record.get("screenshot_filename")
    if filename:
        embed.set_image(url=f"attachment://{filename}")
    return embed

