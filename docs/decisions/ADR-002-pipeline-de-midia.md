# ADR-002 — Pipeline de mídia: MediaMTX + relay FFmpeg por stream

**Estado:** aceito · **Data:** 17/10/2026 · **Revisão:** supervisão dos relays decidida na F1-06 (item 3)
**Evidências:** `docs/compatibility/poc-f0-05-mediamtx.md`, `docs/compatibility/X6C-WEQ.md` §4

## Contexto

- A PoC confirmou que o MediaMTX v1.21.2 cobre ingest RTSP, WebRTC (H.265 + G.711 no Chrome), HLS, gravação fMP4, playback e API de controle, com autenticação por path.
- Lendo a X6C-WEQ diretamente, o gravador do MediaMTX reinicia a cada ~2 min por deriva dos timestamps RTP da câmera, deixando buracos de 1–2 s e descartando a maior parte do áudio. `useAbsoluteTimestamp: true` reduz, mas não elimina.
- Um relay FFmpeg (`-c copy -use_wallclock_as_timestamps 1`) eliminou o problema: 0 resets, áudio completo, histórico contínuo, <2% de CPU por stream.

## Decisão

```
câmera ──RTSP──> FFmpeg relay (-c copy, timestamps do servidor) ──RTSP publish (localhost)──> MediaMTX
                                                                                    ├─ WebRTC/HLS (ao vivo)
                                                                                    ├─ gravação fMP4
                                                                                    └─ playback
```

1. **MediaMTX é o único motor de mídia** (ao vivo, gravação, playback). Sem pipeline de codecs próprio.
2. **Cada stream de câmera passa por um relay FFmpeg** que não recodifica e reescreve os timestamps com o relógio do servidor. O relay é uma estratégia de *ingest* configurável por câmera (`ingest: direct | relay`), com padrão `relay`: câmeras com timestamps corretos podem usar `direct`.
3. **Supervisão do relay: no backend** (decidido na F1-06). O `runOnInit` do MediaMTX foi descartado após ler o código da v1.21.2 (`internal/externalcmd`): ele divide o comando com `shellquote` e depois aplica `os.Expand` em cada argumento, **sem forma de escapar `$`**. Uma senha de câmera com `$` seria corrompida silenciosamente. O backend (`FfmpegRelaySupervisor`) executa o FFmpeg com lista de argumentos (sem shell), mascara o stderr antes do log, reinicia com backoff (2 s → 30 s) e expõe o estado de cada relay para a F1-07.
   - **Custo aceito:** reiniciar o backend reinicia os relays (buraco de poucos segundos na gravação).
   - **Se o backend morrer sem encerrar os filhos** (no Windows, matar à força não encerra a árvore), um relay órfão continua publicando até o próximo backend assumir o path (`overridePublisher: true`), quando o órfão perde a sessão e sai. No Ubuntu, o systemd encerra o cgroup inteiro.
   - Validado com a X6C-WEQ: paths criados pela API, publicação autorizada pelo backend, gravação só do principal, leitura sem token negada (401), relay morto à força volta em ~2 s, nenhuma senha nos logs.
4. O publish no MediaMTX só é aceito de `127.0.0.1` com credencial aleatória gerada pelo backend a cada inicialização, verificada pelo próprio backend (`authMethod: http` → `/internal/mediamtx/auth`).

## Consequências

- Uma conexão RTSP com a câmera por perfil (principal e secundário). O MediaMTX distribui para todos os espectadores e para o gravador, sem conexões extras na câmera.
- Os timestamps passam a refletir a **chegada no servidor**: jitter de rede vira jitter de timestamp (irrelevante para vigilância), e o horário das gravações fica alinhado ao relógio do servidor, que deve usar NTP.
- **Segredo na linha de comando:** a URL da câmera (com senha, no formato Xiongmai) aparece nos argumentos do processo FFmpeg, visíveis para outros usuários locais do servidor. Mitigação: servidor dedicado, usuário de serviço `nvr`, `/proc` montado com `hidepid=2` no Ubuntu (F1-19). Registrado como risco aceito.
- A saída de erro do FFmpeg precisa passar pelo mascaramento antes de ir para logs.
- O ADR-005 (go2rtc para talk) deve considerar que o ingest já é feito pelo relay.
