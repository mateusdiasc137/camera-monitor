# ADR-008 — Serviços no Linux: usuários separados, sandbox do systemd e instalador testado

**Estado:** aceito · **Data:** 18/10/2026 · **Relacionados:** D1 do plano, ADR-002, ADR-003, ADR-007

## Contexto

O NVR roda num servidor Ubuntu Server LTS que ainda não existe (hoje a máquina usa MX Linux, com sysVinit). O instalador precisa funcionar na primeira vez, sem tentativa e erro no servidor do usuário, e o servidor fica exposto na LAN.

## Decisão

- **Três serviços systemd e três usuários:** `nvr-api` (usuário `nvr`: banco, cofre, relays FFmpeg), `nvr-mediamtx` (`nvr-media`: vídeo e gravação, sem acesso ao banco nem ao cofre) e `nvr-caddy` (`nvr-caddy`: HTTPS e a CA local). Comprometer o serviço exposto à rede (UDP 8189) não entrega as senhas das câmeras.
- **Gravações compartilhadas por grupo:** raízes de armazenamento `nvr:nvr 2770` (setgid) e `UMask=0007`, para o backend apagar (cota) e mover (migração) o que o MediaMTX grava. O MediaMTX cria pastas com modo fixo `0755` (a umask não acrescenta permissão), por isso **o backend cria a pasta de cada câmera** antes de ligar a gravação (`ReconcileMedia(prepare_recording=…)`); o MediaMTX só grava dentro dela.
- **Sandbox do systemd** em todos: `ProtectSystem=strict` (só o estado próprio e as raízes de gravação são graváveis, `ReadWritePaths` gerado de `NVR_STORAGE_ROOTS`), sem capacidades (exceto `CAP_NET_BIND_SERVICE` no Caddy), `ProtectHome`, `PrivateTmp/Devices`, `MemoryDenyWriteExecute`, `SystemCallFilter=@system-service`, famílias de rede restritas. Exposição medida pelo `systemd-analyze security`: 1,5–1,7.
- **Binários e Python fixados:** MediaMTX, Caddy e uv com hash conferido (`tools/manifest.json`); o uv instala um Python 3.12 próprio em `/opt/nvr/python` e as dependências do `uv.lock`, sem depender do Python da distribuição (funciona também no Debian 12/MX iniciado com systemd).
- **Configuração em `/etc/nvr`**, preservada nas atualizações; chave do cofre em arquivo `0600` do usuário `nvr`, criada pelo instalador (o serviço não escreve em `/etc`).
- **Firewall opcional (`--firewall`):** ufw liberando só a LAN (SSH, 80/443, 8189/UDP, mDNS); o instalador recusa se a sessão SSH atual vier de fora da rede informada.
- **Instalador testado a cada mudança** numa VM Ubuntu 24.04 do CI: instala, grava uma câmera falsa, confere usuários, portas, HTTPS, permissões entre os serviços e o recorte de lente pelo FFmpeg dentro do sandbox, reinicia o MediaMTX, reinstala (atualização) e desinstala.

## Consequências

- O primeiro teste real pegou o problema da pasta `0755`, invisível no Windows e no desenvolvimento (um usuário só).
- A senha da câmera continua nos argumentos do FFmpeg (ADR-002): outros usuários locais a veriam com `ps`. O guia recomenda servidor dedicado e, opcionalmente, `hidepid` no `/proc`.
- No MX Linux, o instalador só funciona iniciando com systemd; com sysVinit, rodar manualmente.
- Serviço no Windows (WinSW) fica para a F1-19b.
