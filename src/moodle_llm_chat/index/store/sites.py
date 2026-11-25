"""Registered Moodle sites: secrets, token replay protection and per-site settings."""

from __future__ import annotations

import hmac
import secrets
from typing import Any

from moodle_llm_chat.crypto.envelope import aad, open_box, seal
from moodle_llm_chat.index.models import Site
from moodle_llm_chat.index.store.base import Repository
from moodle_llm_chat.index.store.errors import StoreError
from moodle_llm_chat.index.store.helpers import resolve_now


class SiteRepo(Repository):
    """Registered Moodle sites: secrets, token replay protection and per-site settings."""

    def add_site(self, site_id: str, *, name: str, url: str, now: int | None = None) -> str:
        """Register a Moodle site; returns its secret (shown once to the operator)."""
        if self.connection.execute("SELECT 1 FROM sites WHERE site_id=?", (site_id,)).fetchone():
            raise StoreError(f"site {site_id!r} already exists")
        secret = f"mlc_{secrets.token_urlsafe(32)}"
        sealed = seal(self._site_dek(site_id), secret, aad=aad("sites", site_id, "secret"))
        with self.connection as con:
            con.execute(
                "INSERT INTO sites (site_id, name, url, secret_lookup, sealed_secret, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (site_id, name, url.rstrip("/"), self._lookup(secret), sealed, resolve_now(now)),
            )
        return secret

    def rotate_site_secret(self, site_id: str, *, grace_s: int, now: int | None = None) -> str:
        row = self._site_row(site_id)
        secret = f"mlc_{secrets.token_urlsafe(32)}"
        sealed = seal(self._site_dek(site_id), secret, aad=aad("sites", site_id, "secret"))
        old_plain = open_box(
            self._site_dek(site_id),
            bytes(row["sealed_secret"]),
            aad=aad("sites", site_id, "secret"),
        )
        sealed_prev = seal(self._site_dek(site_id), old_plain, aad=aad("sites", site_id, "prev"))
        with self.connection as con:
            con.execute(
                "UPDATE sites SET secret_lookup=?, sealed_secret=?, prev_secret_lookup=?, "
                "sealed_prev_secret=?, prev_valid_until=? WHERE site_id=?",
                (
                    self._lookup(secret),
                    sealed,
                    row["secret_lookup"],
                    sealed_prev,
                    resolve_now(now) + grace_s,
                    site_id,
                ),
            )
        return secret

    def revoke_site(self, site_id: str, *, now: int | None = None) -> None:
        self._site_row(site_id)
        with self.connection as con:
            con.execute(
                "UPDATE sites SET revoked_at=? WHERE site_id=?", (resolve_now(now), site_id)
            )

    def authenticate_site(self, secret: str, *, now: int | None = None) -> Site | None:
        """The site this secret belongs to, or ``None``. Lookup is by keyed hash."""
        lookup = self._lookup(secret)
        stamp = resolve_now(now)
        row = self.connection.execute(
            "SELECT * FROM sites WHERE revoked_at IS NULL AND (secret_lookup=? OR "
            "(prev_secret_lookup=? AND prev_valid_until >= ?))",
            (lookup, lookup, stamp),
        ).fetchone()
        if row is None:
            return None
        # Defence in depth: compare in constant time even after the indexed lookup.
        if not (
            hmac.compare_digest(row["secret_lookup"], lookup)
            or hmac.compare_digest(row["prev_secret_lookup"] or "", lookup)
        ):
            return None
        return self._to_site(row)

    def touch_site(self, site_id: str, *, now: int | None = None) -> None:
        with self.connection as con:
            con.execute(
                "UPDATE sites SET last_seen_at=? WHERE site_id=?", (resolve_now(now), site_id)
            )

    def consume_jti(
        self, site_id: str, jti: str, expires_at: int, *, now: int | None = None
    ) -> bool:
        """Record a chat token id as used; ``False`` if it was used before (replay)."""
        stamp = resolve_now(now)
        with self.connection as con:
            con.execute("DELETE FROM used_jtis WHERE expires_at < ?", (stamp - 300,))
            cur = con.execute(
                "INSERT OR IGNORE INTO used_jtis (site_id, jti, expires_at) VALUES (?,?,?)",
                (site_id, jti, expires_at),
            )
        return bool(cur.rowcount)

    def site(self, site_id: str) -> Site:
        return self._to_site(self._site_row(site_id))

    def sites(self) -> list[Site]:
        return [
            self._to_site(r)
            for r in self.connection.execute("SELECT * FROM sites ORDER BY created_at, site_id")
        ]

    def site_secret(self, site_id: str) -> str:
        """The current secret, for authenticating callbacks to the plugin."""
        row = self._site_row(site_id)
        return open_box(
            self._site_dek(site_id),
            bytes(row["sealed_secret"]),
            aad=aad("sites", site_id, "secret"),
        )

    def site_secrets(self, site_id: str, *, now: int | None = None) -> list[str]:
        """Current secret first, then the previous one while its grace period lasts."""
        row = self._site_row(site_id)
        result = [self.site_secret(site_id)]
        if row["sealed_prev_secret"] is not None and (row["prev_valid_until"] or 0) >= resolve_now(
            now
        ):
            result.append(
                open_box(
                    self._site_dek(site_id),
                    bytes(row["sealed_prev_secret"]),
                    aad=aad("sites", site_id, "prev"),
                )
            )
        return result

    def _site_row(self, site_id: str) -> Any:
        row = self.connection.execute("SELECT * FROM sites WHERE site_id=?", (site_id,)).fetchone()
        if row is None:
            raise StoreError(f"no such site: {site_id!r}")
        return row

    @staticmethod
    def _to_site(row: Any) -> Site:
        return Site(
            site_id=row["site_id"],
            name=row["name"],
            url=row["url"],
            created_at=row["created_at"],
            revoked_at=row["revoked_at"],
        )

    def _site_json(self, site_id: str, column: str) -> dict[str, Any]:
        row = self._site_row(site_id)
        raw = row[column]
        if not raw:
            return {}
        value: dict[str, Any] = self._open_json(
            self._site_dek(site_id), raw, bound_to=aad("sites", site_id, column)
        )
        return value

    def _set_site_json(self, site_id: str, column: str, value: dict[str, Any]) -> None:
        self._site_row(site_id)
        sealed = self._seal_json(
            self._site_dek(site_id), value, bound_to=aad("sites", site_id, column)
        )
        with self.connection as con:
            con.execute(f"UPDATE sites SET {column}=? WHERE site_id=?", (sealed, site_id))

    def site_policy(self, site_id: str) -> dict[str, Any]:
        return self._site_json(site_id, "policy")

    def set_site_policy(self, site_id: str, policy: dict[str, Any]) -> None:
        self._set_site_json(site_id, "policy", policy)

    def site_ceilings(self, site_id: str) -> dict[str, Any]:
        return self._site_json(site_id, "ceilings")

    def set_site_ceilings(self, site_id: str, ceilings: dict[str, Any]) -> None:
        self._set_site_json(site_id, "ceilings", ceilings)
