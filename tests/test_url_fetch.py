"""The url source's fetcher, against a fake resolver and a fake transport (no network)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Dict, List

import httpx
import pytest

from app.service import url_fetch
from app.service.failure_policy import JobFailure, Reason

ALLOW = frozenset({"media.example.com"})
PUBLIC = "93.184.216.34"
URL = "https://media.example.com/audio/a.wav?sig=s3cr3t"


def resolver(table: Dict[str, List[str]]) -> Callable[[str], List[str]]:
    def resolve(host: str) -> List[str]:
        return table.get(host, [])

    return resolve


def fetch(tmp_path: Path, handler, *, table=None, url=URL, max_bytes=1000, deadline_s=30.0):
    return url_fetch.fetch_url(
        url,
        tmp_path / "source",
        allowed_hosts=ALLOW,
        max_bytes=max_bytes,
        deadline_s=deadline_s,
        resolver=resolver(table or {"media.example.com": [PUBLIC]}),
        transport=httpx.MockTransport(handler),
    )


def test_fetches_from_the_checked_address_with_the_host_name_for_tls(tmp_path: Path) -> None:
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b"RIFF....WAVEdata"))

    got = fetch(tmp_path, handler)
    assert got.bytes == 16 and got.sha256 == hashlib.sha256(b"RIFF....WAVEdata").hexdigest()
    assert got.path.read_bytes() == b"RIFF....WAVEdata"
    assert got.ref == "https://media.example.com/audio/a.wav"
    (req,) = seen
    assert req.url.host == PUBLIC and req.url.raw_path == b"/audio/a.wav?sig=s3cr3t"
    assert req.headers["host"] == "media.example.com"
    assert req.extensions["sni_hostname"] == "media.example.com"
    assert req.headers["accept-encoding"] == "identity"


def test_ipv6_public_address(tmp_path: Path) -> None:
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b"x"))

    fetch(tmp_path, handler, table={"media.example.com": ["2606:2800:220:1::1"]})
    assert seen[0].url.host == "2606:2800:220:1::1"


def _never(request: httpx.Request) -> httpx.Response:
    raise AssertionError("no request may be sent")


@pytest.mark.parametrize(
    "addresses",
    [
        [],
        ["127.0.0.1"],
        ["10.0.0.5"],
        ["192.168.0.10"],
        ["169.254.169.254"],
        ["100.100.100.200"],
        ["0.0.0.0"],
        ["::1"],
        ["fe80::1"],
        ["fd00::1"],
        ["::ffff:127.0.0.1"],
        [PUBLIC, "10.0.0.5"],
        ["10.0.0.5", PUBLIC],
    ],
)
def test_names_resolving_to_non_public_addresses_are_refused(tmp_path: Path, addresses) -> None:
    with pytest.raises(JobFailure) as caught:
        fetch(tmp_path, _never, table={"media.example.com": addresses})
    assert caught.value.reason is Reason.SOURCE_REFUSED
    assert not (tmp_path / "source").exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://media.example.com/a.wav",
        "https://other.example.com/a.wav",
        "https://user:pw@media.example.com/a.wav",
        "https://127.0.0.1/a.wav",
        "https://[::1]/a.wav",
    ],
)
def test_policy_refusals_send_nothing(tmp_path: Path, url: str) -> None:
    with pytest.raises(JobFailure) as caught:
        fetch(tmp_path, _never, url=url, table={"other.example.com": [PUBLIC]})
    assert caught.value.reason is Reason.SOURCE_REFUSED


@pytest.mark.parametrize(
    "location",
    [
        "https://other.example.com/a.wav",
        "http://media.example.com/a.wav",
        "https://127.0.0.1/a.wav",
        "https://user@media.example.com/a.wav",
        "//other.example.com/a.wav",
    ],
)
def test_a_redirect_to_another_host_or_scheme_is_refused(tmp_path: Path, location: str) -> None:
    calls: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": location})

    with pytest.raises(JobFailure) as caught:
        fetch(
            tmp_path, handler, table={"media.example.com": [PUBLIC], "other.example.com": [PUBLIC]}
        )
    assert caught.value.reason is Reason.SOURCE_REFUSED
    assert len(calls) == 1


def test_a_same_host_redirect_is_followed_and_re_resolved(tmp_path: Path) -> None:
    lookups: List[str] = []
    answers = iter([[PUBLIC], ["10.0.0.9"]])

    def resolve(host: str) -> List[str]:
        lookups.append(host)
        return next(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": "/moved.wav"})

    with pytest.raises(JobFailure) as caught:
        url_fetch.fetch_url(
            URL,
            tmp_path / "source",
            allowed_hosts=ALLOW,
            max_bytes=100,
            deadline_s=10,
            resolver=resolve,
            transport=httpx.MockTransport(handler),
        )
    # The second hop resolved to a private address (DNS rebinding) and was refused.
    assert caught.value.reason is Reason.SOURCE_REFUSED
    assert lookups == ["media.example.com", "media.example.com"]


def test_same_host_redirect_then_success(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/audio/a.wav":
            return httpx.Response(307, headers={"location": "b.wav"})
        assert request.url.path == "/audio/b.wav"
        return httpx.Response(200, stream=httpx.ByteStream(b"abc"))

    got = fetch(tmp_path, handler)
    assert got.ref == "https://media.example.com/audio/b.wav" and got.bytes == 3


def test_too_many_redirects(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(JobFailure) as caught:
        fetch(tmp_path, handler)
    assert caught.value.reason is Reason.SOURCE_REFUSED


def test_a_declared_size_over_the_cap_is_refused_before_reading(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "5000"}, content=b"x" * 5000)

    with pytest.raises(JobFailure) as caught:
        fetch(tmp_path, handler, max_bytes=1000)
    assert caught.value.reason is Reason.TOO_LARGE
    assert not (tmp_path / "source").exists()


def test_a_streamed_body_over_the_cap_stops_and_leaves_no_file(tmp_path: Path) -> None:
    produced: List[int] = []

    def body():
        for _ in range(100):
            produced.append(1)
            yield b"y" * 400

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body())

    with pytest.raises(JobFailure) as caught:
        fetch(tmp_path, handler, max_bytes=1000)
    assert caught.value.reason is Reason.TOO_LARGE
    assert len(produced) < 10
    assert not (tmp_path / "source").exists()


def test_the_deadline_ends_a_slow_download(tmp_path: Path, monkeypatch) -> None:
    clock = iter(float(i) for i in range(0, 10_000, 5))
    monkeypatch.setattr(url_fetch.time, "monotonic", lambda: next(clock))

    def body():
        for _ in range(100):
            yield b"z"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body())

    with pytest.raises(JobFailure) as caught:
        fetch(tmp_path, handler, max_bytes=10_000, deadline_s=12)
    assert caught.value.reason is Reason.DEADLINE
    assert not (tmp_path / "source").exists()


def test_a_transport_timeout_is_a_deadline(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(JobFailure) as caught:
        fetch(tmp_path, handler)
    assert caught.value.reason is Reason.DEADLINE


@pytest.mark.parametrize(
    "status,headers,reason,retry",
    [
        (404, {}, Reason.SOURCE_MISSING, None),
        (410, {}, Reason.SOURCE_MISSING, None),
        (403, {}, Reason.SOURCE_MISSING, None),
        (500, {}, Reason.PROVIDER_ERROR, None),
        (503, {}, Reason.PROVIDER_ERROR, None),
        (304, {}, Reason.PROVIDER_ERROR, None),
        (429, {"retry-after": "30"}, Reason.RATE_LIMITED, 30.0),
        (429, {"retry-after": "soon"}, Reason.RATE_LIMITED, 60.0),
        (200, {"content-encoding": "gzip"}, Reason.PROVIDER_ERROR, None),
    ],
)
def test_responses_map_to_reasons(tmp_path: Path, status, headers, reason, retry) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, content=b"x")

    with pytest.raises(JobFailure) as caught:
        fetch(tmp_path, handler)
    assert caught.value.reason is reason
    assert caught.value.retry_after_s == retry


def test_a_connection_error_is_a_provider_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(JobFailure) as caught:
        fetch(tmp_path, handler)
    assert caught.value.reason is Reason.PROVIDER_ERROR


def test_the_real_resolver_is_blocked_by_the_netguard() -> None:
    """The default resolver uses the system's getaddrinfo, which tests never reach."""
    with pytest.raises(Exception) as caught:
        url_fetch.resolve_host("media.example.com")
    assert "blocked" in str(caught.value)
