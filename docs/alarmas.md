# Alarmas: que no se vuelva a perder el producto

## Qué vigila

| Alarma | Cuándo salta | Tipo |
|--------|--------------|------|
| **Temperatura alta** | más de **7 °C** durante 10 min seguidos (abrir la puerta un momento no la dispara) | crítica |
| **La nevera no enfría** | el relé lleva 45 min encendido y la temperatura no baja: nevera desenchufada o averiada, magnetotérmico, puerta abierta, relé roto. **Suele saltar antes de llegar a 7 °C** | crítica |
| **Sin datos** | la ESP32 no reporta en 10 min (corte de luz, WiFi caído) | crítica |
| **Temperatura baja** | menos de 0 °C durante 10 min (relé pegado, riesgo de congelar) | crítica |
| **Sensores caídos** | ningún sensor responde | crítica |
| Humedad fuera de rango, un sensor caído | | aviso |

Los límites y tiempos se cambian en el panel, en la sección **Alarmas**.

**Crítica**: mensaje por todos los canales configurados **y llamada telefónica**. Se repite cada
15 min hasta que alguien la confirme, con el botón *Reconocer alarma* del panel o pulsando **1**
durante la llamada. Cuando se resuelve llega un mensaje de "alarma resuelta".
**Aviso**: sólo un mensaje, sin llamada.

## Canales

Se activan con sólo poner las credenciales en `/etc/curado.env` (ver
[`deploy/curado.env.example`](../deploy/curado.env.example)), reiniciar con
`sudo systemctl restart curado` y probar con **Enviar mensaje de prueba** en el panel. Configura
**al menos dos canales distintos**: si uno falla, los demás siguen enviando.

| Canal | Coste | Notas |
|-------|-------|-------|
| Correo (SMTP, p. ej. Gmail) | gratis | con Gmail hace falta una "contraseña de aplicación" |
| Telegram | gratis | bot propio con @BotFather |
| WhatsApp (CallMeBot) | gratis | sólo a tu propio número, previa activación |
| ntfy | gratis | notificación push con prioridad urgente |
| Slack / Discord / webhook | gratis | cualquier servicio que acepte JSON |
| SMS y llamada con Twilio | de pago, ~1 USD/mes + uso | llama por internet, **sin** línea analógica |
| **Llamada por línea analógica** | pasarela ~65-100 USD | Asterisk en la Pi + Grandstream HT813 |

## Llamadas por la línea analógica

### La pieza: pasarela FXO **Grandstream HT813**

La Raspberry no tiene ranuras PCI para tarjetas de telefonía. Hace falta una **pasarela FXO**, que
convierte la línea analógica a SIP por la red. Asterisk se comunica con ella como si fuera un
teléfono IP más.

- **Grandstream HT813**: 1 puerto **FXO** (línea) + 1 **FXS** (teléfono). Es el modelo estándar para
  esto y se usa mucho con Asterisk y FreePBX. Precio aproximado entre **64 y 97 USD** según la tienda.
- Alternativas: Cisco/Linksys **SPA3102** (descatalogada, de segunda mano), Grandstream **GXW4104**
  (4 líneas, más cara) o pasarelas **Patton** (profesionales, ~300 USD).
- Evita los adaptadores "USB FXO" baratos: dan muchos problemas con Asterisk.

```
Roseta / puerto "Phone" del router ──► HT813 puerto LINE (FXO)
Teléfono de casa (opcional) ──────────► HT813 puerto PHONE (FXS)
HT813 puerto LAN ─────────────────────► router  ◄── Raspberry Pi (Asterisk)
```

> ⚠️ El puerto **LINE** (FXO) va a la línea y el **PHONE** (FXS) al teléfono. Si los conectas al
> revés, la tensión de la línea puede dañar el equipo.

### Instalación

```bash
sudo ./deploy/install.sh            # si no lo has hecho ya
sudo ./deploy/install-asterisk.sh   # Asterisk + configuración + voz en español
```

En Raspberry Pi OS sin paquete `asterisk`, el script compila Asterisk 22 LTS. Tarda entre 30 y 60 min.
Al terminar muestra la **contraseña SIP** para el HT813 y un **PIN** de 4 cifras.

### Configurar el HT813

Entra en la web del HT813 (su IP aparece en el router; usuario `admin`). Los nombres de los menús
pueden variar un poco según el firmware.

**Pestaña FXO Port:**

| Campo | Valor |
|-------|-------|
| Account Active | Yes |
| Primary SIP Server | IP de la Raspberry |
| SIP User ID / Authenticate ID | `fxo` |
| Authenticate Password | la que mostró `install-asterisk.sh` |
| SIP Registration | Yes |
| Local SIP Port | **5062** (el puerto FXS usa 5060; no pueden coincidir) |
| Preferred Vocoder | PCMU (y PCMA) |
| DTMF | RFC2833 |
| Stage Method (VoIP→PSTN) | **1**: marca directamente el número que envía Asterisk |
| AC Termination / Country | **tu país** (impedancia y tonos de la línea) |

**Llamadas entrantes (opcional):** en *PSTN → VoIP* activa *Unconditional Call Forward to VoIP*
con User ID `curado`, servidor = IP de la Pi y puerto 5060. Así, cuando llamas a la línea,
Asterisk contesta, dice la temperatura, la humedad y las alarmas, y permite confirmar una alarma
pulsando **1 + PIN**. Si es la línea de casa, ajusta el número de tonos antes de contestar para no
interferir con las llamadas normales.

Comprueba el registro con `sudo asterisk -rx "pjsip show contacts"`. Debe aparecer `fxo` como
*Avail*. Después pon tus números en `/etc/curado.env`:

```
CURADO_ASTERISK_NUMBERS=5512345678,5587654321
```

`sudo systemctl restart curado` → panel → **Prueba con llamada**.

### Cómo es la llamada

Una línea analógica sólo admite una llamada a la vez, así que los números se llaman **uno tras
otro**, con 2 min de separación. Si no contestan, cada número se reintenta 2 veces. El mensaje se
repite 4 veces: *"Temperatura de la cámara de curado 8 coma 5 grados, por encima del límite de 7
grados. La nevera está encendida. Pulse 1 para confirmar"*. Muchas líneas analógicas no avisan de
cuándo descuelgas, y la repetición garantiza que lo oigas entero. Si tu compañía lo permite,
activa en el HT813 la detección de *polarity reversal* para saber cuándo contestan.

## El corte de luz: el punto débil

Si se va la luz se apagan la nevera **y también** la Pi, el router, la ESP32 y el HT813. Sin
corriente nadie puede avisar. Para cubrirlo:

1. **SAI/UPS pequeño** (p. ej. 600 VA) para la **Raspberry, el router, la ESP32 y el HT813**. La
   nevera no va al SAI. Con eso, un corte de luz provoca la alarma *Sin datos*, que llega por todos
   los canales mientras dure la batería. Si la ESP32 también está en el SAI, además verás subir la
   temperatura.
2. **Vigilancia externa** (`CURADO_HEARTBEAT_URL`, p. ej. healthchecks.io, gratis): la Pi envía
   una señal cada minuto mientras todo está bien. Si se apaga, se cuelga o se queda sin internet,
   ese servicio te manda un correo o un Telegram. Es la única forma de enterarte si falla la propia Pi.
3. La línea analógica de cobre tradicional funciona sin corriente, pero si tu "línea fija" sale
   del router de la compañía (VoIP), sólo funciona mientras el router tenga luz: otro motivo para
   ponerlo en el SAI.
