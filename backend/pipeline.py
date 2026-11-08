import json, os, re, subprocess, gc, shutil, tempfile
from dataclasses import dataclass
from pathlib import Path
import httpx
import yt_dlp

LLM = os.environ.get("CLIPPER_LLM", "qwen3:4b")
WHISPER = os.environ.get("CLIPPER_WHISPER", "mlx-community/whisper-large-v3-turbo")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
FFMPEG = next((x for x in ("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",) if os.path.exists(x)), "ffmpeg")
COOKIES = os.environ.get("CLIPPER_COOKIES_BROWSER", "")
COOKIES_FILE = os.environ.get("CLIPPER_COOKIES_FILE", "")

@dataclass
class Opts:
    num_clips: int = 5
    mode: str = "blur"
    min_len: int = 20
    max_len: int = 60
    zoom: float = 0.12


def download(url, out: Path):
    opts = {"format": "bv*[height<=1080]+ba/b", "merge_output_format": "mp4",
            "outtmpl": str(out / "source.%(ext)s"), "quiet": True, "noplaylist": True}
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    elif COOKIES:
        opts["cookiesfrombrowser"] = (COOKIES,)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e))
        if "Operation not permitted" in msg:
            raise RuntimeError(
                f"macOS bloquea las cookies de {COOKIES or 'el navegador'}. Ajustes del Sistema > Privacidad y "
                "seguridad > Acceso total al disco: activa la app donde ejecutas ./start.sh (Terminal, iTerm...), "
                "ciérrala con Cmd+Q, ábrela y vuelve a ejecutar ./start.sh. "
                "Alternativa: CLIPPER_COOKIES_FILE=$HOME/cookies.txt ./start.sh") from e
        if "not a bot" in msg or "Sign in" in msg:
            raise RuntimeError(
                "YouTube pide iniciar sesión. Abre YouTube en Safari con tu cuenta, comprueba que "
                "CLIPPER_COOKIES_BROWSER=safari y que Terminal tiene Acceso total al disco.") from e
        raise RuntimeError(msg[:300]) from e
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


def ask_llm(chunk, n, lo, hi):
    text = "\n".join(f"[{int(s['start'])}] {s['text']}" for s in chunk)
    body = {"model": LLM, "stream": False, "format": "json", "think": False,
            "options": {"num_ctx": 8192, "temperature": 0.3},
            "messages": [{"role": "user", "content": PROMPT.format(n=n, lo=lo, hi=hi, text=text)}]}
    r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=600)
    r.raise_for_status()
    raw = re.sub(r"<think>.*?</think>", "", r.json()["message"]["content"], flags=re.S)
    try:
        return json.loads(raw).get("clips", [])
    except json.JSONDecodeError:
        return []


def _snap(segs, start, end, lo, hi):
    """Ajusta a límites de frase y fuerza la duración pedida (estira o recorta)."""
    st = min((s["start"] for s in segs), key=lambda x: abs(x - start))
    ends = [s["end"] for s in segs if s["end"] > st]
    if not ends:
        return None
    en = min(ends, key=lambda x: abs(x - end))
    if en - st > hi:
        en = max([e for e in ends if st + lo <= e <= st + hi] or [st + hi])
    elif en - st < lo:  # se estira hasta el siguiente final de frase dentro del rango
        longer = [e for e in ends if st + lo <= e <= st + hi]
        if not longer:
            return None
        en = longer[0]
    return st, en


def _heat(heatmap, st, en):
    vals = [h["value"] for h in heatmap if h["end_time"] > st and h["start_time"] < en]
    return sum(vals) / len(vals) if vals else 0


def pick_clips(segs, heatmap, o: Opts, progress):
    chunks = list(_chunks(segs, window=max(360, o.max_len * 4)))
    found = []
    for i, ch in enumerate(chunks):
        progress(f"Analizando con IA ({i + 1}/{len(chunks)})", 0.35 + 0.35 * i / len(chunks))
        for c in ask_llm(ch, 3, o.min_len, o.max_len):
            try:
                snapped = _snap(segs, float(c["start"]), float(c["end"]), o.min_len, o.max_len)
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
        if len(picked) == o.num_clips:
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


def _beats(words, dur):
    """Divide el clip en 'golpes' de ritmo (frases cortas) usando puntuación y pausas."""
    beats, a = [], 0.0
    for i, w in enumerate(words):
        end = min(w["end"], dur)
        nxt = words[i + 1]["start"] if i + 1 < len(words) else dur
        punct = w["word"].strip()[-1:] in ".,;:?!…"
        length = end - a
        if (length >= 1.8 and (punct or nxt - end > 0.4)) or length >= 4:
            beats.append((a, end))
            a = nxt
    if dur - a >= 0.8:
        beats.append((a, dur))
    return beats


def _zoom_expr(beats, amount, ramp=0.25):
    """Zoom en los golpes pares (el primero incluido), con entrada suave y corte seco al salir."""
    terms = [f"between(t,{a:.2f},{b:.2f})*min(1,(t-{a:.2f})/{ramp})"
             for i, (a, b) in enumerate(beats) if i % 2 == 0]
    return f"1+{amount}*({'+'.join(terms)})" if terms else "1"


def _sample_faces(src, st, en, work: Path, fps=2):
    """Detecta caras ~2 veces por segundo. Devuelve (proporción ancho/alto, [[(centro_x 0-1, área), ...] por muestra])."""
    import cv2
    cmd = [FFMPEG, "-y", "-ss", str(st), "-to", str(en), "-i", str(src),
           "-vf", f"fps={fps},scale=640:-2", "-q:v", "4", str(work / "f%04d.jpg")]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError("FFmpeg falló al muestrear: " + p.stderr[-300:])
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    out, aspect = [], None
    for f in sorted(work.glob("f*.jpg")):
        img = cv2.imread(str(f))
        if img is None:
            out.append([])
            continue
        h, w = img.shape[:2]
        aspect = w / h
        gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        faces = det.detectMultiScale(gray, 1.1, 5, minSize=(w // 25, w // 25))
        out.append([((x + fw / 2) / w, fw * fh / (w * h)) for x, y, fw, fh in faces])
    return aspect, out


def _cam_path(samples, fps=2, dead=0.03, alpha=0.4):
    """Cámara virtual estable: elige una cara por muestra, suaviza y solo se mueve si la cara se aleja del centro.
    Devuelve keyframes [(t, centro_x)] o None si casi no hay caras."""
    if not samples or sum(1 for s in samples if s) < 0.3 * len(samples):
        return None
    pos, prev = [], None
    for faces in samples:
        if faces:
            big = max(faces, key=lambda f: f[1])
            if prev is not None:  # histéresis: no saltar de persona por una diferencia pequeña de tamaño
                near = min(faces, key=lambda f: abs(f[0] - prev))
                if abs(near[0] - prev) < 0.15 and near[1] >= 0.6 * big[1]:
                    big = near
            prev = big[0]
        pos.append(prev)
    first = next(p for p in pos if p is not None)
    pos = [first if p is None else p for p in pos]
    sm = []
    for i in range(len(pos)):  # mediana de 5 muestras: quita falsos positivos sueltos
        w = sorted(pos[max(0, i - 2):i + 3])
        sm.append(w[len(w) // 2])
    cam, c = [], sm[0]
    for v in sm:
        if abs(v - c) > dead:
            c += (v - c) * alpha
        cam.append(round(c, 3))
    n = len(cam)
    keep = [i for i in range(n) if i in (0, n - 1) or cam[i] != cam[i - 1] or cam[i] != cam[i + 1]]
    return [(i / fps, cam[i]) for i in keep]


def _pan_expr(keys):
    """Expresión de FFmpeg: interpolación lineal entre keyframes (centro_x 0-1) en función de t."""
    if len(keys) == 1:
        return f"{keys[0][1]}"
    terms = [f"gte(t,{t0:.2f})*lt(t,{t1:.2f})*({v0}+({v1 - v0:.4f})*(t-{t0:.2f})/{t1 - t0:.2f})"
             for (t0, v0), (t1, v1) in zip(keys, keys[1:])]
    terms.append(f"gte(t,{keys[-1][0]:.2f})*{keys[-1][1]}")
    return "+".join(terms)


def render(video, segs, clip, out_file, o: Opts):
    st, en = clip["start"], clip["end"]
    words = [dict(w, start=w["start"] - st, end=w["end"] - st)
             for s in segs for w in s["words"] if st <= w["start"] < en]
    out_file = Path(out_file)
    ass = out_file.with_suffix(".ass")
    if words:
        _ass(words, ass)
    sub = f",ass=filename={ass.name}" if words else ""
    mode, fallback, pan = o.mode, False, None
    if mode == "face":
        work = Path(tempfile.mkdtemp(prefix="_faces", dir=out_file.parent))
        try:
            aspect, samples = _sample_faces(out_file.parent / "source.mp4", st, en, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        keys = _cam_path(samples) if aspect and aspect > 9 / 16 + 0.05 else None
        if keys:
            pan = _pan_expr(keys)
        else:  # sin caras (diapositivas, pantalla...) o vídeo ya vertical: se conserva todo el encuadre
            mode, fallback = "blur", True
    if mode == "face":
        core = (f"[0:v]crop=w='min(iw,trunc(ih*9/16/2)*2)':h=ih:x='min(max(({pan})*iw-ow/2,0),iw-ow)':y=0,"
                "scale=1080:1920:flags=lanczos,setsar=1[c]")
    elif mode == "crop":
        core = "[0:v]crop=ih*9/16:ih,scale=1080:1920,setsar=1[c]"
    else:  # fondo desenfocado + vídeo completo centrado
        core = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5[bg];"
                "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[c]")
    if o.zoom > 0 and words:
        z = _zoom_expr(_beats(words, en - st), o.zoom)
        last = f"[c]scale=w='1080*({z})':h='1920*({z})':eval=frame,crop=1080:1920,setsar=1{sub}[v]"
    else:
        last = f"[c]null{sub}[v]"
    vf = core + ";" + last
    cmd = [FFMPEG, "-y", "-ss", str(st), "-to", str(en), "-i", "source.mp4", "-filter_complex", vf,
           "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
           "-pix_fmt", "yuv420p",  # necesario para reproducir en Safari/iOS
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_file.name]
    p = subprocess.run(cmd, cwd=out_file.parent, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError("FFmpeg falló: " + p.stderr[-300:])
    return "fallback" if fallback else mode


def run(url, out: Path, o: Opts, progress):
    out.mkdir(parents=True, exist_ok=True)
    progress("Descargando vídeo", 0.05)
    video, title, heat = download(url, out)
    progress("Transcribiendo (puede tardar)", 0.15)
    segs = transcribe(video)
    clips = pick_clips(segs, heat, o, progress)
    results = []
    for i, c in enumerate(clips, 1):
        progress(f"Renderizando clip {i}/{len(clips)}", 0.7 + 0.28 * (i - 1) / max(1, len(clips)))
        f = out / f"clip_{i}.mp4"
        used = render(video, segs, c, f, o)
        results.append({"file": f.name, "start": c["start"], "end": c["end"], "score": c["score"],
                        "title": c.get("title", f"Clip {i}"), "reason": c.get("reason", ""),
                        "hashtags": c.get("hashtags", []), "reframe": used})
    return title, results