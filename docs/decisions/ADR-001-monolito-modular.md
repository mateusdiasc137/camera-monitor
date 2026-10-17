# ADR-001 — Monólito modular com portas e adaptadores

**Estado:** aceito · **Data:** 17/10/2026

## Contexto

Uma instalação doméstica, com uma câmera inicialmente e um servidor Ubuntu. A proposta pede extensibilidade (novos fabricantes, protocolos, mecanismos de mídia), mas alerta contra microserviços e abstrações artificiais.

## Decisão

- Backend único em Python/FastAPI, organizado **por módulo de negócio** (`cameras`, `streaming`, `recordings`, `controls`, `events`, `talk`, `identity`, `system`). Cada módulo tem `domain`, `ports`, `application` e `adapters` só quando necessário.
- Regra de dependência: `http`/`jobs` → `application` → `domain`/`ports`; `adapters` implementam `ports`. Só o `bootstrap` (composition root) instancia adaptadores.
- Regras verificadas na CI com `import-linter`.
- Processos externos especializados (MediaMTX, relays FFmpeg, Caddy e, no futuro, go2rtc) **não** são "microserviços nossos": são infraestrutura acessada por adaptadores.

## Consequências

- Deploy e depuração simples: um processo da aplicação + processos de mídia.
- O domínio não conhece DVRIP, ONVIF, MediaMTX nem SQLAlchemy; trocar de servidor de mídia ou adicionar um fabricante não toca as regras de negócio.
- Risco: módulos acoplados via banco. Mitigação: cada módulo acessa só as próprias tabelas, via o próprio repositório.
