# camera-monitor — NVR local multiplataforma

NVR (Network Video Recorder) self-hosted para câmeras IP na rede local: ao vivo no navegador, gravação contínua em disco, playback e, conforme suporte comprovado da câmera, PTZ, iluminação, eventos e push-to-talk. Funciona sem internet e sem nuvem.

- Proposta: [docs/proposal.md](docs/proposal.md)
- Plano de implementação: [docs/plano_implementacao.md](docs/plano_implementacao.md)
- Decisões (ADRs): [docs/decisions/](docs/decisions/)
- Compatibilidade de câmeras e PoCs: [docs/compatibility/](docs/compatibility/)

**Estado:** Fase 0 (descoberta e fundação). Ainda não há aplicação; só scripts de prova de conceito.

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
