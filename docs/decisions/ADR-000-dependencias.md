# ADR-000 — Versões e licenças das dependências de terceiros

**Estado:** aceito (Fase 0) · **Data:** 17/10/2026 · **Tarefa:** F0-02

## Contexto

A proposta exige confirmar versão e licença antes de integrar qualquer componente (seção 10). Os dados abaixo foram consultados na API do GitHub em 17/10/2026.

## Decisão

| Componente | Versão/commit fixado | Licença | Uso | Como entra no projeto |
|---|---|---|---|---|
| MediaMTX | **v1.21.2** (release de 08/10/2026) | MIT | ingest RTSP, WebRTC/HLS, gravação, playback | binário baixado por `scripts/fetch_tools.py` com SHA-256 fixado em `tools/manifest.json`; processo externo |
| FFmpeg / ffprobe | 9.0.2 (dev Windows: build `full_build` da gyan.dev via winget) | LGPL/GPL conforme a build; a build *full* da gyan.dev é **GPL** | diagnóstico, câmera falsa, snapshots | instalado pelo SO (`apt`/`winget`), chamado como processo externo; **não redistribuído** |
| icsee-ptz | v5.1.8; `asyncio_dvrip.py` no commit `5bf50bc6b5df` (27/09/2026) | MIT | referência DVRIP (PTZ, luz, alarmes) | Fase 0: lido como referência. Fase 2: adaptação do cliente com aviso de copyright MIT preservado (ADR-004) |
| Icsee-android | v1.1.7 (17/10/2026) | MIT | referência de protocolo (talk, handshake) | só leitura/referência; não importado |
| go2rtc | v1.9.14 (19/01/2026) | MIT | PoC de push-to-talk (Fase 4) | ainda não usado; reavaliar versão na Fase 4 (ADR-005) |
| Caddy | v2.11.7 (03/10/2026) | Apache-2.0 | proxy HTTPS (F1-18) | ainda não usado; fixar com hash em `tools/manifest.json` quando entrar |
| python-onvif-zeep | sem releases; último commit `d3a2d68b6c3a` (17/11/2025) | MIT | cliente ONVIF candidato (F0-08, Fase 5) | avaliar manutenção antes de adotar; alternativa: chamadas SOAP mínimas próprias |
| Python | 3.12.10 | PSF | backend e scripts | ambiente de desenvolvimento |
| uv | 0.13.1 | MIT/Apache-2.0 | gestão de dependências Python | ferramenta de desenvolvimento |

## Consequências

- Atualizar MediaMTX = mudar versão + hashes em `tools/manifest.json` e registrar aqui. O script recusa binário com hash divergente.
- Os scripts da Fase 0 usam só a biblioteca padrão do Python; dependências Python/npm do backend e da UI serão fixadas por lockfiles (`uv.lock`, `package-lock.json`) na F1-01.
- FFmpeg GPL: aceitável enquanto for instalado pelo usuário e executado como processo separado. Se um dia o projeto distribuir pacotes com FFmpeg embutido, revisar a licença da build.
- **Achado relevante para a Fase 2 (ADR-004):** o `asyncio_dvrip.py` atual implementa o transporte DVRIP **criptografado** (AES-128-CBC com chave fixa dos apps oficiais, além do login usuário/senha), exigido por firmwares XM mais novos. A F0-09 deve testar os dois modos (simples e criptografado) na X6C-WEQ; o adaptador próprio precisa suportar ambos. Depende do pacote `cryptography`.
