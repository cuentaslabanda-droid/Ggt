#pragma once
// Minimal bit-banged I2C + SHT31-D driver.
//
// The ESP32-CAM camera already owns one hardware I2C port (SCCB), and the
// SHT31 only offers two addresses, so the three sensors live on two software
// buses that share SCL and each have their own SDA line. Bus traffic is
// sequential, and a bus whose SDA stays high never sees a START condition,
// so sharing SCL is safe.

#include <Arduino.h>

class SoftI2C {
 public:
  SoftI2C(int sda, int scl) : sda_(sda), scl_(scl) {}
  void begin();
  bool write(uint8_t addr, const uint8_t* data, size_t len);
  bool read(uint8_t addr, uint8_t* data, size_t len);

 private:
  void release(int pin);
  void pullLow(int pin);
  void start();
  void stop();
  bool writeByte(uint8_t b);
  uint8_t readByte(bool ack);
  void clockHigh();

  int sda_;
  int scl_;
};

class SHT31 {
 public:
  SHT31(SoftI2C& bus, uint8_t addr) : bus_(bus), addr_(addr) {}
  bool begin();                       // soft reset + clear status
  bool read(float& t, float& rh);     // single shot, high repeatability

 private:
  bool command(uint16_t cmd);
  static uint8_t crc8(const uint8_t* data, size_t len);

  SoftI2C& bus_;
  uint8_t addr_;
};
