# ADR-003 — Autenticação do painel, autorização de mídia e segredos

**Estado:** aceito · **Data:** 17/10/2026 · **Revisão (F1-08):** item 2 trocado — sinalização intermediada

## Contexto

Nenhum vídeo, gravação ou evento pode ser acessível sem login. O MediaMTX expõe WebRTC, HLS e playback por HTTP próprios. As credenciais da X6C-WEQ vão no caminho da URL RTSP e já apareceram em lugares inesperados (URL de snapshot ONVIF, relatórios).

## Decisão

1. **Painel:** usuários locais com senha argon2id, sessão em cookie `HttpOnly; Secure; SameSite=Strict`, checagem de `Origin` nas mutações, rate limit no login.
2. **Mídia (revisto na F1-08):** o navegador **não fala com o MediaMTX por HTTP**. A negociação WebRTC (WHEP) vai para o backend (`POST /api/v1/cameras/{id}/whep`, PATCH/DELETE da sessão), que exige a sessão do painel e repassa ao MediaMTX com a credencial interna `reader`. Só a mídia (UDP/ICE, porta 8189) vai direto do MediaMTX ao navegador, cifrada (DTLS-SRTP) e só após a negociação.
   - MediaMTX com `authMethod: http` → `/internal/mediamtx/auth` (só loopback): `publish` só do `relay`, `read`/`playback` só do `reader`, ambos com segredo aleatório por inicialização; `api` só loopback.
   - Os servidores HTTP do MediaMTX (WebRTC, HLS, playback) escutam só em `127.0.0.1`.
   - **Por que mudou:** sem tokens em URL (nada copiável ou vazável), uma única regra de acesso (a sessão do painel), sem CORS, e o mesmo fluxo em desenvolvimento (proxy do Vite) e produção (Caddy). O custo é a sinalização passar pelo backend (alguns KB por sessão). O playback de gravações (F1-10) seguirá o mesmo modelo.
   - Validado com a X6C-WEQ: oferta pelo backend → 201 com H.265 + PCMA, candidatos ICE com o IP da LAN; acesso direto ao MediaMTX sem credencial → 401; API sem login → 401.
3. **Exposição:** só o Caddy (HTTPS) escuta na LAN, além da porta UDP do WebRTC. MediaMTX (HTTP), API de controle e backend escutam em `127.0.0.1`.
4. **Segredos de câmera:** `keyring` (Secret Service no Ubuntu; Credential Manager no Windows), com fallback para arquivo criptografado (Fernet) com a chave fora do diretório de dados. Nunca em SQLite, logs, respostas da API ou arquivos de configuração do MediaMTX. As câmeras entram no MediaMTX só pela API (memória).
5. **Mascaramento obrigatório** em todo log/relatório: `user:pass@`, `user=`/`password=` no caminho e o valor literal dos segredos conhecidos. Coberto por teste automatizado de vazamento.

## Consequências

- Na PoC, `authMethod: internal` com senha em hash SHA-256 bastou. A troca para `http` acontece na F1-08 e precisa de teste negativo em todos os endpoints de mídia.
- A senha da câmera ainda aparece em argumentos de processo do relay (ver ADR-002).
