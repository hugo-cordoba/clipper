import { useEffect, useState } from "react"
import { Download, Loader2, Scissors } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

type Mode = "blur" | "crop" | "speaker"
type Aspect = "9:16" | "16:9"
type Pace = "calm" | "normal" | "fast"
type Clip = { url: string; title: string; reason: string; hashtags: string[]; score: number; start: number; end: number; reframe?: string; aspect?: Aspect }
type Job = { id: string; status: string; stage: string; progress: number; title?: string; clips: Clip[]; error?: string }

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`

const MODES: Record<Mode, string> = { blur: "Fondo desenfocado", crop: "Recorte central", speaker: "Seguir al que habla" }
const ASPECTS: Record<Aspect, string> = { "9:16": "Vertical 9:16", "16:9": "Horizontal 16:9 (YouTube)" }
const REFRAME: Record<string, string> = {
  blur: "Fondo desenfocado",
  crop: "Recorte central",
  crop_track: "Recorte central siguiendo al que habla",
  speaker: "Seguimiento del que habla",
  fallback: "Sin recorte (no se detectaron caras)",
}

const PRESETS = [
  { label: "Cortos 15–30 s", lo: 15, hi: 30 },
  { label: "Medios 30–60 s", lo: 30, hi: 60 },
  { label: "Largos 60–120 s", lo: 60, hi: 120 },
]

const ZOOMS = [
  { label: "Sin zoom", v: 0 },
  { label: "Suave", v: 0.08 },
  { label: "Medio", v: 0.12 },
  { label: "Fuerte", v: 0.18 },
]

// Cuántos cambios de plano hace al alternar los que hablan
const PACES: { label: string; v: Pace; hint: string }[] = [
  { label: "Pocos", v: "calm", hint: "Solo cambia cuando alguien habla un rato seguido; ignora las réplicas cortas." },
  { label: "Normal", v: "normal", hint: "Cambia al hablar otra persona, incluso en réplicas de un segundo." },
  { label: "Muchos", v: "fast", hint: "Cambia en cuanto alguien responde, aunque sea un momento." },
]

// Cuánto del ancho del encuadre ocupa la cara (más alto = plano más cerrado)
const FRAMINGS = [
  { label: "Amplio", v: 0.12 },
  { label: "Medio", v: 0.17 },
  { label: "Cerrado", v: 0.24 },
]

export default function App() {
  const [url, setUrl] = useState("")
  const [n, setN] = useState(5)
  const [aspect, setAspect] = useState<Aspect>("9:16")
  const [mode, setMode] = useState<Mode>("blur")
  const [len, setLen] = useState({ lo: 20, hi: 60 })
  const [zoom, setZoom] = useState(0.12)
  const [framing, setFraming] = useState(0.17)
  const [pace, setPace] = useState<Pace>("normal")
  const [job, setJob] = useState<Job | null>(null)
  const [err, setErr] = useState("")
  const busy = job?.status === "running"
  const badLen = len.lo >= len.hi
  const horizontal = aspect === "16:9"
  const modes = Object.keys(MODES) as Mode[]
  const tracking = mode === "speaker" || (horizontal && mode === "crop") // modos que siguen al que habla
  const modeLabel = (m: Mode) => {
    if (!horizontal) return MODES[m]
    if (m === "blur") return "Plano completo"
    if (m === "crop") return "Recorte central con seguimiento"
    return MODES[m]
  }
  const modeHint =
    horizontal && mode === "crop"
      ? "Pantalla completa con recorte; el encuadre sigue a quien habla. Si no se detectan caras, recorta al centro."
      : horizontal && mode === "speaker"
        ? "El plano se adapta al tamaño de la cara. Si no se detectan caras, se muestra el plano completo."
        : ""
  const reframeLabel = (c: Clip) => (c.aspect === "16:9" && c.reframe === "blur" ? "Plano completo" : c.reframe ? REFRAME[c.reframe] : undefined)
  const wide = job?.clips[0]?.aspect === "16:9"

  useEffect(() => {
    if (!busy || !job) return
    const t = setInterval(async () => setJob(await (await fetch(`/api/jobs/${job.id}`)).json()), 1500)
    return () => clearInterval(t)
  }, [busy, job?.id])

  function pickAspect(a: Aspect) {
    if (a === aspect) return
    setAspect(a)
    if (a === "16:9") setMode("crop") // en horizontal, el recorte central también sigue al que habla
  }

  async function start() {
    setErr("")
    const r = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, num_clips: n, mode, min_len: len.lo, max_len: len.hi, zoom, aspect, framing, pace }),
    })
    if (!r.ok) {
      const d = (await r.json()).detail
      return setErr(typeof d === "string" ? d : "Revisa los ajustes")
    }
    setJob(await r.json())
  }

  return (
    <main className="mx-auto max-w-3xl px-5 py-12">
      <h1 className="flex items-center gap-2 text-3xl font-semibold tracking-tight">
        <Scissors className="size-7 text-primary" /> Clipper
      </h1>
      <p className="mt-2 max-w-prose text-muted-foreground">
        Pega un vídeo de YouTube y recibe clips con subtítulos para TikTok, Reels, Shorts o YouTube.
      </p>

      <Card className="mt-8">
        <CardContent className="space-y-4">
          <Input placeholder="https://www.youtube.com/watch?v=..." value={url} onChange={(e) => setUrl(e.target.value)} disabled={busy} />

          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="mr-1">Formato</span>
            {(Object.keys(ASPECTS) as Aspect[]).map((a) => (
              <Button key={a} size="sm" variant={aspect === a ? "default" : "outline"} onClick={() => pickAspect(a)} disabled={busy}>
                {ASPECTS[a]}
              </Button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-4 text-sm">
            <label className="flex items-center gap-2">
              Clips
              <Input type="number" min={1} max={10} value={n} onChange={(e) => setN(+e.target.value)} className="w-20" disabled={busy} />
            </label>
            <div className="flex flex-wrap gap-2">
              {modes.map((m) => (
                <Button key={m} size="sm" variant={mode === m ? "default" : "outline"} onClick={() => setMode(m)} disabled={busy}>
                  {modeLabel(m)}
                </Button>
              ))}
            </div>
            <Button className="ml-auto" onClick={start} disabled={busy || !url || badLen}>
              {busy && <Loader2 className="size-4 animate-spin" />} Generar clips
            </Button>
          </div>

          {modeHint && <p className="text-xs text-muted-foreground">{modeHint}</p>}

          {horizontal && tracking && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="mr-1">Encuadre</span>
              {FRAMINGS.map((f) => (
                <Button key={f.label} size="sm" disabled={busy}
                  variant={framing === f.v ? "default" : "outline"}
                  onClick={() => setFraming(f.v)}>
                  {f.label}
                </Button>
              ))}
            </div>
          )}

          {tracking && (
            <div className="space-y-1">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="mr-1">Cambios de plano</span>
                {PACES.map((p) => (
                  <Button key={p.v} size="sm" disabled={busy}
                    variant={pace === p.v ? "default" : "outline"}
                    onClick={() => setPace(p.v)}>
                    {p.label}
                  </Button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">{PACES.find((p) => p.v === pace)?.hint}</p>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="mr-1">Duración</span>
            {PRESETS.map((p) => (
              <Button key={p.label} size="sm" disabled={busy}
                variant={len.lo === p.lo && len.hi === p.hi ? "default" : "outline"}
                onClick={() => setLen({ lo: p.lo, hi: p.hi })}>
                {p.label}
              </Button>
            ))}
            <label className="ml-2 flex items-center gap-1">
              Mín
              <Input type="number" min={5} max={170} value={len.lo} className="w-20" disabled={busy}
                onChange={(e) => setLen({ ...len, lo: +e.target.value })} />
            </label>
            <label className="flex items-center gap-1">
              Máx
              <Input type="number" min={10} max={180} value={len.hi} className="w-20" disabled={busy}
                onChange={(e) => setLen({ ...len, hi: +e.target.value })} />
            </label>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="mr-1">Zoom</span>
            {ZOOMS.map((z) => (
              <Button key={z.label} size="sm" disabled={busy}
                variant={zoom === z.v ? "default" : "outline"}
                onClick={() => setZoom(z.v)}>
                {z.label}
              </Button>
            ))}
          </div>

          {badLen && <p className="text-sm text-destructive">El mínimo debe ser menor que el máximo.</p>}
          {err && <p className="text-sm text-destructive">{err}</p>}
        </CardContent>
      </Card>

      {job && (
        <div className="mt-6">
          <div className="mb-1 flex justify-between text-sm">
            <span className={cn(job.status === "error" && "text-destructive")}>{job.status === "error" ? job.error : job.stage}</span>
            <span className="text-muted-foreground">{Math.round(job.progress * 100)}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div className="h-full bg-primary transition-all" style={{ width: `${job.progress * 100}%` }} />
          </div>
        </div>
      )}

      <div className={cn("mt-8 grid gap-4", !wide && "sm:grid-cols-2")}>
        {job?.clips.map((c) => (
          <Card key={c.url}>
            <CardContent className="space-y-3">
              <video src={c.url} controls className={cn("w-full rounded-lg bg-black", c.aspect === "16:9" ? "aspect-video" : "aspect-[9/16]")} />
              <div className="flex items-start justify-between gap-2">
                <h2 className="font-medium leading-snug">{c.title}</h2>
                <Badge>{c.score}</Badge>
              </div>
              <p className="text-sm text-muted-foreground">{c.reason}</p>
              <p className="text-xs text-muted-foreground">
                {fmt(c.start)}–{fmt(c.end)} del original · {reframeLabel(c) ? reframeLabel(c) + " · " : ""}{c.hashtags.map((h) => (h.startsWith("#") ? h : "#" + h)).join(" ")}
              </p>
              <a href={c.url} download>
                <Button variant="secondary" size="sm"><Download className="size-4" /> Descargar</Button>
              </a>
            </CardContent>
          </Card>
        ))}
      </div>
    </main>
  )
}