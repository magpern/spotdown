"""The external-corpus runner end to end: loading, the report, the exit codes, and one real
isolated job. Fakes stand in for the jobs everywhere except the last test, which runs the real
sandboxed analysis on a rendered synthetic file (no network, no real account)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest

from app.quality import corpus, corpus_io, corpus_report, fixtures_io, runner
from app.quality.corpus_calc import CorpusError
from tests.corpus_helpers import make_doc, result

GATES = json.loads(runner.GATES.read_text(encoding="utf-8"))
BEATS = [round(i * 0.5, 4) for i in range(600)]
TRUTH = {
    "bpm": 120,
    "beats": BEATS,
    "boundaries": [30, 60],
    "drops": [150],
    "edges": [0, 300],
    "four_on_the_floor": True,
    "duration_s": 300,
}
PROV = {"title": "T", "artist": "A", "source_url": "https://example.org/t"}


def good_doc(**more: Any) -> Dict[str, Any]:
    return make_doc(
        duration=300.0,
        content=(0.0, 300.0),
        beats=BEATS,
        conf=0.97,
        bound_conf=0.95,
        drops=[150.0],
        **more,
    )


def write_corpus(root: Path, name: str, n: int, kind: str = "cc") -> Path:
    directory = root / name
    (directory / "audio").mkdir(parents=True)
    tracks = []
    for i in range(n):
        (directory / "audio" / f"t{i}.wav").write_bytes(b"RIFF0000WAVE")
        t: Dict[str, Any] = {"id": f"t{i}", "audio": f"audio/t{i}.wav", "truth": TRUTH}
        if kind == "cc":
            t.update(licence="CC0-1.0", provenance=PROV)
        tracks.append(t)
    index = {"corpus_version": 1, "corpus": kind, "tracks": tracks}
    (directory / "corpus.json").write_text(json.dumps(index), encoding="utf-8")
    return directory


def fake(docs: Callable[[str], Dict[str, Any]]) -> Callable[..., Dict[str, Any]]:
    def run(
        track: Dict[str, Any], audio: Path, timeout_s: float, python: str = ""
    ) -> Dict[str, Any]:
        return result(docs(track["id"]), id=track["id"], duration_s=300.0)

    return run


def run_main(
    monkeypatch: pytest.MonkeyPatch, args: List[str], docs: Callable[[str], Dict[str, Any]]
) -> int:
    monkeypatch.setattr(corpus, "run_isolated", fake(docs))
    return corpus.main(args)


def test_a_complete_passing_corpus_exits_zero_and_the_report_holds_no_audio_or_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    d = write_corpus(tmp_path, "cc", 20)
    out = tmp_path / "report.json"
    assert (
        run_main(monkeypatch, ["--corpus", str(d), "--json", str(out)], lambda _: good_doc()) == 0
    )
    text = out.read_text(encoding="utf-8")
    assert "RIFF" not in text and ".wav" not in text and '"doc"' not in text
    report = json.loads(text)[0]
    assert report["result"]["verdict"] == "pass" and report["metrics"]["false_exact"] == 0
    assert "corpus cc: pass" in capsys.readouterr().out


LATE = [t + 0.3 for t in BEATS]


def late_doc() -> Dict[str, Any]:
    return good_doc(**{}) | {"grid": {"beats": {"times_s": LATE}}}


def test_one_weak_track_of_twenty_passes_and_many_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = write_corpus(tmp_path, "cc", 20)

    def one(tid: str) -> Dict[str, Any]:
        return late_doc() if tid == "t0" else good_doc()

    def many(tid: str) -> Dict[str, Any]:
        return late_doc() if int(tid[1:]) < 6 else good_doc()

    assert run_main(monkeypatch, ["--corpus", str(d)], one) == 0  # beat F 0.95 of 1.0
    assert run_main(monkeypatch, ["--corpus", str(d)], many) == 1  # 0.70 < 0.75


def test_a_small_or_unmeasured_corpus_exits_two_not_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = write_corpus(tmp_path, "cc", 6)
    assert run_main(monkeypatch, ["--corpus", str(d)], lambda _: good_doc()) == 2


def test_an_exact_claim_on_a_spotify_corpus_fails_the_hard_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = write_corpus(tmp_path, "spotify", 30, "spotify")
    code = run_main(
        monkeypatch, ["--corpus", str(d)], lambda _: good_doc(extra={"timing_class": "exact"})
    )
    assert code == 1


def test_corpora_are_judged_apart_and_a_weak_one_fails_alone(tmp_path: Path) -> None:
    reports = []
    for kind, weak in (("cc", False), ("studio", True)):
        index, _ = corpus_io.load_corpus(write_corpus(tmp_path, kind, 20, kind))
        docs = [late_doc() if weak else good_doc() for _ in index["tracks"]]
        res = [result(d, duration_s=300.0) for d in docs]
        reports.append(corpus_report.build(GATES, index, res, None))
    assert corpus_report.overall(reports[0]) == "pass"
    assert corpus_report.overall(reports[1]) == "FAIL"
    assert corpus.exit_code(reports) == 1 and corpus.exit_code(reports[:1]) == 0


def test_a_baseline_comparison_failure_fails_the_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = write_corpus(tmp_path, "studio", 20, "studio")
    entry = {
        "bpm": 120.0,
        "beats": BEATS,
        "boundaries": [30, 60],
        "edges": [0, 300],
        "drops": [150],
    }
    b = tmp_path / "base.json"
    doc = {
        "baseline_version": 1,
        "source": "mowl.music.analyze_track",
        "tracks": {f"t{i}": entry for i in range(20)},
    }
    b.write_text(json.dumps(doc), encoding="utf-8")
    args = ["--corpus", str(d), "--baseline", str(b)]
    assert run_main(monkeypatch, args, lambda _: good_doc()) == 0

    def worse(tid: str) -> Dict[str, Any]:
        return late_doc() if int(tid[1:]) < 3 else good_doc()  # beat F 0.85: gate passes, row fails

    assert run_main(monkeypatch, args, worse) == 1


def test_a_source_with_a_baseline_rule_and_no_baseline_file_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = write_corpus(tmp_path, "studio", 20, "studio")
    assert run_main(monkeypatch, ["--corpus", str(d)], lambda _: good_doc()) == 2


def test_failed_jobs_lower_robustness_and_are_listed(tmp_path: Path) -> None:
    d = write_corpus(tmp_path, "cc", 20)
    index, _ = corpus_io.load_corpus(d)
    res = [result(good_doc(), duration_s=300.0) for _ in range(19)] + [result(None)]
    rep = corpus_report.build(GATES, index, res, None)
    assert rep["metrics"]["robustness_fraction"] == 0.95 and rep["failed_jobs"] == ["t19"]
    assert "robustness_fraction" in rep["result"]["failed"]


def test_a_directory_inside_the_repository_is_refused() -> None:
    with pytest.raises(CorpusError):
        corpus_io.load_corpus(corpus_io.REPO / "quality" / "fixtures" / "cc")


def test_an_audio_path_that_escapes_the_directory_is_refused(tmp_path: Path) -> None:
    d = write_corpus(tmp_path, "cc", 1)
    (tmp_path / "secret.wav").write_bytes(b"RIFF0000WAVE")
    (d / "audio" / "t0.wav").unlink()
    (d / "audio" / "t0.wav").symlink_to(tmp_path / "secret.wav")
    with pytest.raises(CorpusError):
        corpus_io.load_corpus(d)


def test_a_missing_audio_file_is_refused(tmp_path: Path) -> None:
    d = write_corpus(tmp_path, "cc", 1)
    (d / "audio" / "t0.wav").unlink()
    with pytest.raises(CorpusError):
        corpus_io.load_corpus(d)


def test_a_bad_corpus_is_a_clear_error_and_exit_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    assert corpus.main(["--corpus", str(d)]) == 1
    assert "corpus.json" in capsys.readouterr().err


def test_only_narrows_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = write_corpus(tmp_path, "cc", 3)
    index, audio = corpus_io.load_corpus(d)
    monkeypatch.setattr(corpus, "run_isolated", fake(lambda _: good_doc()))
    run = corpus.run_corpus(index, audio, "t1", 10.0)
    assert [t["id"] for t in run["index"]["tracks"]] == ["t1"] and len(run["results"]) == 1


def test_a_crashing_job_process_is_a_failed_job_not_a_crash(tmp_path: Path) -> None:
    track = {"id": "boom"}
    res = corpus.run_isolated(track, tmp_path / "missing.wav", 30.0, python="/bin/false")
    assert res["ok"] is False and res["doc"] is None and res["peak_gib"] is None


@pytest.mark.skipif(os.name != "posix", reason="the sandbox uses POSIX resource limits")
def test_one_real_track_runs_isolated_with_its_memory_measured_and_the_file_left_alone(
    tmp_path: Path,
) -> None:
    entry = next(t for t in fixtures_io.load_manifest()["tracks"] if t["id"] == "groove-four-135")
    audio = fixtures_io.render_to(entry["spec"], tmp_path)
    before = hashlib.sha256(audio.read_bytes()).hexdigest()
    truth = {k: entry["truth"][k] for k in ("bpm", "beats", "boundaries", "drops", "edges")}
    res = corpus.run_isolated({"id": "real-1", "truth": truth}, audio, 600.0)
    assert res["ok"] and res["audio_left"] == 0
    assert 0.0 < res["peak_gib"] < 1.5 and res["work_s"] > 0
    assert abs(res["duration_s"] - entry["truth"]["duration_s"]) < 0.5
    assert hashlib.sha256(audio.read_bytes()).hexdigest() == before
    index = {
        "corpus": "cc",
        "tracks": [{"id": "real-1", "truth": dict(truth, duration_s=res["duration_s"])}],
    }
    rep = corpus_report.build(GATES, index, [res], None)
    assert rep["metrics"]["bpm_within_2pct_fraction"] == 1.0
    assert rep["metrics"]["p95_seconds_per_5min"] is None  # 36 s is not a five-minute track
    assert "p95_seconds_per_5min" in rep["result"]["not_measured"]
    assert rep["result"]["verdict"] != "pass"


@pytest.mark.skipif(os.name != "posix", reason="a shell script stands in for the job process")
def test_a_killed_job_that_left_its_audio_copy_is_counted_and_fails_the_hard_gate(
    tmp_path: Path,
) -> None:
    fake_job = tmp_path / "fake_job.sh"
    fake_job.write_text(
        "#!/bin/sh\n"
        'mkdir -p "$TMPDIR/quality-x/data/uploads"\n'
        'echo audio > "$TMPDIR/quality-x/data/uploads/up_job_1"\n'
        "sleep 30\n",
        encoding="utf-8",
    )
    fake_job.chmod(0o755)
    res = corpus.run_isolated({"id": "t0"}, tmp_path / "a.wav", 1.5, python=str(fake_job))
    assert res["ok"] is False and res["audio_left"] == 1
    d = write_corpus(tmp_path, "cc", 1)
    index, _ = corpus_io.load_corpus(d)
    rep = corpus_report.build(GATES, index, [res], None)
    assert rep["metrics"]["audio_kept_after_job"] == 1.0
    assert "audio_kept_after_job" in rep["result"]["failed"]
    assert rep["result"]["verdict"] == "FAIL"


def test_the_hard_gates_are_not_measured_when_nothing_was_examined(tmp_path: Path) -> None:
    d = write_corpus(tmp_path, "cc", 2)
    index, _ = corpus_io.load_corpus(d)
    rep = corpus_report.build(GATES, index, [result(None), result(None)], None)
    rows = {r["gate"]: r for r in rep["result"]["gates"]}
    assert rows["false_exact"]["status"] == "not_measured" and rows["false_exact"]["n"] == 0
    assert rows["audio_kept_after_job"]["status"] == "pass"  # 2 jobs ran, nothing left
    assert rows["audio_kept_after_job"]["n"] == 2
    none = corpus_report.build(GATES, dict(index, tracks=[]), [], None)
    rows = {r["gate"]: r for r in none["result"]["gates"]}
    assert rows["audio_kept_after_job"]["status"] == "not_measured"
    assert rows["false_exact"]["status"] == "not_measured"


def test_baseline_files_are_tied_to_their_source_and_ids_are_not_overwritten(
    tmp_path: Path,
) -> None:
    def write(name: str, source: str, ids: List[str]) -> Path:
        path = tmp_path / name
        doc = {"baseline_version": 1, "source": source, "tracks": {i: {"bpm": 120} for i in ids}}
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    ok = corpus.load_baselines([write("a.json", "mowl_sections", ["x"])], GATES)
    assert list(ok["mowl_sections"]) == ["x"]
    with pytest.raises(CorpusError):
        corpus.load_baselines([write("b.json", "somewhere.else", ["x"])], GATES)
    twice = [write("c.json", "mowl_sections", ["x"]), write("d.json", "mowl_sections", ["x"])]
    with pytest.raises(CorpusError):
        corpus.load_baselines(twice, GATES)
    other = [
        write("e.json", "mowl_sections", ["x"]),
        write("f.json", "design/mixanalysis.py", ["x"]),
    ]
    assert set(corpus.load_baselines(other, GATES)) == {"mowl_sections", "design/mixanalysis.py"}


def test_a_studio_corpus_is_not_judged_against_a_spotify_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = write_corpus(tmp_path, "studio", 20, "studio")
    b = tmp_path / "base.json"
    doc = {
        "baseline_version": 1,
        "source": "mowl_sections",
        "tracks": {f"t{i}": {"bpm": 120.0} for i in range(20)},
    }
    b.write_text(json.dumps(doc), encoding="utf-8")
    args = ["--corpus", str(d), "--baseline", str(b)]
    assert run_main(monkeypatch, args, lambda _: good_doc()) == 2  # no studio baseline: incomplete
