# Instalação no servidor Linux (Ubuntu Server)

O instalador `infra/linux/install.sh` deixa o NVR rodando como serviços do systemd, que sobem sozinhos com a máquina. Ele é testado a cada mudança no CI, numa máquina Ubuntu 24.04 de verdade: instala, grava uma câmera de teste, reinicia o servidor de vídeo, atualiza e desinstala.

## O que fica instalado

| Serviço | Usuário | Rede | Função |
|---|---|---|---|
| `nvr-caddy` | `nvr-caddy` | **80 e 443/TCP** (LAN) | HTTPS com a CA local; entrega a interface e repassa `/api` ao backend |
| `nvr-api` | `nvr` | 127.0.0.1:8000 | API, relays FFmpeg das câmeras, índice, cota |
| `nvr-mediamtx` | `nvr-media` | **8189/UDP** (vídeo ao vivo); o resto só em 127.0.0.1 | Servidor de vídeo e gravação |

| Caminho | Conteúdo |
|---|---|
| `/opt/nvr` | programas (backend, interface, MediaMTX, Caddy, uv, Python 3.12 próprio), só leitura para os serviços |
| `/etc/nvr` | configuração: `nvr.env`, `caddy.env`, `mediamtx.yml`, `Caddyfile`, `secrets.key` (chave do cofre, só do usuário `nvr`) |
| `/var/lib/nvr` | banco, cofre de credenciais das câmeras (cifrado) |
| `/var/lib/nvr-caddy` | CA local do HTTPS (faça backup) |
| `/srv/nvr` | gravações (ou as raízes passadas em `--storage-root`) |

Cada serviço roda isolado pelo systemd: sistema de arquivos somente leitura (só as próprias pastas são graváveis), sem privilégios, sem acesso às pastas pessoais e com chamadas de sistema restritas. O MediaMTX grava com o grupo `nvr`, para o backend poder aplicar a cota e mover as gravações; o MediaMTX não lê o banco nem o cofre.

## Antes de instalar

1. **IP fixo**, fora da faixa que o roteador distribui (DHCP). Exemplo para o Ubuntu (`/etc/netplan/01-nvr.yaml`; troque a interface, os endereços e o roteador):
   ```yaml
   network:
     version: 2
     ethernets:
       enp1s0:
         dhcp4: false
         addresses: [10.0.0.50/24]
         routes: [{to: default, via: 10.0.0.1}]
         nameservers: {addresses: [10.0.0.1, 1.1.1.1]}
   ```
   Aplique com `sudo netplan try`: ele desfaz sozinho em 2 min se você perder o acesso.
2. **Disco das gravações** (recomendado, separado do sistema): monte em `/srv/nvr` pelo UUID no `/etc/fstab`, com `nofail`:
   ```
   UUID=<uuid do disco>  /srv/nvr  ext4  defaults,nofail  0  2
   ```
3. **Interface compilada**: o servidor não precisa de Node.js se você compilar em outra máquina (`npm ci && npm run build` em `apps/web`) e copiar a pasta `apps/web/dist` para o mesmo caminho no clone do servidor. Com Node.js no servidor, o instalador compila sozinho.

## Instalar

```bash
git clone https://github.com/mateusdiasc137/camera-monitor.git
cd camera-monitor
sudo infra/linux/install.sh --hostname nvr-camera-monitor --storage-root /srv/nvr --firewall auto
sudo nvr-admin create-user admin          # primeiro usuário (pede a senha)
```

- `--hostname nvr-camera-monitor` faz o servidor responder em `nvr-camera-monitor.local` (Avahi).
- `--firewall auto` ativa o ufw liberando só a sua rede local: SSH, 80/443, 8189/UDP e mDNS. Se você estiver conectado por SSH de fora dessa rede, o instalador para em vez de cortar seu acesso.
- `--storage-root` pode ser repetido (ex.: um segundo disco em `/mnt/hd2`). A aba **Armazenamento** só deixa escolher pastas dentro dessas raízes.
- `--site-address nvr-camera-monitor.home.arpa` acrescenta nomes ao certificado, se o roteador tiver DNS local.

No final, o instalador mostra o endereço, o link da CA e a impressão digital SHA-256 dela. Siga [https-e-certificado.md](https-e-certificado.md) em cada aparelho.

## Conferir

```bash
sudo /opt/nvr/app/.venv/bin/python infra/linux/verify_install.py
```

O verificador confere os serviços, os usuários, as portas, o HTTPS e as permissões. Ele não altera nada. Com `--user admin --password-env VARIAVEL`, testa também o login e a saúde do sistema. **Não use `--camera`** no servidor de verdade: essa opção cadastra uma câmera de teste.

## Atualizar

```bash
cd camera-monitor && git pull
(cd apps/web && npm ci && npm run build)   # ou copie o dist compilado em outra máquina
sudo infra/linux/install.sh --yes
```

Configuração, banco, cofre, CA e gravações são mantidos. Se a instalação do backend falhar, a versão anterior volta.

## Dia a dia

- Estado: `systemctl status nvr-api nvr-mediamtx nvr-caddy` ou a aba **Sistema**.
- Logs (JSON): `journalctl -u nvr-api -u nvr-mediamtx -u nvr-caddy -f`.
- Reiniciar: `sudo systemctl restart nvr-api` (os relays das câmeras reiniciam junto).
- CLI: `sudo nvr-admin --help` (ex.: criar outro usuário).

## Backup

Guarde: `/etc/nvr` (inclui a chave do cofre), `/var/lib/nvr` (banco e cofre) e `/var/lib/nvr-caddy/caddy/pki` (CA). Sem a CA, todos os aparelhos precisam instalar a nova. As gravações são opcionais no backup.

## Segurança: argumentos dos relays

O FFmpeg recebe a URL da câmera, com a senha (padrão Xiongmai), na linha de comando. Outros usuários **locais** do servidor poderiam vê-la com `ps`. Num servidor dedicado ao NVR, sem outros usuários, o risco é baixo. Para esconder os processos entre usuários, ative `hidepid` no `/proc`:

```
# /etc/fstab
proc  /proc  proc  defaults,hidepid=invisible  0  0
```

Teste antes num servidor dedicado: algumas ferramentas administrativas (polkit, por exemplo) precisam de exceção (`gid=`).

## MX Linux (temporário)

O MX usa sysVinit por padrão. O instalador precisa do systemd: no menu de inicialização (GRUB), escolha **Opções avançadas → MX Linux (systemd)**. O Python 3.12 é baixado pelo próprio instalador (o MX/Debian 12 vem com 3.11). Sem systemd, rode o NVR manualmente como no desenvolvimento (README).

## Remover

```bash
sudo infra/linux/uninstall.sh            # remove serviços e programas; mantém configuração, dados e CA
sudo infra/linux/uninstall.sh --purge    # apaga também configuração, banco, cofre, CA e usuários
```

As gravações **nunca** são apagadas pelo desinstalador.
