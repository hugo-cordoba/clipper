import threading, traceback, uuid
from typing import Literal
from pathlib import Path
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import pipeline

OUT = Path(__file__).parent / "output"
OUT.mkdir(exist_ok=True)
app = FastAPI()
app.mount("/clips", StaticFiles(directory=OUT), name="clips")
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()  # un solo trabajo a la vez (8 GB de RAM)


class Req(BaseModel):
    url: str
    num_clips: int = Field(5, ge=1, le=10)
    mode: Literal["blur", "crop", "face"] = "blur"
    min_len: int = Field(20, ge=5, le=170)
    max_len: int = Field(60, ge=10, le=180)
    zoom: float = Field(0.12, ge=0, le=0.4)  # 0 = sin zoom


def work(jid: str, r: Req):
    job = JOBS[jid]

    def progress(stage, p):
        job.update(stage=stage, progress=round(p, 2))

    with LOCK:
        try:
            opts = pipeline.Opts(r.num_clips, r.mode, r.min_len, r.max_len, r.zoom)
            title, clips = pipeline.run(r.url, OUT / jid, opts, progress)
            for c in clips:
                c["url"] = f"/clips/{jid}/{c['file']}"
            job.update(status="done", stage="Listo", progress=1, title=title, clips=clips)
        except Exception as e:
            traceback.print_exc()
            job.update(status="error", stage="Error", error=str(e)[:400])


@app.post("/api/jobs")
def create(r: Req, bg: BackgroundTasks):
    if "youtu" not in r.url:
        raise HTTPException(400, "Pega una URL de YouTube")
    if r.min_len >= r.max_len:
        raise HTTPException(400, "La duración mínima debe ser menor que la máxima")
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"id": jid, "status": "running", "stage": "En cola", "progress": 0, "clips": []}
    bg.add_task(work, jid, r)
    return JOBS[jid]


@app.get("/api/jobs/{jid}")
def get(jid: str):
    if jid not in JOBS:
        raise HTTPException(404)
    return JOBS[jid]