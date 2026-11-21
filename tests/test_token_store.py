"""The generated standalone token (ADR 0003 item 2, spotdown#52 "Token creation"): created once,
never regenerated over an existing file, and every interleaving and crash point converges on one
valid token. Workers are threads driven step by step (tests/token_race_helpers.py), or child
processes killed at a step."""

from __future__ import annotations

import errno
import itertools
import multiprocessing
import os
import random
import re
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from app import token_store
from app.token_store import TokenSource, create_or_read

from .token_race_helpers import OsProxy, Scheduler, install, link_unsupported, tmp_files

STEPS = {"read", "open", "write", "link", "unlink"}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _final(tmp_path: Path) -> Path:
    tmp_path.mkdir(exist_ok=True)
    return tmp_path / "standalone_token"


def _valid(final: Path) -> str:
    raw = final.read_bytes()
    assert re.fullmatch(rb"[0-9a-f]{64}\n", raw), raw
    return raw[:64].decode()


class FakeClock:
    def __init__(self, monkeypatch) -> None:
        self.t = 1000.0
        monkeypatch.setattr(token_store, "_monotonic", lambda: self.t)
        monkeypatch.setattr(token_store, "_sleep", self.sleep)

    def sleep(self, s: float) -> None:
        self.t += s


# --- one construction -----------------------------------------------------------------------------


def test_a_construction_creates_the_token_and_a_second_reuses_it(tmp_path, capsys) -> None:
    final = _final(tmp_path)
    first = TokenSource(final, owner=False).current()
    assert first and HEX64.match(first) and _valid(final) == first
    if os.name == "posix":
        assert stat.S_IMODE(final.stat().st_mode) == 0o600
    assert tmp_files(tmp_path) == []
    assert TokenSource(final, owner=False).current() == first
    out = capsys.readouterr().out
    assert str(final) in out and first not in out


def test_the_temporary_file_is_opened_exclusively_without_following_links(monkeypatch, tmp_path):
    proxy = OsProxy()
    monkeypatch.setattr(token_store, "os", proxy)
    create_or_read(_final(tmp_path))
    opens = [args for _, op, args in proxy.calls if op == "open" and ".tmp-" in str(args[0])]
    assert len(opens) == 1
    flags, mode = opens[0][1], opens[0][2]
    want = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    assert flags & want == want and mode == 0o600


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name != "posix", reason="POSIX symlinks")
def test_a_symlink_at_the_temporary_path_is_not_written_through(monkeypatch, tmp_path) -> None:
    final = _final(tmp_path)
    target = tmp_path / "elsewhere"

    class Secrets:
        @staticmethod
        def token_hex(n: int) -> str:
            return "ab" * n

    monkeypatch.setattr(token_store, "secrets", Secrets)
    planted = final.with_name(f"standalone_token.tmp-{os.getpid()}-{'ab' * 8}")
    planted.symlink_to(target)
    assert create_or_read(final) is None  # the open fails: 503
    assert not target.exists() and not final.exists() and planted.is_symlink()


def test_no_o_nofollow_on_the_platform_still_constructs(monkeypatch, tmp_path) -> None:
    proxy = OsProxy()
    proxy.hidden.add("O_NOFOLLOW")
    monkeypatch.setattr(token_store, "os", proxy)
    final = _final(tmp_path)
    assert create_or_read(final) == _valid(final)


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("how", ["fsync EINVAL", "open fails"])
def test_the_directory_fsync_is_best_effort(monkeypatch, tmp_path, fallback, how) -> None:
    final = _final(tmp_path)

    def before(op: str, args: tuple) -> None:
        if fallback:
            link_unsupported(op, args)
        if how == "open fails" and op == "open" and Path(args[0]) == tmp_path:
            raise OSError(errno.EACCES, "no directory open")
        if how == "fsync EINVAL" and op == "fsync" and stat.S_ISDIR(os.fstat(args[0]).st_mode):
            raise OSError(errno.EINVAL, "no directory fsync")

    proxy = OsProxy(before)
    monkeypatch.setattr(token_store, "os", proxy)
    source = TokenSource(final, owner=False)
    assert source.current() == _valid(final)
    dir_ops = [a for _, op, a in proxy.calls if op == "open" and Path(a[0]) == tmp_path]
    assert dir_ops  # the directory fsync was attempted, and its failure was ignored


# --- never regenerate, never delete another's file ------------------------------------------------


@pytest.mark.parametrize("content", [b"", b"abc123", b"g" * 64, b"A" * 64, b"a" * 64 + b"\n\n"])
def test_a_bad_final_file_is_503_and_never_overwritten(monkeypatch, tmp_path, capsys, content):
    clock = FakeClock(monkeypatch)
    final = _final(tmp_path)
    final.write_bytes(content)
    start = clock.t
    source = TokenSource(final, owner=False)
    assert source.current() is None
    assert clock.t - start >= token_store.CONSTRUCTION_WAIT_S  # it waited the 2 s re-read
    assert final.read_bytes() == content and tmp_files(tmp_path) == []
    assert "delete it and restart" in capsys.readouterr().out


def test_a_foreign_temporary_file_is_left_alone(monkeypatch, tmp_path) -> None:
    final = _final(tmp_path)
    foreign = tmp_path / "standalone_token.tmp-x"

    def before(op: str, args: tuple) -> None:
        if op == "link":
            foreign.write_bytes(b"someone else's")

    proxy = OsProxy(before)
    monkeypatch.setattr(token_store, "os", proxy)
    assert create_or_read(final) == _valid(final)
    assert foreign.read_bytes() == b"someone else's"
    unlinked = [a[0] for _, op, a in proxy.calls if op == "unlink"]
    assert len(unlinked) == 1 and ".tmp-" in str(unlinked[0]) and Path(unlinked[0]) != foreign


def test_the_store_has_no_other_deleting_or_replacing_call() -> None:
    source = Path(token_store.__file__).read_text(encoding="utf-8")
    assert source.count("os.unlink(") == 1 and "os.unlink(tmp)" in source
    for banned in ("remove(", "rmtree", "replace(", "rename(", "truncate(", "O_TRUNC", "glob("):
        assert banned not in source, banned


# --- symlinks -------------------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
@pytest.mark.parametrize("target", ["missing", "garbage", "valid"])
def test_a_symlink_at_the_final_path(monkeypatch, tmp_path, target) -> None:
    FakeClock(monkeypatch)
    final = _final(tmp_path)
    pointee = tmp_path / "pointee"
    if target == "garbage":
        pointee.write_text("not a token")
    if target == "valid":
        pointee.write_text("c" * 64 + "\n")
    final.symlink_to(pointee)
    got = TokenSource(final, owner=False).current()
    assert got == ("c" * 64 if target == "valid" else None)
    assert final.is_symlink() and tmp_files(tmp_path) == []
    assert pointee.exists() == (target != "missing")  # nothing was written through the link


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_the_owner_file_is_read_through_a_symlink(tmp_path) -> None:
    real = tmp_path / "secret"
    real.write_text("S" * 40 + "\n")
    link = tmp_path / "run-secrets-link"
    link.symlink_to(real)
    assert TokenSource(link, owner=True).current() == "S" * 40


# --- interleavings --------------------------------------------------------------------------------


def _race(monkeypatch, tmp_path, names, schedule, fail=None, ops=STEPS) -> dict:
    final = _final(tmp_path)
    sched = Scheduler(ops)
    install(monkeypatch, sched, fail)
    for name in names:
        sched.start(name, lambda: create_or_read(final))
    for name in schedule:
        sched.step(name)
    results = sched.release_all()
    token = _valid(final)
    assert all(r == token for r in results.values()), results
    assert tmp_files(tmp_path) == []
    return results


def _schedules(names, per_worker):
    """Every order of ``per_worker`` steps of each worker."""
    slots = list(range(len(names) * per_worker))
    for picks in itertools.combinations(slots, per_worker):
        yield ["A" if i in picks else "B" for i in slots]


def test_every_order_of_two_constructions_converges(monkeypatch, tmp_path) -> None:
    for i, schedule in enumerate(_schedules(["A", "B"], 5)):
        with monkeypatch.context() as m:
            _race(m, tmp_path / f"s{i}", ["A", "B"], schedule)


def test_a_paused_between_write_and_link_while_b_finishes(monkeypatch, tmp_path) -> None:
    final = _final(tmp_path)
    sched = Scheduler(STEPS)
    install(monkeypatch, sched)
    sched.start("A", lambda: create_or_read(final))
    sched.run_until("A", "link")
    sched.start("B", lambda: create_or_read(final))
    while sched.step("B") is not None:
        pass
    b_token = sched.results["B"]
    assert _valid(final) == b_token
    results = sched.release_all()
    assert results["A"] == b_token  # A's link got EEXIST and A re-read B's file


def test_three_constructions_with_two_losers(monkeypatch, tmp_path) -> None:
    rng = random.Random(52)
    order = ["A"] * 6 + ["B"] * 6 + ["C"] * 6
    for i in range(60):
        rng.shuffle(order)
        with monkeypatch.context() as m:
            _race(m, tmp_path / f"s{i}", ["A", "B", "C"], list(order))


def test_enoent_on_link_uses_an_existing_valid_final_file(monkeypatch, tmp_path) -> None:
    final = _final(tmp_path)

    def before(op: str, args: tuple) -> None:
        if op == "link":
            os.unlink(args[0])  # our temporary file removed from outside
            final.write_text("d" * 64 + "\n")

    monkeypatch.setattr(token_store, "os", OsProxy(before))
    assert create_or_read(final) == "d" * 64


@pytest.mark.parametrize("workers", [["A", "B"], ["A", "B", "C"]])
def test_the_fallback_race_converges(monkeypatch, tmp_path, workers) -> None:
    rng = random.Random(len(workers))
    order = [w for w in workers for _ in range(7)]
    for i in range(40):
        rng.shuffle(order)
        with monkeypatch.context() as m:
            _race(m, tmp_path / f"s{i}", workers, list(order), link_unsupported, ops=None)


def test_a_fallback_loser_reading_before_the_winners_write_gets_the_full_token(
    monkeypatch, tmp_path
) -> None:
    final = _final(tmp_path)
    sched = Scheduler(None)
    install(monkeypatch, sched, link_unsupported)
    sched.start("W", lambda: create_or_read(final))
    sched.run_until("W", "write", nth=2)  # W created the final file and holds before writing
    assert final.exists() and final.read_bytes() == b""
    sched.start("L", lambda: create_or_read(final))
    sched.run_until("L", "read", nth=3)  # L read the empty final file and keeps polling
    sched.step("W")  # the write
    results = sched.release_all()
    assert results["L"] == results["W"] == _valid(final)


def test_a_stalled_fallback_winner_is_picked_up_later(monkeypatch, tmp_path) -> None:
    final = _final(tmp_path)
    sched = Scheduler(None)
    proxy = install(monkeypatch, sched, link_unsupported)
    clock = FakeClock(monkeypatch)
    sched.start("W", lambda: TokenSource(final, owner=False))
    sched.run_until("W", "write", nth=2)
    sched.start("L", lambda: TokenSource(final, owner=False))
    sched.free.add("L")
    with sched.cv:
        sched.cv.notify_all()
    sched.threads["L"].join(10)
    loser: Any = sched.results["L"]
    assert isinstance(loser, TokenSource) and loser.current() is None  # more than 2 s: 503
    results = sched.release_all()
    token = _valid(final)
    assert results["W"].current() == token
    assert loser.current() is None  # at most one re-read a second
    clock.t += token_store.REREAD_INTERVAL_S
    assert loser.current() == token
    # The loser found the winner's (empty) final file, so it created and wrote nothing.
    assert [op for who, op, _ in proxy.calls if who == "L" and op in ("open", "write")] == []
    assert final.read_bytes() == (token + "\n").encode()


# --- recovery and named outcomes ------------------------------------------------------------------


def test_eexist_with_the_final_file_gone_is_503_then_recovers(monkeypatch, tmp_path) -> None:
    clock = FakeClock(monkeypatch)
    final = _final(tmp_path)

    def before(op: str, args: tuple) -> None:
        if op == "link":
            raise FileExistsError(errno.EEXIST, "exists")  # then deleted from outside

    monkeypatch.setattr(token_store, "os", OsProxy(before))
    source = TokenSource(final, owner=False)
    assert source.current() is None and not final.exists()
    final.write_text("e" * 64 + "\n")
    assert source.current() is None
    clock.t += 1.0
    assert source.current() == "e" * 64


def test_a_failed_fallback_write_is_503_then_recovers(monkeypatch, tmp_path, capsys) -> None:
    clock = FakeClock(monkeypatch)
    final = _final(tmp_path)

    def before(op: str, args: tuple) -> None:
        link_unsupported(op, args)
        if op == "write" and final.exists() and os.fstat(args[0]).st_ino == final.stat().st_ino:
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(token_store, "os", OsProxy(before))
    source = TokenSource(final, owner=False)
    assert source.current() is None and final.read_bytes() == b""
    assert str(final) in capsys.readouterr().out
    final.write_text("f" * 64 + "\n")  # the owner deletes it and writes a valid one
    clock.t += 1.0
    assert source.current() == "f" * 64


# --- crash points (child processes) ---------------------------------------------------------------


def _crash_child(final: str, crash_after: str, fallback: bool) -> None:
    """Runs in a child process: construct, and die right after the named operation."""
    path = Path(final)

    class Crashing(OsProxy):
        def __getattr__(self, name: str) -> Any:
            real = super().__getattr__(name)
            if name != crash_after.split(":")[0]:
                return real

            def call(*args: Any) -> Any:
                out = real(*args)
                if crash_after == "open:final" and Path(args[0]) != path:
                    return out
                if crash_after == "open" and ".tmp-" not in str(args[0]):
                    return out
                os._exit(17)

            return call

    token_store.os = Crashing(link_unsupported if fallback else None)  # type: ignore[assignment]
    create_or_read(path)
    os._exit(0)


def _run_crash(final: Path, crash_after: str, fallback: bool = False) -> None:
    ctx = multiprocessing.get_context("fork")
    child = ctx.Process(target=_crash_child, args=(str(final), crash_after, fallback))
    child.start()
    child.join(20)
    assert child.exitcode == 17, child.exitcode


fork_only = pytest.mark.skipif(sys.platform != "linux", reason="needs fork")


@fork_only
@pytest.mark.parametrize("crash_after", ["open", "write", "fsync"])
def test_a_crash_before_the_link_leaves_no_final_file(tmp_path, crash_after) -> None:
    final = _final(tmp_path)
    _run_crash(final, crash_after)
    assert not final.exists()
    orphans = tmp_files(tmp_path)
    assert len(orphans) == 1
    orphan, before = orphans[0], orphans[0].read_bytes()
    token = TokenSource(final, owner=False).current()
    assert token is not None and token == _valid(final) and token.encode() not in before
    assert orphan.exists() and orphan.read_bytes() == before  # never read, never deleted


@fork_only
@pytest.mark.parametrize("crash_after", ["link", "unlink"])
def test_a_crash_after_the_link_leaves_a_valid_final_file(tmp_path, crash_after) -> None:
    final = _final(tmp_path)
    _run_crash(final, crash_after)
    token = _valid(final)
    assert len(tmp_files(tmp_path)) == (1 if crash_after == "link" else 0)
    assert TokenSource(final, owner=False).current() == token


@fork_only
def test_a_crash_between_the_fallback_create_and_write_is_the_documented_residual(
    monkeypatch, tmp_path, capsys
) -> None:
    final = _final(tmp_path)
    _run_crash(final, "open:final", fallback=True)
    assert final.read_bytes() == b""
    FakeClock(monkeypatch)
    assert TokenSource(final, owner=False).current() is None
    assert "delete it and restart" in capsys.readouterr().out
    assert final.read_bytes() == b""  # not overwritten


@pytest.mark.parametrize("other_won", [False, True])
def test_a_failed_temporary_write_rereads_the_final_file(monkeypatch, tmp_path, other_won) -> None:
    final = _final(tmp_path)

    def before(op: str, args: tuple) -> None:
        if op == "write":
            if other_won:
                final.write_text("9" * 64 + "\n")
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(token_store, "os", OsProxy(before))
    assert create_or_read(final) == ("9" * 64 if other_won else None)
    assert tmp_files(tmp_path) == []  # our own temporary file is still removed


def test_an_owner_file_with_junk_past_the_read_limit_is_refused(tmp_path, capsys) -> None:
    owner = tmp_path / "owner"
    owner.write_bytes(b"k" * 40 + b" " * (token_store._READ_LIMIT - 40) + b"junk")
    assert TokenSource(owner, owner=True).current() is None  # not truncated into "k" * 40
    assert "owner-token rule: length" in capsys.readouterr().out
    owner.write_bytes(b"k" * 40 + b" " * (token_store._READ_LIMIT - 40))  # exactly the limit
    assert TokenSource(owner, owner=True).current() == "k" * 40
