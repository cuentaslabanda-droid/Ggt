// Cámara de curado de jamones - ESP32-CAM
//
// The ESP32 runs the climate loop on its own (so the ham is safe if the
// Raspberry Pi or the WiFi go down) and:
//   - reports telemetry to the Pi every 30 s and receives the setpoints back,
//   - keeps the last setpoints in flash (NVS),
//   - takes timelapse photos and uploads them to the Pi.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <esp_task_wdt.h>

#include "camera.h"
#include "config.h"
#include "controller.h"
#include "secrets.h"
#include "sht31.h"

namespace {

constexpr int kSensorCount = 3;
const char* const kSensorNames[kSensorCount] = SENSOR_NAMES;

SoftI2C busA(PIN_I2C_SDA_A, PIN_I2C_SCL);
SoftI2C busB(PIN_I2C_SDA_B, PIN_I2C_SCL);
SHT31 sensors[kSensorCount] = {SHT31(busA, 0x44), SHT31(busA, 0x45), SHT31(busB, 0x44)};
bool sensorReady[kSensorCount] = {};
uint8_t sensorFailures[kSensorCount] = {};

// Settings received from the Pi (persisted in NVS).
struct Settings {
  ControlConfig control;
  float t_offset[kSensorCount] = {};
  float rh_offset[kSensorCount] = {};
  uint32_t photo_interval_s = 900;  // 0 disables the timelapse
  bool flash = true;
  uint32_t version = 0;
} settings;

Controller controller(0);
SensorReading readings[kSensorCount];
ControlOutputs outputs;

Preferences prefs;
bool cameraOk = false;
uint32_t lastControlMs = 0;
uint32_t lastTelemetryMs = 0;
uint32_t lastPhotoMs = 0;
uint32_t lastWifiAttemptMs = 0;

uint32_t nowS() { return millis() / 1000; }

// --- Outputs ---------------------------------------------------------------

void setRelay(int pin, bool on) {
  const bool level = RELAY_ACTIVE_LOW ? !on : on;
  digitalWrite(pin, level ? HIGH : LOW);
}

void setFan(uint8_t percent) {
  const uint32_t max = (1u << FAN_PWM_BITS) - 1;
  ledcWrite(FAN_PWM_CHANNEL, max * percent / 100);
}

void applyOutputs(const ControlOutputs& o) {
  setRelay(PIN_RELAY_FRIDGE, o.compressor);
  setRelay(PIN_RELAY_HUMIDIFIER, o.humidifier);
  setFan(o.fan);
}

// --- Settings --------------------------------------------------------------

OutputMode parseMode(const char* s) {
  if (s && strcmp(s, "on") == 0) return OutputMode::On;
  if (s && strcmp(s, "off") == 0) return OutputMode::Off;
  return OutputMode::Auto;
}

// Missing keys keep their current value, so the Pi can send partial configs.
void applySettings(JsonObjectConst j) {
  ControlConfig& c = settings.control;
  c.temp_c = j["temp_c"] | c.temp_c;
  c.temp_hyst = j["temp_hyst"] | c.temp_hyst;
  c.rh = j["rh"] | c.rh;
  c.rh_hyst = j["rh_hyst"] | c.rh_hyst;
  c.dehum_margin = j["dehum_margin"] | c.dehum_margin;
  c.min_temp_c = j["min_temp_c"] | c.min_temp_c;
  c.fan_duty = j["fan_duty"] | c.fan_duty;
  c.fan_boost_duty = j["fan_boost_duty"] | c.fan_boost_duty;
  c.spread_t = j["spread_t"] | c.spread_t;
  c.spread_rh = j["spread_rh"] | c.spread_rh;
  c.comp_min_on = j["comp_min_on"] | c.comp_min_on;
  c.comp_min_off = j["comp_min_off"] | c.comp_min_off;
  c.hum_min_on = j["hum_min_on"] | c.hum_min_on;
  c.hum_min_off = j["hum_min_off"] | c.hum_min_off;
  c.hum_max_on = j["hum_max_on"] | c.hum_max_on;
  c.fan_manual = j["fan_manual"] | c.fan_manual;
  if (j["comp_mode"].is<const char*>()) c.comp_mode = parseMode(j["comp_mode"].as<const char*>());
  if (j["hum_mode"].is<const char*>()) c.hum_mode = parseMode(j["hum_mode"].as<const char*>());
  if (j["fan_mode"].is<const char*>()) c.fan_mode = parseMode(j["fan_mode"].as<const char*>());

  JsonArrayConst offsets = j["offsets"];
  for (int i = 0; i < kSensorCount && i < (int)offsets.size(); i++) {
    settings.t_offset[i] = offsets[i]["t"] | 0.0f;
    settings.rh_offset[i] = offsets[i]["rh"] | 0.0f;
  }
  settings.photo_interval_s = j["photo_interval"] | settings.photo_interval_s;
  settings.flash = j["flash"] | settings.flash;
}

void loadSettings() {
  prefs.begin("curado", true);
  const String stored = prefs.getString("cfg", "");
  settings.version = prefs.getUInt("ver", 0);
  prefs.end();
  if (stored.isEmpty()) return;
  JsonDocument doc;
  if (deserializeJson(doc, stored) == DeserializationError::Ok) {
    applySettings(doc.as<JsonObjectConst>());
    Serial.printf("Config v%u cargada de NVS\n", settings.version);
  }
}

void saveSettings(JsonObjectConst cfg, uint32_t version) {
  String s;
  serializeJson(cfg, s);
  prefs.begin("curado", false);
  prefs.putString("cfg", s);
  prefs.putUInt("ver", version);
  prefs.end();
}

// --- Sensors ---------------------------------------------------------------

void readSensors() {
  for (int i = 0; i < kSensorCount; i++) {
    SensorReading& r = readings[i];
    r.ok = false;
    if (!sensorReady[i]) sensorReady[i] = sensors[i].begin();
    if (!sensorReady[i]) continue;

    float t, rh;
    if (sensors[i].read(t, rh) && t > -20.0f && t < 60.0f) {
      r.ok = true;
      r.t = t + settings.t_offset[i];
      r.rh = constrain(rh + settings.rh_offset[i], 0.0f, 100.0f);
      sensorFailures[i] = 0;
    } else if (++sensorFailures[i] >= 3) {
      sensorReady[i] = false;  // reset the sensor on the next round
      sensorFailures[i] = 0;
    }
  }
}

// --- Network ---------------------------------------------------------------

void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    digitalWrite(PIN_STATUS_LED, HIGH);  // off (active LOW)
    return;
  }
  digitalWrite(PIN_STATUS_LED, LOW);
  if (millis() - lastWifiAttemptMs < 30000 && lastWifiAttemptMs != 0) return;
  lastWifiAttemptMs = millis();
  Serial.println("Conectando WiFi...");
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void sendTelemetry() {
  JsonDocument doc;
  doc["device"] = DEVICE_NAME;
  doc["uptime"] = nowS();
  doc["rssi"] = WiFi.RSSI();
  doc["cfg_version"] = settings.version;
  doc["camera"] = cameraOk;

  JsonArray arr = doc["sensors"].to<JsonArray>();
  for (int i = 0; i < kSensorCount; i++) {
    JsonObject s = arr.add<JsonObject>();
    s["name"] = kSensorNames[i];
    s["ok"] = readings[i].ok;
    if (readings[i].ok) {
      s["t"] = serialized(String(readings[i].t, 2));
      s["rh"] = serialized(String(readings[i].rh, 1));
    }
  }
  if (outputs.valid) {
    doc["t"] = serialized(String(outputs.avg_t, 2));
    doc["rh"] = serialized(String(outputs.avg_rh, 1));
  }
  JsonObject o = doc["outputs"].to<JsonObject>();
  o["compressor"] = outputs.compressor;
  o["humidifier"] = outputs.humidifier;
  o["fan"] = outputs.fan;
  o["cooling"] = outputs.cooling;
  o["dehumidifying"] = outputs.dehumidifying;

  JsonArray alarms = doc["alarms"].to<JsonArray>();
  for (uint32_t bit = 1; bit != 0 && bit <= ALARM_RH_LOW; bit <<= 1) {
    if (outputs.alarms & bit) alarms.add(alarmName(static_cast<Alarm>(bit)));
  }

  String body;
  serializeJson(doc, body);

  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.begin(String(SERVER_URL) + "/api/telemetry");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  const int code = http.POST(body);
  if (code != 200) {
    Serial.printf("Telemetría falló: %d\n", code);
    http.end();
    return;
  }

  JsonDocument resp;
  const DeserializationError err = deserializeJson(resp, http.getString());
  http.end();
  if (err) return;
  const uint32_t version = resp["version"] | 0u;
  JsonObjectConst cfg = resp["config"];
  if (!cfg.isNull() && version != settings.version) {
    applySettings(cfg);
    settings.version = version;
    saveSettings(cfg, version);
    Serial.printf("Nueva config v%u: %.1f C / %.0f %%HR\n", version, settings.control.temp_c,
                  settings.control.rh);
  }
}

void sendPhoto() {
  camera_fb_t* fb = cameraCapture(settings.flash);
  if (!fb) {
    Serial.println("Captura falló");
    return;
  }
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS * 2);
  http.begin(String(SERVER_URL) + "/api/photo");
  http.addHeader("Content-Type", "image/jpeg");
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  const int code = http.POST(fb->buf, fb->len);
  http.end();
  Serial.printf("Foto %u bytes -> %d\n", fb->len, code);
  esp_camera_fb_return(fb);
}

void controlStep() {
  readSensors();
  outputs = controller.update(settings.control, readings, kSensorCount, nowS());
  applyOutputs(outputs);
  Serial.printf("T=%.2f HR=%.1f comp=%d hum=%d fan=%u alarms=0x%x\n", outputs.avg_t,
                outputs.avg_rh, outputs.compressor, outputs.humidifier, outputs.fan,
                outputs.alarms);
}

}  // namespace

void setup() {
  // Outputs first, all off, before anything else can glitch them.
  pinMode(PIN_RELAY_FRIDGE, OUTPUT);
  pinMode(PIN_RELAY_HUMIDIFIER, OUTPUT);
  setRelay(PIN_RELAY_FRIDGE, false);
  setRelay(PIN_RELAY_HUMIDIFIER, false);
  pinMode(PIN_FLASH_LED, OUTPUT);
  digitalWrite(PIN_FLASH_LED, LOW);
  pinMode(PIN_STATUS_LED, OUTPUT);
  ledcSetup(FAN_PWM_CHANNEL, FAN_PWM_FREQ_HZ, FAN_PWM_BITS);
  ledcAttachPin(PIN_FAN_PWM, FAN_PWM_CHANNEL);
  setFan(0);

  // TX only: GPIO3 (RX) drives the humidifier relay.
  Serial.begin(115200);
  pinMode(PIN_RELAY_HUMIDIFIER, OUTPUT);
  setRelay(PIN_RELAY_HUMIDIFIER, false);
  Serial.println("\nCurado de jamones - arrancando");

  esp_task_wdt_init(WATCHDOG_TIMEOUT_S, true);
  esp_task_wdt_add(nullptr);

  loadSettings();
  busA.begin();
  busB.begin();

  cameraOk = cameraInit();
  Serial.printf("Cámara: %s\n", cameraOk ? "OK" : "ERROR");

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  ensureWifi();

  controlStep();
  lastControlMs = millis();
  lastPhotoMs = millis();
}

void loop() {
  esp_task_wdt_reset();
  const uint32_t now = millis();

  if (now - lastControlMs >= CONTROL_PERIOD_MS) {
    lastControlMs = now;
    controlStep();
  }

  ensureWifi();
  if (WiFi.status() != WL_CONNECTED) {
    delay(50);
    return;
  }

  if (now - lastTelemetryMs >= TELEMETRY_PERIOD_MS || lastTelemetryMs == 0) {
    lastTelemetryMs = now;
    sendTelemetry();
  }

  if (cameraOk && settings.photo_interval_s > 0 &&
      now - lastPhotoMs >= settings.photo_interval_s * 1000UL) {
    lastPhotoMs = now;
    sendPhoto();
  }

  delay(50);
}
