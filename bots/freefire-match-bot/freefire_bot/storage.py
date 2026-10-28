from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

PLAYER_SLOTS = ("first", "second", "third", "fourth")
EDITABLE_COLUMNS = {
    "match_type": "match_type",
    "screenshot_filename": "screenshot_filename",
    **{
        f"{slot}_player_{part}": f"{slot}_player_{part}"
        for slot in PLAYER_SLOTS
        for part in ("id", "name")
    },
}


def _database_connection_options(database_url: str) -> tuple[str, str | None]:
    """Normalize Render's postgres:// URL and move sslmode to asyncpg's ssl option."""
    parsed = urlsplit(database_url)
    scheme = "postgresql" if parsed.scheme == "postgres" else parsed.scheme
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    sslmode: str | None = None
    remaining_query: list[tuple[str, str]] = []
    for key, value in query_items:
        if key == "sslmode":
            sslmode = value
        else:
            remaining_query.append((key, value))
    normalized_url = urlunsplit(
        (scheme, parsed.netloc, parsed.path, urlencode(remaining_query), parsed.fragment)
    )
    if sslmode:
        ssl = None if sslmode == "disable" else sslmode
    else:
        host = (parsed.hostname or "").lower()
        ssl = (
            None
            if host in {"localhost", "127.0.0.1", "::1"}
            or host.endswith(".internal")
            else "require"
        )
    return normalized_url, ssl


class Storage:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        dsn, ssl = _database_connection_options(self.database_url)
        try:
            self.pool = await asyncpg.create_pool(
                dsn=dsn,
                ssl=ssl,
                min_size=1,
                max_size=5,
                command_timeout=30,
            )
        except Exception as exc:
            raise RuntimeError(
                "Could not connect to PostgreSQL. Check the Render DATABASE_URL."
            ) from exc

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("The database pool has not been opened.")
        return self.pool

    async def ensure_schema(self) -> None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    record_channel_id BIGINT,
                    approval_channel_id BIGINT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS match_records (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    submitter_id BIGINT NOT NULL,
                    match_type TEXT NOT NULL,
                    first_player_id BIGINT,
                    first_player_name TEXT,
                    second_player_id BIGINT,
                    second_player_name TEXT,
                    third_player_id BIGINT,
                    third_player_name TEXT,
                    fourth_player_id BIGINT,
                    fourth_player_name TEXT,
                    screenshot_filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN (
                            'pending', 'approving', 'approved',
                            'rejecting', 'rejected'
                        )),
                    approval_channel_id BIGINT,
                    approval_message_id BIGINT UNIQUE,
                    record_channel_id BIGINT,
                    record_message_id BIGINT UNIQUE,
                    decision_user_id BIGINT,
                    rejection_reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    decided_at TIMESTAMPTZ
                )
                """
            )
            await connection.execute(
                """
                CREATE INDEX IF NOT EXISTS match_records_guild_status_created_idx
                ON match_records (guild_id, status, created_at DESC)
                """
            )
            await connection.execute(
                """
                CREATE INDEX IF NOT EXISTS match_records_submitter_created_idx
                ON match_records (guild_id, submitter_id, created_at DESC)
                """
            )
            for slot in PLAYER_SLOTS:
                await connection.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS match_records_{slot}_player_idx
                    ON match_records (guild_id, {slot}_player_id, status)
                    """
                )

    async def get_guild_settings(self, guild_id: int) -> dict[str, Any] | None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM guild_settings WHERE guild_id = $1", guild_id
            )
        return dict(row) if row else None

    async def set_channel(
        self, guild_id: int, column: str, channel_id: int
    ) -> None:
        if column not in {"record_channel_id", "approval_channel_id"}:
            raise ValueError("Unsupported guild channel setting.")
        pool = self._require_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                f"""
                INSERT INTO guild_settings (guild_id, {column})
                VALUES ($1, $2)
                ON CONFLICT (guild_id) DO UPDATE
                SET {column} = EXCLUDED.{column}, updated_at = NOW()
                """,
                guild_id,
                channel_id,
            )

    async def create_record(
        self,
        *,
        guild_id: int,
        submitter_id: int,
        match_type: str,
        players: dict[str, tuple[int, str] | None],
        screenshot_filename: str,
    ) -> dict[str, Any]:
        pool = self._require_pool()
        player_values: list[int | str | None] = []
        for slot in PLAYER_SLOTS:
            player = players.get(slot)
            player_values.extend(player if player else (None, None))

        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO match_records (
                    guild_id, submitter_id, match_type,
                    first_player_id, first_player_name,
                    second_player_id, second_player_name,
                    third_player_id, third_player_name,
                    fourth_player_id, fourth_player_name,
                    screenshot_filename
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
                )
                RETURNING *
                """,
                guild_id,
                submitter_id,
                match_type,
                *player_values,
                screenshot_filename,
            )
        return dict(row)

    async def attach_approval_message(
        self, record_id: int, channel_id: int, message_id: int
    ) -> None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE match_records
                SET approval_channel_id = $2, approval_message_id = $3
                WHERE id = $1
                """,
                record_id,
                channel_id,
                message_id,
            )

    async def get_record(self, record_id: int) -> dict[str, Any] | None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM match_records WHERE id = $1", record_id
            )
        return dict(row) if row else None

    async def get_record_by_approval_message(
        self, message_id: int
    ) -> dict[str, Any] | None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM match_records WHERE approval_message_id = $1",
                message_id,
            )
        return dict(row) if row else None

    async def list_recent(self, guild_id: int, limit: int) -> list[dict[str, Any]]:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT * FROM match_records
                WHERE guild_id = $1 AND status = 'approved'
                ORDER BY created_at DESC
                LIMIT $2
                """,
                guild_id,
                limit,
            )
        return [dict(row) for row in rows]

    async def get_stats(self, guild_id: int, player_id: int) -> dict[str, int]:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT
                    COUNT(*) AS games,
                    COUNT(*) FILTER (WHERE first_player_id = $2) AS first_count,
                    COUNT(*) FILTER (WHERE second_player_id = $2) AS second_count,
                    COUNT(*) FILTER (WHERE third_player_id = $2) AS third_count,
                    COUNT(*) FILTER (WHERE fourth_player_id = $2) AS fourth_count
                FROM match_records
                WHERE guild_id = $1 AND status = 'approved'
                    AND (
                        first_player_id = $2 OR second_player_id = $2
                        OR third_player_id = $2 OR fourth_player_id = $2
                    )
                """,
                guild_id,
                player_id,
            )
        return {key: int(row[key]) for key in row.keys()}

    async def list_user_records(
        self, guild_id: int, submitter_id: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT id, match_type, status, rejection_reason, created_at
                FROM match_records
                WHERE guild_id = $1 AND submitter_id = $2
                ORDER BY created_at DESC
                LIMIT $3
                """,
                guild_id,
                submitter_id,
                limit,
            )
        return [dict(row) for row in rows]

    async def update_record(
        self,
        record_id: int,
        updates: dict[str, Any],
        *,
        expected_status: str,
    ) -> dict[str, Any] | None:
        if expected_status not in {"pending", "approved"}:
            return None
        pool = self._require_pool()
        safe_updates = {
            EDITABLE_COLUMNS[column]: value
            for column, value in updates.items()
            if column in EDITABLE_COLUMNS
        }
        if not safe_updates:
            return await self.get_record(record_id)

        assignments: list[str] = []
        values: list[Any] = [record_id, expected_status]
        for column, value in safe_updates.items():
            values.append(value)
            assignments.append(f"{column} = ${len(values)}")

        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                UPDATE match_records
                SET {", ".join(assignments)}
                WHERE id = $1 AND status = $2
                RETURNING *
                """,
                *values,
            )
        return dict(row) if row else None

    async def claim_review(
        self, approval_message_id: int, new_status: str, reviewer_id: int
    ) -> dict[str, Any] | None:
        if new_status not in {"approving", "rejecting"}:
            raise ValueError("Invalid review transition.")
        pool = self._require_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE match_records
                SET status = $2, decision_user_id = $3
                WHERE approval_message_id = $1 AND status = 'pending'
                RETURNING *
                """,
                approval_message_id,
                new_status,
                reviewer_id,
            )
        return dict(row) if row else None

    async def complete_approval(
        self,
        record_id: int,
        record_channel_id: int,
        record_message_id: int,
        reviewer_id: int,
    ) -> bool:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE match_records
                SET status = 'approved', record_channel_id = $2,
                    record_message_id = $3, decision_user_id = $4,
                    decided_at = NOW()
                WHERE id = $1 AND status = 'approving'
                """,
                record_id,
                record_channel_id,
                record_message_id,
                reviewer_id,
            )
        return result == "UPDATE 1"

    async def complete_rejection(
        self, record_id: int, reviewer_id: int, reason: str
    ) -> bool:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE match_records
                SET status = 'rejected', decision_user_id = $2,
                    rejection_reason = $3, decided_at = NOW()
                WHERE id = $1 AND status = 'rejecting'
                """,
                record_id,
                reviewer_id,
                reason,
            )
        return result == "UPDATE 1"

    async def release_review(self, record_id: int) -> None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE match_records
                SET status = 'pending', decision_user_id = NULL
                WHERE id = $1 AND status IN ('approving', 'rejecting')
                """,
                record_id,
            )

    async def delete_record(self, record_id: int) -> dict[str, Any] | None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "DELETE FROM match_records WHERE id = $1 RETURNING *", record_id
            )
        return dict(row) if row else None

