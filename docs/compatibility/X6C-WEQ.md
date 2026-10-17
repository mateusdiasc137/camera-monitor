# Compatibilidade — X6C-WEQ (iCSee/Xiongmai)

**Firmware reportado:** V5.00.R02.000807DD · **Relatório iniciado:** 17/10/2026 · **Estado:** em andamento (Fase 0)

Legenda: ✅ suportado (com evidência) · ❌ não suportado · ❔ desconhecido/não testado

## Rede (F0-06, somente leitura)

| Porta TCP | Estado em 17/10/2026 | Interpretação |
|---|---|---|
| 80 | aberta | interface web (não testada) |
| 554 | aberta | RTSP — ✅ confirmado |
| 8899 | aberta | possível ONVIF — ❔ (F0-08) |
| 34567 | aberta | provável DVRIP — ❔ (F0-09) |

## RTSP (F0-06)

Formato do caminho: Xiongmai com credenciais **no caminho** (`/user=…&password=…&channel=1&stream=N.sdp?`). Consequência: o mascaramento de logs precisa cobrir `user=`/`password=` no caminho, não só `user:pass@` (implementado em `scripts/_common.py`).

| Perfil | Caminho | Vídeo | Áudio | Bitrate medido | GOP |
|---|---|---|---|---|---|
| principal | `stream=0` | **H.265/HEVC Main, 2304x2592, 25 fps** | G.711 A-law (PCMA) 8 kHz mono | 1,16 Mbps em 30 s (≈ 12,5 GB/dia) | ~2,9 s |
| secundário | `stream=1` | **H.265/HEVC Main, 640x720, 25 fps** | G.711 A-law 8 kHz mono | 0,37 Mbps em 20 s (≈ 4,0 GB/dia) | ~2,5 s |

Observações:
- **Resolução quase quadrada nos dois perfis** (2304x2592 = 2 × 2304x1296; 640x720 = 2 × 640x360): forte indício de **câmera de duas lentes com as imagens empilhadas** no mesmo stream. A confirmar visualmente com o usuário. Afeta a UI (exibir o quadro inteiro ou separar as duas metades) e um eventual PTZ (qual lente se move).
- Bitrate medido com cena provavelmente estática; à noite (IR) e com movimento tende a subir. Repetir a medição por mais tempo e à noite antes de dimensionar a cota.
- **Não há perfil H.264.** Ver "Riscos".

## PoC MediaMTX com a câmera real

Mesma suíte da PoC sintética (`scripts/poc_check.py`): **10/10 OK**.
- Paths `cam` (principal) e `camsub` prontos: `H265, G711`.
- Sem credencial: HLS, WHEP e playback → 401.
- Segmento gravado de 60,4 s válido: `hevc` + `pcm_s16be` (o G.711 vira LPCM no fMP4).
- Playback `/get` em MP4: 10,0 s válido, `hevc`.
- HLS com credencial: só vídeo `hevc` (sem áudio, como na PoC sintética).
- 0 ocorrências de credencial nos logs.

## Riscos abertos

1. **H.265 no navegador (crítico para o ao vivo/playback).** O suporte a HEVC no navegador depende do navegador, do SO e do hardware (Safari/iOS em geral suporta; Chrome/Edge só com decodificação por hardware; Firefox, em geral, não). Teste manual pendente em celular e PC. Alternativas se falhar, em ordem de preferência:
   1. Mudar o codec do **substream** para H.264 na própria câmera, se o firmware oferecer (🔒 altera configuração; exige autorização e backup da configuração anterior).
   2. Transcodificar **só o substream** (640x720) para H.264 no servidor com FFmpeg (custo baixo de CPU), mantendo a gravação do principal em H.265 sem recodificar.
   3. Playback de gravações em H.265: download funciona (VLC); no navegador depende do mesmo suporte HEVC.
2. **Áudio G.711:** toca no WebRTC; não passa para HLS; nas gravações vira LPCM, que navegadores geralmente não tocam. Decisão no ADR-006.

## Pendências da Fase 0

- [ ] Ao vivo H.265 no celular e no PC (WebRTC `cam` e `camsub`); latência pelo relógio da imagem
- [ ] Confirmar se são duas lentes empilhadas
- [ ] Bitrate por 10 min, de dia e à noite; queda/retomada desconectando a câmera (F0-07)
- [ ] ONVIF em 8899, somente leitura (F0-08)
- [ ] DVRIP em 34567, somente leitura, modos simples e criptografado (F0-09)
- [ ] Escuta passiva de alarmes (F0-10)
