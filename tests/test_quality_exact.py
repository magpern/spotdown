"""The redefined ``false_exact`` hard gate: it counts claims the case is not entitled to, and it can
fail (every case below is a mutation of a document the analysis could emit)."""

from __future__ import annotations

from typing import Any, Dict

import pytest

from app.quality import exact_calc as ex
from tests.corpus_helpers import make_doc

ALIGN = {"offset_s": -2.0, "confidence": 0.97, "residual_s": 0.01}


def case(corpus: str = "studio", doc: Any = None, **more: Any) -> Dict[str, Any]:
    return {"corpus": corpus, "doc": doc if doc is not None else make_doc(), **more}


def claim(corpus: str = "studio", **more: Any) -> Dict[str, Any]:
    return case(corpus, make_doc(extra={"timing_class": "exact"}), **more)


def test_a_document_without_a_claim_is_never_a_false_exact() -> None:
    assert ex.false_exact([case("spotify"), case("studio", make_doc(alignment=ALIGN))]) == 0


def test_an_alignment_alone_is_evidence_and_not_a_claim() -> None:
    assert ex.exact_claims(make_doc(alignment=ALIGN)) == []


def test_a_claim_is_found_in_any_key_or_list() -> None:
    doc = make_doc(extra={"x": [{"y": "exact"}]})
    assert ex.exact_claims(doc) == ["/x/0/y"]


@pytest.mark.parametrize("corpus", ["studio", "mix", "cc", "baseline", "phrases", "meter"])
def test_a_played_recording_may_claim_exact(corpus: str) -> None:
    assert ex.false_exact([claim(corpus)]) == 0


@pytest.mark.parametrize("corpus", ["spotify", "youtube", "something-new"])
def test_a_matched_or_unknown_source_never_may(corpus: str) -> None:
    assert ex.false_exact([claim(corpus)]) == 1


def test_a_file_that_is_not_the_played_recording_never_may() -> None:
    assert ex.false_exact([claim("studio", same_recording=False)]) == 1


def test_a_claim_with_an_alignment_off_by_more_than_50_ms_is_false() -> None:
    off = make_doc(alignment=dict(ALIGN, offset_s=-2.06), extra={"timing_class": "exact"})
    near = make_doc(alignment=dict(ALIGN, offset_s=-2.04), extra={"timing_class": "exact"})
    cases = [case("studio", off, true_offset_s=-2.0), case("studio", near, true_offset_s=-2.0)]
    assert ex.false_exact(cases) == 1


def test_false_exact_counts_documents_not_claims() -> None:
    twice = case("spotify", make_doc(extra={"a": "exact", "b": "exact"}))
    assert ex.false_exact([twice, claim("spotify"), claim("studio")]) == 2


def test_overreach_counts_confident_alignments_to_another_recording_only() -> None:
    other = case(doc=make_doc(alignment=ALIGN), same_recording=False)
    weak = case(doc=make_doc(alignment=dict(ALIGN, confidence=0.4)), same_recording=False)
    same = case(doc=make_doc(alignment=ALIGN), same_recording=True)
    assert ex.alignment_overreach([other, weak, same]) == 1


def test_offset_hits_cover_the_same_recording_cases_with_a_true_offset() -> None:
    good = case(doc=make_doc(alignment=ALIGN), true_offset_s=-2.0)
    bad = case(doc=make_doc(alignment=dict(ALIGN, offset_s=-1.0)), true_offset_s=-2.0)
    other = case(doc=make_doc(alignment=ALIGN), same_recording=False, true_offset_s=None)
    none = case(doc=make_doc())
    assert ex.offset_hits([good, bad, other, none]) == [True, False]
