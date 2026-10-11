#!/usr/bin/env bash
# Instala el servidor en una Raspberry Pi (Raspberry Pi OS Bookworm).
# Uso:  sudo ./deploy/install.sh   (desde la raíz del repositorio)
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Ejecuta con sudo"; exit 1; fi
REPO="$(cd "$(dirname "$0")/.." && pwd)"

apt-get update
apt-get install -y python3-venv ffmpeg rsync

id curado &>/dev/null || useradd --system --home /var/lib/curado --shell /usr/sbin/nologin curado
install -d -o curado -g curado /var/lib/curado

mkdir -p /opt/curado
rsync -a --delete --exclude .venv --exclude data "$REPO/server/" /opt/curado/server/
python3 -m venv /opt/curado/server/.venv
/opt/curado/server/.venv/bin/pip install -q -r /opt/curado/server/requirements.txt

if [[ ! -f /etc/curado.env ]]; then
  install -m 600 "$REPO/deploy/curado.env.example" /etc/curado.env
  sed -i "s/^CURADO_TOKEN=.*/CURADO_TOKEN=$(openssl rand -hex 16)/" /etc/curado.env
  echo "Creado /etc/curado.env con un token aleatorio: cópialo a secrets.h de la ESP32."
fi

install -m 644 "$REPO/deploy/curado.service" /etc/systemd/system/curado.service
systemctl daemon-reload
systemctl enable curado
systemctl restart curado
echo "Listo: http://$(hostname -I | awk '{print $1}'):8000"
grep CURADO_TOKEN /etc/curado.env
