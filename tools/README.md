# tools/

Binários de terceiros baixados por `python scripts/fetch_tools.py`. Só `manifest.json` e este README são versionados.

- `manifest.json` fixa a versão e o SHA-256 de cada binário por plataforma. Para atualizar, troque versão/asset/hash a partir da página de release oficial e registre no `docs/decisions/ADR-000-dependencias.md`.
- FFmpeg/ffprobe **não** são baixados aqui: instale pelo gerenciador do sistema (`apt install ffmpeg` no Ubuntu, `winget install Gyan.FFmpeg` no Windows).
