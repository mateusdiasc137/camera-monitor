# ADR-006 — Gravação, índice e política de armazenamento

**Estado:** aceito · **Data:** 17/10/2026 · **Revisão (F1-09):** índice por varredura; nome com fuso · **(F1-11):** cota implementada · **(F1-11b):** local pela UI · download por intervalo e lente

## Contexto

Requisitos do usuário: ligar/desligar a gravação por câmera, escolher o local de gravação pela UI, definir uma cota e sobrescrever o mais antigo quando ela encher. Medições da X6C-WEQ: principal ≈ 1,16 Mbps (≈ 12,5 GB/dia), secundário ≈ 0,37 Mbps.

## Decisão

- **Formato:** fMP4 do MediaMTX, segmentos de **5 min** (`recordSegmentDuration`), partes de 1 s. Grava o **perfil principal**; o secundário fica só para o ao vivo.
- **Retenção do MediaMTX desligada** (`recordDeleteAfter: 0s`). O backend é o único dono da política:
  - cota global em GB no local de gravação;
  - `overwrite_oldest` (padrão) ou `stop_when_full`;
  - histerese: limpeza começa em 95% da cota e vai até 90%;
  - piso físico de espaço livre (2 GB) que age mesmo abaixo da cota e mesmo sem cota; quando dispara, limpa até 125% do piso;
  - nunca apaga o segmento aberto, arquivos modificados há < 30 s nem nada fora do diretório gerenciado; toda exclusão vai para o `audit_log`.
- **Implementação (F1-11):** `EnforceStoragePolicy` roda a cada 60 s (e logo após mudar a política). Escolhe os mais antigos **entre todas as câmeras** pelo início do segmento; o arquivo mais novo de cada pasta de câmera **ativa** é protegido (pode estar aberto), mas pastas de câmeras removidas são apagadas por inteiro quando forem as mais antigas. Exclusão só de `.mp4` em `<pasta>/<arquivo>` dentro do local (sem links simbólicos); o índice é atualizado na hora.
  - `stop_when_full`: nada é apagado; uma trava (`RecordingGate`) faz a reconciliação desligar `record` de todos os caminhos no MediaMTX (o ao vivo continua). Volta sozinha abaixo de 90% da cota com o piso folgado. O estado aparece na saúde do sistema (`storage: down`) e na aba Armazenamento.
  - Reduzir a cota abaixo do já gravado com `overwrite_oldest` exige confirmação explícita (`409 confirmation_required` com a estimativa do que será apagado); a cota não pode passar do que cabe no disco (uso + livre − piso).
  - Estimativa de dias: ritmo medido por câmera nas últimas 24 h do índice × capacidade até 90% da cota.
  - Ensaio com a X6C-WEQ (segmentos de 20 s, cota de 12 MB): sobrescrever apagou os mais antigos sem parar a gravação; "parar de gravar" desligou `record` no MediaMTX e religou ao aumentar a cota.
- **Índice no SQLite** (câmera, início, duração, bytes, local, caminho relativo), alimentado **só por varredura periódica** (30 s) do local de gravação (revisto na F1-09). O playback usa o servidor de playback do MediaMTX; o índice serve para timeline, gaps, cota e substituição.
  - `runOnRecordSegmentComplete` foi descartado pelo mesmo motivo do ADR-002: comandos externos do MediaMTX expandem `$` sem escape. A varredura também se corrige sozinha após quedas (arquivos novos, que cresceram ou que sumiram).
  - **Leitura própria do fMP4** (sem FFmpeg): início pela caixa `udta/mtxi` do MediaMTX (`NTP` em ns Unix, UTC); duração pelo `mvhd` (ms, escrito ao fechar) ou, em segmento interrompido, pelo último fragmento completo (`tfdt` + Σ `trun`), a mesma regra do MediaMTX. Validado contra o ffprobe em gravações da X6C-WEQ e em teste de integração com o MediaMTX real (diferença ≤ 0,35 s).
  - O segmento **em gravação** também é indexado e relido enquanto cresce: a timeline chega até ~30 s de "agora"; com a câmera gravando, o último trecho é estendido até o instante atual.
  - **Nome dos arquivos com fuso** (`%Y-%m-%d_%H-%M-%S-%f%z`): sem ambiguidade de horário de verão; o MediaMTX entende ao reproduzir. Arquivos antigos (sem `%z`) continuam indexados (hora local do servidor), mas o playback do MediaMTX não os encontra pelo padrão novo.
- **Gaps** distinguem "pausa do usuário", "câmera indisponível", "desativada" e "desconhecido" a partir do histórico de estado (F1-07). O estado é lido até 15 s dentro do buraco (atraso da amostragem) e buracos longos são divididos nas mudanças de estado (ex.: pausa → servidor de mídia fora → pausa).

- **Reprodução e download (F1-10):** pelo backend (`GET /api/v1/recordings/{id}/video`), que repassa ao servidor de playback do MediaMTX (`/get`, só loopback) com a credencial `reader` — mesmo modelo do ao vivo (ADR-003). `fmp4` progressivo para assistir, `mp4` para baixar; até 2 h por pedido; sem `Range` (o MediaMTX gera o trecho na hora), então "pular" = pedir outro início.
- **Áudio gravado (decidido na F1-10): sem transcodificação.** O G.711 vira LPCM (`pcm_s16be`) no fMP4. Ensaio no Chrome real (Playwright) com gravação da X6C-WEQ: o `<video>` toca e **decodifica o áudio LPCM** (o `canPlayType("ipcm")` responde vazio, mas a reprodução real funciona). Mantido sem recodificar.
- **Navegadores:** Chrome (Windows) decodifica H.265 em WebRTC e no `<video>`. O Edge desta máquina **não** (no Windows depende da extensão "HEVC Video Extensions"): a interface já avisa quando o navegador não informa suporte a H.265.

- **Local de gravação pela UI (F1-11b, D7 do plano):**
  - Navegação só dentro das raízes de `NVR_STORAGE_ROOTS` (JSON; padrão: só a pasta inicial), com caminhos resolvidos (`..` normalizado, links seguidos) antes de conferir; links que apontam para fora não aparecem nem podem ser escolhidos. Só admin.
  - Validação: pasta existe ou pode ser criada, **escrita real** pelo backend (arquivo temporário com `fsync`), um local não pode conter o outro, sem `%`/aspas/controle no caminho (o `recordPath` do MediaMTX usa `%` para variáveis), aviso se estiver no disco do sistema (mesmo `st_dev` de `/`; no Windows, a unidade do sistema), aviso se a cota não couber e se a pasta já tiver outros arquivos. Sem permissão: a mensagem traz o comando (`chown`/`icacls`).
  - **A troca é a quente:** o próximo ciclo de reconciliação faz `replace` do path no MediaMTX com o novo `recordPath`. Medido no MediaMTX v1.21.2: o publicador (relay) não cai, o segmento aberto fecha na hora e o novo começa no novo local no próximo quadro-chave — buraco de até ~1 GOP no instante da troca.
  - `migrate`: tarefa de fundo move os arquivos (os mais recentes primeiro): `rename` no mesmo disco; entre discos, cópia para `.part` + `fsync` + conferência de tamanho, e só então apaga a origem. Cada arquivo muda de local no índice na hora, sem duplicar mesmo se a varredura do novo local chegar antes. Arquivos mexidos há < 30 s esperam (podem estar abertos). Estado persistido (`storage_migrations`): retoma sozinha após reinício; se falhar, a UI oferece **retomar** ou **abandonar** (o resto fica no local antigo, fora do índice, nada é apagado) e bloqueia novas trocas até resolver. Progresso pelo SSE (`storage.migration`).
  - `fresh`: os arquivos antigos ficam onde estão e saem do índice e da cota; se o local for reativado depois, a varredura os traz de volta.
  - Diferenças em relação ao D7: o teste de escrita é feito só pelo backend — a escrita pelo serviço do MediaMTX fica garantida pelo grupo compartilhado da F1-19; trechos ainda não migrados aparecem normalmente na linha do tempo (a tela de armazenamento avisa que ficam indisponíveis até chegarem), sem marcação própria.
- **Reprodução através de buracos curtos (corrigido na F1-11b):** a linha do tempo une buracos < 2 s, mas o servidor de playback do MediaMTX encerra o trecho no buraco e responde 404 a um início dentro dele. Agora o backend avança o início até o próximo segmento (pelo índice) e a interface continua de onde o vídeo parou de fato. Vale para qualquer reinício do gravador (troca de local, câmera que reconectou, pausa).
- Ensaio com a X6C-WEQ (segmentos de 20 s): troca com "mover" durante a gravação → câmera seguiu online, MediaMTX gravando no novo local, 4/4 arquivos migrados, índice sem duplicatas, linha do tempo contínua, trecho anterior à troca tocando do novo local e reprodução continuando depois do buraco da troca; o mesmo fluxo pela interface no Chrome real, inclusive em largura de celular.

- **Download por intervalo e por lente (pedido do usuário, após a F1-11b):**
  - Na aba Gravações, a barra fica logo abaixo do vídeo e funciona como a de um editor simples: duas alças (início/fim), área fora do corte escurecida, zoom por botões, pinça (celular) ou roda do mouse, arrastar para deslocar, tocar para assistir. Os horários do corte também podem ser digitados (HH:MM:SS) e marcados pelo ponto do vídeo. Saíram os botões "baixar 5/15/30 min".
  - O download cobre exatamente o intervalo marcado (até 2 h). O servidor de playback do MediaMTX para no primeiro buraco; por isso, se houver buraco (queda, pausa) o backend busca cada parte contínua (pulando os buracos pelo índice) e junta tudo com o FFmpeg (`concat`, `-c copy`, `+faststart`) num MP4 comum, com tamanho informado ao navegador. Sem buracos e com o quadro inteiro, o MP4 vem direto do MediaMTX, como antes.
  - **Lente superior/inferior sem recodificar:** a janela de exibição do H.265/H.264 é ajustada (`hevc_metadata`/`h264_metadata` `crop_top`/`crop_bottom`) — o arquivo guarda o quadro inteiro, mas Chrome, VLC e FFmpeg mostram só a lente (X6C-WEQ: 2304x2592 → 2304x1296), sem perda de qualidade e quase sem custo de CPU. As dimensões vêm do `stsd` do próprio fMP4 (leitura própria). Temporários em `data/exportacoes`, apagados ao terminar o envio (e sobras antigas na próxima exportação); no máximo 2 exportações simultâneas.
  - Ensaio com a X6C-WEQ no Chrome real: corte de 23:04:53 a 23:06:21 atravessando uma pausa de 25 s → arquivo com só os ~63 s gravados, nas duas versões (inteiro 2304x2592 e lente inferior 2304x1296), com áudio.

## Consequências

- 7 dias da X6C-WEQ ≈ 88 GB; 30 dias ≈ 375 GB (medir à noite antes de fixar os padrões).
- A política de cota é testável sem câmera, com `DiskUsage` falso e diretório temporário.
