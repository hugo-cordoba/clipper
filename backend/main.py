import threading, traceback, uuid
from pathlib import Path
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import pipeline

OUT = Path(__file__).parent / "output"
OUT.mkdir(exist_ok=True)
app = FastAPI()
app.mount("/clips", StaticFiles(directory=OUT), name="clips")
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()  # un solo trabajo a la vez (8 GB de RAM)


class Req(BaseModel):
    url: str
    num_clips: int = 5
    mode: str = "blur"  # "blur" | "crop"


def work(jid: str, r: Req):
    job = JOBS[jid]

    def progress(stage, p):
        job.update(stage=stage, progress=round(p, 2))

    with LOCK:
        try:
            title, clips = pipeline.run(r.url, OUT / jid, r.num_clips, r.mode, progress)
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
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"id": jid, "status": "running", "stage": "En cola", "progress": 0, "clips": []}
    bg.add_task(work, jid, r)
    return JOBS[jid]


@app.get("/api/jobs/{jid}")
def get(jid: str):
    if jid not in JOBS:
        raise HTTPException(404)
    return JOBS[jid]
