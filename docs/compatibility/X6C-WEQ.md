# Compatibilidade — X6C-WEQ (iCSee/Xiongmai)

**Hardware:** `XM530V200_X6C-WEQ_8M` (rev. 1.01) · **Firmware:** `V5.00.R02.000807DD.10010.346732` (build 26/12/2023) · **ONVIF:** 21.06
**Relatório:** 17/10/2026 · **Estado:** Fase 0 concluída, exceto as pendências no fim
**Evidências brutas:** `docs/compatibility/raw/` (fora do Git; serial, MAC e credenciais mascarados)

Legenda: ✅ suportado com evidência · ❌ não suportado · ❔ não testado · 🔒 teste exige autorização (altera estado)

## 1. Resumo das capacidades

| Recurso | Estado | Via | Evidência |
|---|---|---|---|
| Vídeo RTSP principal/secundário | ✅ | RTSP 554 | ffprobe + PoC gravando |
| Ao vivo no navegador (H.265 + áudio) | ✅ Chrome Android e Windows | WebRTC (MediaMTX) | teste do usuário; latência < 1 s |
| Gravação contínua sem buracos | ✅ **somente via relay FFmpeg** | ver seção 4 | 0 resets em 6 min |
| Duas lentes (fixa + PTZ) | ✅ | `SupportGunBallTwoSensorCamera`; usuário confirmou imagens empilhadas | DVRIP + visual |
| Detecção de pessoa → alarme no NVR | ✅ | DVRIP alarme `appEventHumanDetectAlarm` (Start/Stop) | 9 eventos no teste |
| Detecção de movimento → alarme | ❔ não chegou nenhum evento de movimento puro | DVRIP / ONVIF `CellMotionDetector` | ver seção 5 |
| PTZ direcional, presets, ronda | ✅ declarado (não movido) 🔒 | DVRIP `OPPTZControl` e ONVIF PTZ (contínuo/absoluto/relativo, 128 presets) | capacidades lidas |
| Rastreamento automático | ✅ ligado (volta em 15 s) | DVRIP `Detect.DetectTrack` | config lida |
| Luz branca + automação noturna | ✅ ligada: modo `Intelligent`, 18h–6h, 60 s ao detectar | DVRIP `Camera.WhiteLight` | config lida |
| Modo dia/noite / IR | ✅ leitura (`DayNightColor`, `IRCUTMode`) 🔒 escrita | DVRIP `Camera.Param` | config lida |
| Áudio bidirecional (talk) | ✅ declarado (`TalkOutChannel: 1`) | DVRIP | Fase 4 |
| Snapshot da câmera | ✅ URL HTTP (porta 80) e perfil ONVIF `snapStream` | HTTP/ONVIF | `GetSnapshotUri` |
| Cartão SD | ❌ nenhum instalado | — | `StorageInfo` vazio |
| DVRIP transporte | simples (sem AES) | 34567 | login OK, `encrypted_transport: false` |

## 2. Rede

| Porta TCP | Serviço | Autenticação |
|---|---|---|
| 80 | HTTP (snapshot `webcapture.jpg`, interface web) | não testada |
| 554 | RTSP | ✅ exige (Digest) |
| 8899 | ONVIF | ⚠️ **não exige** para consultas (`NetWork.OnvifPwdCheckout: false`) |
| 34567 | DVRIP | ✅ exige; 1 tentativa por execução nos scripts |

## 3. Streams

Caminho Xiongmai com credenciais **no caminho**: `/user=…&password=…&channel=1&stream=N.sdp?`. O mascaramento de logs cobre esse formato (`scripts/_common.py`).

| Perfil | Vídeo | Áudio | Bitrate medido | GOP |
|---|---|---|---|---|
| principal (`stream=0`) | H.265 Main **2304x2592** (2 × 2304x1296 empilhadas) | G.711 A-law 8 kHz mono | 1,16 Mbps (30 s e 5 min com movimento) ≈ **12,5 GB/dia** | ~2,9 s |
| secundário (`stream=1`) | H.265 Main **640x720** (2 × 640x360) | G.711 A-law 8 kHz mono | 0,37 Mbps ≈ 4,0 GB/dia | ~2,5 s |

- A configuração da câmera (`Simplify.Encode`) diz 12 fps, VBR, 1536 kbps no principal e 1024 kbps no secundário; o SDP anuncia 25 fps. Na prática chegam **~12,5 quadros/s**.
- **Não há perfil H.264.** O H.265 funciona no Chrome do usuário (celular e PC).
- **Os metadados ONVIF não batem com o stream real** (ONVIF diz H.264 3072x2048). Não usar ONVIF como fonte de verdade sobre codecs: medir o stream (ffprobe).

## 4. Problema de timestamps e solução (relay)

**Sintoma (MediaMTX lendo a câmera direto):** a cada ~2 min, `detected drift between recording duration and absolute time, resetting`, com buracos de 1–2 s no histórico e milhares de amostras `received too late, discarding`. Segmentos de 60 s de vídeo ficavam com só 1–33 s de áudio.

**Causa:** o gravador do MediaMTX (v1.21.2, `internal/recorder`) reinicia quando a diferença entre o avanço dos timestamps RTP e o horário de referência passa de 5 s (`ntpDriftTolerance`). Os timestamps RTP da X6C-WEQ são inconsistentes tanto com o relógio do servidor quanto com o próprio horário RTCP da câmera. Medido pelo ffprobe (que sincroniza via RTCP), o relógio da câmera atrasa só ~6–8 s/hora, mas os timestamps RTP crus variam o bastante para estourar a tolerância.

| Modo testado (6 min cada) | Resets | Descartes | Áudio/segmento de 60 s |
|---|---|---|---|
| Direto, `useAbsoluteTimestamp: false` | 3 | 4.789 | 1–33 s |
| Direto, `useAbsoluteTimestamp: true` | 2 | 781 | 24–57 s |
| **Relay FFmpeg `-c copy -use_wallclock_as_timestamps 1`** | **0** | **1** | **60,5 s** |

**Decisão:** cada câmera é lida por um processo FFmpeg (sem recodificar) que republica no MediaMTX local com timestamps do relógio do servidor. Custo medido: <2% de um núcleo e ~23 MB de RAM por stream. Ver ADR-002.

## 5. Eventos (F0-10)

Escuta passiva DVRIP por ~12 min, com o usuário passando na frente da câmera:
- 9 mensagens `appEventHumanDetectAlarm` em pares `Start`/`Stop`, com `StartTime` do relógio da câmera (diferença < 1 s do PC).
- **Nenhum evento de movimento puro** foi recebido, embora `Detect.MotionDetect.Enable = true`. Hipótese: o firmware filtra movimento por pessoa (`MotionHumanDection`). Repetir com movimento não humano (ex.: objeto) antes de concluir.
- O alarme **não identifica a lente** (sempre `Channel: 0`).
- Às 16:17:07, durante a detecção, o stream principal caiu por ~4 s (provável reposicionamento do rastreamento). O MediaMTX reconectou sozinho.
- Eventos ONVIF disponíveis: `CellMotionDetector/Motion`, `TamperDetector/Tamper`, `LineDetector/Crossed`, `FieldDetector/ObjectsInside`. **Não há tópico de pessoa no ONVIF.**

**Conclusão para a Fase 3:** a fonte de eventos de pessoa é o DVRIP (conexão persistente com keep-alive de 30 s e reconexão).

## 6. Queda e retomada (F0-07)

Câmera desligada da tomada às ~16:20 e religada pelo usuário:
- O MediaMTX detectou a queda **no mesmo segundo** (16:20:19, os dois paths `ready=False`).
- Retomada às 16:21:24, tempo dominado pelo boot da câmera (~60 s). O histórico registrou o buraco corretamente (72,6 s).
- A conexão DVRIP de alarmes caiu (`WinError 10054`) e **não se reconecta sozinha**: o adaptador da Fase 3 precisa de reconexão com backoff.

## 7. Comportamentos da câmera que dependem de nuvem (decisão do usuário)

Somente leitura; nada foi alterado.

| Config | Valor atual | Implicação |
|---|---|---|
| `NetWork.Nat` | ativo (`secu100.net`) | acesso P2P pelo app iCSee via internet |
| `NetWork.PMS` | ativo (`push.umeye.cn`) | notificações push na nuvem |
| `NetWork.OnlineUpgrade` | `AutoCheck` e `AutoUpgradeImp` ativos | **pode atualizar o firmware sozinha** e mudar o comportamento que o NVR usa |
| `NetWork.OnvifPwdCheckout` | desligado | ONVIF responde sem senha na LAN |
| `NetWork.Upnp` | desligado | ok |
| `NetWork.NetNTP` | ativo (`time.windows.com`, fuso 14) | horário da câmera confiável enquanto houver internet |

## 8. Riscos abertos

1. **H.265 em outros navegadores** (Firefox, em geral, não decodifica): a UI deve detectar e recomendar o Chrome. Opções futuras: H.264 no substream pela câmera (🔒) ou transcodificação só do substream.
2. **Áudio nas gravações:** o G.711 vira LPCM no fMP4. Toca no VLC; no navegador, provavelmente não. Testar o playback no Chrome (F1-10) e decidir no ADR-006 entre aceitar, transcodificar o áudio no relay para AAC/Opus (custo baixo) ou gravar sem áudio.
3. **Atualização automática de firmware** pode quebrar a integração sem aviso.
4. **ONVIF sem senha:** qualquer dispositivo da LAN pode consultar a câmera e possivelmente movê-la.
5. **Duas lentes no mesmo stream:** recortes e zoom na UI precisam tratar as metades separadamente; o PTZ age só na lente "ball" (a confirmar qual metade).

## 9. Pendências

- [ ] Medição de bitrate à noite (IR / luz branca) por ≥ 10 min
- [ ] Playback de gravação H.265 no Chrome (com o áudio LPCM)
- [ ] Evento de movimento não humano
- [ ] Identificar qual metade da imagem é a lente PTZ (observação visual, sem mover)
- [ ] Testes que exigem autorização: ver `X6C-WEQ-testes-pendentes.md` 🔒
