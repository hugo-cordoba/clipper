import { useEffect, useRef, useState, type ReactNode } from "react"
import { Activity, ChevronDown, Circle, CircleCheck, CircleX, Download, Flame, Link2, Loader2, Plus, Scissors, Sparkles, Terminal } from "lucide-react"
import { AppSidebar } from "@/components/app-sidebar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { cn } from "@/lib/utils"

type Mode = "blur" | "crop" | "speaker"
type Aspect = "9:16" | "16:9"
type Pace = "calm" | "normal" | "fast"
type Clip = { url: string; title: string; reason: string; hashtags: string[]; score: number; start: number; end: number; reframe?: string; aspect?: Aspect }
type Job = { id: string; status: string; stage: string; progress: number; title?: string; clips: Clip[]; error?: string }
type Log = { t: string; m: string }

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`
const stamp = () => new Date().toLocaleTimeString("es-ES", { hour12: false })

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

// Etapas del pipeline, deducidas del progreso que informa el backend
const STEPS = ["Descarga", "Transcripción", "Análisis con IA", "Render"]
const stepOf = (j: Job) => (j.status === "done" ? STEPS.length : j.stage === "En cola" ? -1 : j.progress >= 0.7 ? 3 : j.progress >= 0.35 ? 2 : j.progress >= 0.15 ? 1 : 0)

const STATUS = {
  idle: { label: "En espera", cls: "bg-muted text-muted-foreground" },
  running: { label: "Procesando", cls: "bg-primary/15 text-orange-800" },
  done: { label: "Listo", cls: "bg-emerald-100 text-emerald-800" },
  error: { label: "Error", cls: "bg-destructive/10 text-destructive" },
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
      <span className="w-full text-sm text-muted-foreground sm:w-28">{label}</span>
      {children}
    </div>
  )
}

function Choice<T>({ label, items, value, onChange, extra }: {
  label: string; items: { label: string; v: T }[]; value: T; onChange: (v: T) => void; extra?: ReactNode
}) {
  return (
    <Row label={label}>
      <div role="group" aria-label={label} className="inline-flex flex-wrap gap-0.5 rounded-lg bg-muted p-0.5">
        {items.map((i) => (
          <button
            key={i.label} type="button" aria-pressed={i.v === value} onClick={() => onChange(i.v)}
            className={cn(
              "rounded-md px-3 py-1.5 text-xs font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60",
              i.v === value ? "bg-card text-foreground shadow-sm ring-1 ring-border" : "text-muted-foreground hover:text-foreground"
            )}
          >
            {i.label}
          </button>
        ))}
      </div>
      {extra}
    </Row>
  )
}

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
  const [logs, setLogs] = useState<Log[]>([])
  const logRef = useRef<HTMLDivElement>(null)

  const busy = job?.status === "running"
  const phase = !job ? "idle" : job.status === "error" ? "error" : job.status === "done" ? "done" : "running"
  const step = job ? stepOf(job) : -1
  const pct = Math.round((job?.progress ?? 0) * 100)
  const badLen = len.lo >= len.hi
  const canStart = !busy && url.trim() !== "" && !badLen
  const horizontal = aspect === "16:9"
  const modes = Object.keys(MODES) as Mode[]
  const tracking = mode === "speaker" || (horizontal && mode === "crop") // modos que siguen al que habla
  const clips = job?.clips ?? []
  const wide = clips[0]?.aspect === "16:9"
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

  // Sondeo del trabajo en curso
  useEffect(() => {
    if (!busy || !job) return
    const id = job.id
    const t = setInterval(async () => {
      const r = await fetch(`/api/jobs/${id}`).catch(() => null)
      if (!r) return // sin conexión: reintenta en el siguiente ciclo
      if (r.status === 404) return setJob((j) => j && { ...j, status: "error", stage: "Error", error: "El servidor ya no recuerda este trabajo (¿se reinició?)." })
      if (r.ok) setJob(await r.json())
    }, 1500)
    return () => clearInterval(t)
  }, [busy, job?.id])

  // Registro del sistema: una línea por cambio de etapa
  useEffect(() => {
    if (!job) return
    const m = job.status === "error" ? `Error: ${job.error ?? "fallo desconocido"}` : job.stage
    setLogs((l) => (l[l.length - 1]?.m === m ? l : [...l.slice(-199), { t: stamp(), m }]))
  }, [job?.stage, job?.status, job?.error])

  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight }) }, [logs])

  function pickAspect(a: Aspect) {
    setAspect(a)
    if (a === "16:9") setMode("crop") // en horizontal, el recorte central también sigue al que habla
  }

  async function start() {
    setErr("")
    try {
      const r = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, num_clips: n, mode, min_len: len.lo, max_len: len.hi, zoom, aspect, framing, pace }),
      })
      if (!r.ok) {
        const d = ((await r.json().catch(() => ({}))) as { detail?: unknown }).detail
        return setErr(typeof d === "string" ? d : "Revisa los ajustes")
      }
      setLogs([])
      setJob(await r.json())
    } catch {
      setErr("No se pudo conectar con el servidor. ¿Está en marcha ./start.sh?")
    }
  }

  function newProject() {
    if (busy) return
    setJob(null); setErr(""); setLogs([]); setUrl("")
    requestAnimationFrame(() => document.getElementById("url")?.focus())
  }

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="sticky top-0 z-20 flex h-14 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
          <Button variant="outline" size="sm" className="rounded-full bg-card" onClick={newProject} disabled={busy}>
            <Plus /> Nuevo proyecto
          </Button>
        </header>

        <div className="mx-auto grid w-full max-w-[1400px] gap-5 p-4 md:p-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)] lg:items-start xl:grid-cols-[minmax(0,1fr)_minmax(0,30rem)]">
          <h1 className="sr-only">Generador de clips</h1>

          <div className="min-w-0 space-y-5">
            {/* Nuevo proyecto */}
            <Card className="rounded-2xl">
              <CardContent className="space-y-4 p-5">
                <div>
                  <h2 className="text-base font-semibold">Nuevo proyecto</h2>
                  <p className="text-sm text-muted-foreground">Pega un vídeo de YouTube y recibe clips con subtítulos para TikTok, Reels, Shorts o YouTube.</p>
                </div>
                <form onSubmit={(e) => { e.preventDefault(); if (canStart) start() }} className="space-y-4">
                  <fieldset disabled={busy} className="min-w-0 space-y-3">
                    <div className="relative">
                      <Link2 className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                      <Input id="url" aria-label="URL del vídeo de YouTube" autoComplete="off" inputMode="url" className="h-11 pl-9"
                        placeholder="https://www.youtube.com/watch?v=..." value={url} onChange={(e) => setUrl(e.target.value)} />
                    </div>

                    <Choice label="Formato" value={aspect} onChange={pickAspect}
                      items={(Object.keys(ASPECTS) as Aspect[]).map((a) => ({ label: ASPECTS[a], v: a }))} />
                    <Choice label="Reencuadre" value={mode} onChange={setMode} items={modes.map((m) => ({ label: modeLabel(m), v: m }))} />
                    {modeHint && <p className="text-xs text-muted-foreground sm:pl-28">{modeHint}</p>}
                    {horizontal && tracking && <Choice label="Encuadre" value={framing} onChange={setFraming} items={FRAMINGS} />}
                    {tracking && (
                      <>
                        <Choice label="Cambios de plano" value={pace} onChange={setPace} items={PACES} />
                        <p className="text-xs text-muted-foreground sm:pl-28">{PACES.find((p) => p.v === pace)?.hint}</p>
                      </>
                    )}
                    <Choice label="Duración" items={PRESETS.map((p) => ({ label: p.label, v: p.label }))}
                      value={PRESETS.find((p) => p.lo === len.lo && p.hi === len.hi)?.label ?? ""}
                      onChange={(l) => { const p = PRESETS.find((x) => x.label === l); if (p) setLen({ lo: p.lo, hi: p.hi }) }}
                      extra={
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                          <label className="flex items-center gap-1.5">Mín
                            <Input type="number" min={5} max={170} value={len.lo} className="h-8 w-20" onChange={(e) => setLen({ ...len, lo: +e.target.value })} />
                          </label>
                          <label className="flex items-center gap-1.5">Máx
                            <Input type="number" min={10} max={180} value={len.hi} className="h-8 w-20" onChange={(e) => setLen({ ...len, hi: +e.target.value })} />
                          </label>
                        </div>
                      } />
                    <Choice label="Zoom" value={zoom} onChange={setZoom} items={ZOOMS} />
                    <Row label="Clips">
                      <Input type="number" aria-label="Número de clips" min={1} max={10} value={n} className="h-8 w-20" onChange={(e) => setN(+e.target.value)} />
                    </Row>
                  </fieldset>

                  {badLen && <p className="text-sm text-destructive">El mínimo debe ser menor que el máximo.</p>}
                  {err && <p role="alert" className="text-sm text-destructive">{err}</p>}
                  <Button type="submit" size="lg" disabled={!canStart} className="w-full sm:w-auto">
                    {busy ? <Loader2 className="animate-spin" /> : <Sparkles />} Generar clips
                  </Button>
                </form>
              </CardContent>
            </Card>

            {/* Análisis en vivo */}
            <Card className="rounded-2xl">
              <CardContent className="space-y-4 p-5">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="flex items-center gap-2 text-base font-semibold"><Activity className="size-4 text-orange-600" /> Análisis en vivo</h2>
                  <Badge className={STATUS[phase].cls}>{STATUS[phase].label}</Badge>
                </div>

                <div className="relative h-56 overflow-hidden rounded-xl border bg-muted/40 bg-grid">
                  {busy && <div className="absolute inset-x-0 h-0.5 animate-scan bg-primary shadow-[0_0_16px_3px] shadow-primary/50 motion-reduce:animate-none" />}
                  {!job && (
                    <div className="absolute inset-0 grid place-items-center pb-16 text-muted-foreground">
                      <div className="grid size-14 place-items-center rounded-full border bg-card"><Scissors className="size-6" /></div>
                    </div>
                  )}
                  <div className="absolute inset-x-3 bottom-3 rounded-lg border bg-card/90 p-3 backdrop-blur">
                    <div className="mb-2 flex items-baseline justify-between gap-3">
                      <span aria-live="polite" className={cn("font-serif text-lg leading-snug", phase === "error" && "line-clamp-3 font-sans text-sm text-destructive")}>
                        {phase === "idle" ? "Esperando un vídeo" : phase === "error" ? job?.error : job?.stage}
                      </span>
                      {job && <span className="text-sm tabular-nums text-muted-foreground">{pct}%</span>}
                    </div>
                    <Progress value={pct} aria-label="Progreso" />
                  </div>
                </div>

                <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {STEPS.map((s, i) => {
                    const st = i < step ? "done" : i > step ? "todo" : phase === "error" ? "error" : "now"
                    const Icon = st === "done" ? CircleCheck : st === "error" ? CircleX : st === "now" ? Loader2 : Circle
                    return (
                      <li key={s} className={cn("flex items-center gap-2 rounded-lg border px-3 py-2 text-sm",
                        st === "now" && "border-primary/50 bg-primary/10 font-medium",
                        st === "todo" && "text-muted-foreground",
                        st === "error" && "border-destructive/40 text-destructive")}>
                        <Icon className={cn("size-4 shrink-0", st === "now" && "animate-spin text-orange-600 motion-reduce:animate-none", st === "done" && "text-emerald-600")} />
                        <span className="truncate">{s}</span>
                      </li>
                    )
                  })}
                </ol>

                <Collapsible defaultOpen className="overflow-hidden rounded-xl border">
                  <CollapsibleTrigger className="group flex w-full items-center justify-between px-4 py-3 text-sm font-medium outline-none focus-visible:bg-muted">
                    <span className="flex items-center gap-2"><Terminal className="size-4 text-muted-foreground" /> Registro del sistema</span>
                    <ChevronDown className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <div ref={logRef} className="max-h-40 space-y-0.5 overflow-y-auto border-t bg-muted/50 px-4 py-3 font-mono text-xs">
                      {logs.length === 0 && <span className="text-muted-foreground">Sin actividad todavía.</span>}
                      {logs.map((l, i) => (
                        <div key={i}><span className="text-muted-foreground">{l.t}</span>  {l.m}</div>
                      ))}
                    </div>
                  </CollapsibleContent>
                </Collapsible>
              </CardContent>
            </Card>
          </div>

          {/* Clips generados */}
          <Card className="flex rounded-2xl lg:sticky lg:top-20 lg:max-h-[calc(100svh-6rem)]">
            <CardContent className="flex min-h-0 flex-1 flex-col gap-4 p-5">
              <div className="flex items-center justify-between gap-3">
                <h2 className="font-serif text-xl">Clips generados</h2>
                {clips.length > 0 && <Badge className="bg-primary/15 text-orange-800">{clips.length}</Badge>}
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto">
                {clips.length === 0 ? (
                  <div className="grid min-h-72 place-items-center text-center">
                    <div className="max-w-64 space-y-2 text-muted-foreground">
                      {busy ? <Loader2 className="mx-auto size-8 animate-spin text-orange-600 motion-reduce:animate-none" /> : <Scissors className="mx-auto size-8" />}
                      <p className="font-medium text-foreground">
                        {busy ? "Esperando clips…" : phase === "error" ? "No se generaron clips" : job ? "No hay momentos que encajen" : "Aún no hay clips"}
                      </p>
                      <p className="text-sm">
                        {busy ? "Aparecerán aquí cuando termine el renderizado."
                          : phase === "error" ? "Revisa el error del análisis e inténtalo de nuevo."
                          : job ? "Prueba con otro rango de duración."
                          : "Pega una URL y pulsa «Generar clips»."}
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className={cn("grid gap-4", !wide && "sm:grid-cols-2")}>
                    {clips.map((c) => (
                      <Card key={c.url} className="rounded-xl shadow-none">
                        <CardContent className="space-y-3 p-3">
                          <video src={c.url} controls preload="metadata" className={cn("w-full rounded-lg bg-black", c.aspect === "16:9" ? "aspect-video" : "aspect-[9/16]")} />
                          <div className="flex items-start justify-between gap-2">
                            <h3 className="text-sm font-medium leading-snug">{c.title}</h3>
                            <Badge className="shrink-0 gap-1 bg-primary/15 text-orange-800" title="Puntuación viral"><Flame className="size-3" />{c.score}</Badge>
                          </div>
                          <p className="text-sm text-muted-foreground">{c.reason}</p>
                          <p className="text-xs text-muted-foreground">
                            {fmt(c.start)}–{fmt(c.end)} del original · {reframeLabel(c) ? reframeLabel(c) + " · " : ""}{c.hashtags.map((h) => (h.startsWith("#") ? h : "#" + h)).join(" ")}
                          </p>
                          <Button asChild variant="secondary" size="sm">
                            <a href={c.url} download><Download /> Descargar</a>
                          </Button>
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}