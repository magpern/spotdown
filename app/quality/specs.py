"""The committed synthetic corpus: 30 specs built from a few section templates (pure data).

Every spec is deterministic. ``python -m app.quality.fixtures_io --write`` renders their truth to
``quality/fixtures/synthetic/manifest.json``; a test fails if the committed file is stale.
"""

from __future__ import annotations

from typing import Any, Dict, List

Layers = Dict[str, float]


def _s(bars: int, label: str, drop: bool = False, **layers: Any) -> Dict[str, Any]:
    sec: Dict[str, Any] = {"bars": bars, "label": label, "layers": dict(layers)}
    if drop:
        sec["drop"] = True
    return sec


def _sd(bars: int, label: str, layers: Dict[str, float]) -> Dict[str, Any]:
    return {"bars": bars, "label": label, "layers": dict(layers)}


def _spec(
    sid: str, bpm: float, seed: int, sections: List[Dict[str, Any]], **kw: Any
) -> Dict[str, Any]:
    spec = {
        "id": sid,
        "bpm": bpm,
        "seed": seed,
        "lead_in_s": 0.0,
        "tail_s": 0.0,
        "fade_in_s": 0.0,
        "fade_out_s": 0.0,
        "sections": sections,
    }
    spec.update(kw)
    return spec


def _edm(sid: str, bpm: float, seed: int, bars: int = 4, **kw: Any) -> Dict[str, Any]:
    """Intro, build, drop, breakdown, build, drop, outro: a club track with two drops.

    Label annotation (the label gate's truth): a build into a drop is a ``verse`` (pre-chorus), a
    drop is a ``chorus``, a pulse-free pad section is a ``breakdown``.
    """
    k = bars
    return _spec(
        sid,
        bpm,
        seed,
        [
            _s(k, "intro", kick=0.8, hat=1.0, pad=1.0),
            _s(k, "verse", snare=1.0, hat=1.0, riser=1.0, pad=1.0),
            _s(2 * k, "chorus", True, kick=1.0, snare=1.0, hat=1.0, bass=1.0, lead=1.0),
            _s(k, "breakdown", pad=1.0, lead=0.6),
            _s(k, "verse", snare=1.0, hat=1.0, riser=1.0, pad=1.0),
            _s(2 * k, "chorus", True, kick=1.0, snare=1.0, hat=1.0, bass=1.0, lead=1.0),
            _s(k, "outro", kick=0.8, hat=1.0, pad=1.0),
        ],
        **kw,
    )


def _groove(
    sid: str, bpm: float, seed: int, kick: str, hat: str, bars: int = 4, **kw: Any
) -> Dict[str, Any]:
    """Verse and chorus style changes in density, no drop.

    Label annotation: intro (bass and kick), verse (adds snare and hat), chorus (full), bridge (the
    thinner middle section between two choruses), chorus.
    """
    k = bars
    return _spec(
        sid,
        bpm,
        seed,
        [
            _sd(k, "intro", {kick: 0.9, "bass": 1.0}),
            _sd(k, "verse", {kick: 0.9, "snare": 1.0, hat: 1.0, "bass": 1.0}),
            _sd(
                k,
                "chorus",
                {kick: 1.0, "snare": 1.0, hat: 1.0, "bass": 1.0, "lead": 1.0, "pad": 1.0},
            ),
            _sd(k, "bridge", {kick: 0.9, "snare": 1.0, "bass": 1.0}),
            _sd(
                k,
                "chorus",
                {kick: 1.0, "snare": 1.0, hat: 1.0, "bass": 1.0, "lead": 1.0, "pad": 1.0},
            ),
        ],
        **kw,
    )


def _slow_drop(sid: str, bpm: float, seed: int, bars: int = 4, **kw: Any) -> Dict[str, Any]:
    k = bars
    return _spec(
        sid,
        bpm,
        seed,
        [
            _s(k, "intro", pad=1.0, kick_half=0.8, hat=0.8),
            _s(k, "chorus", True, kick_half=1.0, snare=1.0, hat16=0.8, bass=1.0, lead=1.0),
            _s(k, "breakdown", pad=1.0, lead=0.6),
            _s(k, "chorus", True, kick_half=1.0, snare=1.0, hat16=0.8, bass=1.0, lead=1.0),
        ],
        **kw,
    )


PHRASES = "phrases"  # corpus of the fixtures that only the phrase gate measures


def _pickup_edm(sid: str, bpm: float, seed: int, **kw: Any) -> Dict[str, Any]:
    """A 4-bar pickup intro, then 8-bar sections: the phrases start after the pickup."""
    return _spec(
        sid,
        bpm,
        seed,
        [
            _s(4, "intro", kick=0.8, hat=1.0, pad=1.0),
            _s(8, "verse", snare=1.0, hat=1.0, riser=1.0, pad=1.0),
            _s(8, "chorus", True, kick=1.0, snare=1.0, hat=1.0, bass=1.0, lead=1.0),
            _s(8, "breakdown", pad=1.0, lead=0.6),
            _s(8, "verse", snare=1.0, hat=1.0, riser=1.0, pad=1.0),
            _s(8, "chorus", True, kick=1.0, snare=1.0, hat=1.0, bass=1.0, lead=1.0),
            _s(4, "outro", kick=0.8, hat=1.0, pad=1.0),
        ],
        **kw,
    )


def _pickup_groove(sid: str, bpm: float, seed: int, **kw: Any) -> Dict[str, Any]:
    """A 4-bar pickup intro, then 8-bar groove sections (annotation as in ``_groove``)."""
    full = {"kick": 1.0, "snare": 1.0, "hat": 1.0, "bass": 1.0, "lead": 1.0, "pad": 1.0}
    return _spec(
        sid,
        bpm,
        seed,
        [
            _sd(4, "intro", {"kick": 0.9, "bass": 1.0}),
            _sd(8, "verse", {"kick": 0.9, "snare": 1.0, "hat": 1.0, "bass": 1.0}),
            _sd(8, "chorus", full),
            _sd(8, "bridge", {"kick": 0.9, "snare": 1.0, "bass": 1.0}),
            _sd(8, "chorus", full),
        ],
        **kw,
    )


METER = "meter"  # corpus of the 3/4 fixtures: the downbeat and meter gates, no baseline metric


def _waltz(sid: str, bpm: float, seed: int, kick: str, hat: str, **kw: Any) -> Dict[str, Any]:
    """A 3/4 track (``beats_per_bar`` 3): intro, verse, chorus, breakdown, chorus, outro.

    ``kick`` is ``kick`` (beat 1 only) or ``kick_all`` (every beat, so only the bass note and the
    snare mark the bar); ``hat`` is ``hat`` or ``hat16``. Annotation as in ``_groove``."""
    full = {kick: 1.0, "snare": 1.0, hat: 0.8, "bass": 1.0, "lead": 1.0, "pad": 1.0}
    return _spec(
        sid,
        bpm,
        seed,
        [
            _sd(4, "intro", {kick: 0.9, hat: 0.8, "pad": 1.0}),
            _sd(8, "verse", {kick: 0.9, "snare": 1.0, hat: 0.8, "bass": 1.0}),
            _sd(8, "chorus", full),
            _sd(4, "breakdown", {"pad": 1.0, "lead": 0.6}),
            _sd(8, "chorus", full),
            _sd(4, "outro", {kick: 0.9, hat: 0.8, "pad": 1.0}),
        ],
        beats_per_bar=3,
        corpus=METER,
        **kw,
    )


SPECS: List[Dict[str, Any]] = [
    _edm("edm-100", 100, 1, 4),
    _edm("edm-110", 110, 2, 4),
    _edm("edm-120", 120, 3, 4),
    _edm("edm-126", 126, 4, 4),
    _edm("edm-128", 128, 5, 4),
    _edm("edm-132", 132, 6, 4),
    _edm("edm-140", 140, 7, 4),
    _edm("edm-150", 150, 8, 4),
    _edm("edm-160", 160, 9, 8),
    _groove("groove-break-90", 90, 10, "kick_break", "hat"),
    _groove("groove-break-105", 105, 11, "kick_break", "hat16"),
    _groove("groove-break-125", 125, 12, "kick_break", "hat"),
    _groove("groove-four-115", 115, 13, "kick", "hat"),
    _groove("groove-four-135", 135, 14, "kick", "hat16"),
    _slow_drop("slow-drop-74", 74, 15, 3),
    _slow_drop("slow-drop-80", 80, 16, 3),
    _edm("fast-166", 166, 17, 8),
    _groove("fast-168", 168, 18, "kick", "hat16", 8),
    _edm("long-intro-124", 124, 19, 4, lead_in_s=1.37),
    _edm("fade-in-out-118", 118, 20, 4, fade_in_s=6.0, fade_out_s=6.0),
    _groove("fade-out-108", 108, 21, "kick", "hat", fade_out_s=7.0),
    _edm("silence-edges-130", 130, 22, 4, lead_in_s=2.0, tail_s=3.0),
    _edm("noisy-122", 122, 23, 4, noise_db=-32.0),
    _groove("noisy-break-98", 98, 24, "kick_break", "hat", noise_db=-30.0, lead_in_s=0.37),
    _pickup_edm("pickup-edm-140", 140, 25, corpus=PHRASES),
    _pickup_groove("pickup-groove-118", 118, 26, corpus=PHRASES),
    _waltz("waltz-kick1-96", 96, 27, "kick", "hat"),
    _waltz("waltz-kick1-126", 126, 28, "kick", "hat16"),
    _waltz("waltz-allkick-108", 108, 29, "kick_all", "hat"),
    _waltz("waltz-allkick-138", 138, 30, "kick_all", "hat16", noise_db=-32.0),
]
