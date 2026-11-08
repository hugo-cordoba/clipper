#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
command -v brew >/dev/null || { echo "Instala Homebrew primero: https://brew.sh"; exit 1; }
brew list ffmpeg-full >/dev/null 2>&1 || brew install ffmpeg-full
brew list node >/dev/null 2>&1 || brew install node
brew list deno >/dev/null 2>&1 || brew install deno
command -v ollama >/dev/null || brew install ollama
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -U pip
backend/.venv/bin/pip install -U -r backend/requirements.txt
(cd frontend && npm install)
(pgrep -x ollama >/dev/null || (ollama serve >/dev/null 2>&1 &) ; sleep 3)
ollama pull "${CLIPPER_LLM:-qwen3:4b}"
echo "Listo. Ejecuta ./start.sh"