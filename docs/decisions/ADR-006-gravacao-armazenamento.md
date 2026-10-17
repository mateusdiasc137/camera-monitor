# ADR-006 — Gravação, índice e política de armazenamento

**Estado:** aceito, exceto o tratamento do áudio (decidir na F1-10) · **Data:** 17/10/2026

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
- **Índice no SQLite** (câmera, início, duração, bytes, local, caminho relativo), alimentado por `runOnRecordSegmentComplete` e por uma varredura de reconciliação. O playback usa o servidor de playback do MediaMTX; o índice serve para timeline, gaps, cota e substituição.
- **Gaps** distinguem "pausa do usuário" de "falha" (câmera offline), a partir dos eventos de status do módulo `cameras`.

## Pendente

- **Áudio:** o G.711 da câmera é gravado como LPCM no fMP4. Na F1-10, testar o playback no Chrome. Se não tocar, a opção preferida é transcodificar **só o áudio** no relay (`-c:v copy -c:a libopus`, ou AAC), com custo baixo. Isso também traria áudio ao HLS.

## Consequências

- 7 dias da X6C-WEQ ≈ 88 GB; 30 dias ≈ 375 GB (medir à noite antes de fixar os padrões).
- A política de cota é testável sem câmera, com `DiskUsage` falso e diretório temporário.
