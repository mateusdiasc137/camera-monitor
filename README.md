# camera-monitor — NVR local multiplataforma

NVR (Network Video Recorder) self-hosted para câmeras IP na rede local: ao vivo no navegador, gravação contínua em disco, playback e, conforme suporte comprovado da câmera, PTZ, iluminação, eventos e push-to-talk. Funciona sem internet e sem nuvem.

- Proposta: [docs/proposal.md](docs/proposal.md)
- Plano de implementação: [docs/plano_implementacao.md](docs/plano_implementacao.md)
- Decisões (ADRs): [docs/decisions/](docs/decisions/)
- Compatibilidade de câmeras e PoCs: [docs/compatibility/](docs/compatibility/)

**Estado:** Fase 1 (MVP) em andamento: login, cadastro de câmeras com teste de conexão, e gravação contínua via MediaMTX + relays FFmpeg comandados pelo backend. Ao vivo na interface e playback ainda não (F1-08 e F1-10).

## Desenvolvimento

```bash
python scripts/fetch_tools.py                     # uma vez: baixa o MediaMTX
python scripts/dev_mediamtx.py                    # terminal 1: MediaMTX (portas 8554/8888/8889/9996/9997)

# backend (apps/backend), terminal 2
cd apps/backend
uv sync
uv run nvr create-user admin                       # pede a senha no terminal
NVR_COOKIE_SECURE=false uv run nvr serve           # http://127.0.0.1:8000 (só em dev: cookie sem HTTPS)
uv run pytest && uv run mypy && uv run lint-imports

# UI (apps/web), terminal 3
cd apps/web
npm ci
npm run dev                                        # http://localhost:5173
```

No PowerShell, defina a variável com `$env:NVR_COOKIE_SECURE="false"` antes de `uv run nvr serve`, e use `npm.cmd` se a política de execução bloquear `npm`.

Com o MediaMTX rodando, o backend cria sozinho um path por câmera cadastrada (`cam-<id>-main`/`-sub`), sobe um relay FFmpeg para cada um e grava o perfil principal em `apps/backend/data/recordings/` (enquanto a escolha do local pela UI não chega, na F1-11b).

## Pré-requisitos

- Python 3.12+
- FFmpeg/ffprobe no PATH (`sudo apt install ffmpeg` no Ubuntu; `winget install Gyan.FFmpeg` no Windows)
- Alvo de produção: Ubuntu Server LTS. Windows suportado.

## PoC: gravação e ao vivo com câmera falsa

```bash
python scripts/fetch_tools.py     # baixa o MediaMTX fixado e confere o SHA-256
python scripts/fake_camera.py     # terminal 1: câmera RTSP falsa em 127.0.0.1:8654
python scripts/poc_nvr.py         # terminal 2: MediaMTX gravando + ao vivo na LAN
python scripts/poc_check.py       # terminal 3: checagens automáticas
```

Com a câmera real: copie `.env.example` para `.env`, preencha `NVR_CAM_*` e rode `python scripts/poc_nvr.py --source real`.

## Segredos

Credenciais reais ficam só no `.env` local (ignorado pelo Git) e, na aplicação, no cofre de credenciais do SO. Nunca coloque usuário/senha da câmera, URL RTSP completa, gravações ou capturas em commits, issues ou logs.
