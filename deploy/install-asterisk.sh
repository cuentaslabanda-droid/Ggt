#!/usr/bin/env bash
# Instala Asterisk en la Raspberry Pi para las llamadas de alarma por la línea
# analógica (a través de una pasarela FXO Grandstream HT813).
# Ejecuta antes deploy/install.sh. Uso:  sudo ./deploy/install-asterisk.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Ejecuta con sudo"; exit 1; fi
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SOUNDS=/var/lib/curado/sounds
ASTERISK_VERSION=22   # rama LTS

apt-get update
apt-get install -y curl ffmpeg
# Voz en español: pico2wave suena mejor; espeak-ng siempre está disponible.
apt-get install -y libttspico-utils || apt-get install -y espeak-ng

# --- Asterisk ------------------------------------------------------------------
if ! command -v asterisk >/dev/null; then
  if apt-cache policy asterisk | grep -q 'Candidate: [0-9]'; then
    apt-get install -y asterisk
  else
    # Debian 12/13 ya no traen el paquete: se compila (tarda 30-60 min en una Pi 4).
    echo "Compilando Asterisk $ASTERISK_VERSION desde el código fuente..."
    cd /usr/src
    curl -fsSLO "https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}-current.tar.gz"
    tar xzf "asterisk-${ASTERISK_VERSION}-current.tar.gz"
    cd asterisk-${ASTERISK_VERSION}.*/
    contrib/scripts/install_prereq install
    ./configure --with-jansson-bundled --with-pjproject-bundled
    make menuselect.makeopts
    make -j"$(nproc)"
    make install
    make samples
    make config
    ldconfig
    id asterisk &>/dev/null || useradd --system --home /var/lib/asterisk --shell /usr/sbin/nologin asterisk
    chown -R asterisk:asterisk /etc/asterisk /var/lib/asterisk /var/log/asterisk /var/spool/asterisk /var/run/asterisk 2>/dev/null || true
    sed -i 's/^;runuser = asterisk/runuser = asterisk/; s/^;rungroup = asterisk/rungroup = asterisk/' /etc/asterisk/asterisk.conf
  fi
fi

# --- Configuración ------------------------------------------------------------------
FXO_PASSWORD=$(openssl rand -hex 12)
PIN=$(shuf -i 1000-9999 -n 1)
if [[ -f /etc/asterisk/.curado ]]; then
  # Reinstalación: conserva la contraseña del HT813 y el PIN.
  source /etc/asterisk/.curado
fi
for f in pjsip.conf extensions.conf; do
  if [[ -f /etc/asterisk/$f && ! -f /etc/asterisk/$f.orig ]]; then
    cp /etc/asterisk/$f /etc/asterisk/$f.orig
  fi
done
sed "s/@FXO_PASSWORD@/$FXO_PASSWORD/" "$REPO/deploy/asterisk/pjsip.conf" > /etc/asterisk/pjsip.conf
sed "s#@SOUNDS@#$SOUNDS#; s/@PIN@/$PIN/" "$REPO/deploy/asterisk/extensions.conf" > /etc/asterisk/extensions.conf
printf 'FXO_PASSWORD=%s\nPIN=%s\n' "$FXO_PASSWORD" "$PIN" > /etc/asterisk/.curado
chmod 600 /etc/asterisk/.curado
chown asterisk:asterisk /etc/asterisk/pjsip.conf /etc/asterisk/extensions.conf
chmod 640 /etc/asterisk/pjsip.conf

# El servidor (usuario curado) deja los .call en el spool de Asterisk, y
# Asterisk lee los audios que genera el servidor.
usermod -aG asterisk curado
chgrp asterisk /var/spool/asterisk/outgoing
chmod 2775 /var/spool/asterisk/outgoing
install -d -o curado -g asterisk -m 2755 "$SOUNDS"
chmod 755 /var/lib/curado
(cd /opt/curado/server && sudo -u curado .venv/bin/python -m curado.prompts "$SOUNDS")

grep -q 'CURADO_ASTERISK_NUMBERS' /etc/curado.env || cat >> /etc/curado.env <<'EOF'

# Llamadas de alarma por la línea analógica (números separados por comas,
# tal como los marcarías desde el teléfono de casa):
#CURADO_ASTERISK_NUMBERS=5512345678,5587654321
EOF

systemctl enable asterisk
systemctl restart asterisk
systemctl restart curado

IP=$(hostname -I | awk '{print $1}')
cat <<EOF

Asterisk listo. Configura el puerto FXO del Grandstream HT813 (ver docs/alarmas.md):
  Primary SIP Server:   $IP
  SIP User ID:          fxo
  Authenticate ID:      fxo
  Authenticate Password $FXO_PASSWORD
PIN para confirmar alarmas llamando a la línea: $PIN
Después pon tus números en CURADO_ASTERISK_NUMBERS en /etc/curado.env y
reinicia:  sudo systemctl restart curado
Comprueba el registro con:  sudo asterisk -rx "pjsip show contacts"
EOF
