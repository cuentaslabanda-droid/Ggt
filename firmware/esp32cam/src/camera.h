#pragma once
#include "esp_camera.h"

// Initializes the OV2640 on an AI-Thinker ESP32-CAM.
bool cameraInit();

// Takes a still. With use_flash the onboard LED lights the (dark) fridge and
// a few frames are thrown away so auto exposure can settle. The caller must
// release the frame with esp_camera_fb_return().
camera_fb_t* cameraCapture(bool use_flash);
