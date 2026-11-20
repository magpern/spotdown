from __future__ import annotations

import hashlib
import json as _json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).parent / "static"

from . import db, jobs, spotify
from .config import get_settings
from .queue import get_queue, get_redis
from .schemas import ConvertRequest, ConvertResponse, TaskStatus, TrackMeta


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().ensure_dirs()
    db.init_db()
    yield


app = FastAPI(title="spotdown", version="0.1.0", lifespan=lifespan)

# Dev convenience: allow the page to call the API when it's opened cross-origin
# (e.g. a local file:// editor preview hitting http://localhost:8000). Tighten
# allow_origins for any real deployment.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict:
    try:
        get_redis().ping()
        redis_ok = True
    except Exception:  # noqa: BLE001
        redis_ok = False
    return {"ok": True, "redis": redis_ok}


@app.get("/api/Gettrack")
def get_track(spotify_url: str = Query(...)) -> dict:
    """Resolve a Spotify track/album/playlist URL to metadata (no audio)."""
    try:
        tracks = spotify.resolve(spotify_url)
    except spotify.SpotifyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Lookup failed: {exc}")
    return {"count": len(tracks), "tracks": [t.model_dump() for t in tracks]}


@app.post("/api/v1/convert", response_model=ConvertResponse)
def convert(req: ConvertRequest) -> ConvertResponse:
    settings = get_settings()
    bitrate = req.bitrate or settings.default_bitrate
    fmt = req.format or settings.default_format

    if bool(req.spotify_url) == bool(req.source_url):
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of spotify_url or source_url",
        )

    # Build metadata + a stable track_id (cache key).
    if req.spotify_url:
        try:
            tracks = spotify.resolve(req.spotify_url)
        except spotify.SpotifyError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not tracks:
            raise HTTPException(status_code=404, detail="No tracks found or invalid format")
        if len(tracks) > 1:
            # This MVP converts one track per request; enqueue album/playlist items yourself.
            raise HTTPException(
                status_code=400,
                detail=f"URL resolved to {len(tracks)} tracks; pass a single track URL",
            )
        meta = tracks[0]
        source_url = ""
    else:
        tid = "src_" + hashlib.sha1(req.source_url.encode()).hexdigest()[:16]
        meta = TrackMeta(id=tid, title=tid)
        source_url = req.source_url

    # Cache hit for this exact (track, format, bitrate) → return immediately.
    vid = db.variant_id(meta.id, fmt, bitrate)
    cached = db.get_done(vid)
    if cached:
        from . import storage
        return ConvertResponse(
            status="done",
            download_url=storage.public_url(cached["file_name"]),
            track=meta,
        )

    job = get_queue().enqueue(
        jobs.run_pipeline,
        meta.model_dump(),
        source_url,
        bitrate,
        fmt,
        # job_id == variant_id: distinct per quality, colon-free (RQ treats ':' as a separator).
        job_id=vid,
    )
    return ConvertResponse(status="queued", task_id=job.id, track=meta)


@app.get("/api/v1/tasks/{task_id}", response_model=TaskStatus)
def task_status(task_id: str) -> TaskStatus:
    from rq.job import Job
    from rq.exceptions import NoSuchJobError

    try:
        job = Job.fetch(task_id, connection=get_redis())
    except NoSuchJobError:
        raise HTTPException(status_code=404, detail="Unknown task_id")

    status = job.get_status(refresh=True)
    if status == "finished":
        return TaskStatus(status="done", download_url=job.result)
    if status == "failed":
        return TaskStatus(status="failed", error=(job.latest_result().exc_string if job.latest_result() else "failed"))
    if status == "started":
        return TaskStatus(status="processing")
    return TaskStatus(status="queued")


@app.get("/api/v1/analyze/{track_id}")
def analyze_track_endpoint(track_id: str, refresh: bool = False) -> dict:
    """MIR analysis (tempo, beats, energy, drops, segments) of an already-converted track.

    Runs on the highest-bitrate file for this track_id; result is cached per
    analyzer version. Pass ?refresh=true to force re-analysis.
    """
    from . import analyze as analysis
    from . import spotify as spotify_mod

    if not refresh:
        cached = db.get_analysis(track_id, analysis.ANALYZER_VERSION)
        if cached:
            return _json.loads(cached)

    file_name = db.get_best_file_for_track(track_id)
    if not file_name:
        raise HTTPException(
            status_code=404,
            detail="No converted file for this track_id; run /api/v1/convert first.",
        )
    path = get_settings().files_dir / file_name

    # Best-effort Spotify duration, to report timeline alignment (no-op for src_ ids).
    spotify_dur = None
    try:
        tracks = spotify_mod.resolve(f"https://open.spotify.com/track/{track_id}")
        if tracks:
            spotify_dur = tracks[0].duration_s
    except Exception:  # noqa: BLE001
        pass

    try:
        result = analysis.analyze_track(path, track_id, spotify_dur)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")

    db.set_analysis(track_id, analysis.ANALYZER_VERSION, _json.dumps(result))
    return result


@app.get("/files/{file_name}")
def serve_file(file_name: str) -> FileResponse:
    settings = get_settings()
    # Prevent path traversal: only serve a bare file name from files_dir.
    if "/" in file_name or "\\" in file_name or ".." in file_name:
        raise HTTPException(status_code=400, detail="Bad file name")
    path = settings.files_dir / file_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, filename=file_name)
