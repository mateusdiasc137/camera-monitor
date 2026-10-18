#!/usr/bin/env bash
# Instala ou atualiza o NVR Camera Monitor num servidor Linux com systemd e apt.
# Alvo: Ubuntu Server LTS (24.04 ou mais novo). Serve também ao Debian 12 / MX Linux iniciado com systemd.
#
# Uso, num clone do repositório (com a interface compilada em apps/web/dist ou Node.js instalado):
#   sudo infra/linux/install.sh [--storage-root DIR]... [--site-address NOME_OU_IP]...
#                               [--hostname NOME] [--firewall CIDR|auto] [--yes]
#
#   --storage-root DIR    raiz em que a UI pode gravar (repetível; padrão /srv/nvr). Substitui a lista atual.
#   --site-address X      nome ou IP extra no certificado HTTPS (repetível), ex.: nvr-camera-monitor.home.arpa
#   --hostname NOME       renomeia a máquina; o Avahi publica NOME.local (ex.: nvr-camera-monitor)
#   --firewall CIDR|auto  ativa o ufw liberando só a LAN: SSH, 80/443, 8189/udp (vídeo), 5353/udp (mDNS)
#   --yes                 não pede confirmação
#
# Rodar de novo atualiza o código e mantém configuração, banco, cofre, CA e gravações.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PREFIX=/opt/nvr
ETC=/etc/nvr
STATE=/var/lib/nvr
CADDY_STATE=/var/lib/nvr-caddy
CA_DIR="$CADDY_STATE/caddy/pki/authorities/local"
DEFAULT_ROOT=/srv/nvr
PYTHON_VERSION=3.12
UNITS=(nvr-mediamtx nvr-api nvr-caddy)

STORAGE_ROOTS=()
SITE_ADDRESSES=()
NEW_HOSTNAME=""
FIREWALL=""
ASSUME_YES=0

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die() {
  printf '\033[1;31mERRO:\033[0m %s\n' "$*" >&2
  exit 1
}
usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --storage-root) STORAGE_ROOTS+=("${2:?falta o diretório}"); shift 2 ;;
    --site-address) SITE_ADDRESSES+=("${2:?falta o nome ou IP}"); shift 2 ;;
    --hostname) NEW_HOSTNAME="${2:?falta o nome}"; shift 2 ;;
    --firewall) FIREWALL="${2:?falta a rede (ex.: 192.168.0.0/24 ou auto)}"; shift 2 ;;
    --yes | -y) ASSUME_YES=1; shift ;;
    -h | --help) usage 0 ;;
    *) warn "opção desconhecida: $1"; usage 2 ;;
  esac
done

# ------------------------------------------------------------------ verificações

[[ $EUID -eq 0 ]] || die "rode como root: sudo $0"
[[ -d /run/systemd/system ]] || die "este sistema não iniciou com systemd.
  No MX Linux: no menu de inicialização (GRUB), 'Opções avançadas' → 'MX Linux (systemd)'.
  Sem systemd, rode o NVR manualmente (README, seção Desenvolvimento)."
command -v apt-get >/dev/null || die "este instalador usa apt (Ubuntu/Debian)"
case "$(uname -m)" in x86_64 | aarch64) ;; *) die "arquitetura não suportada: $(uname -m)" ;; esac
[[ -f "$REPO/apps/backend/uv.lock" ]] || die "rode a partir de um clone do repositório ($REPO)"

for root in "${STORAGE_ROOTS[@]}"; do
  [[ "$root" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "raiz inválida: '$root' (caminho absoluto, sem espaços nem acentos)"
done
if [[ -n "$NEW_HOSTNAME" && ! "$NEW_HOSTNAME" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
  die "nome de máquina inválido: $NEW_HOSTNAME (letras minúsculas, números e hífen)"
fi

BUILD_USER="${SUDO_USER:-root}"
as_builder() { # baixar e compilar como o dono do clone, não como root
  if [[ "$BUILD_USER" == root ]]; then "$@"; else runuser -u "$BUILD_USER" -- "$@"; fi
}

lan_ip() { ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") {print $(i + 1); exit}}'; }

if [[ $ASSUME_YES -ne 1 ]]; then
  echo "Instalar/atualizar o NVR em $PREFIX (serviços: ${UNITS[*]})?"
  read -r -p "Continuar? [s/N] " answer
  [[ "$answer" =~ ^[sSyY]$ ]] || exit 0
fi

# ------------------------------------------------------------------ sistema

log "Pacotes do sistema: FFmpeg, Avahi (nome .local), utilitários"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends ffmpeg avahi-daemon ca-certificates curl iproute2 openssl python3

if [[ -n "$NEW_HOSTNAME" && "$(hostname)" != "$NEW_HOSTNAME" ]]; then
  log "Nome da máquina: $NEW_HOSTNAME (publicado como $NEW_HOSTNAME.local)"
  hostnamectl set-hostname "$NEW_HOSTNAME"
  if grep -qE '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
    sed -i -E "s/^(127\.0\.1\.1[[:space:]]+).*/\1$NEW_HOSTNAME/" /etc/hosts
  else
    echo "127.0.1.1 $NEW_HOSTNAME" >>/etc/hosts
  fi
fi
systemctl enable --now avahi-daemon >/dev/null 2>&1 || warn "Avahi não iniciou: use o IP em vez do nome .local"
HOST="$(hostname)"

log "Usuários dos serviços: nvr (backend), nvr-media (MediaMTX), nvr-caddy (HTTPS)"
getent group nvr >/dev/null || groupadd --system nvr
id nvr >/dev/null 2>&1 || useradd --system --gid nvr --home-dir "$STATE" --no-create-home --shell /usr/sbin/nologin nvr
id nvr-media >/dev/null 2>&1 || useradd --system --gid nvr --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin nvr-media
id nvr-caddy >/dev/null 2>&1 || useradd --system --user-group --home-dir "$CADDY_STATE" --no-create-home --shell /usr/sbin/nologin nvr-caddy

# ------------------------------------------------------------------ binários e código

WEB_DIST="$REPO/apps/web/dist"
if [[ ! -f "$WEB_DIST/index.html" ]]; then
  command -v npm >/dev/null || die "interface não compilada e npm ausente: rode 'npm ci && npm run build' em apps/web"
  log "Compilando a interface"
  (cd "$REPO/apps/web" && as_builder npm ci --no-audit --no-fund && as_builder npm run build)
fi

log "Ferramentas fixadas, com hash conferido: MediaMTX, Caddy, uv"
as_builder python3 "$REPO/scripts/fetch_tools.py"
manifest() { python3 - "$REPO/tools/manifest.json" "$@" <<'PY'
import json, platform, sys
spec = json.load(open(sys.argv[1]))[sys.argv[2]]
arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
asset = spec["assets"][f"linux-{arch}"]
print(spec["version"] if sys.argv[3] == "version" else asset.get("binary", sys.argv[2]))
PY
}
install -d -m 0755 "$PREFIX" "$PREFIX/tools"
for tool in mediamtx caddy uv; do
  version="$(manifest "$tool" version)"
  install -d -m 0755 "$PREFIX/tools/$tool"
  rm -rf "$PREFIX/tools/$tool/$version.new"
  cp -a "$REPO/tools/$tool/$version" "$PREFIX/tools/$tool/$version.new"
  chown -R root:root "$PREFIX/tools/$tool/$version.new"
  chmod -R a+rX,go-w "$PREFIX/tools/$tool/$version.new"
  rm -rf "$PREFIX/tools/$tool/$version"
  mv "$PREFIX/tools/$tool/$version.new" "$PREFIX/tools/$tool/$version"
  ln -sfn "$version" "$PREFIX/tools/$tool/current"
done
UV="$PREFIX/tools/uv/current/$(manifest uv binary)"

log "Backend: Python $PYTHON_VERSION próprio (uv) e dependências travadas no uv.lock"
api_was_active=0
systemctl is-active --quiet nvr-api && api_was_active=1
rm -rf "$PREFIX/app.new" "$PREFIX/app.old"
install -d -m 0755 "$PREFIX/app.new"
cp -a "$REPO/apps/backend/pyproject.toml" "$REPO/apps/backend/uv.lock" "$REPO/apps/backend/src" "$PREFIX/app.new/"
systemctl stop nvr-api 2>/dev/null || true
[[ -d "$PREFIX/app" ]] && mv "$PREFIX/app" "$PREFIX/app.old"
mv "$PREFIX/app.new" "$PREFIX/app"
if ! env UV_PYTHON_INSTALL_DIR="$PREFIX/python" UV_PYTHON_PREFERENCE=only-managed UV_CACHE_DIR=/var/cache/nvr-uv \
  UV_PROJECT_ENVIRONMENT="$PREFIX/app/.venv" UV_NO_CONFIG=1 \
  "$UV" sync --project "$PREFIX/app" --frozen --no-dev --no-editable --compile-bytecode --python "$PYTHON_VERSION"; then
  if [[ -d "$PREFIX/app.old" ]]; then
    rm -rf "$PREFIX/app"
    mv "$PREFIX/app.old" "$PREFIX/app"
    [[ $api_was_active -eq 1 ]] && systemctl start nvr-api
  fi
  die "falha ao instalar o backend; a versão anterior foi mantida"
fi
rm -rf "$PREFIX/app.old"
chown -R root:root "$PREFIX/app" "$PREFIX/python"
chmod -R a+rX,go-w "$PREFIX/app" "$PREFIX/python"

log "Interface"
rm -rf "$PREFIX/web.new"
cp -a "$WEB_DIST" "$PREFIX/web.new"
chown -R root:root "$PREFIX/web.new"
chmod -R a+rX,go-w "$PREFIX/web.new"
rm -rf "$PREFIX/web"
mv "$PREFIX/web.new" "$PREFIX/web"

# ------------------------------------------------------------------ configuração

log "Configuração em $ETC"
install -d -m 0755 -o root -g root "$ETC"
backup_and_install() { # arquivo do repositório → /etc/nvr (guarda o anterior se foi editado)
  local src=$1 dst=$2
  if [[ -f "$dst" ]] && ! cmp -s "$src" "$dst"; then cp -a "$dst" "$dst.anterior"; fi
  install -m 0644 -o root -g root "$src" "$dst"
}
backup_and_install "$REPO/integrations/mediamtx/mediamtx.yml" "$ETC/mediamtx.yml"
backup_and_install "$REPO/infra/caddy/Caddyfile" "$ETC/Caddyfile"

if [[ ! -s "$ETC/secrets.key" ]]; then
  # Chave do cofre de credenciais das câmeras, fora do diretório de dados (ADR-003); só o usuário nvr lê.
  umask 077
  "$PREFIX/app/.venv/bin/python" -c 'import sys; from cryptography.fernet import Fernet; sys.stdout.write(Fernet.generate_key().decode())' >"$ETC/secrets.key"
  umask 022
fi
chown nvr:nvr "$ETC/secrets.key"
chmod 0600 "$ETC/secrets.key"

roots_json() { python3 -c 'import json, sys; print(json.dumps(sys.argv[1:]))' "$@"; }
env_value() { # valor de uma variável num arquivo de ambiente (sem as aspas externas)
  sed -n -E "s/^$2=(.*)$/\1/p" "$1" | tail -n 1 | sed -E "s/^'(.*)'$/\1/; s/^\"(.*)\"$/\1/"
}
if [[ ! -f "$ETC/nvr.env" ]]; then
  [[ ${#STORAGE_ROOTS[@]} -gt 0 ]] || STORAGE_ROOTS=("$DEFAULT_ROOT")
  cat >"$ETC/nvr.env" <<EOF
# Backend do NVR (EnvironmentFile do systemd). O instalador não sobrescreve este arquivo;
# --storage-root troca só a linha NVR_STORAGE_ROOTS. Depois de editar: sudo systemctl restart nvr-api
NVR_DATA_DIR=$STATE
NVR_BIND_HOST=127.0.0.1
NVR_BIND_PORT=8000
NVR_RECORDINGS_DIR=${STORAGE_ROOTS[0]}/recordings
NVR_STORAGE_ROOTS='$(roots_json "${STORAGE_ROOTS[@]}")'
NVR_SECRET_BACKEND=file
NVR_SECRET_KEY_FILE=$ETC/secrets.key
NVR_FFMPEG_PATH=/usr/bin/ffmpeg
NVR_LOG_FORMAT=json
EOF
elif [[ ${#STORAGE_ROOTS[@]} -gt 0 ]]; then
  sed -i -E "s|^NVR_STORAGE_ROOTS=.*$|NVR_STORAGE_ROOTS='$(roots_json "${STORAGE_ROOTS[@]}")'|" "$ETC/nvr.env"
fi
chmod 0644 "$ETC/nvr.env"
mapfile -t ROOTS < <(python3 -c 'import json, sys; print("\n".join(json.loads(sys.argv[1])))' "$(env_value "$ETC/nvr.env" NVR_STORAGE_ROOTS)")
RECORDINGS_DIR="$(env_value "$ETC/nvr.env" NVR_RECORDINGS_DIR)"

log "Armazenamento: ${ROOTS[*]} (grupo nvr: o MediaMTX grava, o backend aplica a cota e migra)"
for root in "${ROOTS[@]}"; do
  install -d -o nvr -g nvr -m 2770 "$root"
  chown nvr:nvr "$root"
  chmod 2770 "$root"
done
[[ -d "$RECORDINGS_DIR" ]] || install -d -o nvr -g nvr -m 2770 "$RECORDINGS_DIR"

for unit in nvr-api nvr-mediamtx; do
  install -d -m 0755 "/etc/systemd/system/$unit.service.d"
  {
    echo "# Gerado pelo instalador a partir de NVR_STORAGE_ROOTS ($ETC/nvr.env): o único lugar gravável fora do estado."
    echo "[Service]"
    for root in "${ROOTS[@]}"; do echo "ReadWritePaths=-$root"; done
  } >"/etc/systemd/system/$unit.service.d/storage.conf"
done

IP="$(lan_ip || true)"
EXTRA="$(env_value "$ETC/caddy.env" NVR_EXTRA_SITE_ADDRESSES 2>/dev/null || true)"
[[ ${#SITE_ADDRESSES[@]} -gt 0 ]] && EXTRA="$(IFS=,; echo "${SITE_ADDRESSES[*]}")"
ADDRESSES="$(python3 -c '
import sys
names = [part.strip() for arg in sys.argv[1:] for part in arg.split(",") if part.strip()]
print(", ".join(dict.fromkeys(names)))' "$HOST.local" "$EXTRA" "$IP" localhost 127.0.0.1)"
cat >"$ETC/caddy.env" <<EOF
# HTTPS do NVR (EnvironmentFile do nvr-caddy). Regenerado pelo instalador; extras com --site-address.
NVR_SITE_ADDRESSES="$ADDRESSES"
NVR_EXTRA_SITE_ADDRESSES="$EXTRA"
NVR_BACKEND=127.0.0.1:8000
NVR_WEB_ROOT=$PREFIX/web
NVR_CA_DIR=$CA_DIR
NVR_CADDY_ADMIN=off
EOF
chmod 0644 "$ETC/caddy.env"

# ------------------------------------------------------------------ serviços

log "Serviços systemd: ${UNITS[*]}"
install -m 0644 "$REPO"/infra/linux/systemd/*.service /etc/systemd/system/
install -m 0755 "$REPO/infra/linux/nvr-admin" /usr/local/bin/nvr-admin
systemctl daemon-reload
systemctl enable "${UNITS[@]}" >/dev/null
systemctl restart "${UNITS[@]}"

if [[ -n "$FIREWALL" ]]; then
  if [[ "$FIREWALL" == auto ]]; then
    dev="$(ip -4 route get 1.1.1.1 | awk '{for (i = 1; i <= NF; i++) if ($i == "dev") {print $(i + 1); exit}}')"
    FIREWALL="$(ip -o -4 addr show dev "$dev" | awk '{print $4; exit}')"
  fi
  FIREWALL="$(python3 -c 'import ipaddress, sys; print(ipaddress.ip_network(sys.argv[1], strict=False))' "$FIREWALL")" ||
    die "rede inválida para o firewall"
  if [[ -n "${SSH_CONNECTION:-}" ]]; then
    client="${SSH_CONNECTION%% *}"
    python3 -c 'import ipaddress, sys; sys.exit(ipaddress.ip_address(sys.argv[1]) not in ipaddress.ip_network(sys.argv[2]))' \
      "$client" "$FIREWALL" || die "sua sessão SSH vem de $client, fora de $FIREWALL: o firewall cortaria seu acesso"
  fi
  log "Firewall (ufw): entrada só da LAN $FIREWALL"
  apt-get install -y -q --no-install-recommends ufw
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow from "$FIREWALL" to any port 22 proto tcp comment 'NVR: SSH so da LAN' >/dev/null
  ufw allow from "$FIREWALL" to any port 80,443 proto tcp comment 'NVR: HTTPS (Caddy)' >/dev/null
  ufw allow from "$FIREWALL" to any port 8189 proto udp comment 'NVR: video ao vivo (WebRTC)' >/dev/null
  ufw allow from "$FIREWALL" to any port 5353 proto udp comment 'NVR: nome .local (mDNS)' >/dev/null
  ufw --force enable >/dev/null
fi

log "Aguardando os serviços"
for _ in $(seq 60); do
  curl -fsS --max-time 2 http://127.0.0.1:8000/api/v1/system/ping >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS --max-time 2 http://127.0.0.1:8000/api/v1/system/ping >/dev/null ||
  die "o backend não respondeu; veja: journalctl -u nvr-api -n 100"
for _ in $(seq 60); do [[ -s "$CA_DIR/root.crt" ]] && break; sleep 1; done
[[ -s "$CA_DIR/root.crt" ]] || die "a CA do HTTPS não foi criada; veja: journalctl -u nvr-caddy -n 100"
FINGERPRINT="$(openssl x509 -noout -fingerprint -sha256 -in "$CA_DIR/root.crt" | cut -d= -f2)"

cat <<EOF

NVR instalado e no ar.
  Endereço:        https://$HOST.local   (ou https://${IP:-<IP do servidor>})
  CA para baixar:  http://${IP:-<IP do servidor>}/nvr-ca.crt
  SHA-256 da CA:   $FINGERPRINT
  Primeiro acesso: sudo nvr-admin create-user admin
  Logs:            journalctl -u nvr-api -u nvr-mediamtx -u nvr-caddy -f
  Guias:           docs/operations/instalacao-linux.md e docs/operations/https-e-certificado.md
EOF
