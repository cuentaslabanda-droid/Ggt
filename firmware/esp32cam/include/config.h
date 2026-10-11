#pragma once

// ---------------------------------------------------------------------------
// Pin map for the AI-Thinker ESP32-CAM (see docs/cableado.md).
//
// The camera uses most GPIOs; with the SD card unused the free ones are
// 2, 4 (flash LED), 12, 13, 14, 15 and 3 (UART RX). Strapping pins are
// assigned so that relays cannot glitch the compressor at boot:
//   - GPIO12 must be LOW at boot  -> fan MOSFET gate (10k pull-down).
//   - GPIO2  has an internal pull-down at reset -> fridge relay.
//   - GPIO3  is UART RX (serial input is lost, logs on TX still work)
//     -> humidifier relay; a short pulse at boot is harmless.
// ---------------------------------------------------------------------------

// SHT31-D sensors. The chip only has two addresses (0x44 / 0x45), so the
// three sensors use two bit-banged I2C buses that share SCL.
#define PIN_I2C_SCL        15
#define PIN_I2C_SDA_A      14   // sensor 0 (0x44, ADDR->GND) + sensor 1 (0x45, ADDR->VIN)
#define PIN_I2C_SDA_B      13   // sensor 2 (0x44, ADDR->GND)

#define PIN_RELAY_FRIDGE    2
#define PIN_RELAY_HUMIDIFIER 3
#define PIN_FAN_PWM        12
#define PIN_FLASH_LED       4
#define PIN_STATUS_LED     33   // onboard red LED, active LOW

// Most cheap relay boards switch on with a LOW input. Set to 0 for
// active-HIGH boards or SSRs.
#define RELAY_ACTIVE_LOW    1

#define FAN_PWM_CHANNEL     2   // LEDC channel 0 / timer 0 belong to the camera clock
#define FAN_PWM_FREQ_HZ 25000
#define FAN_PWM_BITS        8

// Sensor names as they show up on the dashboard.
#define SENSOR_NAMES { "arriba", "medio", "abajo" }

// Timing
#define CONTROL_PERIOD_MS      5000UL
#define TELEMETRY_PERIOD_MS   30000UL
#define HTTP_TIMEOUT_MS        8000
#define WATCHDOG_TIMEOUT_S       60

#define DEVICE_NAME "curado-esp32cam"
