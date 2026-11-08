#!/usr/bin/env bash
cd "$(dirname "$0")"

# Cookies de Safari por defecto. Para desactivarlas: CLIPPER_COOKIES_BROWSER= ./start.sh
export CLIPPER_COOKIES_BROWSER="${CLIPPER_COOKIES_BROWSER-safari}"

pgrep -x ollama >/dev/null || (ollama serve >/dev/null 2>&1 &)
trap 'kill 0' EXIT
(cd backend && .venv/bin/uvicorn main:app --port 8000) &
(cd frontend && npm run dev) &
(sleep 3; open -a Safari http://localhost:5173) &
wait