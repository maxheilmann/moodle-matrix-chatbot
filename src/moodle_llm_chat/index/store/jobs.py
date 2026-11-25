"""The processing pipeline's persisted job queue."""

from __future__ import annotations

from typing import Any

from moodle_llm_chat.index.models import Job
from moodle_llm_chat.index.store.base import Repository
from moodle_llm_chat.index.store.errors import StoreError
from moodle_llm_chat.index.store.helpers import placeholders, resolve_now


class JobRepo(Repository):
    """The processing pipeline's persisted job queue."""

    def requeue_failed_jobs(
        self, stage: str, *, older_than_s: int, priority: int = 1, now: int | None = None
    ) -> int:
        """Re-arm ``stage`` jobs that ran out of retries at least ``older_than_s`` ago.

        Only state ``failed`` (temporary errors that exhausted their attempts) is
        re-armed; ``skipped`` jobs failed for good (404, too large, unsupported).
        Documents removed since are left alone.
        """
        stamp = resolve_now(now)
        with self.connection as con:
            n = con.execute(
                "UPDATE jobs SET state='queued', attempts=0, priority=?, next_attempt_at=?, "
                "enqueued_at=? WHERE stage=? AND state='failed' AND finished_at<=? "
                "AND EXISTS (SELECT 1 FROM documents d WHERE d.site_id=jobs.site_id "
                "  AND d.doc_id=jobs.doc_id AND d.tombstoned_at IS NULL)",
                (priority, stamp, stamp, stage, stamp - older_than_s),
            ).rowcount
        return int(n or 0)

    def enqueue_job(
        self,
        site_id: str,
        doc_id: str,
        stage: str,
        *,
        priority: int = 0,
        now: int | None = None,
    ) -> None:
        """Queue ``stage`` for a document. Re-enqueueing resets attempts.

        While already queued, the more urgent (lower) priority wins; a finished job
        is re-armed with the new priority.
        """
        stamp = resolve_now(now)
        with self.connection as con:
            con.execute(
                "INSERT INTO jobs (site_id, doc_id, stage, state, priority, attempts, "
                "next_attempt_at, enqueued_at) VALUES (?,?,?,'queued',?,0,?,?) "
                "ON CONFLICT(site_id, doc_id, stage) DO UPDATE SET "
                "priority = CASE WHEN jobs.state='queued' "
                "  THEN min(jobs.priority, excluded.priority) ELSE excluded.priority END, "
                "state='queued', attempts=0, next_attempt_at=excluded.next_attempt_at, "
                "enqueued_at=CASE WHEN jobs.state='queued' THEN jobs.enqueued_at "
                "  ELSE excluded.enqueued_at END, "
                "last_error=NULL, finished_at=NULL",
                (site_id, doc_id, stage, priority, stamp, stamp),
            )

    def claim_jobs(self, stage: str, *, limit: int, now: int | None = None) -> list[Job]:
        if limit <= 0:
            return []
        stamp = resolve_now(now)
        con = self.connection
        with con:
            rows = con.execute(
                "SELECT * FROM jobs WHERE stage=? AND state='queued' AND next_attempt_at<=? "
                "ORDER BY priority, next_attempt_at, id LIMIT ?",
                (stage, stamp, limit),
            ).fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                con.execute(
                    "UPDATE jobs SET state='running', started_at=? "
                    f"WHERE id IN ({placeholders(ids)})",
                    [stamp, *ids],
                )
        return [self._to_job(r) for r in rows]

    def finish_job(
        self, job_id: int, *, state: str, error: str | None = None, now: int | None = None
    ) -> None:
        if state not in ("done", "skipped", "failed", "waiting"):
            raise StoreError(f"invalid final job state {state!r}")
        with self.connection as con:
            con.execute(
                "UPDATE jobs SET state=?, finished_at=?, last_error=? WHERE id=?",
                (state, resolve_now(now), error, job_id),
            )

    def retry_job(
        self,
        job_id: int,
        *,
        delay_s: float,
        error: str,
        max_attempts: int,
        now: int | None = None,
    ) -> bool:
        """Schedule another attempt; returns ``False`` once attempts are exhausted."""
        stamp = resolve_now(now)
        row = self.connection.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        attempts = int(row[0]) + 1 if row else max_attempts
        with self.connection as con:
            if attempts >= max_attempts:
                con.execute(
                    "UPDATE jobs SET state='failed', attempts=?, finished_at=?, last_error=? "
                    "WHERE id=?",
                    (attempts, stamp, error, job_id),
                )
                return False
            con.execute(
                "UPDATE jobs SET state='queued', attempts=?, next_attempt_at=?, last_error=? "
                "WHERE id=?",
                (attempts, stamp + int(delay_s), error, job_id),
            )
        return True

    def reset_running_jobs(self) -> int:
        """Crash recovery: anything 'running' at startup never finished."""
        with self.connection as con:
            n = con.execute("UPDATE jobs SET state='queued' WHERE state='running'").rowcount
        return int(n or 0)

    def has_due_jobs(self, *, now: int | None = None) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM jobs WHERE state IN ('queued','running') "
                "AND (state='running' OR next_attempt_at<=?) LIMIT 1",
                (resolve_now(now),),
            ).fetchone()
            is not None
        )

    def next_job_due_at(self) -> int | None:
        row = self.connection.execute(
            "SELECT min(next_attempt_at) FROM jobs WHERE state='queued'"
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def job_counts(self, *, now: int | None = None) -> dict[str, dict[str, int]]:
        stamp = resolve_now(now)
        out: dict[str, dict[str, int]] = {}
        for r in self.connection.execute(
            "SELECT stage, state, count(*) AS n, min(enqueued_at) AS oldest "
            "FROM jobs GROUP BY stage, state"
        ):
            entry = out.setdefault(r["stage"], {})
            entry[r["state"]] = int(r["n"])
            if r["state"] == "queued":
                entry["oldest_queued_age_s"] = stamp - int(r["oldest"])
        return out

    def failed_jobs(self, *, stage: str | None = None) -> list[Job]:
        sql = "SELECT * FROM jobs WHERE state='failed'"
        params: list[Any] = []
        if stage:
            sql += " AND stage=?"
            params.append(stage)
        return [self._to_job(r) for r in self.connection.execute(sql + " ORDER BY id", params)]

    @staticmethod
    def _to_job(row: Any) -> Job:
        return Job(
            id=row["id"],
            site_id=row["site_id"],
            doc_id=row["doc_id"],
            stage=row["stage"],
            state=row["state"],
            priority=row["priority"],
            attempts=row["attempts"],
            next_attempt_at=row["next_attempt_at"],
            last_error=row["last_error"],
        )
