# Plano de implementação — NVR Local Multiplataforma

**Base:** `proposta_projeto_nvr_local_multiplataforma.md` v1.0 (17/10/2026)
**Versão do plano:** 1.2 — 17/10/2026 (incorpora as respostas do usuário: hostname, alvo Ubuntu Server, gestão de armazenamento pela UI, repositório GitHub)
**Escopo detalhado:** Fase 0 e Fase 1 (MVP). Fases 2–5 em nível de marcos; cada uma recebe plano detalhado próprio ao terminar a anterior, como pede a seção 11.1 da proposta.

**Definições do usuário (v1.1):**
- **Endereço na LAN, sem depender do roteador:**
  1. **Principal:** `nvr-camera-monitor.local`, anunciado pelo próprio servidor via mDNS (Avahi). Funciona sem configurar o roteador em Windows 10+, macOS/iOS, Linux com Avahi e Android recente.
  2. **Reserva:** o IP fixo do servidor (ex.: `https://192.168.x.y`), também incluído no certificado, para aparelhos que não resolvem `.local`.
  3. **Opcional:** `nvr-camera-monitor.home.arpa` (RFC 8375), se mais tarde descobrirmos que o roteador aceita entradas de DNS local.
- **IP fixo sem mexer no roteador:** o servidor recebe IP estático pelo netplan do Ubuntu, escolhido **fora da faixa DHCP** do roteador (a faixa é descoberta na instalação observando os IPs distribuídos). A câmera continua por DHCP; ver D5 sobre mudança de IP.
- **Plataforma:** produção em **Ubuntu Server LTS** (26.04, ou 24.04 se houver motivo), alvo principal com guia de operação completo; **Windows suportado** (roda e passa na CI, guia mais enxuto). O **MX Linux** atual é tratado apenas como máquina de desenvolvimento/teste: o MX usa sysVinit por padrão, então lá o sistema roda por `scripts/dev.sh`, sem units systemd.
- **Repositório:** https://github.com/mateusdiasc137/camera-monitor (privado, vazio e acessível desta máquina em 17/10/2026).
- **Armazenamento pela UI:** habilitar/desabilitar gravação, escolher o local de gravação, definir a cota de espaço e a política de substituição, com padrão **sobrescrever as gravações mais antigas** (ver D4 e D7).
- **CI:** GitHub Actions com Ubuntu (obrigatório) + Windows.

## Estado (17/10/2026)

**Fase 0 concluída**, com pendências menores listadas em `docs/compatibility/X6C-WEQ.md` §9. Resultados que alteram este plano:
- **Relay FFmpeg por stream** entre a câmera e o MediaMTX (ADR-002): a leitura direta causava resets do gravador a cada ~2 min. F1-06 inclui o relay.
- **H.265 + G.711 funcionam ao vivo no Chrome** (celular e PC) sem transcodificação; o HLS fica sem áudio.
- **Eventos de pessoa vêm do DVRIP** (`appEventHumanDetectAlarm`); o ONVIF não tem tópico de pessoa.
- **Câmera de duas lentes empilhadas** no mesmo stream (fixa + PTZ): a UI deve permitir ver as metades separadamente.

**Fase 1 em andamento:**
- [x] F1-01 esqueleto backend/UI + CI Ubuntu/Windows (contratos de arquitetura, cliente tipado, gitleaks)
- [x] F1-02 persistência SQLite + Alembic (migrações 0001–0002)
- [x] F1-03 autenticação do painel (backend + UI de login)
- [x] F1-04 cadastro de câmeras (senha no cofre do SO; caminho com {user}/{password})
- [x] F1-05 teste de conexão (DESCRIBE RTSP próprio; validado na X6C-WEQ: H265+PCMA, auth_error com senha errada)
- [x] F1-06 MediaMTX + relays FFmpeg supervisionados pelo backend + reconciliação (validado com a câmera real)
- [x] F1-06b liga/desliga gravação por câmera (campo `recording_enabled`, aplicado na reconciliação)
- [x] F1-07 estado da câmera em tempo real (SSE) e histórico; senha recusada reduz tentativas a 1/5 min (validado na X6C-WEQ)
- [ ] F1-08 ao vivo na interface com autorização de mídia (próximo)

> Convenções: cada tarefa tem ID (`F1-07`), entregáveis, critério de "pronto" verificável e dependências.
> 🔒 = exige autorização explícita do usuário antes de executar (altera estado da câmera ou age fisicamente).
> 📷 = exige a câmera real (teste de hardware opt-in, marcado `manual_camera`).

---

## 0. Situação atual e pré-requisitos

Estado do diretório: só a proposta; não é repositório git.
Ferramentas na máquina de desenvolvimento (Windows 11):

| Ferramenta | Situação | Ação |
|---|---|---|
| git | instalado | `git init` na tarefa F0-01; remoto `https://github.com/mateusdiasc137/camera-monitor` (já criado e vazio) |
| gh CLI | ausente | opcional (`winget install GitHub.cli`); útil para configurar branch protection e acompanhar a CI |
| Node.js | v24 instalado | ok para o frontend |
| Python 3.12+ | **ausente** (só o alias da Microsoft Store) | instalar Python 3.12/3.13 (python.org ou `winget install Python.Python.3.12`) + `uv` |
| FFmpeg/ffprobe | **ausente** | instalar (`winget install Gyan.FFmpeg`) — usado para diagnóstico, câmera fake e snapshots |
| MediaMTX | ausente | baixar o binário da release fixada em F0-02 para `tools/` (fora do Git) via script |
| Docker | ausente | **não necessário** no MVP (binários nativos primeiro, conforme a proposta) |

Como a produção será em **Ubuntu Server**, o desenvolvimento neste PC Windows deve ser acompanhado de um ambiente Linux para os testes de integração e de operação: a máquina MX Linux atual (testes funcionais via `scripts/dev.sh`), WSL2 com Ubuntu ou uma VM Ubuntu Server (para testar as units systemd e o guia de instalação antes da migração). A CI do GitHub roda os dois sistemas a partir da F1-01; o job Linux é obrigatório para o merge e o Windows também precisa passar, mas os testes de serviço de SO (systemd) só existem no Linux.

---

## 1. Decisões técnicas que este plano assume

As decisões abaixo fecham lacunas da proposta. Cada uma vira ADR e pode ser revista com base nas evidências da Fase 0.

### D1 — Topologia de processos (ADR-002)
```
Navegador ──HTTPS──> Caddy (único ponto exposto na LAN)
                       ├── /api/*              → FastAPI (127.0.0.1:8000)
                       ├── /media/webrtc/*     → MediaMTX WebRTC/WHEP (127.0.0.1:8889)
                       ├── /media/hls/*        → MediaMTX HLS (127.0.0.1:8888)
                       ├── /media/playback/*   → MediaMTX playback (127.0.0.1:9996)
                       └── /*                  → arquivos estáticos do frontend
câmera ──RTSP──> relay FFmpeg (-c copy) ──publish localhost──> MediaMTX   (ADR-002)
MediaMTX: API de controle em 127.0.0.1:9997, nunca exposta
MediaMTX ──UDP 8189──> navegador (mídia WebRTC; única porta extra liberada na LAN)
```
- Endereço na LAN: `https://nvr-camera-monitor.local` (reserva: `https://<IP fixo do servidor>`; opcional: `.home.arpa`).
- FastAPI e MediaMTX são **serviços separados**, cada um com auto-start próprio (systemd no servidor Linux; WinSW no Windows). O backend **não** é pai do processo MediaMTX; ele o **reconcilia** pela API de controle.
- No desenvolvimento, `scripts/dev.(ps1|sh)` sobe tudo, sem Caddy (HTTP em localhost).

### D2 — Credenciais da câmera nunca em disco em texto puro (ADR-003/segurança)
- O `mediamtx.yml` versionado **não contém paths de câmera**. No boot, o backend lê as câmeras do SQLite, busca o segredo no `SecretStore` e cria os paths via API de controle (`/v3/config/paths/add|replace`), que ficam só em memória no MediaMTX.
- Um reconciliador periódico (ex.: a cada 30 s) recria paths caso o MediaMTX tenha reiniciado.
- `SecretStore`: adaptador `keyring` (Windows Credential Manager / Secret Service no Linux) como padrão; fallback para arquivo criptografado (Fernet) com chave em variável de ambiente, para servidores Linux headless sem keyring. O fallback tem risco documentado.
- Logs passam por um filtro que mascara `rtsp://user:pass@` e cabeçalhos `Authorization`.

### D3 — Autorização de mídia (ADR-003)
- MediaMTX configurado com `authMethod: http` e `authHTTPAddress` apontando para `http://127.0.0.1:8000/internal/mediamtx/auth`.
- `GET /api/v1/cameras/{id}/live` devolve URLs `/media/...` com um **token curto assinado** (JWT HS256, ~2 min para iniciar a sessão, escopo `camera_id + action`). O endpoint interno valida token **ou** cookie de sessão e o escopo `read`/`playback`; recusa `publish` e `api` vindos de fora do loopback.
- Resultado: copiar uma URL de mídia não dá acesso durável; usuário sem login não assiste nem lista gravações.

### D4 — Gravação, índice e política de substituição (ADR-006)
- MediaMTX grava em **fMP4**, `recordPath: <local_de_gravação>/%path/%Y-%m-%d_%H-%M-%S-%f`, `recordSegmentDuration` padrão **5 min**. Segmentos curtos permitem liberar espaço em passos pequenos quando a cota enche. `recordPartDuration` 1 s.
- **Liga/desliga por câmera:** o estado `recording_enabled` fica no SQLite. O reconciliador aplica `record: yes|no` no path do MediaMTX via API, sem derrubar o ao vivo. A UI distingue três estados: `gravando`, `pausada pelo usuário` e `parada por erro` (disco cheio, sem permissão, câmera offline).
- **Fonte da verdade da reprodução:** servidor de playback do MediaMTX (`/list` e `/get`), atrás do proxy autenticado.
- **Índice de aplicação no SQLite:** um job varre o diretório (e/ou recebe `runOnRecordSegmentComplete`) e registra segmentos finalizados (câmera, início, duração, tamanho, local de gravação, caminho relativo). Serve para timeline, gaps, contabilização da cota e substituição. Sem blobs no banco.
- **Política de armazenamento = backend**, não `recordDeleteAfter` (desligado = `0s`), para haver um único responsável:
  - **Cota** em GB definida pelo usuário para o local de gravação (todas as câmeras somadas).
  - **Política de substituição** (enum): `overwrite_oldest` (**padrão**: ao atingir a cota, apaga os segmentos finalizados mais antigos, de qualquer câmera, até voltar abaixo do limite) ou `stop_when_full` (pausa a gravação, alerta e não apaga nada).
  - **Limiares com histerese:** a limpeza começa em 95% da cota e para em 90%, com margem mínima ≥ bitrate total × 2 segmentos, para o segmento em escrita nunca estourar a cota.
  - **Piso físico de segurança:** independentemente da cota, se o espaço livre real do volume ficar abaixo de um mínimo (padrão 2 GB ou 2%), a política age como se a cota tivesse sido atingida. Isso protege quando o disco é compartilhado com outros dados.
  - Nunca apaga segmento aberto, modificado há menos de N s ou fora do diretório gerenciado. Toda exclusão vai para o `audit_log` (o quê, quanto, por quê).
  - Idade máxima por câmera fica como opção extra, **desligada por padrão**.

### D7 — Local de gravação escolhido pela UI
- Um navegador não consegue escolher pastas **do servidor** pelo seletor nativo. A UI terá um **navegador de diretórios do servidor** servido pela API, restrito a **raízes permitidas** configuradas na instalação (`NVR_STORAGE_ROOTS`; Linux, por exemplo: `/srv/nvr`, `/mnt`, `/media`; Windows: letras de unidade escolhidas). Fora dessas raízes a API recusa, e a UI nunca envia caminhos livres além dos listados.
- Só o perfil admin altera o local. Antes de aplicar, o backend **valida**: o diretório existe ou pode ser criado, os processos `nvr-api` e `nvr-mediamtx` conseguem escrever (teste de escrita real), o espaço livre comporta a cota (se não comportar, a UI avisa e sugere o máximo possível), sistema de arquivos e montagem estão presentes (no Linux, alerta se o caminho não for um ponto de montagem e puder encher a partição raiz). Se faltar permissão, mostra o comando exato para o administrador do servidor (`chown`/`setfacl`), sem tentar elevar privilégios.
- **Troca de local** com gravações existentes, o usuário escolhe:
  1. **Migrar** (recomendado): as novas gravações vão imediatamente para o novo local; um job em segundo plano move os segmentos antigos com progresso visível; trechos ainda não migrados aparecem como "em migração" na timeline.
  2. **Começar do zero no novo local**: os arquivos antigos ficam intactos no disco e saem do índice e da cota (nada é apagado sem pedido explícito).
- O `recordPath` é aplicado pelo reconciliador no MediaMTX (`pathdefaults`/paths via API); o novo local passa a valer sem reiniciar serviços.
- Estimativa exibida ao configurar: "com X GB e o bitrate atual medido (Y Mbps), a cota cobre ~N dias", pela fórmula da proposta (GB/dia ≈ Mbps × 10,8).

### D5 — Estado da câmera
Função pura no domínio, a partir de sinais coletados por adaptadores:
- `online`: path do MediaMTX `ready` e com bytes recebidos crescendo;
- `degraded`: pronto, mas com reconexões recentes, bitrate muito baixo ou gravação parada;
- `auth_error`: sonda RTSP `DESCRIBE` retorna 401 (sonda própria, mínima, no adaptador `rtsp_probe`);
- `offline`: TCP recusado ou timeout.

**Mudança de IP da câmera (DHCP sem reserva no roteador):** o cadastro guarda host/IP **e** um identificador estável do aparelho (MAC ou serial lido via DVRIP na Fase 0, armazenado sem expor na UI pública). Quando a câmera fica `offline` por mais de alguns minutos, um job de redescoberta (busca de broadcast DVRIP/ONVIF WS-Discovery na sub-rede, somente leitura) procura o mesmo identificador e, se achar, atualiza o host, avisa na UI e registra no `audit_log`. Antes de a redescoberta ficar pronta (Fase 2), a UI permite editar o IP manualmente. Fixar o IP na própria câmera é possível, mas altera a configuração dela e por isso é 🔒.

### D6 — Ferramentas
- Backend: Python 3.12+, `uv`, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, httpx, argon2-cffi, PyJWT, keyring, cryptography; `ruff`, `mypy --strict` no domínio/aplicação, `pytest`, `pytest-asyncio`, **`import-linter`** para fazer valer as regras de dependência hexagonais na CI.
- Frontend: Vite + React + TypeScript, TanStack Query, `openapi-typescript` + `openapi-fetch` (cliente gerado do OpenAPI do FastAPI), player WHEP próprio (~150 linhas) com fallback `hls.js`; Vitest + Testing Library; Playwright para E2E.
- Versões e licenças **fixadas na F0-02** (nada de `latest` em scripts).

---

## 2. Fase 0 — Descoberta e fundação

Objetivo: transformar hipóteses sobre a X6C-WEQ em fatos registrados e montar o esqueleto do repositório. Nenhuma escrita na câmera.

| ID | Tarefa | Entregáveis | Pronto quando |
|---|---|---|---|
| F0-01 | Inicializar o repositório | `git init`, `.gitignore` (data/, tools/, `.env`, `*.mp4`, `*.db`), `.env.example`, `README.md`, árvore da seção 8 da proposta (só pastas usadas), proposta movida para `docs/proposal.md` | `git status` limpo; nenhum segredo rastreado |
| F0-02 | Fixar versões e licenças | `docs/decisions/ADR-000-dependencias.md`: versão/commit e licença de MediaMTX, go2rtc, icsee-ptz, FFmpeg (tipo de build), libs Python e npm | tabela completa, com links das releases |
| F0-03 | Script de ferramentas | `scripts/fetch-tools.(ps1\|sh)`: baixa o MediaMTX fixado para `tools/`, confere SHA-256 | roda no Windows e no Linux |
| F0-04 | Câmera fake | `integrations/fixtures/fake-camera/`: 2ª instância MediaMTX (porta 8654, **com usuário/senha de teste**) + FFmpeg publicando `testsrc2` H.264 + tom de áudio; perfis `main` e `sub` | VLC/ffprobe abre `rtsp://test:test@127.0.0.1:8654/main`; senha errada → 401 |
| F0-05 | PoC MediaMTX sem app próprio (itens 1–2 da Fase 1 da proposta) | `integrations/mediamtx/mediamtx.poc.yml`; gravação fMP4 da câmera fake; WebRTC aberto em outro dispositivo da LAN | segmentos gerados com o navegador fechado; vídeo assistido de um celular |
| F0-06 📷 | Diagnóstico RTSP real | `scripts/diag/rtsp_probe.py`: lê URL/credencial de variável de ambiente, roda ffprobe, imprime codecs/resolução/fps/bitrate **com a URL mascarada** | relatório para main e substream (H.264 ou H.265? áudio PCMA/AAC?) |
| F0-07 📷 | Bitrate e reconexão | medir 10 min de bitrate; desconectar o cabo de rede da câmera por 60 s e observar o MediaMTX | bitrate médio registrado; tempo de recuperação medido |
| F0-08 📷 | ONVIF em 8899 (somente leitura) | script que chama `GetDeviceInformation`, `GetCapabilities`, `GetProfiles`, `GetServices` | resposta registrada ou "ONVIF não responde/sem auth" |
| F0-09 📷 | DVRIP 34567 (somente leitura) | script baseado no cliente do `icsee-ptz` (respeitando a licença): login, `SystemInfo`, `SystemFunction`/`AbilityGet`, leitura de `Camera.Param`/`Camera.ParamEx`, `Detect.*`, `AlarmInfo` | dump JSON sanitizado (sem serial/MAC/senha) em `docs/compatibility/raw/` (gitignored) + resumo |
| F0-10 📷 | Escuta de alarmes (passiva) | assinar alarmes DVRIP por 10 min enquanto o usuário caminha na frente da câmera | payloads de movimento/pessoa registrados (ou ausência) |
| F0-11 | Relatório de compatibilidade | `docs/compatibility/X6C-WEQ.md`: matriz `supported / unsupported / unknown` por funcionalidade da seção 6.2, firmware, evidência de cada item, riscos | todas as linhas da matriz preenchidas com evidência |
| F0-12 | ADRs iniciais | ADR-001 (monólito modular), ADR-002 (MediaMTX/topologia D1), ADR-003 (auth de mídia D3 + segredos D2), ADR-006 (gravação/retenção D4). ADR-004/005 ficam como "proposto" | arquivos em `docs/decisions/` |
| F0-13 | Lista de testes que exigem autorização 🔒 | `docs/compatibility/X6C-WEQ-testes-pendentes.md`: mover PTZ, mudar modo noturno, acender luz branca, alterar detecção, talk, cada um com procedimento de backup/restauração da configuração | revisado e aprovado pelo usuário |

**Ponto de decisão ao fim da Fase 0:**
- Se o stream principal for **H.265**: ao vivo usa o substream H.264 por WebRTC; a gravação continua no principal; a transcodificação fica como opção desligada (ADR).
- Se o DVRIP não expuser alarmes de pessoa: Fase 3 fica restrita a "movimento", com ADR para ONVIF Events/Frigate.

---

## 3. Fase 1 — MVP de mídia e gravação

Fatias verticais em ordem. Cada fatia termina com testes passando na CI Windows + Linux.

### 3.1 Estrutura do backend (só o que o MVP usa)

```
apps/backend/src/nvr/
├── shared/            # erros de aplicação (CameraOffline, AuthenticationFailed, OperationTimeout,
│                      #  ResourceBusy, UnsupportedFeature, NotFound), clock, ids, logging com máscara
├── cameras/
│   ├── domain.py      # Camera, StreamProfile, CameraStatus, derive_status()
│   ├── ports.py       # CameraRepository, SecretStore, StreamProbe
│   ├── application.py # RegisterCamera, UpdateCamera, DisableCamera, TestConnection, GetStatus
│   └── adapters/      # sqlalchemy_repo.py, keyring_secrets.py, file_secrets.py, rtsp_probe.py
├── streaming/
│   ├── ports.py       # MediaServerPort (ensure_path, remove_path, path_state), LiveStreamPort
│   ├── application.py # GetLiveDescriptor, ReconcileMediaPaths
│   └── adapters/mediamtx.py
├── recordings/
│   ├── domain.py      # Segment, RecordingInterval, merge_intervals(), StorageLocation,
│   │                  #  StoragePolicy (cota, ReplacementPolicy, limiares), select_for_deletion()
│   ├── ports.py       # SegmentIndex, SegmentStorage, DiskUsage, DirectoryBrowser
│   ├── application.py # SetRecordingEnabled, IndexSegments, ListIntervals, GetPlaybackUrl,
│   │                  #  EnforceStoragePolicy, StorageReport, BrowseStorageRoots,
│   │                  #  ValidateStorageLocation, ChangeStorageLocation, MigrateSegments
│   └── adapters/      # fs_storage.py, sqlalchemy_index.py, mediamtx_playback.py
├── identity/          # User, hash, sessões, tokens de mídia, rate limit de login
├── system/            # Health agregado
├── http/              # routers FastAPI por módulo, dependências de auth, /internal/mediamtx/auth
├── jobs/              # agendador asyncio: reconcile, index, storage policy, migration, status poll
└── bootstrap/         # composition root (único lugar que instancia adaptadores), settings, CLI
```
`import-linter`: `*.domain` não importa nada de fora de `shared`; `*.application` só importa `domain`/`ports`/`shared`; `http`, `jobs` e `adapters` não se importam entre si.

### 3.2 Tarefas

| ID | Fatia | Conteúdo | Testes / Pronto quando |
|---|---|---|---|
| F1-01 | Esqueleto e CI | `pyproject.toml` (uv), ruff, mypy, import-linter, pytest; `apps/web` Vite+TS; GitHub Actions no repositório privado com matriz `ubuntu-latest`/`windows-latest`, branch protection exigindo CI verde | CI verde com teste vazio nos dois SOs |
| F1-02 | Configuração e persistência | `Settings` (Pydantic Settings: data_dir, binds, `NVR_STORAGE_ROOTS`), SQLAlchemy + migração Alembic inicial (`users`, `cameras` com `recording_enabled`, `storage_locations`, `storage_policy`, `segments` com `location_id`, `audit_log`), caminhos com `pathlib` | migração sobe/desce em SQLite temporário |
| F1-03 | Identity | CLI `nvr create-admin`; login com argon2id; cookie de sessão `HttpOnly; Secure; SameSite=Strict`; verificação de `Origin` nas mutações; rate limit no login; logout | unit: hash/verify, lockout; API: rotas sem sessão → 401 |
| F1-04 | Cadastro de câmeras (RF-01) | entidade + validações (host, porta 1–65535, path RTSP, perfis main/sub); segredo salvo no `SecretStore` e referenciado por `secret_ref`; `GET/POST/PATCH /api/v1/cameras`, desabilitar | contrato: resposta **nunca** contém senha/URL completa; teste que procura a senha de teste em todas as respostas e logs |
| F1-05 | Teste de conexão (RF-02) | `POST /cameras/{id}/test`: sonda TCP → RTSP DESCRIBE (401?) → ffprobe opcional para codecs | integração contra a câmera fake: ok, senha errada → `auth_error`, porta fechada → `offline` |
| F1-06 | Adaptador MediaMTX + relay + reconciliador | relay FFmpeg por stream (ADR-002; validar supervisão via `runOnInit`/`runOnInitRestart`), `ensure_path` com `source`, `rtspTransport: tcp`, `record` conforme `recording_enabled`, `recordPath` do local ativo, `sourceOnDemand: no`; job de reconciliação; leitura de `/v3/paths/list` | integração: sobe MediaMTX de `tools/` num diretório temporário, cadastra câmera fake, path fica `ready`, gravação aparece no disco |
| F1-06b | Liga/desliga gravação | `PUT /api/v1/cameras/{id}/recording` `{enabled}` (admin); estado `gravando / pausada / parada por erro` no status e no SSE; registro no `audit_log` | integração: desligar → nenhum segmento novo e o ao vivo continua; religar → segmentos voltam; gap marcado como "pausa do usuário" (diferente de "falha") |
| F1-07 | Estado da câmera (RF-07) | `derive_status()` puro + job de polling; histórico de transições; SSE `GET /api/v1/events/stream` com mudanças de status | unit: tabela de sinais → estado; integração: matar a câmera fake → `offline` → religar → `online` e gravação retomada |
| F1-08 | Auth de mídia | `/internal/mediamtx/auth` (só loopback), tokens curtos, `GET /cameras/{id}/live` → descritor `{webrtc: url, hls: url, expiresAt}` | integração: WHEP sem token → 401; token de outra câmera → 401; token expirado → 401 |
| F1-09 | Índice de segmentos | `IndexSegments` (varredura incremental + tolerância a arquivo em escrita), `merge_intervals` com detecção de gaps | unit: intervalos/gaps; integração: N segmentos da câmera fake indexados corretamente |
| F1-10 | Consulta e playback (RF-05) | `GET /recordings?cameraId&start&end` → intervalos + gaps; `GET /recordings/playback-url` → URL `/media/playback/get?...` com token; download do intervalo como MP4 (`format=mp4` do playback) | contrato e integração: baixar 2 min da câmera fake e validar com ffprobe |
| F1-11 | Cota e substituição (RF-06) | `StoragePolicy` de D4 (cota, `overwrite_oldest`/`stop_when_full`, histerese 95%→90%, piso físico); job `EnforceStoragePolicy`; `GET/PUT /api/v1/storage/policy`; `GET /api/v1/storage` (uso por câmera, cota, livre real, bitrate medido, estimativa de dias) | unit: seleção dos mais antigos entre câmeras, histerese, nunca escolhe segmento aberto/recente, `stop_when_full` pausa sem apagar; integração com diretório temporário e `DiskUsage` fake: encher até a cota e verificar que apaga só o necessário, sempre os mais antigos |
| F1-11b | Local de gravação (D7) | `GET /api/v1/storage/browse?path=` restrito a `NVR_STORAGE_ROOTS`; `POST /api/v1/storage/locations/validate` (escrita real pelos dois serviços, espaço x cota, aviso de partição raiz); `PUT /api/v1/storage/location` com modo `migrate` ou `fresh`; job de migração com progresso via SSE e retomada após reinício | unit: tentativas de path traversal (`..`, symlink para fora, caminho absoluto fora das raízes) recusadas; integração: trocar local com a câmera fake gravando → nenhum segmento perdido, playback contínuo após a migração; local sem permissão → erro claro com o comando sugerido |
| F1-12 | Health (observabilidade) | `GET /system/health`: MediaMTX alcançável, DB, espaço, câmeras por estado, último segmento por câmera; logs estruturados JSON com máscara | API test; teste de máscara de log |
| F1-13 | Frontend — base | login, layout responsivo, cliente gerado do OpenAPI (`npm run gen:api`), guarda de rota, SSE de status | Vitest nos componentes |
| F1-14 | Frontend — câmeras | lista com badge de estado, formulário de cadastro/edição (campo de senha só de escrita), botão "testar conexão" com mensagens claras por tipo de erro | Vitest + Playwright com câmera fake |
| F1-15 | Frontend — ao vivo (RF-03) | player WHEP; fallback HLS automático; seletor main/sub; grade de câmeras; reconexão automática do player | Playwright: vídeo toca (frame avança) com câmera fake |
| F1-16 | Frontend — gravações | seletor de data, timeline de 24 h com intervalos e gaps, reprodução a partir de um ponto, download de trecho | Playwright: selecionar trecho e reproduzir |
| F1-17 | Frontend — armazenamento e sistema | tela **Armazenamento**: chave de gravação por câmera; navegador de pastas do servidor com validação ao vivo; campo de cota (GB) com barra de uso e estimativa "~N dias"; seletor de política (`Sobrescrever as mais antigas` como padrão / `Parar quando encher`); diálogo de troca de local (migrar ou começar do zero) com progresso; confirmação explícita ao reduzir a cota abaixo do uso atual (mostra quanto será apagado). Tela **Sistema**: health | Vitest + Playwright: alterar cota/política/local com a câmera fake |
| F1-18 | HTTPS e proxy | `infra/caddy/Caddyfile` com as rotas de D1 para `nvr-camera-monitor.local`, o IP fixo do servidor e (opcional) `.home.arpa`; CA local do Caddy (`tls internal`); Avahi publicando o nome; guia de como instalar a CA no Android/iOS/Linux/Windows **sem ignorar erros TLS** | celular abre `https://nvr-camera-monitor.local` (ou pelo IP) sem aviso após instalar a CA |
| F1-19 | Serviços de SO | **Ubuntu Server (principal):** units systemd (`nvr-api`, `nvr-mediamtx`, `nvr-caddy`) com usuário `nvr` dedicado e grupo compartilhado para escrita no local de gravação, hardening (`ProtectSystem=strict` + `ReadWritePaths` gerado a partir de `NVR_STORAGE_ROOTS`), `ufw` liberando só a sub-rede LAN (443/TCP, 8189/UDP, 5353/UDP mDNS), `avahi-daemon`, IP estático via netplan. **Windows (suportado):** WinSW + `New-NetFirewallRule` no perfil Private. **MX Linux:** apenas `scripts/dev.sh` | reinício do Ubuntu Server → tudo volta sozinho; idem no Windows |
| F1-20 | Documentação de operação | `docs/operations/instalacao-ubuntu-server.md` (completa: instalação do Ubuntu Server LTS, IP estático fora da faixa DHCP, pacotes, usuário, disco dedicado/montagem em `/etc/fstab`, permissões, Avahi, CA, serviços, atualização), `migracao-mx-para-ubuntu.md` (o que copiar: banco SQLite, segredos re-cadastrados, gravações), `instalacao-windows.md` (enxuta), `credenciais.md` (credencial real fora do Git), `armazenamento.md` (cota, política, troca de disco), `backup.md` (SQLite + gravações) | um terceiro segue o guia Ubuntu do zero numa VM limpa |
| F1-21 📷 | Aceite com a câmera real | rodar o checklist 12.1 da proposta com a X6C-WEQ | ver seção 3.3 |

Ordem/paralelismo: F1-01→02→03→04→05→06→06b→07/08/09 (paralelos)→10→11→11b→12. O frontend (F1-13…17) pode começar após F1-04 usando o OpenAPI. F1-18…20 depois de F1-10.

### 3.3 Aceite do MVP (rastreado aos critérios 12.1 da proposta)

| Critério da proposta | Como será verificado |
|---|---|
| Inicia em Linux e Windows | CI nos dois SOs + instalação no Ubuntu Server seguindo F1-20 + instalação de teste no Windows |
| UI em outro dispositivo da LAN | celular em `https://nvr-camera-monitor.local` ou pelo IP (F1-18) assistindo ao vivo |
| Gravação controlada pela UI (requisito do usuário) | ligar/desligar por câmera, trocar local com migração, alterar cota e política: tudo pela UI, sem editar arquivos no servidor |
| Gravação continua sem navegador | fechar todos os navegadores por 30 min; índice mostra intervalo contínuo |
| Opera sem internet | desconectar o WAN do roteador; ao vivo, gravação e playback seguem |
| Recuperação após queda da câmera | desligar a câmera por 2 min: status `offline` na UI, gap registrado, gravação retomada |
| Listar/reproduzir/baixar por intervalo | Playwright (fake) + manual (real) |
| Retenção e disco cheio | integração: com `overwrite_oldest`, a gravação roda por horas numa cota pequena (ex.: 1 GB) com uso estável abaixo da cota e só os segmentos mais antigos apagados; com `stop_when_full`, a gravação pausa e alerta |
| Sem acesso anônimo | suíte de API percorre todas as rotas e URLs de mídia sem sessão/token → 401/403 |
| Segredos fora de logs/Git/API | teste automatizado de vazamento + `gitleaks` na CI |
| Testes sem hardware | `pytest -m "not manual_camera"` verde na CI |

---

## 4. Fases seguintes (marcos)

Cada fase começa por um plano detalhado próprio, baseado no relatório de compatibilidade.

### Fase 2 — Controles da câmera (RF-09, RF-10, RF-11, RF-16)
- ADR-004: cliente DVRIP próprio inspirado no `asyncio_dvrip.py` do icsee-ptz, isolado em `controls/adapters/dvrip/` (avisos MIT preservados) com conexão persistente, keep-alive e reconexão.
- `CameraCapabilities` com estados `supported/unsupported/unknown` (derivados da F0-11 + sondagem em tempo de execução); `CameraAdapterFactory` registrando drivers `dvrip`, `onvif` e `none`.
- PTZ: `move` com **lease** — o backend para sozinho se não receber renovação em ~500 ms (watchdog); frontend envia `stop` em `pointerup/pointercancel/blur/visibilitychange` e renova enquanto o botão estiver pressionado.
- Modo noturno/IR/luz: GET → PUT → **read-back** obrigatório; snapshot da configuração anterior e botão "restaurar"; confirmação no UI para ações físicas.
- 🔒 Testes físicos de PTZ/luz/modo só após aprovação da F0-13.

### Fase 3 — Eventos e snapshots (RF-12, RF-13)
- `EventSourcePort` DVRIP (alarmes) → `CameraEvent` normalizado (`motion_detected` ≠ `person_detected`), UTC, origem, atraso medido, deduplicação/cooldown.
- `SnapshotPort` via FFmpeg lendo o stream **do MediaMTX local** (não abre nova conexão com a câmera); arquivo em `data/snapshots/`, metadados no SQLite, link para o instante na timeline.
- UI: lista filtrável, miniaturas, "ir para a gravação".
- Automação embarcada de luz: só leitura/edição de parâmetros da câmera; o servidor não replica a regra.

### Fase 4 — Push-to-talk (RF-14)
- PoC isolada: go2rtc com fonte `dvrip://` e backchannel; validar codec (G.711A), voz inteligível, sessão concorrente com o MediaMTX.
- ADR-005 decide: go2rtc só para áudio vs. hub principal (`câmera → go2rtc → RTSP local → MediaMTX`).
- Sessão única por câmera, timeout, encerramento em `pointerup`/fechamento da página; só por HTTPS.

### Fase 5 — Extensibilidade e robustez (RF-15)
- Adaptador ONVIF genérico + segunda câmera/fabricante; descoberta ONVIF (WS-Discovery) opcional.
- Testes de concorrência (múltiplos espectadores, 2+ câmeras), Docker Compose opcional, backup/atualização, perfis de usuário (admin/visualizador).

---

## 5. Riscos do plano e mitigação

| Risco | Mitigação no plano |
|---|---|
| H.265 no stream principal | ao vivo pelo substream H.264; decidido na saída da Fase 0 |
| Auth HTTP do MediaMTX não cobrir algum endpoint (ex.: playback) | teste de integração F1-08/F1-10 que tenta todos os endpoints sem token; se falhar, o Caddy faz `forward_auth` no backend |
| WebRTC atrás do Caddy/firewall do Windows (UDP) | porta UDP 8189 fixa + `webrtcAdditionalHosts` com IP/hostname LAN; HLS como fallback já previsto |
| `keyring` indisponível em Linux headless | fallback Fernet documentado (D2) |
| Varredura de diretório lenta com muitos segmentos | indexação incremental por mtime + `runOnRecordSegmentComplete` como gatilho |
| Firmware DVRIP diverge do icsee-ptz | Fase 0 com leitura apenas; recursos sem evidência ficam `unknown` e desabilitados na UI |
| Escolha de pasta pela UI virar escrita arbitrária no servidor | raízes permitidas fixadas na instalação, só para admin, normalização + `realpath` contra symlinks, testes de traversal (F1-11b) e `ReadWritePaths` do systemd limitando o processo |
| Cota maior que o espaço real ou disco compartilhado | piso físico de segurança (D4) além da cota; validação ao configurar |
| Migração de local interrompida (queda de energia) | job idempotente: copia, confere tamanho/hash, atualiza o índice e só então apaga a origem; retoma no boot |
| Exposição de dados reais em fixtures/logs | `data/`, `tools/`, `docs/compatibility/raw/` no `.gitignore`; gitleaks na CI; máscara de logs testada |

---

## 6. Próximos passos imediatos

1. Instalar Python 3.12+, `uv` e FFmpeg nesta máquina; preparar WSL2 com Ubuntu ou uma VM Ubuntu Server.
2. Executar F0-01 a F0-05 (não precisam da câmera) e fazer o primeiro push para `mateusdiasc137/camera-monitor` com a CI.
3. Combinar com o usuário uma sessão para F0-06 a F0-10 📷 (credencial local fornecida via variável de ambiente, nunca colada no chat ou em arquivo versionado).
4. Revisar a F0-11/F0-13 com o usuário e só então iniciar a Fase 1.
5. A migração MX → Ubuntu Server pode acontecer a qualquer momento antes da F1-19; nada do plano depende do MX.

### Perguntas ainda em aberto (não bloqueiam a Fase 0)
- Haverá disco dedicado às gravações no servidor? Afeta o guia F1-20 e o valor padrão de `NVR_STORAGE_ROOTS` (sugestão: montar em `/srv/nvr/recordings`).
- Se quiser, verificar depois no painel do roteador (normalmente em `192.168.0.1` ou `192.168.1.1`) se há opções "DHCP reservation"/"IP binding" e "DNS local/host". Se houver, ativamos a reserva da câmera e o nome `.home.arpa`; se não, o plano já funciona sem elas.
