"""Site-authenticated admin routes for the plugin: quota policy push and usage report."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from moodle_llm_chat.quota.service import QuotaPolicy, QuotaService, SiteCeilings
from moodle_llm_chat.web.auth import SITE_RESPONSES, SiteDep

router = APIRouter(prefix="/ingest", tags=["site"], responses=SITE_RESPONSES)


class PolicyApplied(BaseModel):
    policy: QuotaPolicy = Field(description="The policy now in force for the site.")
    ceilings: SiteCeilings = Field(
        description="Ceilings the server operator set for the site with the CLI. A policy "
        "cannot raise a limit above them."
    )


class UsageRow(BaseModel):
    user_id: int | None = Field(
        default=None, description="Moodle user id; absent when the report is for the whole site."
    )
    tokens: int = Field(description="Tokens counted against the limits in the period.")
    calls: int = Field(description="Number of model calls in the period.")


class UsageReport(BaseModel):
    period: Literal["day", "month"] = Field(description="The window the rows cover.")
    by: Literal["user", "site"] = Field(description="How the rows are grouped.")
    rows: list[UsageRow] = Field(
        description="Usage per user, heaviest first, or a single row for the site. "
        "Empty when nothing was used."
    )


def _quota(request: Request) -> QuotaService:
    quota: QuotaService = request.app.state.quota
    return quota


@router.put("/policy", summary="Set the site's token limits")
async def put_policy(policy: QuotaPolicy, request: Request, site: SiteDep) -> PolicyApplied:
    """The plugin pushes its quota settings whenever they are saved (and on full syncs).

    The policy replaces the previous one. Limits left out (`null`) mean no limit from the
    plugin; the operator's ceilings still apply.
    """
    quota = _quota(request)
    quota.set_policy(site.site_id, policy)
    return PolicyApplied(policy=policy, ceilings=quota.ceilings(site.site_id))


@router.get("/usage", summary="Report token usage")
async def usage(
    request: Request,
    site: SiteDep,
    period: Annotated[
        Literal["day", "month"], Query(description="The window to report on.")
    ] = "day",
    by: Annotated[
        Literal["user", "site"], Query(description="Group the rows per user or for the site.")
    ] = "user",
) -> UsageReport:
    """Token usage of the current day or month in the site's time zone.

    Only chat usage is reported, counted the same way as against the limits. A day or
    month starts at midnight in the policy's time zone.
    """
    rows = _quota(request).report(site.site_id, period=period, by=by)
    return UsageReport(period=period, by=by, rows=[UsageRow(**r) for r in rows])
