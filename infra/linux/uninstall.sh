#!/usr/bin/env bash
# Remove os serviços e os programas do NVR. NUNCA apaga gravações.
#   sudo infra/linux/uninstall.sh            mantém configuração, banco, cofre e CA (reinstalar volta igual)
#   sudo infra/linux/uninstall.sh --purge    apaga também /etc/nvr, /var/lib/nvr, /var/lib/nvr-caddy e os usuários
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "rode como root: sudo $0" >&2; exit 1; }
PURGE=0
[[ "${1:-}" == --purge ]] && PURGE=1

for unit in nvr-caddy nvr-api nvr-mediamtx; do
  systemctl disable --now "$unit" 2>/dev/null || true
  rm -rf "/etc/systemd/system/$unit.service" "/etc/systemd/system/$unit.service.d"
done
systemctl daemon-reload
rm -rf /opt/nvr /usr/local/bin/nvr-admin /var/cache/nvr-uv

if [[ $PURGE -eq 1 ]]; then
  rm -rf /etc/nvr /var/lib/nvr /var/lib/nvr-caddy
  for user in nvr-caddy nvr-media nvr; do userdel "$user" 2>/dev/null || true; done
  if getent group nvr-caddy >/dev/null; then groupdel nvr-caddy 2>/dev/null || true; fi
  echo "NVR removido (configuração e dados apagados; gravações mantidas)."
else
  echo "NVR removido. Mantidos: /etc/nvr, /var/lib/nvr, /var/lib/nvr-caddy (CA) e as gravações."
fi
