# Clipper — clips virales desde una URL de YouTube (100% local)

Optimizado para **Mac M1 con 8 GB de RAM**. Todo corre en tu ordenador con modelos open source:

- **mlx-whisper** (Whisper large-v3-turbo en Metal) → transcripción con tiempos por palabra
- **Ollama + qwen3:4b** → elige los mejores momentos
- **yt-dlp + FFmpeg** → descarga, corte, 9:16 y subtítulos
- **React + Vite + shadcn/ui** → interfaz

## Instalar (una sola vez)
    chmod +x setup.sh start.sh
    ./setup.sh

## Usar
    ./start.sh
Abre http://localhost:5173, pega la URL y pulsa "Generar clips".
Los clips quedan en `backend/output/<id>/`.

## Ajustes (variables de entorno antes de ./start.sh)
- `CLIPPER_LLM=qwen3:4b` (prueba `gemma3:4b` u otro; en 8 GB evita modelos de más de ~5 GB)
- `CLIPPER_WHISPER=mlx-community/whisper-large-v3-turbo` (más ligero: `mlx-community/whisper-small-mlx`)

## Notas
- En 8 GB cierra Chrome y apps pesadas mientras procesas. Un vídeo de 1 h tarda ~10–20 min.
- Usa solo vídeos propios o con permiso del creador (derechos de autor y términos de YouTube).
- Mejoras futuras: seguimiento de caras para el reencuadre, detección de risas/aplausos, subida directa a redes.
