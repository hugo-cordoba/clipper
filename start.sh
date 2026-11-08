#!/usr/bin/env bash
cd "$(dirname "$0")"
pgrep -x ollama >/dev/null || (ollama serve >/dev/null 2>&1 &)
trap 'kill 0' EXIT
(cd backend && .venv/bin/uvicorn main:app --port 8000) &
(cd frontend && npm run dev -- --open) &
wait
