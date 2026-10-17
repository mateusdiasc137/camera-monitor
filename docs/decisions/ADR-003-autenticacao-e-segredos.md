# ADR-003 — Autenticação do painel, autorização de mídia e segredos

**Estado:** aceito · **Data:** 17/10/2026

## Contexto

Nenhum vídeo, gravação ou evento pode ser acessível sem login. O MediaMTX expõe WebRTC, HLS e playback por HTTP próprios. As credenciais da X6C-WEQ vão no caminho da URL RTSP e já apareceram em lugares inesperados (URL de snapshot ONVIF, relatórios).

## Decisão

1. **Painel:** usuários locais com senha argon2id, sessão em cookie `HttpOnly; Secure; SameSite=Strict`, checagem de `Origin` nas mutações, rate limit no login.
2. **Mídia:** MediaMTX com `authMethod: http` apontando para `127.0.0.1:<api>/internal/mediamtx/auth`. O backend autoriza `read`/`playback` por sessão ou por **token curto assinado** (escopo câmera + ação, ~2 min para iniciar), emitido por `GET /api/v1/cameras/{id}/live`. `publish` só de loopback com a credencial do relay; `api` só de loopback.
3. **Exposição:** só o Caddy (HTTPS) escuta na LAN, além da porta UDP do WebRTC. MediaMTX (HTTP), API de controle e backend escutam em `127.0.0.1`.
4. **Segredos de câmera:** `keyring` (Secret Service no Ubuntu; Credential Manager no Windows), com fallback para arquivo criptografado (Fernet) com a chave fora do diretório de dados. Nunca em SQLite, logs, respostas da API ou arquivos de configuração do MediaMTX. As câmeras entram no MediaMTX só pela API (memória).
5. **Mascaramento obrigatório** em todo log/relatório: `user:pass@`, `user=`/`password=` no caminho e o valor literal dos segredos conhecidos. Coberto por teste automatizado de vazamento.

## Consequências

- Na PoC, `authMethod: internal` com senha em hash SHA-256 bastou. A troca para `http` acontece na F1-08 e precisa de teste negativo em todos os endpoints de mídia.
- A senha da câmera ainda aparece em argumentos de processo do relay (ver ADR-002).
