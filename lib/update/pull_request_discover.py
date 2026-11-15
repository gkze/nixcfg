"""Discover upstream movement for the update pull-request planner.

Discovery reuses each updater's ``fetch_latest`` and the flake-ref check the
Update pipeline already uses. It does not recompute hashes and it does not
write the checkout. Hashing stays on the prepare blueprint.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping

import aiohttp

from lib.diagnostics import redact_urls
from lib.nix.models.sources import SourceEntry
from lib.update.pull_request_plan import (
    OpenUpdatePR,
    PlanResult,
    ProbeFailure,
    UpdateOffer,
    plan_updates,
    updater_touches_darwin,
)
from lib.update.refs import (
    FlakeInputRef,
    RefUpdateResult,
    check_flake_ref_update,
    get_flake_inputs_with_refs,
)
from lib.update.sources import load_all_sources
from lib.update.updaters import ensure_updaters_loaded
from lib.update.updaters.core import UpdateContext, Updater

_PROBE_TIMEOUT_SECONDS = 45
_PROBE_LIMIT = 16
_ERROR_LIMIT = 200

type SourceFetcher = Callable[
    [str, type[object], SourceEntry | None, aiohttp.ClientSession],
    Awaitable[UpdateOffer | ProbeFailure | None],
]
type RefFetcher = Callable[
    [FlakeInputRef, aiohttp.ClientSession],
    Awaitable[RefUpdateResult],
]


async def discover_offers(
    *,
    updaters: Mapping[str, type[object]],
    currents: Mapping[str, SourceEntry],
    refs: tuple[FlakeInputRef, ...] | list[FlakeInputRef],
    session: aiohttp.ClientSession,
    security: frozenset[str],
    main_fix: frozenset[str],
    fetch_source: SourceFetcher,
    fetch_ref: RefFetcher,
    probe_timeout: float = _PROBE_TIMEOUT_SECONDS,
    limit: int = _PROBE_LIMIT,
) -> tuple[tuple[UpdateOffer, ...], tuple[ProbeFailure, ...]]:
    """Probe every source and version-like flake input."""
    semaphore = asyncio.Semaphore(limit)

    async def bounded(
        name: str,
        work: Awaitable[UpdateOffer | ProbeFailure | None],
    ) -> UpdateOffer | ProbeFailure | None:
        try:
            async with semaphore:
                return await asyncio.wait_for(work, probe_timeout)
        except TimeoutError:
            return ProbeFailure(name=name, error="discovery timed out")
        except Exception as exc:  # noqa: BLE001 -- one probe must not cancel the rest
            return ProbeFailure(name=name, error=_error_text(exc))

    tasks = [
        bounded(
            name,
            fetch_source(name, updater_cls, currents.get(name), session),
        )
        for name, updater_cls in sorted(updaters.items())
    ]
    tasks.extend(
        bounded(ref.name, _offer_from_ref(ref, session, fetch_ref)) for ref in refs
    )
    offers: list[UpdateOffer] = []
    failures: list[ProbeFailure] = []
    for result in await asyncio.gather(*tasks):
        if isinstance(result, UpdateOffer):
            offers.append(_flag(result, security=security, main_fix=main_fix))
        elif isinstance(result, ProbeFailure):
            failures.append(result)
    return tuple(offers), tuple(failures)


async def discover_live(
    *,
    security: frozenset[str] = frozenset(),
    main_fix: frozenset[str] = frozenset(),
    open_prs: tuple[OpenUpdatePR, ...] = (),
) -> PlanResult:
    """Probe the current checkout and plan pull requests."""
    updaters = ensure_updaters_loaded()
    currents = load_all_sources().entries
    refs = tuple(get_flake_inputs_with_refs())
    async with aiohttp.ClientSession(max_field_size=65536) as session:
        offers, failures = await discover_offers(
            updaters=updaters,
            currents=currents,
            refs=refs,
            session=session,
            security=security,
            main_fix=main_fix,
            fetch_source=_fetch_source,
            fetch_ref=_fetch_ref,
        )
    return plan_updates(
        offers, updaters=dict(updaters), open_prs=open_prs, failures=failures
    )


async def _fetch_source(
    name: str,
    updater_cls: type[object],
    current: SourceEntry | None,
    session: aiohttp.ClientSession,
) -> UpdateOffer | None:
    updater = updater_cls()
    if not isinstance(updater, Updater):
        msg = f"{name} updater is not an Updater"
        raise TypeError(msg)
    context = UpdateContext(current=current)
    info = await updater.fetch_latest(session, context=context)
    # Same proof prepare uses. The method is async on the updater base class.
    if await updater._is_latest(context, info):  # noqa: SLF001
        return None
    raw_version = None if current is None else current.version
    version = raw_version if isinstance(raw_version, str) else ""
    return UpdateOffer(
        name=name,
        kind="source",
        current=version,
        latest=info.version,
        touches_darwin=updater_touches_darwin(updater_cls),
    )


async def _fetch_ref(
    ref: FlakeInputRef,
    session: aiohttp.ClientSession,
) -> RefUpdateResult:
    return await check_flake_ref_update(ref, session)


async def _offer_from_ref(
    ref: FlakeInputRef,
    session: aiohttp.ClientSession,
    fetch_ref: RefFetcher,
) -> UpdateOffer | ProbeFailure | None:
    result = await fetch_ref(ref, session)
    if result.error:
        return ProbeFailure(
            name=ref.name, error=_error_text(RuntimeError(result.error))
        )
    if result.latest_ref is None or result.latest_ref == result.current_ref:
        return None
    return UpdateOffer(
        name=ref.name,
        kind="input",
        current=result.current_ref,
        latest=result.latest_ref,
        touches_darwin=True,
    )


def _flag(
    offer: UpdateOffer, *, security: frozenset[str], main_fix: frozenset[str]
) -> UpdateOffer:
    if offer.name not in security and offer.name not in main_fix:
        return offer
    return UpdateOffer(
        name=offer.name,
        kind=offer.kind,
        current=offer.current,
        latest=offer.latest,
        security=offer.security or offer.name in security,
        main_fix=offer.main_fix or offer.name in main_fix,
        touches_darwin=offer.touches_darwin,
    )


def _error_text(exc: BaseException) -> str:
    text = redact_urls(str(exc)).strip() or exc.__class__.__name__
    if len(text) > _ERROR_LIMIT:
        return text[:_ERROR_LIMIT]
    return text


__all__ = ["discover_live", "discover_offers"]
