import json, os, re, subprocess, gc
from dataclasses import dataclass
from pathlib import Path
import httpx
import numpy as np
import yt_dlp

LLM = os.environ.get("CLIPPER_LLM", "qwen3:4b")
WHISPER = os.environ.get("CLIPPER_WHISPER", "mlx-community/whisper-large-v3-turbo")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
FFMPEG = next((x for x in ("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",) if os.path.exists(x)), "ffmpeg")
COOKIES = os.environ.get("CLIPPER_COOKIES_BROWSER", "")
COOKIES_FILE = os.environ.get("CLIPPER_COOKIES_FILE", "")

# --- Seguir al que habla ---
YUNET = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
SAMPLE_FPS = 10            # muestras de cara por segundo (más muestras = labios mejor medidos)
SAMPLE_W = 1024            # ancho al que se analizan los fotogramas
HOLD = 3 * SAMPLE_FPS      # una cara perdida se sigue buscando 3 s
MIN_TRACK = round(0.8 * SAMPLE_FPS)  # muestras mínimas para que una pista cuente como persona
CUT_DIFF = 15              # diferencia media (0-255) entre miniaturas que cuenta como corte de plano
MIN_ACT = 0.10             # movimiento de boca mínimo para considerar que alguien habla (se limita solo si el vídeo es muy "tranquilo")
ONSET_GAP = 0.35           # s de silencio tras los que una frase nueva cuenta como arranque de turno
ONSET_LEN = 0.8            # s tras el arranque en los que basta con mover la boca más que el otro
KEY_EVERY = max(1, round(SAMPLE_FPS / 6))  # un keyframe de cámara cada N muestras (mantiene corto el filtro de FFmpeg)
CAM_EASE = 1 - 0.6 ** (6 / SAMPLE_FPS)     # suavizado de cámara por muestra, independiente de SAMPLE_FPS
# Ritmo de cortes al cambiar de hablante:
#   min_shot: duración mínima de un plano (s) · switch: cuánto más debe mover la boca el nuevo hablante
#   dwell: tiempo (s) que debe mantener esa ventaja · window: semiventana (s) con la que se mide la actividad
PACES = {
    "calm":   dict(min_shot=2.0, switch=1.40, dwell=0.50, window=0.6),
    "normal": dict(min_shot=1.0, switch=1.20, dwell=0.30, window=0.4),
    "fast":   dict(min_shot=0.7, switch=1.10, dwell=0.20, window=0.3),
}
MAX_ZOOM = 2.5             # recorte máximo sobre el original (más borroso a partir de aquí)
CROP_MIN_ZOOM = 1.35       # 16:9 + recorte central: zoom mínimo (siempre es un recorte, nunca el plano completo)
FACE_Y = 0.42              # la cara queda al 42 % de la altura del encuadre


@dataclass
class Opts:
    num_clips: int = 5
    mode: str = "blur"       # blur | crop | speaker
    min_len: int = 20
    max_len: int = 60
    zoom: float = 0.12
    aspect: str = "9:16"     # "9:16" vertical | "16:9" horizontal (YouTube)
    framing: float = 0.17    # solo 16:9 + speaker/crop: ancho de la cara / ancho del encuadre (menor = plano más abierto)
    pace: str = "normal"     # calm | normal | fast: cuántos cambios de plano al alternar hablantes


def _size(o: Opts):
    return (1920, 1080) if o.aspect == "16:9" else (1080, 1920)


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


def _ass(words, path, W=1080, H=1920):
    def t(x):
        return f"{int(x // 3600)}:{int(x % 3600 // 60):02d}:{x % 60:05.2f}"
    # (tamaño, contorno, margen vertical, margen lateral, palabras por línea)
    size, outline, mv, mlr, per = (78, 6, 420, 60, 3) if H > W else (62, 4, 70, 160, 4)
    head = ("[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
            "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: D,Arial,{size},&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{outline},2,2,{mlr},{mlr},{mv},1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    lines = []
    for i in range(0, len(words), per):
        g = words[i:i + per]
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


# ---------------------------------------------------------------------------
# Seguir al que habla: detección de caras, quién mueve la boca, planos y recorte
# ---------------------------------------------------------------------------

def _dims(src):
    import cv2
    cap = cv2.VideoCapture(str(src))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if not w or not h:
        raise RuntimeError("No pude leer las dimensiones del vídeo")
    return w, h


def _detector():
    """Devuelve detect(bgr, gris) -> [(cx, cy, ancho, alto, boca_x, boca_y, boca_ancho)] en px.
    Usa YuNet (caras de frente y de perfil, con puntos de la boca) y, si no está disponible, Haar."""
    import cv2
    if not YUNET.exists():
        try:
            YUNET.parent.mkdir(exist_ok=True)
            r = httpx.get(YUNET_URL, follow_redirects=True, timeout=60)
            r.raise_for_status()
            if len(r.content) < 50_000:
                raise ValueError("descarga incompleta")
            YUNET.write_bytes(r.content)
        except Exception as e:
            print("[clipper] No pude bajar YuNet, uso Haar (peor con caras de perfil):", e)
    if YUNET.exists() and hasattr(cv2, "FaceDetectorYN"):
        try:
            net = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.6, 0.3, 500)
        except Exception as e:
            net = None
            print("[clipper] YuNet no arranca, uso Haar:", e)
        if net is not None:
            def det(img, gray):
                h, w = gray.shape
                net.setInputSize((w, h))
                _, faces = net.detect(img)
                out = []
                for f in (faces if faces is not None else []):
                    x, y, fw, fh = (float(v) for v in f[:4])
                    if fw < 0.035 * w:
                        continue
                    mx, my = (f[10] + f[12]) / 2, (f[11] + f[13]) / 2
                    mw = float(np.hypot(f[10] - f[12], f[11] - f[13]))
                    if not (x <= mx <= x + fw and y <= my <= y + fh) or mw < 0.2 * fw:
                        mx, my, mw = x + fw / 2, y + 0.78 * fh, 0.5 * fw
                    out.append((x + fw / 2, y + fh / 2, fw, fh, float(mx), float(my), mw))
                return out
            return det
    cas = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    def det_haar(img, gray):
        h, w = gray.shape
        g = cv2.equalizeHist(gray)
        return [(x + fw / 2, y + fh / 2, float(fw), float(fh), x + fw / 2, y + 0.78 * fh, 0.5 * fw)
                for x, y, fw, fh in cas.detectMultiScale(g, 1.1, 5, minSize=(w // 25, w // 25))]
    return det_haar


def _scene_cuts(src, st, en, fps=50):
    """Instantes (s, relativos al clip) en los que el propio vídeo original cambia de plano."""
    cmd = [FFMPEG, "-v", "error", "-ss", str(st), "-to", str(en), "-i", str(src),
           "-vf", f"fps={fps},scale=32:18:flags=area,format=gray", "-f", "rawvideo", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode:
        raise RuntimeError("FFmpeg falló al buscar cortes: " + p.stderr.decode(errors="ignore")[-300:])
    a = np.frombuffer(p.stdout, np.uint8)
    a = a[: len(a) // 576 * 576].reshape(-1, 18, 32).astype(np.float32)
    if len(a) < 2:
        return []
    d = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
    cuts = []
    for i in np.nonzero(d > CUT_DIFF)[0]:
        c = round((i + 1) / fps, 3)
        if not cuts or c - cuts[-1] > 0.3:
            cuts.append(c)
    return cuts


class _Track:
    """Una persona seguida a lo largo del clip."""
    def __init__(self, i):
        self.first = self.last = i
        self.box = None      # caja suavizada [cx, cy, w, h, boca_x, boca_y, boca_w] en px
        self.patch = None    # última imagen de la boca (normalizada)
        self.pos = {}        # muestra -> (cx, cy, w, h) normalizados 0-1
        self.mot = {}        # muestra -> cuánto cambió la boca respecto a la muestra anterior
        self.open = True


def _mouth_patch(gray, b):
    import cv2
    h, w = gray.shape
    pw = min(w, max(10, int(b[6] * 1.4)))
    ph = min(h, max(8, int(pw * 0.7)))
    x0 = min(max(int(b[4] - pw / 2), 0), w - pw)
    y0 = min(max(int(b[5] - ph / 2), 0), h - ph)
    p = gray[y0:y0 + ph, x0:x0 + pw]
    if p.size == 0:
        return None
    p = cv2.resize(p, (24, 16), interpolation=cv2.INTER_AREA).astype(np.float32)
    return (p - p.mean()) / (p.std() + 1.0)


def _patch_motion(a, b):
    """Diferencia media entre dos imágenes de boca tolerando un desplazamiento de ±1 px: el temblor del
    detector o un cabeceo no cuentan como hablar; abrir y cerrar los labios sí."""
    h, w = a.shape
    core = a[1:h - 1, 1:w - 1]
    return float(min(np.abs(core - b[1 + dy:h - 1 + dy, 1 + dx:w - 1 + dx]).mean()
                     for dy in (-1, 0, 1) for dx in (-1, 0, 1)))


def _track_step(tracks, i, faces, gray):
    h, w = gray.shape
    live = [t for t in tracks if t.open and i - t.last <= HOLD]
    used = set()
    for f in sorted(faces, key=lambda f: -f[2]):
        best, bd = None, 0.9
        for t in live:
            if id(t) in used:
                continue
            d = np.hypot(f[0] - t.box[0], f[1] - t.box[1]) / max(f[2], t.box[2])
            if d < bd:
                best, bd = t, d
        if best is None:
            best = _Track(i)
            best.box = np.array(f, float)
            tracks.append(best)
        else:
            best.box = 0.5 * best.box + 0.5 * np.array(f, float)  # suaviza el temblor del detector
        used.add(id(best))
        patch = _mouth_patch(gray, best.box)
        if patch is not None and best.patch is not None and best.last == i - 1:
            best.mot[i] = _patch_motion(patch, best.patch)
        best.patch = patch
        best.last = i
        b = best.box
        best.pos[i] = (b[0] / w, b[1] / h, b[2] / w, b[3] / h)


def _sample(src, st, en, cuts, detect):
    """Detecta caras SAMPLE_FPS veces por segundo, las enlaza en pistas y mide el movimiento de la boca.
    Devuelve (nº de muestras, pistas)."""
    import cv2
    sw0, sh0 = _dims(src)
    w = min(SAMPLE_W, sw0) // 2 * 2
    h = round(sh0 * w / sw0) // 2 * 2
    cmd = [FFMPEG, "-v", "error", "-ss", str(st), "-to", str(en), "-i", str(src),
           "-vf", f"fps={SAMPLE_FPS},scale={w}:{h}", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    size, tracks, i = w * h * 3, [], 0
    while True:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        img = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if any((i - 1) / SAMPLE_FPS < c <= i / SAMPLE_FPS for c in cuts):  # otro plano: nadie sigue siendo el mismo
            for t in tracks:
                t.open = False
        _track_step(tracks, i, detect(img, gray), gray)
        i += 1
    p.stdout.close()
    err = p.stderr.read().decode(errors="ignore")
    if p.wait() and i == 0:
        raise RuntimeError("FFmpeg falló al muestrear: " + err[-300:])
    return i, [t for t in tracks if len(t.pos) >= MIN_TRACK]


def _speech_mask(words, n):
    m = np.zeros(n, bool)
    for w in words:
        m[max(0, int((w["start"] - 0.3) * SAMPLE_FPS)):min(n, int((w["end"] + 0.3) * SAMPLE_FPS) + 1)] = True
    return m


def _onset_mask(words, n):
    """Muestras que caen justo después de un silencio: ahí es donde suele cambiar el turno de palabra."""
    m = np.zeros(n, bool)
    prev_end = None
    for w in words:
        if prev_end is None or w["start"] - prev_end >= ONSET_GAP:
            m[max(0, int((w["start"] - 0.1) * SAMPLE_FPS)):min(n, int((w["start"] + ONSET_LEN) * SAMPLE_FPS) + 1)] = True
        prev_end = w["end"]
    return m


def _activity(t, n, half):
    """Movimiento medio de la boca en una ventana centrada (el análisis es offline, se puede mirar al futuro)."""
    m = np.full(n, np.nan)
    for i, v in t.mot.items():
        m[i] = v
    a = np.zeros(n)
    for i in range(n):
        seg = m[max(0, i - half):i + half + 1]
        seg = seg[~np.isnan(seg)]
        a[i] = seg.mean() if len(seg) >= 2 else 0.0
    return a


def _pick(tracks, n, speech, onset, p):
    """Para cada muestra, la persona que habla (o None si no hay ninguna cara).
    - Cambia solo si otra mueve claramente más la boca mientras suena voz, así no salta en las pausas ni por un gesto.
    - La ventaja debe mantenerse `dwell` segundos; al confirmarse, el cambio se adelanta al momento en que empezó
      (el análisis es offline), así el corte no llega tarde.
    - Justo después de un silencio (arranque de turno) basta con mover más la boca y se confirma antes."""
    half = max(1, round(p["window"] * SAMPLE_FPS))
    act = {id(t): _activity(t, n, half) for t in tracks}
    vals = np.concatenate([a[a > 0] for a in act.values()]) if act else np.array([])
    min_act = min(MIN_ACT, 0.35 * float(np.percentile(vals, 75))) if len(vals) else MIN_ACT
    need = max(1, round(p["dwell"] * SAMPLE_FPS))
    need_on = max(2, need // 2)
    spk, cur, lead, lead_n = [], None, None, 0
    for i in range(n):
        cand = [t for t in tracks if t.first <= i <= t.last]
        if not cand:
            spk.append(None)
            cur, lead, lead_n = None, None, 0
            continue
        if len(cand) == 1 or cur not in cand:
            cur = cand[0] if len(cand) == 1 else max(cand, key=lambda t: act[id(t)][i])
            lead, lead_n = None, 0
        else:
            best = max(cand, key=lambda t: act[id(t)][i])
            ratio, k = (1.0, need_on) if (onset is not None and onset[i]) else (p["switch"], need)
            if (best is not cur and (speech is None or speech[i])
                    and act[id(best)][i] >= max(min_act, ratio * act[id(cur)][i])):
                lead_n = lead_n + 1 if lead is best else 1
                lead = best
                if lead_n >= k:
                    for j in range(i - lead_n + 1, i):  # el cambio se adelanta al inicio de la ventaja
                        spk[j] = best
                    cur, lead, lead_n = best, None, 0
            else:
                lead, lead_n = None, 0
        spk.append(cur)
    return spk


def _runs(spk, cuts, min_shot):
    """Agrupa las muestras en planos [i0, i1, persona]. Un corte del original siempre es un corte."""
    cut_at = {int(np.ceil(c * SAMPLE_FPS - 1e-9)) for c in cuts}
    runs = []
    for i, s in enumerate(spk):
        if runs and runs[-1][2] is s and i not in cut_at:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, s])
    min_n = max(1, round(min_shot * SAMPLE_FPS))

    def src_shot(i):
        return sum(1 for c in cut_at if c <= i)

    def merge_once():
        for k in sorted(range(len(runs)), key=lambda j: runs[j][1] - runs[j][0]):
            if runs[k][1] - runs[k][0] >= min_n:
                return False
            left = k > 0 and src_shot(runs[k][0]) == src_shot(runs[k - 1][1] - 1)
            right = k + 1 < len(runs) and src_shot(runs[k][1] - 1) == src_shot(runs[k + 1][0])
            if not (left or right):
                continue
            if left and (not right or runs[k - 1][1] - runs[k - 1][0] >= runs[k + 1][1] - runs[k + 1][0]):
                runs[k - 1][1] = runs[k][1]
            else:
                runs[k + 1][0] = runs[k][0]
            del runs[k]
            j = 0
            while j + 1 < len(runs):
                if runs[j][2] is runs[j + 1][2] and runs[j + 1][0] not in cut_at:
                    runs[j][1] = runs[j + 1][1]
                    del runs[j + 1]
                else:
                    j += 1
            return True
        return False

    while merge_once():
        pass
    return runs


def _boundary(i, cuts, words):
    """Instante (s) en el que empieza el plano que arranca en la muestra i."""
    t = i / SAMPLE_FPS
    for c in cuts:
        if t - 1 / SAMPLE_FPS < c <= t + 1e-6:
            return c
    near = [w["start"] for w in words if abs(w["start"] - t) <= 0.6]
    if near:  # el corte entra justo antes de que el nuevo hablante empiece la frase
        return max(0.0, min(near, key=lambda x: abs(x - t)) - 0.1)
    return t


def _smooth(v, half=round(0.4 * SAMPLE_FPS)):
    """Mediana móvil (~0,9 s): quita saltos sueltos del detector."""
    return np.array([np.median(v[max(0, i - half):i + half + 1]) for i in range(len(v))])


def _lerp(keys):
    """Expresión de FFmpeg: interpolación lineal entre keyframes [(t, valor)] en función de t."""
    ks = []
    for t, v in keys:
        t = round(t, 2)
        if not ks or t > ks[-1][0]:
            ks.append((t, int(v)))
    if len(ks) == 1:
        return str(ks[0][1])
    terms = [f"gte(t,{t0:.2f})*lt(t,{t1:.2f})*({v0}+({v1 - v0})*(t-{t0:.2f})/{t1 - t0:.2f})"
             for (t0, v0), (t1, v1) in zip(ks, ks[1:])]
    terms.append(f"gte(t,{ks[-1][0]:.2f})*{ks[-1][1]}")
    return "+".join(terms)


def _frame(track, i0, i1, t0, iw, ih, o: Opts, W, H, central=False):
    """Ventana de recorte de un plano: tamaño fijo (zoom según el tamaño de la cara) y posición que sigue a la cara.
    central=True (16:9 + recorte central): siempre es un recorte, con al menos CROP_MIN_ZOOM de zoom."""
    ar = W / H
    cw_max = min(iw, ih * ar)
    if track is None:  # sin caras: centrado (y con zoom fijo si es recorte central)
        cw = int(cw_max / CROP_MIN_ZOOM if central else cw_max) // 2 * 2
        ch = int(round(cw / ar)) // 2 * 2
        return dict(cw=cw, ch=ch, xe=str(int((iw - cw) / 2)), ye=str(int((ih - ch) / 2)))
    idx = sorted(track.pos)
    samples = np.arange(i0, i1)

    def series(k, scale):
        return np.interp(samples, idx, [track.pos[j][k] for j in idx]) * scale

    px, py, pw = series(0, iw), series(1, ih), series(2, iw)
    cw = cw_max
    if o.aspect == "16:9":  # horizontal: zoom hasta que la cara ocupe `framing` del ancho
        cw = min(cw_max, max(cw_max / MAX_ZOOM, float(np.median(pw)) / o.framing))
        if central:  # recorte central: nunca más abierto que CROP_MIN_ZOOM
            cw = min(cw, cw_max / CROP_MIN_ZOOM)
        elif cw > 0.93 * cw_max:
            cw = cw_max
    cw = int(cw) // 2 * 2
    ch = int(round(cw / ar)) // 2 * 2
    px, py = _smooth(px), _smooth(py)
    dead_x, dead_y = 0.10 * cw, 0.12 * ch
    cx, cy, keys = px[0], py[0], []
    for k, s in enumerate(samples):  # operador de cámara: solo se mueve si la cara se sale de la zona muerta
        if abs(px[k] - cx) > dead_x:
            cx += (px[k] - cx) * CAM_EASE
        if abs(py[k] - cy) > dead_y:
            cy += (py[k] - cy) * CAM_EASE
        x0 = int(min(max(cx - cw / 2, 0), iw - cw))
        y0 = int(min(max(cy - FACE_Y * ch, 0), ih - ch))
        keys.append((max(0.0, s / SAMPLE_FPS - t0), x0, y0))
    keys = keys[::KEY_EVERY] + ([keys[-1]] if (len(keys) - 1) % KEY_EVERY else [])
    n = len(keys)
    keys = [k for j, k in enumerate(keys)
            if j in (0, n - 1) or k[1:] != keys[j - 1][1:] or k[1:] != keys[j + 1][1:]]
    return dict(cw=cw, ch=ch, xe=_lerp([(k[0], k[1]) for k in keys]), ye=_lerp([(k[0], k[2]) for k in keys]))


def _plan_shots(src, st, en, words, o: Opts, W, H, central=False):
    """Lista de planos [{t0, t1, cw, ch, xe, ye}] o None si no hay caras que seguir."""
    iw, ih = _dims(src)
    if o.aspect == "9:16" and iw / ih <= 9 / 16 + 0.05:  # ya es vertical: nada que recortar
        return None
    cuts = _scene_cuts(src, st, en)
    n, tracks = _sample(src, st, en, cuts, _detector())
    if not n or not tracks:
        return None
    p = PACES.get(o.pace, PACES["normal"])
    speech = _speech_mask(words, n) if words else None
    onset = _onset_mask(words, n) if words else None
    spk = _pick(tracks, n, speech, onset, p)
    if sum(s is not None for s in spk) < 0.3 * n:
        return None
    runs = _runs(spk, cuts, p["min_shot"])
    starts = [0.0]
    for r in runs[1:]:
        starts.append(max(starts[-1] + 0.1, _boundary(r[0], cuts, words)))
    ends = starts[1:] + [en - st]
    shots = []
    for (i0, i1, tr), a, b in zip(runs, starts, ends):
        if b - a < 0.08 and shots:  # plano degenerado: lo absorbe el anterior
            shots[-1]["t1"] = b
            continue
        shots.append(dict(t0=a, t1=b, **_frame(tr, i0, i1, a, iw, ih, o, W, H, central)))
    return shots


def _crop_graph(shots, W, H):
    n = len(shots)

    def seg(s, k):
        trim = ""
        if n > 1:
            args = ([f"start={s['t0']:.3f}"] if k > 0 else []) + ([f"end={s['t1']:.3f}"] if k < n - 1 else [])
            trim = f"trim={':'.join(args)},setpts=PTS-STARTPTS,"
        return (f"{trim}crop=w={s['cw']}:h={s['ch']}:x='{s['xe']}':y='{s['ye']}',"
                f"scale={W}:{H}:flags=lanczos,setsar=1")
    if n == 1:
        return f"[0:v]{seg(shots[0], 0)}[c]"
    parts = ["[0:v]split=%d%s" % (n, "".join(f"[s{k}]" for k in range(n)))]
    parts += [f"[s{k}]{seg(s, k)}[v{k}]" for k, s in enumerate(shots)]
    parts.append("".join(f"[v{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0[c]")
    return ";".join(parts)


def render(video, segs, clip, out_file, o: Opts):
    st, en = clip["start"], clip["end"]
    W, H = _size(o)
    words = [dict(w, start=w["start"] - st, end=w["end"] - st)
             for s in segs for w in s["words"] if st <= w["start"] < en]
    out_file = Path(out_file)
    src = out_file.parent / "source.mp4"
    ass = out_file.with_suffix(".ass")
    if words:
        _ass(words, ass, W, H)
    sub = f",ass=filename={ass.name}" if words else ""
    mode, fallback, shots, label = o.mode, False, None, o.mode
    central = mode == "crop" and o.aspect == "16:9"  # recorte central horizontal: a pantalla completa y siguiendo al que habla
    if mode == "speaker" or central:
        shots = _plan_shots(src, st, en, words, o, W, H, central)
        if shots is not None:
            label = "crop_track" if central else "speaker"
        elif central:  # sin caras: recorte central fijo (sigue siendo un recorte, no el plano completo)
            iw, ih = _dims(src)
            shots = [dict(t0=0.0, t1=en - st, **_frame(None, 0, 0, 0.0, iw, ih, o, W, H, True))]
        else:  # sin caras (diapositivas, pantalla...) o vídeo ya vertical: se conserva todo el encuadre
            mode, fallback = "blur", True
    if shots:
        core = _crop_graph(shots, W, H)
    elif mode == "crop":
        core = (f"[0:v]crop=w='trunc(min(iw,ih*{W}/{H})/2)*2':h='trunc(min(ih,iw*{H}/{W})/2)*2',"
                f"scale={W}:{H},setsar=1[c]")
    else:  # fondo desenfocado + vídeo completo centrado
        iw, ih = _dims(src)
        if o.aspect == "16:9" and abs(iw / ih - 16 / 9) < 0.03:  # ya es 16:9: no hace falta fondo
            core = f"[0:v]scale={W}:{H}:flags=lanczos,setsar=1[c]"
        else:
            core = (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:5[bg];"
                    f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[c]")
    if o.zoom > 0 and words:
        z = _zoom_expr(_beats(words, en - st), o.zoom)
        last = f"[c]scale=w='{W}*({z})':h='{H}*({z})':eval=frame,crop={W}:{H},setsar=1{sub}[v]"
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
    return "fallback" if fallback else label


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
                        "hashtags": c.get("hashtags", []), "reframe": used, "aspect": o.aspect})
    return title, results