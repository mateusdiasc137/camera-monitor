# X6C-WEQ — testes que exigem autorização 🔒 (F0-13)

Cada teste abaixo **altera o estado** da câmera ou causa ação física. Nenhum será executado sem autorização explícita **daquele teste**. Procedimento comum:

1. Ler e salvar a configuração atual (`diag_dvrip.py info`) em `docs/compatibility/raw/`.
2. Executar a mudança mínima, uma de cada vez.
3. Fazer read-back e observar o efeito (imagem/luz/movimento).
4. Restaurar o valor original e confirmar por read-back.

| # | Teste | Fase | Efeito visível | Risco | Restauração |
|---|---|---|---|---|---|
| T1 | PTZ: mover 1 s em cada direção e parar | 2 | lente "ball" gira | baixo; pode interromper o rastreamento por instantes | voltar ao preset 100 (existente) |
| T2 | PTZ: salvar preset de teste e voltar a ele | 2 | lente gira | cria preset novo | apagar o preset de teste |
| T3 | Modo dia/noite forçado (cor ↔ P&B) | 2 | imagem muda de cor | baixo | restaurar `DayNightColor` original (`0x3`) |
| T4 | Luz branca: acender manualmente por 10 s | 2 | luz acende | pode incomodar vizinhos à noite | restaurar `WorkMode: Intelligent` |
| T5 | Ajustar duração/agenda da luz automática | 2 | nenhum imediato | altera automação embarcada | restaurar `WorkPeriod` 18:00–06:00 e `Duration` 60 |
| T6 | Ligar/desligar rastreamento automático | 2 | nenhum imediato | altera automação | restaurar `DetectTrack.Enable = 1` |
| T7 | Talk: enviar 3 s de tom pelo alto-falante | 4 | som na câmera | baixo | nenhuma |
| T8 | Exigir senha no ONVIF (`OnvifPwdCheckout`) | segurança | nenhum | apps que usam ONVIF sem senha param de funcionar | desligar de novo |
| T9 | Desligar atualização automática de firmware | segurança | nenhum | deixa de receber correções automaticamente | religar |
| T10 | Desligar P2P/push da nuvem (`Nat`, `PMS`) | segurança | nenhum | **o app iCSee fora de casa para de funcionar** | religar |

T8–T10 são decisões de segurança e privacidade do usuário, não requisitos do NVR.
