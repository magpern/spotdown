"""Maps a yt-dlp failure to a failure reason (pure; classbeat plan §6).

yt-dlp reports failures as messages. The message is read here only to classify it and is never
logged or returned: it can quote a URL. Order matters: a bot challenge also says "Sign in".
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from ..service.failure_policy import Reason

DEFAULT_RETRY_AFTER_S = 300.0

_RULES: Tuple[Tuple[re.Pattern[str], Reason], ...] = tuple(
    (re.compile(pattern, re.I), reason)
    for pattern, reason in (
        (r"http error 429|too many requests|rate.?limit", Reason.RATE_LIMITED),
        (r"not a bot|confirm you.re not a robot|captcha|po.?token", Reason.PROVIDER_ERROR),
        (
            r"video unavailable|private video|has been removed|no longer available|"
            r"available in your country|geo.?restrict|blocked it in your country|"
            r"confirm your age|age.?restrict|inappropriate for some users|members.only|"
            r"this live event|premieres? in|"
            r"copyright claim|account associated with this video has been terminated|"
            r"http error 404|http error 410",
            Reason.SOURCE_MISSING,
        ),
        (
            # A 403 on YouTube media is usually throttling or a bot measure: retryable. With the
            # https-only audio format, "requested format is not available" means the provider
            # served no such format (an HLS- or SABR-only response after a client change), not
            # that the video is gone: an outage, so alternates are not tried in vain.
            r"requested format is not available|http error 403|http error 5\d\d|"
            r"unable to download|timed out|connection|network|"
            r"temporary failure|name resolution|ssl|eof occurred",
            Reason.PROVIDER_ERROR,
        ),
    )
)


def classify(message: str) -> Tuple[Reason, Optional[float]]:
    """``(reason, retry_after_s)``; anything unrecognised is a provider error."""
    for pattern, reason in _RULES:
        if pattern.search(message or ""):
            retry = DEFAULT_RETRY_AFTER_S if reason is Reason.RATE_LIMITED else None
            return reason, retry
    return Reason.PROVIDER_ERROR, None
