"""The encrypted, site-scoped store.

Two layers of encryption (see :mod:`moodle_llm_chat.crypto`):

1. The whole file is SQLCipher-encrypted, covering every table and index.
2. Every content-bearing column is additionally sealed with AES-GCM under a
   per-course (content), per-blob or per-user (chat) data key, bound by AAD to the
   exact row it belongs to. Ids, timestamps, kinds and sizes stay plain so SQL can
   filter and order on them; text never does.

FTS5 is *contentless*: the token index is populated from plaintext at insert time
(protected by layer 1) and no second plaintext copy of the chunk exists. Vectors are
scored with ``vec_distance_cosine`` over exactly the chunks a caller may see, so a
per-user access scope never costs recall.

All methods are synchronous and must be called from the thread that opened the
store (the event loop thread in the API).

Layout: :class:`~moodle_llm_chat.index.store.core.Store` owns the connection and the keys; each
area of the data is a repository on it (``store.sites``, ``store.documents``, ``store.chunks``,
``store.blobs``, ``store.jobs``, ``store.chat``, ``store.usage``, ``store.checkpoints``).
"""

from moodle_llm_chat.index.store.core import Store
from moodle_llm_chat.index.store.errors import StoreError, SyncStateError

__all__ = ["Store", "StoreError", "SyncStateError"]
