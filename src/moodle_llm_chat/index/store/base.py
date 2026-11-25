"""What every repository shares: the store it belongs to and the sealing helpers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from moodle_llm_chat.crypto.envelope import aad, open_box, seal
from moodle_llm_chat.crypto.keys import KeyRing, Scope

if TYPE_CHECKING:
    from moodle_llm_chat.index.store.core import Store


class Repository:
    """One area of the store (sites, documents, chat, ...).

    A repository owns no connection or key: it borrows them from the :class:`Store` that built
    it, so every area shares one transaction scope, one key ring and one thread.
    """

    def __init__(self, db: Store) -> None:
        self._db = db

    @property
    def connection(self) -> Any:
        return self._db.connection

    @property
    def ring(self) -> KeyRing:
        return self._db.ring

    def _seq(self) -> int:
        return self._db.seen_seq()

    def _lookup(self, value: str) -> str:
        return self._db.lookup(value)

    def _course_dek(self, site_id: str, course_id: int) -> bytes:
        return self.ring.dek(self._db, Scope.CONTENT, f"{site_id}:{course_id}")

    def _site_dek(self, site_id: str) -> bytes:
        return self.ring.dek(self._db, Scope.CONTENT, f"site:{site_id}")

    def _seal_json(self, dek: bytes, value: Any, *, bound_to: bytes) -> bytes:
        return seal(dek, json.dumps(value, ensure_ascii=False), aad=bound_to)

    def _open_json(self, dek: bytes, box: bytes, *, bound_to: bytes) -> Any:
        return json.loads(open_box(dek, bytes(box), aad=bound_to))

    def _seal_course_json(
        self, site_id: str, course_id: int, value: Any, *aad_parts: str | int
    ) -> bytes:
        """Seal ``value`` under the course's key, bound to the row named by ``aad_parts``."""
        return self._seal_json(
            self._course_dek(site_id, course_id), value, bound_to=aad(*aad_parts)
        )

    def _open_course_json(
        self, site_id: str, course_id: int, box: bytes, *aad_parts: str | int
    ) -> Any:
        """Open what :meth:`_seal_course_json` sealed."""
        return self._open_json(self._course_dek(site_id, course_id), box, bound_to=aad(*aad_parts))
