import { useEffect, useState } from "react"
import { Download, Loader2, Scissors } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

type Clip = { url: string; title: string; reason: string; hashtags: string[]; score: number; start: number; end: number; reframe?: string }
type Job = { id: string; status: string; stage: string; progress: number; title?: string; clips: Clip[]; error?: string }

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`

const MODES = { blur: "Fondo desenfocado", crop: "Recorte central", face: "Seguir cara" } as const
const REFRAME: Record<string, string> = {
  blur: "Fondo desenfocado",
  crop: "Recorte central",
  face: "Seguimiento de cara",
  fallback: "Fondo desenfocado (no se detectaron caras)",
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

export default function App() {
  const [url, setUrl] = useState("")
  const [n, setN] = useState(5)
  const [mode, setMode] = useState<keyof typeof MODES>("blur")
  const [len, setLen] = useState({ lo: 20, hi: 60 })
  const [zoom, setZoom] = useState(0.12)
  const [job, setJob] = useState<Job | null>(null)
  const [err, setErr] = useState("")
  const busy = job?.status === "running"
  const badLen = len.lo >= len.hi

  useEffect(() => {
    if (!busy || !job) return
    const t = setInterval(async () => setJob(await (await fetch(`/api/jobs/${job.id}`)).json()), 1500)
    return () => clearInterval(t)
  }, [busy, job?.id])

  async function start() {
    setErr("")
    const r = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, num_clips: n, mode, min_len: len.lo, max_len: len.hi, zoom }),
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
        Pega un vídeo de YouTube y recibe clips verticales con subtítulos para TikTok, Reels y Shorts.
      </p>

      <Card className="mt-8">
        <CardContent className="space-y-4">
          <Input placeholder="https://www.youtube.com/watch?v=..." value={url} onChange={(e) => setUrl(e.target.value)} disabled={busy} />
          <div className="flex flex-wrap items-center gap-4 text-sm">
            <label className="flex items-center gap-2">
              Clips
              <Input type="number" min={1} max={10} value={n} onChange={(e) => setN(+e.target.value)} className="w-20" disabled={busy} />
            </label>
            <div className="flex gap-2">
              {(Object.keys(MODES) as (keyof typeof MODES)[]).map((m) => (
                <Button key={m} size="sm" variant={mode === m ? "default" : "outline"} onClick={() => setMode(m)} disabled={busy}>
                  {MODES[m]}
                </Button>
              ))}
            </div>
            <Button className="ml-auto" onClick={start} disabled={busy || !url || badLen}>
              {busy && <Loader2 className="size-4 animate-spin" />} Generar clips
            </Button>
          </div>

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

      <div className="mt-8 grid gap-4 sm:grid-cols-2">
        {job?.clips.map((c) => (
          <Card key={c.url}>
            <CardContent className="space-y-3">
              <video src={c.url} controls className="aspect-[9/16] w-full rounded-lg bg-black" />
              <div className="flex items-start justify-between gap-2">
                <h2 className="font-medium leading-snug">{c.title}</h2>
                <Badge>{c.score}</Badge>
              </div>
              <p className="text-sm text-muted-foreground">{c.reason}</p>
              <p className="text-xs text-muted-foreground">
                {fmt(c.start)}–{fmt(c.end)} del original · {c.reframe && REFRAME[c.reframe] ? REFRAME[c.reframe] + " · " : ""}{c.hashtags.map((h) => (h.startsWith("#") ? h : "#" + h)).join(" ")}
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