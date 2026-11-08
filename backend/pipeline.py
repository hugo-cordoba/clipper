import json, os, re, subprocess, gc
from pathlib import Path
import httpx
import yt_dlp

LLM = os.environ.get("CLIPPER_LLM", "qwen3:4b")
WHISPER = os.environ.get("CLIPPER_WHISPER", "mlx-community/whisper-large-v3-turbo")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MIN_LEN, MAX_LEN = 20, 60
FFMPEG = next((x for x in ("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",) if os.path.exists(x)), "ffmpeg")


def download(url, out: Path):
    opts = {"format": "bv*[height<=1080]+ba/b", "merge_output_format": "mp4",
            "outtmpl": str(out / "source.%(ext)s"), "quiet": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    return str(out / "source.mp4"), info.get("title", ""), info.get("heatmap") or []


def transcribe(video):
    import mlx_whisper
    r = mlx_whisper.transcribe(video, path_or_hf_repo=WHISPER, word_timestamps=True)
    gc.collect()
    return [{"start": s["start"], "end": s["end"], "text": s["text"].strip(),
             "words": s.get("words", [])} for s in r["segments"]]


def _chunks(segs, window=360):
    cur, t0 = [], None
    for s in segs:
        t0 = s["start"] if t0 is None else t0
        cur.append(s)
        if s["end"] - t0 >= window:
            yield cur
            cur, t0 = [], None
    if cur:
        yield cur


PROMPT = """Eres editor de clips virales para TikTok, Reels y YouTube Shorts.
Del siguiente fragmento de transcripción (formato [segundos] texto) elige los {n} mejores momentos.
Cada clip debe: durar entre {lo} y {hi} segundos, empezar con un gancho fuerte, tener sentido por sí solo
y terminar en una frase completa. Responde SOLO JSON con esta forma:
{{"clips":[{{"start":0,"end":0,"score":1-10,"title":"...","reason":"...","hashtags":["..."]}}]}}
Escribe title, reason y hashtags en el idioma del vídeo.

{text}"""


def ask_llm(chunk, n=3):
    text = "\n".join(f"[{int(s['start'])}] {s['text']}" for s in chunk)
    body = {"model": LLM, "stream": False, "format": "json", "think": False,
            "options": {"num_ctx": 8192, "temperature": 0.3},
            "messages": [{"role": "user", "content": PROMPT.format(n=n, lo=MIN_LEN, hi=MAX_LEN, text=text)}]}
    r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=600)
    r.raise_for_status()
    raw = re.sub(r"<think>.*?</think>", "", r.json()["message"]["content"], flags=re.S)
    try:
        return json.loads(raw).get("clips", [])
    except json.JSONDecodeError:
        return []


def _snap(segs, start, end):
    """Ajusta a límites de frase y fuerza duración válida."""
    st = min((s["start"] for s in segs), key=lambda x: abs(x - start))
    ends = [s["end"] for s in segs]
    en = min(ends, key=lambda x: abs(x - end))
    if en - st > MAX_LEN:
        en = max([e for e in ends if st + MIN_LEN <= e <= st + MAX_LEN] or [st + MAX_LEN])
    return (st, en) if en - st >= MIN_LEN else None


def _heat(heatmap, st, en):
    vals = [h["value"] for h in heatmap if h["end_time"] > st and h["start_time"] < en]
    return sum(vals) / len(vals) if vals else 0


def pick_clips(segs, heatmap, total, progress):
    chunks = list(_chunks(segs))
    found = []
    for i, ch in enumerate(chunks):
        progress(f"Analizando con IA ({i + 1}/{len(chunks)})", 0.35 + 0.35 * i / len(chunks))
        for c in ask_llm(ch):
            try:
                snapped = _snap(segs, float(c["start"]), float(c["end"]))
                llm = max(1, min(10, float(c.get("score", 5)))) / 10
            except (KeyError, ValueError, TypeError):
                continue
            if not snapped:
                continue
            heat = _heat(heatmap, *snapped)
            c.update(start=snapped[0], end=snapped[1],
                     score=round((0.7 * llm + 0.3 * heat if heatmap else llm) * 100))
            found.append(c)
    found.sort(key=lambda c: -c["score"])
    picked = []
    for c in found:  # sin solapes
        if all(c["end"] <= p["start"] or c["start"] >= p["end"] for p in picked):
            picked.append(c)
        if len(picked) == total:
            break
    return picked


def _ass(words, path):
    def t(x):
        return f"{int(x // 3600)}:{int(x % 3600 // 60):02d}:{x % 60:05.2f}"
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
            "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            "Style: D,Arial,78,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,6,2,2,60,60,420,1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    lines = []
    for i in range(0, len(words), 3):
        g = words[i:i + 3]
        txt = " ".join(w["word"].strip() for w in g).upper().replace("\\", "")
        lines.append(f"Dialogue: 0,{t(g[0]['start'])},{t(g[-1]['end'])},D,,0,0,0,,{txt}")
    Path(path).write_text(head + "\n".join(lines), encoding="utf-8")


def render(video, segs, clip, out_file, mode):
    st, en = clip["start"], clip["end"]
    words = [dict(w, start=w["start"] - st, end=w["end"] - st)
             for s in segs for w in s["words"] if st <= w["start"] < en]
    out_file = Path(out_file)
    ass = out_file.with_suffix(".ass")
    if words:
        _ass(words, ass)
    sub = f",ass=filename={ass.name}" if words else ""
    if mode == "crop":
        vf = f"[0:v]crop=ih*9/16:ih,scale=1080:1920,setsar=1{sub}[v]"
    else:  # fondo desenfocado + vídeo completo centrado
        vf = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5[bg];"
              f"[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1{sub}[v]")
    cmd = [FFMPEG, "-y", "-ss", str(st), "-to", str(en), "-i", "source.mp4", "-filter_complex", vf,
           "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_file.name]
    p = subprocess.run(cmd, cwd=out_file.parent, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError("FFmpeg falló: " + p.stderr[-300:])


def run(url, out: Path, n, mode, progress):
    out.mkdir(parents=True, exist_ok=True)
    progress("Descargando vídeo", 0.05)
    video, title, heat = download(url, out)
    progress("Transcribiendo (puede tardar)", 0.15)
    segs = transcribe(video)
    clips = pick_clips(segs, heat, n, progress)
    results = []
    for i, c in enumerate(clips, 1):
        progress(f"Renderizando clip {i}/{len(clips)}", 0.7 + 0.28 * (i - 1) / max(1, len(clips)))
        f = out / f"clip_{i}.mp4"
        render(video, segs, c, f, mode)
        results.append({"file": f.name, "start": c["start"], "end": c["end"], "score": c["score"],
                        "title": c.get("title", f"Clip {i}"), "reason": c.get("reason", ""),
                        "hashtags": c.get("hashtags", [])})
    return title, results
