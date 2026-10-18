# ADR-007 — HTTPS na rede local com CA própria (Caddy)

**Estado:** aceito · **Data:** 18/10/2026 · **Relacionados:** D1 do plano, ADR-003

## Contexto

O painel precisa de HTTPS: o cookie de sessão é `Secure`, o navegador só libera alguns recursos (microfone para o áudio bidirecional da Fase 4, por exemplo) em contexto seguro, e a senha do painel não pode trafegar aberta na LAN. Não há domínio público nem porta aberta para a internet, então não dá para usar Let's Encrypt. O usuário não sabe se o roteador permite DNS local.

## Decisão

- **Caddy é o único ponto exposto na LAN** (`infra/caddy/Caddyfile`): `/api/*` → backend em `127.0.0.1:8000`; o resto → interface compilada (SPA, `try_files` para o `index.html`). Desde a revisão do ADR-003 (F1-08), o navegador não fala com o MediaMTX por HTTP; só a mídia WebRTC vai direto (UDP 8189). `/internal/*` responde 404 no proxy.
- **CA local do Caddy (`tls internal`)** emite certificados para `nvr-camera-monitor.local`, o IP fixo do servidor e, opcionalmente, um nome `.home.arpa` (`NVR_SITE_ADDRESSES`). A raiz vale 10 anos; a intermediária e os certificados se renovam sozinhos.
- **Confiança por escolha de cada aparelho:** `skip_install_trust` (o Caddy nunca instala a raiz no sistema por conta própria). A raiz é oferecida em `http://<servidor>/nvr-ca.crt` (só esse arquivo; o resto do HTTP redireciona para HTTPS) e o guia manda **conferir a impressão digital SHA-256** antes de instalar. Nada de "Avançar mesmo assim" em erro de certificado.
- **Cabeçalhos:** HSTS (1 ano), CSP restritiva (`script-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`...), `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy` (microfone só para a própria origem). `Server` removido. Arquivos com hash em `/assets/` ficam em cache por 1 ano; o resto, `no-cache`.
- **Fluxo contínuo:** `flush_interval -1` no proxy da API (SSE e vídeo não ficam retidos); limite de 2 MB no corpo das requisições da API.
- **Nome na LAN:** no Ubuntu Server, Avahi publica o próprio nome da máquina (`hostnamectl set-hostname nvr-camera-monitor` → `nvr-camera-monitor.local`). O IP fixo é a reserva (Android nem sempre resolve `.local`).
- **Versão fixada** (`tools/manifest.json`: Caddy v2.11.7, SHA-512 do projeto), a mesma no desenvolvimento, nos testes e no servidor (serviço `nvr-caddy`, F1-19).

## Validação

- Teste automatizado (`tests/integration/test_caddy_proxy.py`, Windows e Ubuntu no CI) com o Caddyfile de produção na frente do backend real: certificado válido só com a CA local (nome e IP), interface com cabeçalhos, sessão `Secure` pela API, origem estranha recusada, SSE chegando na hora, `/internal` bloqueado, HTTP servindo só a CA e redirecionando o resto.
- Ensaio com a X6C-WEQ no Chrome real, confiando na CA só durante o teste: pelo nome `.local` e pelo IP da LAN, ao vivo (WebRTC) decodificando, gravação tocando e download por lente, sem nenhuma violação de CSP; portas HTTP do MediaMTX fechadas para a LAN.

## Consequências

- **Risco da CA própria:** um aparelho que confia nela aceitaria certificados de qualquer site emitidos com a chave da raiz. A chave fica só no servidor (`/var/lib/nvr-caddy/caddy/pki`, legível só pelo usuário `nvr-caddy`); proteger o servidor protege os aparelhos. Ao desativar o NVR, remova a CA dos aparelhos.
- **Backup:** perder a pasta `pki` do Caddy obriga a reinstalar a CA em todos os aparelhos (entra no guia de backup da F1-20).
- O primeiro acesso de cada aparelho exige instalar a CA uma vez (guia em `docs/operations/https-e-certificado.md`).
- Portas 80/443 exigem `CAP_NET_BIND_SERVICE` para o Caddy no systemd (F1-19); em desenvolvimento, `scripts/dev_caddy.py` usa 8443/8080.
