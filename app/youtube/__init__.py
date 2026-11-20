"""The ``youtube`` matcher and source (spotdown#10; classbeat plan §2, §5, §6).

Pure rules (``normalise_policy``, ``match_calc``, ``error_policy``) are kept apart from the one
module that drives yt-dlp (``ytdlp_client``). Agents and CI never call YouTube: every test fakes
yt-dlp, and the source is off unless the operator sets ``ALLOW_YTDLP_SOURCING=true``.
"""
