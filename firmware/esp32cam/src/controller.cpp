#include "controller.h"

namespace {

// Applies minimum on/off times to a switching output. Returns the new state.
bool applyMinTimes(bool on, bool want, uint32_t& changed_at, uint32_t now_s,
                   uint32_t min_on, uint32_t min_off) {
  if (want == on) return on;
  const uint32_t elapsed = now_s - changed_at;
  if (on && elapsed < min_on) return on;
  if (!on && elapsed < min_off) return on;
  changed_at = now_s;
  return want;
}

float maxf(float a, float b) { return a > b ? a : b; }

}  // namespace

Controller::Controller(uint32_t now_s) : comp_changed_(now_s), hum_changed_(now_s) {}

ControlOutputs Controller::update(const ControlConfig& cfg, const SensorReading* readings,
                                  int count, uint32_t now_s) {
  ControlOutputs out;

  // --- Sensor fusion: average of the valid sensors -----------------------
  int valid = 0;
  float sum_t = 0, sum_rh = 0;
  float min_t = 0, max_t = 0, min_rh = 0, max_rh = 0;
  for (int i = 0; i < count; i++) {
    const SensorReading& r = readings[i];
    if (!r.ok) continue;
    if (valid == 0) {
      min_t = max_t = r.t;
      min_rh = max_rh = r.rh;
    } else {
      if (r.t < min_t) min_t = r.t;
      if (r.t > max_t) max_t = r.t;
      if (r.rh < min_rh) min_rh = r.rh;
      if (r.rh > max_rh) max_rh = r.rh;
    }
    sum_t += r.t;
    sum_rh += r.rh;
    valid++;
  }

  bool want_comp = false;
  bool want_hum = false;
  bool stratified = false;

  if (valid == 0) {
    // Fail safe: without measurements keep the fridge and humidifier off and
    // only circulate air.
    out.alarms |= ALARM_SENSOR_FAIL;
    cool_demand_ = dehum_demand_ = hum_demand_ = false;
  } else {
    if (valid < count) out.alarms |= ALARM_SENSOR_PARTIAL;
    const float t = sum_t / valid;
    const float rh = sum_rh / valid;
    out.valid = true;
    out.avg_t = t;
    out.avg_rh = rh;

    // Temperature (hysteresis band around the setpoint).
    if (t > cfg.temp_c + cfg.temp_hyst) cool_demand_ = true;
    else if (t < cfg.temp_c - cfg.temp_hyst) cool_demand_ = false;

    // Too humid -> dry with the compressor.
    if (rh > cfg.rh + cfg.rh_hyst) dehum_demand_ = true;
    else if (rh <= cfg.rh) dehum_demand_ = false;

    // Too dry -> humidifier.
    if (rh < cfg.rh - cfg.rh_hyst) hum_demand_ = true;
    else if (rh >= cfg.rh) hum_demand_ = false;

    const float dehum_floor = maxf(cfg.temp_c - cfg.dehum_margin, cfg.min_temp_c);
    const bool dehum_allowed = t > dehum_floor;

    want_comp = (cool_demand_ || (dehum_demand_ && dehum_allowed)) && t > cfg.min_temp_c;
    want_hum = hum_demand_ && !dehum_demand_;

    out.cooling = cool_demand_;
    out.dehumidifying = dehum_demand_ && dehum_allowed;

    stratified = valid > 1 && (max_t - min_t > cfg.spread_t || max_rh - min_rh > cfg.spread_rh);

    if (t > cfg.temp_c + ALARM_TEMP_DELTA) out.alarms |= ALARM_TEMP_HIGH;
    if (t < cfg.temp_c - ALARM_TEMP_DELTA || t < 0.0f) out.alarms |= ALARM_TEMP_LOW;
    if (rh > cfg.rh + ALARM_RH_DELTA) out.alarms |= ALARM_RH_HIGH;
    if (rh < cfg.rh - ALARM_RH_DELTA) out.alarms |= ALARM_RH_LOW;
  }

  // --- Compressor -------------------------------------------------------
  if (cfg.comp_mode == OutputMode::On) want_comp = true;
  if (cfg.comp_mode == OutputMode::Off) want_comp = false;
  if (valid == 0 && comp_on_ && cfg.comp_mode != OutputMode::On) {
    // Losing every sensor stops the compressor right away; min_on only
    // protects against short cycling, not against running blind.
    comp_on_ = false;
    comp_changed_ = now_s;
  }
  comp_on_ = applyMinTimes(comp_on_, want_comp, comp_changed_, now_s, cfg.comp_min_on,
                           cfg.comp_min_off);

  // --- Humidifier (bursts limited by hum_max_on) -------------------------
  if (cfg.hum_mode == OutputMode::On) want_hum = true;
  if (cfg.hum_mode == OutputMode::Off) want_hum = false;
  if (hum_on_ && cfg.hum_mode == OutputMode::Auto && now_s - hum_changed_ >= cfg.hum_max_on) {
    want_hum = false;
  }
  hum_on_ = applyMinTimes(hum_on_, want_hum, hum_changed_, now_s, cfg.hum_min_on,
                          cfg.hum_min_off);

  // --- Fan --------------------------------------------------------------
  uint8_t fan = cfg.fan_duty;
  if (comp_on_ || hum_on_ || stratified) fan = cfg.fan_boost_duty > fan ? cfg.fan_boost_duty : fan;
  if (cfg.fan_mode == OutputMode::On) fan = cfg.fan_manual;
  if (cfg.fan_mode == OutputMode::Off) fan = 0;
  if (fan > 100) fan = 100;

  out.compressor = comp_on_;
  out.humidifier = hum_on_;
  out.fan = fan;
  return out;
}

const char* alarmName(Alarm a) {
  switch (a) {
    case ALARM_SENSOR_FAIL: return "SENSOR_FAIL";
    case ALARM_SENSOR_PARTIAL: return "SENSOR_PARTIAL";
    case ALARM_TEMP_HIGH: return "TEMP_HIGH";
    case ALARM_TEMP_LOW: return "TEMP_LOW";
    case ALARM_RH_HIGH: return "RH_HIGH";
    case ALARM_RH_LOW: return "RH_LOW";
    default: return "UNKNOWN";
  }
}
