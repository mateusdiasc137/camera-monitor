# Acesso por HTTPS e instalação do certificado da rede

O NVR é aberto em **`https://nvr-camera-monitor.local`** (ou **`https://<IP do servidor>`**). O certificado é emitido por uma **CA própria do servidor** (o Caddy), que não é conhecida pelos aparelhos. Cada celular ou computador precisa **instalar essa CA uma vez**; depois disso, o navegador abre o NVR com o cadeado normal, sem avisos.

> **Nunca clique em "Avançar mesmo assim" / "Continuar para o site (não seguro)".** Se aparecer aviso de certificado, a CA não está instalada naquele aparelho (ou não é a do seu servidor). Instale a CA conferindo a impressão digital, como abaixo.

## 1. Pegue a impressão digital (SHA-256) no servidor

Ela identifica a sua CA. Anote os primeiros e os últimos grupos para comparar no aparelho.

- **Servidor Ubuntu:**
  ```bash
  sudo openssl x509 -noout -fingerprint -sha256 \
    -in /var/lib/nvr-caddy/caddy/pki/authorities/local/root.crt
  ```
- **Desenvolvimento (este PC):** `python scripts/dev_caddy.py` mostra a linha `SHA-256 da CA`.

## 2. Baixe a CA no aparelho

No navegador do aparelho, abra **`http://<IP do servidor>/nvr-ca.crt`** (em desenvolvimento: `http://<IP do PC>:8080/nvr-ca.crt`). É o único arquivo que o servidor entrega sem HTTPS; ele é público (não contém chave privada).

## 3. Instale e confie

### Android (11 ou mais novo)
1. Configurações → **Segurança e privacidade** → **Mais configurações de segurança** → **Criptografia e credenciais** → **Instalar um certificado** → **Certificado de CA** → **Instalar mesmo assim**.
2. Escolha o `nvr-ca.crt` baixado.
3. Para conferir: **Credenciais confiáveis** → aba **Usuário** → *NVR Camera Monitor - CA local* → compare o SHA-256.
4. Abra `https://<IP do servidor>` no Chrome. Se `nvr-camera-monitor.local` não abrir no Android, use o IP: nem todo Android resolve nomes `.local`.

### iPhone / iPad
1. Abra o link da CA no **Safari** → **Permitir** (perfil de configuração).
2. Ajustes → **Geral** → **VPN e Gerenciamento de Dispositivo** → *NVR Camera Monitor - CA local* → **Instalar**. Confira o SHA-256 em "Mais detalhes".
3. Ajustes → **Geral** → **Sobre** → **Ajustes de Confiança de Certificados** → ative **confiança total** para a CA. Sem este passo o Safari continua avisando.

### Windows
1. Confira a impressão digital (PowerShell):
   ```powershell
   (New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 "$HOME\Downloads\nvr-ca.crt").GetCertHashString([System.Security.Cryptography.HashAlgorithmName]::SHA256)
   ```
2. Instale (PowerShell **como administrador**):
   ```powershell
   Import-Certificate -FilePath "$HOME\Downloads\nvr-ca.crt" -CertStoreLocation Cert:\LocalMachine\Root
   ```
   Ou: duas vezes no arquivo → **Instalar Certificado** → **Máquina Local** → **Autoridades de Certificação Raiz Confiáveis**.
3. Chrome e Edge usam o repositório do Windows. No Firefox: Configurações → Privacidade e Segurança → Certificados → Ver certificados → Autoridades → Importar.

### Linux (Ubuntu, MX Linux)
```bash
openssl x509 -noout -fingerprint -sha256 -in nvr-ca.crt          # conferir
sudo cp nvr-ca.crt /usr/local/share/ca-certificates/nvr-ca.crt && sudo update-ca-certificates
# Chrome/Chromium usam a base NSS do usuário:
sudo apt install libnss3-tools
certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n "NVR Camera Monitor CA" -i nvr-ca.crt
```
No Firefox, importe pelas configurações de certificados (como no Windows).

## Nome `nvr-camera-monitor.local` no servidor

O nome é publicado por mDNS (Avahi) a partir do nome da máquina:

```bash
sudo hostnamectl set-hostname nvr-camera-monitor
sudo apt install avahi-daemon        # já vem na maioria das instalações
```

O servidor deve ter **IP fixo** (fora da faixa do DHCP do roteador). Esse IP também vai no certificado (`NVR_SITE_ADDRESSES`, ex.: `nvr-camera-monitor.local, 10.0.0.50`). Se o roteador permitir DNS local, dá para acrescentar `nvr-camera-monitor.home.arpa`.

## Testar agora, no PC de desenvolvimento (Windows)

1. Backend rodando (`uv run nvr serve`) e interface compilada (`npm.cmd run build` em `apps/web`).
2. `python scripts/dev_caddy.py`: mostra os endereços e o SHA-256 da CA.
3. Para o celular alcançar o PC, libere as portas no firewall (PowerShell **como administrador**, rede Privada):
   ```powershell
   New-NetFirewallRule -DisplayName "NVR dev HTTPS" -Direction Inbound -Protocol TCP -LocalPort 8443,8080 -Profile Private -Action Allow
   ```
4. No celular: baixe `http://<IP do PC>:8080/nvr-ca.crt`, instale como acima e abra `https://<IP do PC>:8443`.

## Riscos e cuidados

- Um aparelho que confia na CA aceitaria certificados de **qualquer** site emitidos com a chave dela. A chave fica só no servidor: proteja o servidor (atualizações, senha forte, sem acesso remoto aberto).
- Faça backup da pasta `pki` do Caddy (`/var/lib/nvr-caddy/caddy/pki`). Se ela se perder, uma nova CA é criada e todos os aparelhos precisam reinstalar.
- Ao desativar o NVR, **remova a CA** dos aparelhos (mesmos menus da instalação).
