// Unit tests for the climate logic: pio test -e native
#include <unity.h>

#include "controller.h"

namespace {

ControlConfig baseConfig() {
  ControlConfig c;
  c.temp_c = 14;
  c.temp_hyst = 0.5;
  c.rh = 75;
  c.rh_hyst = 3;
  c.comp_min_on = 120;
  c.comp_min_off = 300;
  c.hum_min_on = 10;
  c.hum_min_off = 30;
  c.hum_max_on = 120;
  return c;
}

SensorReading r3[3];

void setAll(float t, float rh) {
  for (auto& r : r3) {
    r.ok = true;
    r.t = t;
    r.rh = rh;
  }
}

}  // namespace

void setUp() { setAll(14, 75); }
void tearDown() {}

void test_compressor_waits_min_off_after_boot() {
  Controller c(0);
  auto cfg = baseConfig();
  setAll(16, 75);
  TEST_ASSERT_FALSE(c.update(cfg, r3, 3, 10).compressor);
  TEST_ASSERT_TRUE(c.update(cfg, r3, 3, 300).compressor);
}

void test_temperature_hysteresis_and_min_on() {
  Controller c(0);
  auto cfg = baseConfig();
  setAll(15, 75);
  TEST_ASSERT_TRUE(c.update(cfg, r3, 3, 400).compressor);
  setAll(13.4, 75);  // below the band, but min_on not reached yet
  TEST_ASSERT_TRUE(c.update(cfg, r3, 3, 450).compressor);
  TEST_ASSERT_FALSE(c.update(cfg, r3, 3, 520).compressor);
  setAll(14.3, 75);  // inside the band: stays off
  TEST_ASSERT_FALSE(c.update(cfg, r3, 3, 900).compressor);
}

void test_dehumidify_with_compressor_until_margin() {
  Controller c(0);
  auto cfg = baseConfig();
  setAll(14, 80);
  auto o = c.update(cfg, r3, 3, 400);
  TEST_ASSERT_TRUE(o.compressor);
  TEST_ASSERT_TRUE(o.dehumidifying);
  TEST_ASSERT_FALSE(o.humidifier);
  setAll(12.4, 80);  // colder than temp_c - dehum_margin: stop drying
  o = c.update(cfg, r3, 3, 600);
  TEST_ASSERT_FALSE(o.compressor);
  TEST_ASSERT_FALSE(o.dehumidifying);
}

void test_humidifier_bursts() {
  Controller c(0);
  auto cfg = baseConfig();
  setAll(14, 70);
  TEST_ASSERT_TRUE(c.update(cfg, r3, 3, 100).humidifier);
  TEST_ASSERT_TRUE(c.update(cfg, r3, 3, 200).humidifier);
  TEST_ASSERT_FALSE(c.update(cfg, r3, 3, 221).humidifier);  // hum_max_on reached
  TEST_ASSERT_FALSE(c.update(cfg, r3, 3, 240).humidifier);  // hum_min_off
  TEST_ASSERT_TRUE(c.update(cfg, r3, 3, 252).humidifier);
  setAll(14, 75);
  TEST_ASSERT_FALSE(c.update(cfg, r3, 3, 300).humidifier);
}

void test_fan_boost_and_stratification() {
  Controller c(0);
  auto cfg = baseConfig();
  TEST_ASSERT_EQUAL_UINT8(cfg.fan_duty, c.update(cfg, r3, 3, 10).fan);
  r3[0].t = 16;  // 2 C between top and bottom
  TEST_ASSERT_EQUAL_UINT8(cfg.fan_boost_duty, c.update(cfg, r3, 3, 20).fan);
  cfg.fan_mode = OutputMode::Off;
  TEST_ASSERT_EQUAL_UINT8(0, c.update(cfg, r3, 3, 30).fan);
}

void test_sensor_failure_is_fail_safe() {
  Controller c(0);
  auto cfg = baseConfig();
  setAll(16, 70);
  auto o = c.update(cfg, r3, 3, 400);
  TEST_ASSERT_TRUE(o.compressor);
  for (auto& r : r3) r.ok = false;
  o = c.update(cfg, r3, 3, 410);
  TEST_ASSERT_FALSE(o.compressor);
  TEST_ASSERT_FALSE(o.humidifier);
  TEST_ASSERT_TRUE(o.alarms & ALARM_SENSOR_FAIL);
}

void test_partial_sensors_average_valid_ones() {
  Controller c(0);
  auto cfg = baseConfig();
  r3[0] = {true, 13, 70};
  r3[1] = {false, 99, 99};
  r3[2] = {true, 15, 80};
  auto o = c.update(cfg, r3, 3, 10);
  TEST_ASSERT_FLOAT_WITHIN(0.01, 14, o.avg_t);
  TEST_ASSERT_FLOAT_WITHIN(0.01, 75, o.avg_rh);
  TEST_ASSERT_TRUE(o.alarms & ALARM_SENSOR_PARTIAL);
}

void test_never_cools_below_min_temp() {
  Controller c(0);
  auto cfg = baseConfig();
  cfg.temp_c = 1;
  cfg.min_temp_c = 1;
  setAll(0.8, 95);
  TEST_ASSERT_FALSE(c.update(cfg, r3, 3, 400).compressor);
}

void test_manual_overrides() {
  Controller c(0);
  auto cfg = baseConfig();
  cfg.hum_mode = OutputMode::On;
  cfg.comp_mode = OutputMode::On;
  auto o = c.update(cfg, r3, 3, 400);
  TEST_ASSERT_TRUE(o.humidifier);
  TEST_ASSERT_TRUE(o.compressor);
  o = c.update(cfg, r3, 3, 1000);  // hum_max_on does not apply to manual mode
  TEST_ASSERT_TRUE(o.humidifier);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_compressor_waits_min_off_after_boot);
  RUN_TEST(test_temperature_hysteresis_and_min_on);
  RUN_TEST(test_dehumidify_with_compressor_until_margin);
  RUN_TEST(test_humidifier_bursts);
  RUN_TEST(test_fan_boost_and_stratification);
  RUN_TEST(test_sensor_failure_is_fail_safe);
  RUN_TEST(test_partial_sensors_average_valid_ones);
  RUN_TEST(test_never_cools_below_min_temp);
  RUN_TEST(test_manual_overrides);
  return UNITY_END();
}
