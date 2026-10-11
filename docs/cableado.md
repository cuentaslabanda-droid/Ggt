# Cableado y montaje

> ⚠️ **220/120 V AC.** La nevera y el humidificador funcionan a tensión de red. Si no tienes
> experiencia con instalaciones eléctricas, pide ayuda. Todo lo que lleve red debe ir en una caja
> cerrada, con fusible/magnetotérmico y la tierra de la nevera conectada.

## Esquema general

```
                    ┌──────────── Fuente 5 V / 3 A ─────────────┐
                    │                                           │
              ┌─────┴──────────────┐                     ┌──────┴──────┐
              │   ESP32-CAM        │ GPIO2  ───────────► │ Relé nevera │──► enchufe de la nevera
 SHT31 arriba ┤ GPIO15 = SCL       │ GPIO3  ───────────► │ Relé humid. │──► enchufe humidificador
 SHT31 medio  ┤ GPIO14 = SDA A     │ GPIO12 ──┐          └─────────────┘
 SHT31 abajo  ┤ GPIO13 = SDA B     │          │  MOSFET
              │ GPIO4  = flash LED │          └──► ventilador 12 V ◄── fuente 12 V
              └────────────────────┘
                     WiFi  ⇅  HTTP
              ┌────────────────────┐
              │  Raspberry Pi      │  panel web :8000, histórico, fotos, timelapse, avisos
              └────────────────────┘
```

## Pines (ESP32-CAM AI-Thinker)

| GPIO | Uso | Notas |
|------|-----|-------|
| 15 | SCL de los 3 sensores | compartido por los dos buses |
| 14 | SDA bus A | sensor **arriba** (0x44) + sensor **medio** (0x45) |
| 13 | SDA bus B | sensor **abajo** (0x44) |
| 2  | Relé nevera | arranca a nivel bajo: el compresor no da tirones al encender |
| 3  | Relé humidificador | es el RX del puerto serie: **desconéctalo al programar** |
| 12 | PWM ventilador (MOSFET) | pin de arranque: **necesita la resistencia de 10 kΩ a GND** |
| 4  | LED flash de la placa | se enciende solo al hacer la foto |
| 33 | LED rojo de la placa | encendido = sin WiFi |

Los pines 2, 4, 12, 13, 14 y 15 están libres porque no se usa la tarjeta SD (no la insertes).

## Sensores SHT31-D

El SHT31 sólo tiene dos direcciones I²C, por eso el tercero va en otro cable SDA.

| Sensor | VIN | GND | SCL | SDA | ADR / AD |
|--------|-----|-----|-----|-----|----------|
| arriba | 3V3 | GND | GPIO15 | GPIO14 | GND → 0x44 |
| medio  | 3V3 | GND | GPIO15 | GPIO14 | **VIN → 0x45** |
| abajo  | 3V3 | GND | GPIO15 | GPIO13 | GND → 0x44 |

- Alimenta los sensores a **3,3 V** (la ESP32 no tolera 5 V en sus pines).
- Las placas Teyleten ya llevan resistencias pull-up de 10 kΩ en SDA/SCL; no hace falta añadir más.
- Cables de hasta ~2 m funcionan bien (el bus va a ~100 kHz). Usa cable trenzado o apantallado
  (p. ej. un cable de red: un par para SCL/GND, otro para SDA/3V3).
- Colócalos a tres alturas (arriba, centro, abajo), separados de las paredes y **a más de 30 cm de
  la salida del humidificador**: si la niebla los moja, leerán 100 % durante horas.
- Los nombres "arriba/medio/abajo" se cambian en `firmware/esp32cam/include/config.h`.

## Nevera (relé en GPIO2)

- Pon el termostato de la nevera **al máximo frío**: el relé es el que decide cuándo enfriar.
- Corta la fase (marrón/negro) del cable de la nevera con el relé; el neutro y la tierra van directos.
- El compresor tiene un pico de arranque de 5-10 veces su consumo. Usa un **relé de al menos 10 A
  (mejor uno de 30 A) o un contactor**. Los módulos de relé baratos de 10 A aguantan una nevera
  doméstica, pero sus contactos se gastan antes. El Midea MDRT210CCNLS consume 1 A (150 W) a
  115 V: un relé de 10 A basta; uno de 30 A dura más.
- Es de **descongelación manual** (placa fría en la pared trasera): con el relé apagando el
  compresor la placa no se descongela sola. Quita la escarcha cuando pase de ~5 mm.
- El firmware protege el compresor: mínimo 5 min apagado entre arranques (también tras un corte de
  luz) y mínimo 2 min encendido.

## Humidificador (relé en GPIO3)

- Usa un humidificador ultrasónico **con interruptor mecánico** (o que se encienda solo al
  enchufarlo). Los de botón táctil se quedan apagados cuando el relé les da corriente.
- Alternativa: un módulo humidificador USB de 5 V (disco piezo) controlado por el mismo relé o un MOSFET.
- El firmware lo hace funcionar en ráfagas (máx. 2 min) para que los sensores alcancen a medir antes
  de pasarse de humedad.
- **Al programar la ESP32 desconecta el cable de GPIO3**, porque es la línea RX del puerto serie.

## Ventilador (PWM en GPIO12)

Ventilador de PC de 12 V (80-120 mm, 2 o 3 cables) a través de un MOSFET de canal N de nivel lógico
(AO3400, IRLZ44N, IRLB8721…):

```
GPIO12 ──[100 Ω]──┬── Gate
                  │
                [10 kΩ]          Ventilador +  ── +12 V
                  │              Ventilador −  ── Drain
GND ──────────────┴── Source ─── GND (común con la ESP32)
                 Diodo 1N4148/1N5819 en paralelo con el ventilador (cátodo a +12 V)
```

- La resistencia de 10 kΩ a GND **es obligatoria**: GPIO12 debe estar a nivel bajo al arrancar o la
  ESP32 no arranca.
- **No conectes** el cable PWM de un ventilador de 4 hilos directamente a GPIO12: estos ventiladores
  llevan un pull-up interno a 5 V que impide arrancar a la ESP32.
- Coloca el ventilador para que mueva el aire suavemente entre las piezas, sin soplar directamente
  sobre ellas (un chorro directo forma costra).

## Cámara

- La ESP32-CAM se puede montar dentro de la nevera en una cajita con ventana, mirando a las piezas.
  Protege la placa con barniz (conformal coating) contra la condensación, sin tocar la lente.
- La fuente y los relés, mejor **fuera** de la nevera.
- Para sacar cables, pásalos por la junta de la puerta con cable plano o usa el orificio del desagüe.
  **No taladres las paredes**: por dentro van los tubos del circuito de frío.

## Alimentación

- ESP32-CAM: 5 V con al menos 2 A (con WiFi + cámara + flash una fuente floja provoca reinicios
  "brownout"). Pon un condensador de 470-1000 µF entre 5 V y GND cerca de la placa.
- Ventilador: 12 V aparte. Une las masas (GND) de ambas fuentes.

## Calibrar los sensores (prueba de la sal)

Los SHT31 tienen ±2 % HR de error. Para afinarlos:

1. Mete en un táper hermético un vaso con sal común y un poco de agua (pasta húmeda, sin que se
   disuelva toda) y los tres sensores, sin que toquen la sal.
2. Espera 12 h a temperatura estable. El aire sobre sal saturada está a **75,3 % HR**.
3. Con las correcciones a 0, anota la HR de cada sensor y en el panel → *Ajustes avanzados →
   Calibración* pon para cada uno `75.3 − lectura`.
