#include "sht31.h"

namespace {
constexpr uint32_t kHalfPeriodUs = 5;      // ~100 kHz
constexpr uint32_t kStretchTimeoutUs = 2000;
}  // namespace

// --- SoftI2C ---------------------------------------------------------------
// Lines are driven open-drain: "high" means releasing the pin and letting the
// pull-up (10k on the SHT31 breakout + internal pull-up) raise it.

void SoftI2C::release(int pin) { pinMode(pin, INPUT_PULLUP); }

void SoftI2C::pullLow(int pin) {
  digitalWrite(pin, LOW);
  pinMode(pin, OUTPUT);
}

void SoftI2C::clockHigh() {
  release(scl_);
  const uint32_t t0 = micros();
  while (digitalRead(scl_) == LOW && micros() - t0 < kStretchTimeoutUs) {
  }
  delayMicroseconds(kHalfPeriodUs);
}

void SoftI2C::begin() {
  release(sda_);
  release(scl_);
  // Free a slave that might be stuck mid-byte holding SDA low.
  for (int i = 0; i < 9 && digitalRead(sda_) == LOW; i++) {
    pullLow(scl_);
    delayMicroseconds(kHalfPeriodUs);
    clockHigh();
  }
  stop();
}

void SoftI2C::start() {
  release(sda_);
  clockHigh();
  pullLow(sda_);
  delayMicroseconds(kHalfPeriodUs);
  pullLow(scl_);
  delayMicroseconds(kHalfPeriodUs);
}

void SoftI2C::stop() {
  pullLow(sda_);
  delayMicroseconds(kHalfPeriodUs);
  clockHigh();
  release(sda_);
  delayMicroseconds(kHalfPeriodUs);
}

bool SoftI2C::writeByte(uint8_t b) {
  for (int i = 7; i >= 0; i--) {
    if (b & (1 << i)) release(sda_);
    else pullLow(sda_);
    delayMicroseconds(kHalfPeriodUs);
    clockHigh();
    pullLow(scl_);
  }
  release(sda_);
  delayMicroseconds(kHalfPeriodUs);
  clockHigh();
  const bool ack = digitalRead(sda_) == LOW;
  pullLow(scl_);
  delayMicroseconds(kHalfPeriodUs);
  return ack;
}

uint8_t SoftI2C::readByte(bool ack) {
  uint8_t b = 0;
  release(sda_);
  for (int i = 0; i < 8; i++) {
    delayMicroseconds(kHalfPeriodUs);
    clockHigh();
    b = (b << 1) | (digitalRead(sda_) == HIGH ? 1 : 0);
    pullLow(scl_);
  }
  if (ack) pullLow(sda_);
  else release(sda_);
  delayMicroseconds(kHalfPeriodUs);
  clockHigh();
  pullLow(scl_);
  release(sda_);
  delayMicroseconds(kHalfPeriodUs);
  return b;
}

bool SoftI2C::write(uint8_t addr, const uint8_t* data, size_t len) {
  start();
  bool ok = writeByte(addr << 1);
  for (size_t i = 0; ok && i < len; i++) ok = writeByte(data[i]);
  stop();
  return ok;
}

bool SoftI2C::read(uint8_t addr, uint8_t* data, size_t len) {
  start();
  const bool ok = writeByte((addr << 1) | 1);
  if (ok) {
    for (size_t i = 0; i < len; i++) data[i] = readByte(i + 1 < len);
  }
  stop();
  return ok;
}

// --- SHT31 -----------------------------------------------------------------

bool SHT31::command(uint16_t cmd) {
  const uint8_t buf[2] = {static_cast<uint8_t>(cmd >> 8), static_cast<uint8_t>(cmd & 0xFF)};
  return bus_.write(addr_, buf, 2);
}

uint8_t SHT31::crc8(const uint8_t* data, size_t len) {
  uint8_t crc = 0xFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (int b = 0; b < 8; b++) crc = (crc & 0x80) ? (crc << 1) ^ 0x31 : (crc << 1);
  }
  return crc;
}

bool SHT31::begin() {
  if (!command(0x30A2)) return false;  // soft reset
  delay(2);
  return command(0x3041);              // clear status register
}

bool SHT31::read(float& t, float& rh) {
  // Single shot, high repeatability, clock stretching disabled.
  if (!command(0x2400)) return false;
  delay(16);
  uint8_t buf[6];
  if (!bus_.read(addr_, buf, sizeof(buf))) return false;
  if (crc8(buf, 2) != buf[2] || crc8(buf + 3, 2) != buf[5]) return false;
  const uint16_t raw_t = (buf[0] << 8) | buf[1];
  const uint16_t raw_rh = (buf[3] << 8) | buf[4];
  t = -45.0f + 175.0f * raw_t / 65535.0f;
  rh = 100.0f * raw_rh / 65535.0f;
  return true;
}
