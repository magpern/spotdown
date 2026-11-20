"""The bounded lifetime of the global rows that hold Spotify-catalogue-derived references (ADR
0040 §7): the match record of a chosen video and the reference of a global alignment."""

from __future__ import annotations

import time
from pathlib import Path

from app.service import cache_policy, sweep

from .service_helpers import SCOPE
from .youtube_fakes import YoutubeStack, entry

VID = "dQw4w9WgXcQ"
REF = {"kind": "spotify", "id": "4uLU6hMCjMI75M1A2tKUQC", "duration_s": 12.0}
TRACK = {
    "client_ref": "c",
    "title": "Blinding Lights",
    "artists": ["The Weeknd"],
    "duration_s": 12.0,
}


def age(stack: YoutubeStack, table: str, seconds: float) -> None:
    with stack.store._conn() as conn:
        conn.execute(f"UPDATE {table} SET created_at = created_at - ?", (seconds,))


def test_the_default_lifetime_is_a_conservative_setting(tmp_path: Path) -> None:
    stack = YoutubeStack(tmp_path)
    assert stack.settings.global_reference_ttl_s == cache_policy.GLOBAL_REFERENCE_TTL_S
    assert cache_policy.GLOBAL_REFERENCE_TTL_S <= 3 * 24 * 3600
    app_store = stack.client.app.state.store  # type: ignore[attr-defined]
    assert app_store.global_ttl_s == stack.settings.global_reference_ttl_s
    (tmp_path / "short").mkdir()
    short = YoutubeStack(tmp_path / "short", global_reference_ttl_s=60)
    assert short.client.app.state.store.global_ttl_s == 60  # type: ignore[attr-defined]


def test_an_expired_match_record_is_not_used_and_is_purged(tmp_path: Path) -> None:
    stack = YoutubeStack(tmp_path)
    stack.world.entries = [entry(VID, "Blinding Lights", "The Weeknd - Topic", 12.3)]
    stack.match([TRACK], scope=SCOPE)
    now = time.time()
    assert stack.store.get_choice(VID, now) is not None
    age(stack, "match_choices", stack.store.global_ttl_s + 1)
    assert stack.store.get_choice(VID, now) is None
    doc = stack.get_analysis(
        stack.done_analysis_id(stack.post({"source_ref": f"youtube:{VID}"})), None
    )
    source = doc.json()["provenance"]["source"]
    assert "match" not in source and source["decoded_duration_check"]["expected_from"] == "metadata"
    stack.store.put_choice("otherid0001", {"chosen": "otherid0001"}, 10.0, now)  # purges
    with stack.store._conn() as conn:
        left = [r[0] for r in conn.execute("SELECT video_id FROM match_choices")]
    assert left == ["otherid0001"]


def test_an_expired_global_alignment_is_not_served_and_is_purged(tmp_path: Path) -> None:
    stack = YoutubeStack(tmp_path)
    body = {"source_ref": f"youtube:{VID}", "reference": REF}
    aligned = stack.done_analysis_id(stack.post(body))
    assert stack.get_analysis(aligned, None).status_code == 200
    age(stack, "alignments", stack.store.global_ttl_s + 1)
    assert stack.get_analysis(aligned, None).status_code == 404
    again = stack.post(body)  # a cache hit aligns afresh under a new id
    assert again.status_code == 200 and again.json()["analysis_id"] != aligned
    assert stack.get_analysis(again.json()["analysis_id"], None).status_code == 200
    with stack.store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM alignments").fetchone()[0] == 1


def test_the_sweep_purges_expired_global_references_only(tmp_path: Path) -> None:
    stack = YoutubeStack(tmp_path)
    stack.world.entries = [entry(VID, "Blinding Lights", "The Weeknd - Topic", 12.3)]
    stack.match([TRACK])
    stack.done_analysis_id(stack.post({"source_ref": f"youtube:{VID}", "reference": REF}))
    upload_id = stack.upload(stack.audio("a.wav")).json()["upload_id"]
    scoped = stack.post({"cache_scope": SCOPE, "upload_id": upload_id, "reference": REF})
    stack.analysis_id(scoped)
    age(stack, "match_choices", stack.store.global_ttl_s + 1)
    age(stack, "alignments", stack.store.global_ttl_s + 1)
    sweep.sweep(stack.settings, stack.store)
    with stack.store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM match_choices").fetchone()[0] == 0
        scopes = [r[0] for r in conn.execute("SELECT cache_scope FROM alignments")]
    assert scopes == [SCOPE]  # a scoped alignment lives with its scope, not by this lifetime
