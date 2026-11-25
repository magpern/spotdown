"""The ``youtube`` matcher and source (spotdown#10; classbeat plan §2, §5, §6).

Pure rules (``normalise_policy``, ``match_calc``, ``error_policy``) are kept apart from the one
module that drives yt-dlp (``ytdlp_client``). Tests and CI never call YouTube: every test fakes
yt-dlp (an agent may check it by hand, read-only: AGENTS.md "Safety envelope"). The source is on by default and off with ``ALLOW_YTDLP_SOURCING=false``.
"""
