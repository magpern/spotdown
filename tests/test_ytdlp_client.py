"""The yt-dlp driver against a fake ``YoutubeDL``: hardened options, id handling, the checks made
before download, and every failure kind (classbeat plan §6). No network, no real yt-dlp."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.service.failure_policy import JobFailure, Reason, outcome_for
from app.youtube import error_policy
from app.youtube.ytdlp_client import YtDlpClient

from .youtube_fakes import FakeWorld, entry

VID = "dQw4w9WgXcQ"


def test_search_is_flat_hardened_and_downloads_nothing() -> None:
    world = FakeWorld()
    world.entries = [entry(VID, "t", "c", 200.0)]
    found = world.client().search("the weeknd blinding lights", 5)
    assert found == world.entries
    assert world.searches == ["ytsearch5:the weeknd blinding lights"] and world.downloads == []
    (opts,) = world.opened
    assert opts["allowed_extractors"] == ["youtube:search"]
    assert opts["extract_flat"] == "in_playlist" and opts["skip_download"] is True
    assert opts["noplaylist"] is True and opts["postprocessors"] == []
    assert opts["cachedir"] is False and opts["logger"] is not None


def test_search_limits_the_result_count() -> None:
    world = FakeWorld()
    world.entries = [entry(f"{i:011d}", "t", "c", 1.0) for i in range(40)]
    assert len(world.client().search("q", 500)) == 20
    assert world.searches == ["ytsearch20:q"]


def test_download_builds_the_url_from_the_validated_id(tmp_path: Path) -> None:
    world = FakeWorld()
    got = world.client().download(VID, tmp_path / "yt", max_bytes=10_000, max_duration_s=600)
    assert got.path.name == "source.m4a" and got.path.read_bytes() == world.audio
    assert got.metadata_duration_s == 12.0
    assert world.calls == [("extract_info", f"https://www.youtube.com/watch?v={VID}"),
                           ("download", VID)]  # fmt: skip
    (opts,) = world.opened
    assert opts["allowed_extractors"] == ["youtube"]
    assert opts["max_filesize"] == 10_000 and opts["noplaylist"] is True
    assert opts["outtmpl"] == str(tmp_path / "yt" / "source.%(ext)s")
    assert opts["format"] == (
        "bestaudio[ext=m4a][protocol=https]/bestaudio[ext=mp3][protocol=https]"
    )
    assert_runs_no_program(opts)


def assert_runs_no_program(opts) -> None:
    """Nothing in the options lets yt-dlp run ffmpeg (or anything) on the downloaded bytes."""
    assert opts["postprocessors"] == [] and opts["fixup"] == "never"
    assert opts["postprocessor_hooks"] == [] and opts["post_hooks"] == []
    assert opts["external_downloader"] == {} and opts["hls_prefer_native"] is True
    for key in ("writethumbnail", "writesubtitles", "writeautomaticsub", "writeinfojson"):
        assert opts[key] is False
    for key in ("exec", "exec_cmd", "merge_output_format", "final_ext", "extractaudio"):
        assert key not in opts


def test_search_options_run_no_program() -> None:
    world = FakeWorld()
    world.client().search("q", 5)
    assert_runs_no_program(world.opened[0])


@pytest.mark.parametrize(
    "video_id",
    [
        "",
        "short",
        "dQw4w9WgXcQx",
        "dQw4w9WgXc/",
        "../../etc/pa",
        "https://x.y",
        "dQw4w9WgXc\n",
        "dQw4w9WgXcQ\n",  # 11 valid characters and a newline: "$" alone would accept it
    ],
)
def test_download_refuses_anything_but_an_11_character_id(tmp_path: Path, video_id: str) -> None:
    world = FakeWorld()
    with pytest.raises(JobFailure) as caught:
        world.client().download(video_id, tmp_path, max_bytes=10, max_duration_s=10)
    assert caught.value.reason is Reason.SOURCE_REFUSED
    assert world.calls == [] and world.opened == []


@pytest.mark.parametrize(
    "info,reason",
    [
        ({"id": "otherid0000", "duration": 12.0}, Reason.SOURCE_MISSING),
        ({"id": VID, "duration": 12.0, "live_status": "is_live"}, Reason.SOURCE_MISSING),
        ({"id": VID, "duration": 12.0, "live_status": "is_upcoming"}, Reason.SOURCE_MISSING),
        ({"id": VID}, Reason.SOURCE_MISSING),
        ({"id": VID, "duration": 4000.0}, Reason.TOO_LONG),
        ({"id": VID, "duration": 12.0, "filesize": 10**9}, Reason.TOO_LARGE),
        ({"id": VID, "duration": 12.0, "filesize_approx": 10**9}, Reason.TOO_LARGE),
    ],
)
def test_metadata_checks_stop_before_any_audio_is_fetched(tmp_path: Path, info, reason) -> None:
    world = FakeWorld()
    world.infos[VID] = info
    with pytest.raises(JobFailure) as caught:
        world.client().download(VID, tmp_path, max_bytes=10_000, max_duration_s=1200)
    assert caught.value.reason is reason
    assert world.downloads == []


def test_a_file_over_the_cap_is_refused(tmp_path: Path) -> None:
    world = FakeWorld()
    world.audio = b"x" * 5000
    with pytest.raises(JobFailure) as caught:
        world.client().download(VID, tmp_path, max_bytes=100, max_duration_s=1200)
    assert caught.value.reason is Reason.TOO_LARGE


# Messages in the shape yt-dlp prints them; the URL in them must never surface.
FAILURES = [
    ("ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader",
     "source_unavailable"),
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access",
     "source_unavailable"),
    ("ERROR: [youtube] x: The uploader has not made this video available in your country",
     "source_unavailable"),
    ("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate",
     "source_unavailable"),
    # An HLS- or SABR-only response leaves no https audio format: an outage, not a gone video.
    ("ERROR: [youtube] x: Requested format is not available. Use --list-formats",
     "provider_error"),
    ("ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies", "provider_error"),
    ("ERROR: [youtube] x: Sign in to confirm you're not a bot", "provider_error"),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "provider_error"),
    ("ERROR: unable to download video data: HTTP Error 503: Service Unavailable",
     "provider_error"),
    ("ERROR: [youtube] x: Unable to download API page: <urlopen error timed out>",
     "provider_error"),
    ("ERROR: something nobody has seen before", "provider_error"),
    ("ERROR: unable to download webpage: HTTP Error 429: Too Many Requests (caused by "
     "https://www.youtube.com/watch?v=x&sig=s3cr3t)", "rate_limited"),
]  # fmt: skip


@pytest.mark.parametrize("message,status", FAILURES)
def test_every_failure_kind_maps_to_its_status(tmp_path: Path, message: str, status: str) -> None:
    for action in ("search", "download"):
        world = FakeWorld()
        world.search_error = world.download_error = message
        with pytest.raises(JobFailure) as caught:
            if action == "search":
                world.client().search("q", 5)
            else:
                world.client().download(VID, tmp_path, max_bytes=100, max_duration_s=100)
        assert outcome_for(caught.value.reason)[0] == status
        assert "s3cr3t" not in str(caught.value) and "http" not in str(caught.value)
        if status == "rate_limited":
            assert caught.value.retry_after_s == error_policy.DEFAULT_RETRY_AFTER_S
        else:
            assert caught.value.retry_after_s is None


def test_the_real_factory_is_never_needed_by_the_tests() -> None:
    """The default client builds a real YoutubeDL lazily; constructing it calls nothing."""
    YtDlpClient()
