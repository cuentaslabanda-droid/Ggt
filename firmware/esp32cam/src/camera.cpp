#include "camera.h"

#include <Arduino.h>

#include "config.h"

bool cameraInit() {
  camera_config_t c = {};
  c.pin_pwdn = 32;
  c.pin_reset = -1;
  c.pin_xclk = 0;
  c.pin_sccb_sda = 26;
  c.pin_sccb_scl = 27;
  c.pin_d7 = 35;
  c.pin_d6 = 34;
  c.pin_d5 = 39;
  c.pin_d4 = 36;
  c.pin_d3 = 21;
  c.pin_d2 = 19;
  c.pin_d1 = 18;
  c.pin_d0 = 5;
  c.pin_vsync = 25;
  c.pin_href = 23;
  c.pin_pclk = 22;
  c.xclk_freq_hz = 20000000;
  c.ledc_timer = LEDC_TIMER_0;
  c.ledc_channel = LEDC_CHANNEL_0;
  c.pixel_format = PIXFORMAT_JPEG;
  c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  c.fb_count = 1;
  if (psramFound()) {
    c.frame_size = FRAMESIZE_UXGA;  // 1600x1200
    c.jpeg_quality = 10;
    c.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    c.frame_size = FRAMESIZE_SVGA;
    c.jpeg_quality = 12;
    c.fb_location = CAMERA_FB_IN_DRAM;
  }

  if (esp_camera_init(&c) != ESP_OK) return false;

  sensor_t* s = esp_camera_sensor_get();
  s->set_whitebal(s, 1);
  s->set_exposure_ctrl(s, 1);
  s->set_gain_ctrl(s, 1);
  return true;
}

camera_fb_t* cameraCapture(bool use_flash) {
  if (use_flash) {
    digitalWrite(PIN_FLASH_LED, HIGH);
    delay(150);
  }
  // Discard frames captured before the light changed so AE/AWB can adapt.
  for (int i = 0; i < 3; i++) {
    camera_fb_t* stale = esp_camera_fb_get();
    if (stale) esp_camera_fb_return(stale);
  }
  camera_fb_t* fb = esp_camera_fb_get();
  digitalWrite(PIN_FLASH_LED, LOW);
  return fb;
}
