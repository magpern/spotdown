"""The ``url`` source: fetch one operator-allowlisted https file with a size cap and a deadline.

Each hop is checked by ``url_policy``; the name is resolved here and every address must be public.
The connection then goes to that checked address (the TLS name and certificate check still use the
host name), so a second DNS answer cannot move it elsewhere. No proxy from the environment, no
automatic redirects (only same-host ones, re-checked), no content encoding.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, FrozenSet, List, Optional

import httpx

from . import url_policy
from .failure_policy import JobFailure, Reason

MAX_REDIRECTS = 3
DEFAULT_RETRY_AFTER_S = 60.0

Resolver = Callable[[str], List[str]]


@dataclass(frozen=True)
class Fetched:
    path: Path
    sha256: str
    bytes: int
    ref: str


def resolve_host(host: str) -> List[str]:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return []
    return sorted({str(info[4][0]) for info in infos})


def _address_url(address: str, target: str) -> httpx.URL:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    netloc = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
    return httpx.URL(f"https://{netloc}{target}")


def _retry_after(value: Optional[str]) -> float:
    try:
        return max(0.0, float(value)) if value else DEFAULT_RETRY_AFTER_S
    except ValueError:
        return DEFAULT_RETRY_AFTER_S


def _check_status(status: int, headers: httpx.Headers) -> None:
    if status in (403, 404, 410, 451):
        raise JobFailure(Reason.SOURCE_MISSING)
    if status == 429:
        raise JobFailure(Reason.RATE_LIMITED, _retry_after(headers.get("retry-after")))
    if not 200 <= status < 300:
        raise JobFailure(Reason.PROVIDER_ERROR)
    if headers.get("content-encoding", "identity").lower() != "identity":
        raise JobFailure(Reason.PROVIDER_ERROR)


def _ordered(addresses: List[str]) -> List[str]:
    """IPv4 first (many containers have no IPv6 route), each family in resolver order."""
    return sorted(addresses, key=lambda a: ipaddress.ip_address(a.split("%", 1)[0]).version)


def _connect(
    client: httpx.Client, addresses: List[str], checked: url_policy.CheckedUrl, end: float
) -> httpx.Response:
    """Sends the request to each checked address in turn until one connects. Each hop gets the
    time left of the job's deadline, at most 30 s per network wait."""
    failure = Reason.PROVIDER_ERROR
    for address in addresses:
        left = end - time.monotonic()
        if left <= 0:
            raise JobFailure(Reason.DEADLINE)
        request = client.build_request(
            "GET",
            _address_url(address, checked.target),
            headers={"Host": checked.host, "Accept-Encoding": "identity"},
            extensions={"sni_hostname": checked.host},
            timeout=httpx.Timeout(min(30.0, left)),
        )
        try:
            return client.send(request, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Nothing was sent to this address: the next one may still answer.
            failure = Reason.DEADLINE if isinstance(exc, httpx.ConnectTimeout) else failure
            continue
        except httpx.TimeoutException:
            raise JobFailure(Reason.DEADLINE) from None
        except httpx.HTTPError:
            raise JobFailure(Reason.PROVIDER_ERROR) from None
    raise JobFailure(failure)


def fetch_url(
    url: str,
    dest: Path,
    *,
    allowed_hosts: FrozenSet[str],
    max_bytes: int,
    deadline_s: float,
    resolver: Resolver = resolve_host,
    transport: Optional[httpx.BaseTransport] = None,
) -> Fetched:
    """Download ``url`` to ``dest``. Raises ``JobFailure``; a partial file is removed."""
    end = time.monotonic() + deadline_s
    try:
        checked = url_policy.check_url(url, allowed_hosts)
    except url_policy.UrlRefused:
        raise JobFailure(Reason.SOURCE_REFUSED) from None
    current = url
    with httpx.Client(transport=transport, trust_env=False, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            addresses = resolver(checked.host)
            if not url_policy.all_public(addresses):
                raise JobFailure(Reason.SOURCE_REFUSED)
            response = _connect(client, _ordered(addresses), checked, end)
            try:
                if response.has_redirect_location:
                    location = response.headers.get("location", "")
                    try:
                        current = url_policy.check_redirect(current, location, allowed_hosts)
                    except url_policy.UrlRefused:
                        raise JobFailure(Reason.SOURCE_REFUSED) from None
                    checked = url_policy.check_url(current, allowed_hosts)
                    continue
                _check_status(response.status_code, response.headers)
                return _stream_to(response, dest, max_bytes, end, checked.ref)
            finally:
                response.close()
    raise JobFailure(Reason.SOURCE_REFUSED)  # too many redirects


def _stream_to(
    response: httpx.Response, dest: Path, max_bytes: int, end: float, ref: str
) -> Fetched:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise JobFailure(Reason.TOO_LARGE)
    digest = hashlib.sha256()
    size = 0
    try:
        with dest.open("wb") as out:
            for chunk in response.iter_raw():
                size += len(chunk)
                if size > max_bytes:
                    raise JobFailure(Reason.TOO_LARGE)
                if time.monotonic() > end:
                    raise JobFailure(Reason.DEADLINE)
                digest.update(chunk)
                out.write(chunk)
    except httpx.TimeoutException:
        dest.unlink(missing_ok=True)
        raise JobFailure(Reason.DEADLINE) from None
    except httpx.HTTPError:
        dest.unlink(missing_ok=True)
        raise JobFailure(Reason.PROVIDER_ERROR) from None
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return Fetched(path=dest, sha256=digest.hexdigest(), bytes=size, ref=ref)
