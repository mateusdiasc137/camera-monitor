# ADR-006 — Gravação, índice e política de armazenamento

**Estado:** aceito · **Data:** 17/10/2026 · **Revisão (F1-09):** índice por varredura; nome com fuso

## Contexto

Requisitos do usuário: ligar/desligar a gravação por câmera, escolher o local de gravação pela UI, definir uma cota e sobrescrever o mais antigo quando ela encher. Medições da X6C-WEQ: principal ≈ 1,16 Mbps (≈ 12,5 GB/dia), secundário ≈ 0,37 Mbps.

## Decisão

- **Formato:** fMP4 do MediaMTX, segmentos de **5 min** (`recordSegmentDuration`), partes de 1 s. Grava o **perfil principal**; o secundário fica só para o ao vivo.
- **Retenção do MediaMTX desligada** (`recordDeleteAfter: 0s`). O backend é o único dono da política:
  - cota global em GB no local de gravação;
  - `overwrite_oldest` (padrão) ou `stop_when_full`;
  - histerese: limpeza começa em 95% da cota e vai até 90%;
  - piso físico de espaço livre (padrão 2 GB ou 2%) que age mesmo abaixo da cota;
  - nunca apaga o segmento aberto, arquivos modificados há < 30 s nem nada fora do diretório gerenciado; toda exclusão vai para o `audit_log`.
- **Índice no SQLite** (câmera, início, duração, bytes, local, caminho relativo), alimentado **só por varredura periódica** (30 s) do local de gravação (revisto na F1-09). O playback usa o servidor de playback do MediaMTX; o índice serve para timeline, gaps, cota e substituição.
  - `runOnRecordSegmentComplete` foi descartado pelo mesmo motivo do ADR-002: comandos externos do MediaMTX expandem `$` sem escape. A varredura também se corrige sozinha após quedas (arquivos novos, que cresceram ou que sumiram).
  - **Leitura própria do fMP4** (sem FFmpeg): início pela caixa `udta/mtxi` do MediaMTX (`NTP` em ns Unix, UTC); duração pelo `mvhd` (ms, escrito ao fechar) ou, em segmento interrompido, pelo último fragmento completo (`tfdt` + Σ `trun`), a mesma regra do MediaMTX. Validado contra o ffprobe em gravações da X6C-WEQ e em teste de integração com o MediaMTX real (diferença ≤ 0,35 s).
  - O segmento **em gravação** também é indexado e relido enquanto cresce: a timeline chega até ~30 s de "agora"; com a câmera gravando, o último trecho é estendido até o instante atual.
  - **Nome dos arquivos com fuso** (`%Y-%m-%d_%H-%M-%S-%f%z`): sem ambiguidade de horário de verão; o MediaMTX entende ao reproduzir. Arquivos antigos (sem `%z`) continuam indexados (hora local do servidor), mas o playback do MediaMTX não os encontra pelo padrão novo.
- **Gaps** distinguem "pausa do usuário", "câmera indisponível", "desativada" e "desconhecido" a partir do histórico de estado (F1-07). O estado é lido até 15 s dentro do buraco (atraso da amostragem) e buracos longos são divididos nas mudanças de estado (ex.: pausa → servidor de mídia fora → pausa).

- **Reprodução e download (F1-10):** pelo backend (`GET /api/v1/recordings/{id}/video`), que repassa ao servidor de playback do MediaMTX (`/get`, só loopback) com a credencial `reader` — mesmo modelo do ao vivo (ADR-003). `fmp4` progressivo para assistir, `mp4` para baixar; até 2 h por pedido; sem `Range` (o MediaMTX gera o trecho na hora), então "pular" = pedir outro início.
- **Áudio gravado (decidido na F1-10): sem transcodificação.** O G.711 vira LPCM (`pcm_s16be`) no fMP4. Ensaio no Chrome real (Playwright) com gravação da X6C-WEQ: o `<video>` toca e **decodifica o áudio LPCM** (o `canPlayType("ipcm")` responde vazio, mas a reprodução real funciona). Mantido sem recodificar.
- **Navegadores:** Chrome (Windows) decodifica H.265 em WebRTC e no `<video>`. O Edge desta máquina **não** (no Windows depende da extensão "HEVC Video Extensions"): a interface já avisa quando o navegador não informa suporte a H.265.

## Consequências

- 7 dias da X6C-WEQ ≈ 88 GB; 30 dias ≈ 375 GB (medir à noite antes de fixar os padrões).
- A política de cota é testável sem câmera, com `DiskUsage` falso e diretório temporário.
