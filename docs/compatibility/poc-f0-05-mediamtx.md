# PoC F0-05 — MediaMTX com câmera falsa

**Data:** 17/10/2026 · **Ambiente:** Windows 11, MediaMTX v1.21.2, FFmpeg 9.0.2, Python 3.12.10
**Fonte:** câmera falsa (`scripts/fake_camera.py`): H.264 1280x720/640x360 a 15 fps + áudio G.711 A-law 8 kHz

## Como reproduzir

```bash
python scripts/fetch_tools.py        # baixa MediaMTX conferindo SHA-256
python scripts/fake_camera.py        # terminal 1
python scripts/poc_nvr.py            # terminal 2 (segmentos de 1 min)
python scripts/poc_check.py          # terminal 3: checagens automáticas
```

## Resultados

| Checagem | Resultado | Evidência |
|---|---|---|
| Câmera falsa exige senha | OK | senha correta: `h264` + `pcm_alaw`; senha errada: `DESCRIBE failed: 401` |
| Câmera cadastrada só pela API (sem senha em disco) | OK | `data/poc/mediamtx.yml` sem `source`/URL; senha do viewer só como `sha256:` |
| Paths `cam` e `camsub` prontos | OK | API: `ready=True`, faixas `H264, G711` |
| HLS / WHEP / playback sem credencial | OK (recusados) | HTTP 401 nos três |
| Página WebRTC com credencial | OK | HTTP 200 |
| HLS com credencial | OK, **sem áudio** | ffprobe: só `video:h264` (ver achado 1) |
| Gravação em disco sem navegador | OK | segmento fMP4 de 60,2 s válido: `video:h264, audio:pcm_s16be` |
| Playback `/list` e `/get` (MP4 de 10 s) | OK | HTTP 200, 10,0 s, válido no ffprobe |
| Queda e retomada da câmera | OK | câmera derrubada ~30 s: path `ready=False`, MediaMTX tenta a cada 5 s; ao voltar, gravação retomada em ~5 s; `/list` mostra **2 intervalos com o buraco** entre eles |
| Credenciais em logs | OK | 0 ocorrências de URL com credencial nos logs do NVR e da câmera falsa |
| **Ao vivo em outro dispositivo da LAN** | **PENDENTE** | depende de teste manual do usuário em um celular (ver abaixo) |

## Achados que afetam a Fase 1

1. **Áudio G.711 não passa para HLS.** O HLS do MediaMTX entregou só vídeo. O WebRTC aceita G.711 (PCMA) nativamente, então o ao vivo com áudio deve ir por WebRTC. Se a câmera real também usar G.711, HLS (fallback) fica sem áudio, a menos que o áudio seja transcodificado.
2. **Gravação converte G.711 em LPCM (`pcm_s16be`) no fMP4.** Navegadores geralmente não tocam LPCM dentro de MP4, então o playback no navegador pode sair **sem áudio**, embora o arquivo baixado toque no VLC. Opções a decidir no ADR-006 depois de conhecer o codec real (F0-06): (a) aceitar playback sem áudio no navegador; (b) transcodificar só o áudio para AAC/Opus num path intermediário via FFmpeg (custo baixo de CPU, mas uma etapa a mais); (c) usar o áudio AAC se a câmera oferecer.
3. **Reconexão nativa do MediaMTX é suficiente** para RF-07 (tentativas a cada 5 s). O backend só precisa observar o estado e registrar os gaps.
4. **Bind por serviço funciona:** API e RTSP de saída ficaram em `127.0.0.1`; WebRTC, HLS e playback na LAN. Na Fase 1 os três passam para `127.0.0.1` atrás do Caddy (D1).
5. O Windows pode pedir permissão de firewall na primeira execução do `mediamtx.exe` (portas 8888, 8889, 8189/UDP, 9996). Para o teste na LAN, permitir **somente em redes privadas**.

## Teste pendente: ao vivo em outro dispositivo

Com `fake_camera.py` e `poc_nvr.py` rodando neste PC:
1. No celular, na mesma Wi-Fi, abrir `http://<IP-deste-PC>:8889/cam` (o `poc_nvr.py` mostra o endereço).
2. Usuário `viewer`; senha em `data/poc/viewer.secret` (gerada a cada execução).
3. Esperado: imagem de teste colorida em movimento com tom de 440 Hz. Anotar a latência aproximada e se o áudio toca.
