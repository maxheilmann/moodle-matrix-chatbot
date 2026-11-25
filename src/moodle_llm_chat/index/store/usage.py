"""Token usage metering for quotas."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from moodle_llm_chat.index.store.base import Repository
from moodle_llm_chat.index.store.helpers import placeholders


class UsageRepo(Repository):
    """Token usage metering for quotas."""

    def record_usage(
        self,
        site_id: str,
        *,
        user_id: int | None,
        conversation_id: str | None,
        turn_id: str | None,
        purpose: str,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        estimated: bool,
        ts: int,
    ) -> None:
        with self.connection as con:
            con.execute(
                "INSERT INTO usage (site_id, user_id, conversation_id, turn_id, purpose, "
                "input_tokens, output_tokens, reasoning_tokens, estimated, ts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    site_id,
                    user_id,
                    conversation_id,
                    turn_id,
                    purpose,
                    input_tokens,
                    output_tokens,
                    reasoning_tokens,
                    int(estimated),
                    ts,
                ),
            )

    def usage_rows(
        self,
        site_id: str,
        *,
        since: int,
        until: int,
        purposes: Collection[str],
        user_id: int | None = None,
    ) -> list[Any]:
        sql = (
            "SELECT user_id, input_tokens, output_tokens, reasoning_tokens FROM usage "
            f"WHERE site_id=? AND ts>=? AND ts<? AND purpose IN ({placeholders(purposes)})"
        )
        params: list[Any] = [site_id, since, until, *purposes]
        if user_id is not None:
            sql += " AND user_id=?"
            params.append(user_id)
        return list(self.connection.execute(sql, params).fetchall())
