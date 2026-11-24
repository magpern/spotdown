"""``POST /v2/match`` per track: the source-match cache, the provider backoff, the search and the
cached-analysis summaries. The scoring itself is pure (``app.youtube.match_calc``).

No download and no analysis ever starts here. Everything this stores is global (public search
results): no ``cache_scope``, ``client_ref`` or job id is written. The caller's scope is used only
to read which candidates already have an analysis it may see.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from ..youtube import error_policy
from ..youtube import match_calc as calc
from ..youtube import normalise_policy as norm
from ..youtube.ytdlp_client import YtDlpClient
from . import analysis_doc
from . import match_policy as policy
from .api_models import MatchTrack
from .failure_policy import JobFailure, Reason, outcome_for
from .store import Store

PROVIDER = policy.PROVIDER
_SEARCH_STATUSES = frozenset({"source_unavailable", "provider_error", "rate_limited"})
_ENTRY_TEXT_MAX = 300


def compact_entry(entry: Any) -> Optional[Dict[str, Any]]:
    """The fields the matcher reads, and nothing else (no URL, thumbnail or description)."""
    if not isinstance(entry, dict):
        return None
    kept: Dict[str, Any] = {}
    for key in ("id", "title", "channel", "uploader", "isrc", "live_status"):
        value = entry.get(key)
        if isinstance(value, str):
            kept[key] = value[:_ENTRY_TEXT_MAX]
    duration = entry.get("duration")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        kept["duration"] = float(duration)
    for key in ("is_live", "channel_is_verified"):
        if entry.get(key) is True:
            kept[key] = True
    return kept


def _failure(client_ref: str, status: str, retry_after_s: Optional[float] = None) -> Dict:
    view: Dict[str, Any] = {
        "schema_version": "match-v1",
        "client_ref": client_ref,
        "status": status,
        "alternates": [],
        "matcher_version": calc.MATCHER_VERSION,
    }
    if status == "rate_limited":
        view["retry_after_s"] = float(retry_after_s or error_policy.DEFAULT_RETRY_AFTER_S)
    return view


class Matcher:
    def __init__(
        self,
        store: Store,
        client: YtDlpClient,
        *,
        search_results: int,
        concurrency: int = 4,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.client = client
        self.search_results = search_results
        self.concurrency = max(1, concurrency)
        self.clock = clock

    def match_all(self, tracks: List[MatchTrack], scope: Optional[str]) -> List[Dict]:
        """Results in request order."""
        workers = min(self.concurrency, len(tracks))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda t: self.match_one(t, scope), tracks))

    def _entries(self, wanted: calc.Wanted, query: str) -> List[Any]:
        """The (cached) search entries. Raises ``JobFailure`` for a failed search."""
        fingerprint = norm.fingerprint(
            title=wanted.title,
            artists=wanted.artists,
            duration_s=wanted.duration_s,
            isrc=wanted.isrc,
            matcher_version=calc.MATCHER_VERSION,
        )
        now = self.clock()
        cached = self.store.match_get(fingerprint, now)
        if cached is not None:
            return cached
        wait = policy.retry_after(self.store.backoff_until(PROVIDER), now)
        if wait is not None:
            raise JobFailure(Reason.RATE_LIMITED, wait)
        try:
            found = self.client.search(query, self.search_results)
        except JobFailure as failure:
            if failure.reason is Reason.RATE_LIMITED:
                retry = failure.retry_after_s or error_policy.DEFAULT_RETRY_AFTER_S
                self.store.set_backoff(PROVIDER, now + retry)
                raise JobFailure(Reason.RATE_LIMITED, retry) from None
            raise
        entries = [e for e in map(compact_entry, found) if e is not None]
        usable = sum(calc.candidate_from_entry(e, i) is not None for i, e in enumerate(entries))
        self.store.match_put(fingerprint, entries, now, policy.ttl_for(usable))
        return entries

    def match_one(self, track: MatchTrack, scope: Optional[str]) -> Dict[str, Any]:
        wanted = calc.Wanted(track.title, tuple(track.artists or ()), track.duration_s, track.isrc)
        query = norm.search_query(wanted.title, wanted.artists)
        try:
            entries = self._entries(wanted, query)
        except JobFailure as failure:
            status = outcome_for(failure.reason)[0]
            status = status if status in _SEARCH_STATUSES else "provider_error"
            return _failure(track.client_ref, status, failure.retry_after_s)
        candidates = [
            c for c in (calc.candidate_from_entry(e, i) for i, e in enumerate(entries)) if c
        ]
        decision = calc.decide(wanted, candidates)
        if decision.best is None:
            return _failure(track.client_ref, "not_found")
        record = calc.match_record(query, decision)
        assert record is not None
        if wanted.duration_s is not None:
            # Without a requested duration no match record is kept: a later analysis of the
            # video checks against its own metadata duration, and analysis-v1 never carries a
            # null duration delta.
            self.store.put_choice(
                decision.best.candidate.video_id,
                record,
                wanted.duration_s,
                self.clock(),
                track.explicit,
            )
        return {
            "schema_version": "match-v1",
            "client_ref": track.client_ref,
            "status": decision.status,
            "best": self._candidate(decision.best, scope),
            "alternates": [self._candidate(s, scope) for s in decision.alternates],
            "matcher_version": calc.MATCHER_VERSION,
        }

    def _candidate(self, scored: calc.Scored, scope: Optional[str]) -> Dict[str, Any]:
        view: Dict[str, Any] = {
            "source": f"{PROVIDER}:{scored.candidate.video_id}",
            "recording_match_confidence": scored.confidence,
            "band": scored.band,
            "duration_s": scored.candidate.duration_s,
            "duration_delta_s": scored.delta_s,
            "duration_check": scored.check,
        }
        hit = self.store.find_by_source(
            PROVIDER, scored.candidate.video_id, scope, analysis_doc.ANALYZER_VERSION_V2
        )
        doc = self.store.get_analysis(hit.analysis_id) if hit is not None else None
        if hit is not None and doc is not None:
            view["cached_analysis_id"] = hit.analysis_id
            view["cached_summary"] = policy.cached_summary(doc)
        return view
