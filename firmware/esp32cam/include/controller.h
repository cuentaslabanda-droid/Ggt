#pragma once
// Climate control logic. Plain C++ with no Arduino dependencies so it can be
// unit-tested on a PC (pio test -e native).

#include <stdint.h>

enum class OutputMode : uint8_t { Auto = 0, On = 1, Off = 2 };

struct ControlConfig {
  // Temperature: compressor starts above temp_c + temp_hyst, stops below
  // temp_c - temp_hyst.
  float temp_c = 14.0f;
  float temp_hyst = 0.5f;

  // Humidity: humidifier starts below rh - rh_hyst and stops at rh.
  // Above rh + rh_hyst the compressor is used to dry the air (it condenses
  // water on the evaporator) as long as the temperature stays above
  // temp_c - dehum_margin; it stops drying once rh is reached.
  float rh = 75.0f;
  float rh_hyst = 3.0f;
  float dehum_margin = 1.5f;

  // Never cool below this, whatever the setpoints say (freezing protection).
  float min_temp_c = 1.0f;

  // Airflow (fan duty in %). The fan always runs at fan_duty and goes to
  // fan_boost_duty while the compressor or humidifier run, or when the
  // three sensors disagree by more than spread_t / spread_rh (stratification).
  uint8_t fan_duty = 30;
  uint8_t fan_boost_duty = 70;
  float spread_t = 1.5f;
  float spread_rh = 6.0f;

  // Compressor protection against short cycling (seconds).
  uint32_t comp_min_on = 120;
  uint32_t comp_min_off = 300;

  // Humidifier runs in bursts so the sensors can catch up with the mist.
  uint32_t hum_min_on = 10;
  uint32_t hum_min_off = 30;
  uint32_t hum_max_on = 120;

  // Manual overrides, mainly for wiring tests.
  OutputMode comp_mode = OutputMode::Auto;
  OutputMode hum_mode = OutputMode::Auto;
  OutputMode fan_mode = OutputMode::Auto;  // On = fan_manual %, Off = 0 %
  uint8_t fan_manual = 50;
};

struct SensorReading {
  bool ok = false;
  float t = 0.0f;
  float rh = 0.0f;
};

enum Alarm : uint32_t {
  ALARM_NONE = 0,
  ALARM_SENSOR_FAIL = 1u << 0,     // no valid sensor -> outputs off
  ALARM_SENSOR_PARTIAL = 1u << 1,  // some sensor missing
  ALARM_TEMP_HIGH = 1u << 2,
  ALARM_TEMP_LOW = 1u << 3,
  ALARM_RH_HIGH = 1u << 4,
  ALARM_RH_LOW = 1u << 5,
};

// Distance from the setpoint that raises an alarm.
constexpr float ALARM_TEMP_DELTA = 4.0f;
constexpr float ALARM_RH_DELTA = 10.0f;

struct ControlOutputs {
  bool compressor = false;
  bool humidifier = false;
  uint8_t fan = 0;
  bool cooling = false;       // compressor wanted for temperature
  bool dehumidifying = false; // compressor wanted for humidity
  bool valid = false;         // avg_t / avg_rh are meaningful
  float avg_t = 0.0f;
  float avg_rh = 0.0f;
  uint32_t alarms = ALARM_NONE;
};

class Controller {
 public:
  // now_s: monotonic seconds (millis() / 1000). At boot the compressor is
  // considered to have just stopped, so it waits comp_min_off before its
  // first start; this protects it after a power cut.
  explicit Controller(uint32_t now_s = 0);

  ControlOutputs update(const ControlConfig& cfg, const SensorReading* readings,
                        int count, uint32_t now_s);

 private:
  bool comp_on_ = false;
  uint32_t comp_changed_ = 0;
  bool hum_on_ = false;
  uint32_t hum_changed_ = 0;
  bool cool_demand_ = false;
  bool dehum_demand_ = false;
  bool hum_demand_ = false;
};

const char* alarmName(Alarm a);
