# Cámara de curado de jamones

Sistema para curar jamones en una nevera doméstica (7 pies). Mantiene **temperatura, humedad y
circulación de aire** constantes, sigue un **programa de curado por fases** y graba un
**timelapse** de las piezas.

- **ESP32-CAM**: lee 3 sensores SHT31-D (arriba / medio / abajo), controla nevera, humidificador y
  ventilador y hace las fotos. Funciona **sola** aunque se caiga la Raspberry o el WiFi; guarda las
  últimas consignas en su memoria.
- **Raspberry Pi**: servidor con panel web, histórico, programas de curado, seguimiento de la merma
  de cada pieza, fotos, generación de timelapse y avisos al móvil.

```
firmware/esp32cam/   firmware PlatformIO (Arduino) para la ESP32-CAM
server/              servidor FastAPI + panel web para la Raspberry Pi
server/programs/     programas de curado (JSON, editables)
deploy/              servicio systemd e instalador para la Pi
docs/cableado.md     conexiones, montaje y calibración
```

## Material

| Pieza | Notas |
|-------|-------|
| Raspberry Pi (3B+, 4 o 5) + microSD | cualquier modelo con WiFi/red |
| ESP32-CAM AI-Thinker + programador ESP32-CAM-MB | con PSRAM (las normales la llevan) |
| 3 × SHT31-D (Teyleten, pack de 3) | I²C, 3,3 V |
| Módulo relé 2 canales 5 V (≥10 A) | o un relé de 30 A / contactor para la nevera |
| Humidificador ultrasónico con interruptor mecánico | ver `docs/cableado.md` |
| Ventilador 12 V 80-120 mm + MOSFET AO3400/IRLZ44N + 10 kΩ + 100 Ω + diodo | |
| Fuente 5 V 3 A y fuente 12 V 1 A | |

## Puesta en marcha

### 1. Raspberry Pi

```bash
git clone <este repo> && cd <repo>
sudo ./deploy/install.sh
```

Instala el servicio `curado`, crea `/etc/curado.env` con un **token aleatorio** (lo muestra al
final) y deja el panel en `http://<ip-de-la-pi>:8000`. Para recibir avisos en el móvil instala la
app **ntfy**, suscríbete a un tema y ponlo en `CURADO_ALERT_URL` dentro de `/etc/curado.env`
(`sudo systemctl restart curado`).

Conviene dar a la Pi una IP fija (en el router) porque la ESP32 la necesita.

### 2. ESP32-CAM

```bash
pip install platformio
cd firmware/esp32cam
cp include/secrets.example.h include/secrets.h   # WiFi, IP de la Pi y el token
pio run -t upload        # desconecta el relé del humidificador (GPIO3) mientras programas
pio device monitor
```

Cablea según [`docs/cableado.md`](docs/cableado.md). Para probar el cableado usa
*Ajustes avanzados → Forzar salidas* en el panel.

### 3. Probar sin hardware

```bash
cd server
pip install -r requirements-dev.txt
uvicorn curado.app:app --port 8000 &
python tools/simulate_esp32.py          # ESP32 simulada
```

## Cómo controla el clima

Cada 5 s la ESP32 hace la media de los sensores válidos y decide:

| Situación | Acción |
|-----------|--------|
| T > consigna + histéresis | enfría (nevera) hasta consigna − histéresis |
| HR > consigna + histéresis | **deshumidifica con la nevera** (el evaporador condensa agua) mientras la T no baje más de `dehum_margin` (1,5 °C) bajo la consigna |
| HR < consigna − histéresis | humidificador en ráfagas (máx. 2 min, pausa 30 s) hasta la consigna |
| Siempre | ventilador al % base; sube al % de refuerzo con nevera/humidificador encendidos o si los sensores difieren > 1,5 °C / 6 % HR (aire estratificado) |

Protecciones: el compresor espera 5 min entre arranques (también al volver la luz) y funciona al
menos 2 min; nunca enfría por debajo de `min_temp_c`; si fallan **todos** los sensores apaga nevera
y humidificador y deja sólo el ventilador. La ESP32 envía alarmas (temperatura/humedad fuera de
rango, sensores caídos) y la Pi avisa también si la ESP32 deja de reportar 10 min.

En el panel, *Últimas 24 h* muestra el % de tiempo que funcionan nevera y humidificador: una nevera
al 90 % indica mala junta o demasiado calor exterior; un humidificador constante es normal en
neveras **no-frost**, que secan mucho el aire.

## Programas de curado

En modo **Programa**, la temperatura, humedad y ventilador base siguen las fases del programa
elegido desde la fecha de inicio, con transiciones graduales (`ramp_days`) para que la pieza no
forme costra. El programa principal, **Jamón curado en frío (`jamon_4c`)**, mantiene **4 °C** de
principio a fin:

| Fase | Días | °C | % HR |
|------|------|----|------|
| Salado (≈1 día por kg) | 10 | 4 | 88 |
| Post-salado / asentamiento | 60 | 4 | 84 |
| Secado | 150 | 4 | 84 → 80 |
| Secado final y afinado | 240 | 4 | 80 → 76 |

A 4 °C el agua sale más despacio que en una bodega a 12-17 °C, así que el curado es más largo
(fácilmente 12-16 meses para un jamón entero). Da la pieza por terminada **por la merma**, no por
los días: la última fase se mantiene indefinidamente. También tiene ventajas: es más seguro frente
a bacterias y la nevera trabaja cómoda (no necesita calefacción en invierno).

También se incluyen `jamon_serrano` y `paleta`, el método tradicional que sube a 12-17 °C para el
secado y la maduración (más rápido y con más sabor "de bodega", pero la habitación tiene que estar
por encima de esa temperatura porque la nevera sólo enfría).

Edita o añade programas en `server/programs/*.json` (reinicia el servicio para cargarlos).
Entre salado y post-salado hay que sacar, lavar y secar la pieza: el sistema no lo hace por ti.

**Merma:** registra el peso inicial de cada pieza y pésala cada semana o dos. El jamón suele estar
listo hacia el **30-35 % de pérdida de peso**; el panel muestra el progreso hasta el objetivo.

## Timelapse

La ESP32 hace una foto cada `photo_interval` segundos (15 min por defecto) con el flash encendido.
Las fotos se guardan en la Pi (`/var/lib/curado/photos/AAAA-MM-DD/`) y desde el panel se genera un
vídeo MP4 de los últimos N días con ffmpeg. A 15 min/foto, un mes son ~2900 fotos (~1 GB).

## API

| Método | Ruta | Uso |
|--------|------|-----|
| POST | `/api/telemetry` | ESP32 → Pi (cabecera `X-Device-Token`); responde con la config |
| POST | `/api/photo` | ESP32 → Pi, JPEG |
| GET | `/api/status`, `/api/history?hours=24` | estado y histórico |
| GET/PUT | `/api/settings` | modo, programa y consignas |
| GET | `/api/programs` | programas disponibles |
| GET/POST | `/api/hams`, `/api/hams/{id}/weights` | piezas y pesadas |
| GET/POST | `/api/timelapse` | vídeos y generación |

El panel no tiene contraseña: úsalo sólo en tu red local (o detrás de una VPN como Tailscale).

## Tests

```bash
cd firmware/esp32cam && pio test -e native   # lógica de control en el PC
cd server && pytest                          # API del servidor
```
