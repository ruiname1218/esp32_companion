/*
 * ESP32-S3 Voice Assistant - Real-time Conversation Mode
 * 
 * Starts listening immediately on power up.
 * Uses VAD (Voice Activity Detection) on server side for turn detection.
 * 
 * Hardware:
 * INMP441 (Mic):     SCK->GPIO4, WS->GPIO5, SD->GPIO6
 * MAX98357A (Spk):   BCLK->GPIO15, LRC->GPIO16, DIN->GPIO17
 */

#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <driver/i2s.h>
#include "config.h"

// I2S Microphone (INMP441)
#define I2S_MIC_PORT      I2S_NUM_0
#define I2S_MIC_SCK       4
#define I2S_MIC_WS        5
#define I2S_MIC_SD        6
#define MIC_SAMPLE_RATE   24000  // 24kHz for Realtime API

// I2S Speaker (MAX98357A)
#define I2S_SPK_PORT      I2S_NUM_1
#define I2S_SPK_BCLK      15
#define I2S_SPK_LRC       16
#define I2S_SPK_DIN       17

// Audio buffers
#define MIC_BUFFER_SIZE   512
#define AUDIO_BUFFER_SIZE (512 * 1024)  // 512KB ring buffer for TTS
#define BUFFERING_THRESHOLD (32 * 1024)

uint8_t* audioRingBuffer = nullptr;
volatile size_t writePos = 0;
volatile size_t readPos = 0;
volatile size_t totalBytesReceived = 0;
volatile bool isPlaying = false;
volatile bool audioComplete = false;
volatile bool bufferingComplete = false;

// WebSocket
WebSocketsClient webSocket;
bool wsConnected = false;

// State machine
enum State {
  STATE_CONNECTING,
  STATE_LISTENING,    // Continuously streaming audio to server
  STATE_PLAYING       // Playing TTS response
};
volatile State currentState = STATE_CONNECTING;

// Timing
unsigned long lastAudioSendTime = 0;
const unsigned long AUDIO_SEND_INTERVAL = 20;  // Send audio every 20ms

void setup() {
  Serial.begin(115200);
  delay(2000);
  
  Serial.println();
  Serial.println("================================================");
  Serial.println("  ESP32-S3 Voice Assistant - Realtime Mode");
  Serial.println("================================================");
  Serial.printf("Free heap: %d bytes\n", ESP.getFreeHeap());
  Serial.printf("PSRAM: %d bytes\n", ESP.getPsramSize());
  
  // Allocate buffers in PSRAM
  audioRingBuffer = (uint8_t*)ps_malloc(AUDIO_BUFFER_SIZE);
  if (!audioRingBuffer) {
    Serial.println("ERROR: Failed to allocate audio buffer!");
    while(1) delay(1000);
  }
  memset(audioRingBuffer, 0, AUDIO_BUFFER_SIZE);
  
  // Connect WiFi
  connectWiFi();
  
  // Initialize I2S
  if (!initMicrophoneI2S()) {
    Serial.println("ERROR: Microphone I2S init failed!");
    while(1) delay(1000);
  }
  
  if (!initSpeakerI2S()) {
    Serial.println("ERROR: Speaker I2S init failed!");
    while(1) delay(1000);
  }
  
  // Connect WebSocket
  connectWebSocket();
  
  Serial.println("\nReady - Listening will start automatically...");
}

void loop() {
  // Check WiFi connection
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi lost! Reconnecting...");
    wsConnected = false;
    currentState = STATE_CONNECTING;
    WiFi.reconnect();
    delay(1000);
    return;
  }
  
  webSocket.loop();
  
  // State machine
  switch (currentState) {
    case STATE_CONNECTING:
      // Waiting for WebSocket connection
      break;
      
    case STATE_LISTENING:
      // Continuously stream audio to server
      if (wsConnected && millis() - lastAudioSendTime >= AUDIO_SEND_INTERVAL) {
        sendMicrophoneAudio();
        lastAudioSendTime = millis();
      }
      break;
      
    case STATE_PLAYING:
      // Play TTS response
      playStreamingAudio();
      break;
  }
  
  // Memory monitoring (every 30 seconds)
  static unsigned long lastMemCheck = 0;
  if (millis() - lastMemCheck > 30000) {
    Serial.printf("Free heap: %d bytes\n", ESP.getFreeHeap());
    lastMemCheck = millis();
  }
  
  delay(1);
}

void connectWiFi() {
  Serial.print("Connecting to WiFi: ");
  Serial.println(WIFI_SSID);
  
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi connected!");
    Serial.print("IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\nWiFi failed!");
    ESP.restart();
  }
}

void connectWebSocket() {
  Serial.println("Connecting to server...");
  
  webSocket.begin(SERVER_HOST, SERVER_PORT, "/ws");
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(3000);
  webSocket.enableHeartbeat(30000, 10000, 3);
}

void webSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch(type) {
    case WStype_DISCONNECTED:
      Serial.println("[WS] Disconnected");
      wsConnected = false;
      currentState = STATE_CONNECTING;
      break;
      
    case WStype_CONNECTED:
      Serial.println("[WS] Connected!");
      wsConnected = true;
      currentState = STATE_LISTENING;
      Serial.println("\n*** Listening - Speak now! ***\n");
      break;
      
    case WStype_TEXT: {
      StaticJsonDocument<1024> doc;
      if (deserializeJson(doc, payload, length)) {
        Serial.println("[WS] JSON parse error");
        return;
      }
      
      const char* event = doc["event"];
      
      if (strcmp(event, "transcription") == 0) {
        Serial.println("\n========== YOU SAID ==========");
        Serial.println(doc["text"].as<const char*>());
        Serial.println("==============================");
      }
      else if (strcmp(event, "response") == 0) {
        Serial.println("\n========== AI RESPONSE ==========");
        Serial.println(doc["text"].as<const char*>());
        Serial.println("=================================");
      }
      else if (strcmp(event, "audio_start") == 0) {
        Serial.println("--- TTS Audio starting ---");
        
        // Stop listening, start playing
        currentState = STATE_PLAYING;
        
        // Reset ring buffer
        writePos = 0;
        readPos = 0;
        memset(audioRingBuffer, 0, AUDIO_BUFFER_SIZE);
        audioComplete = false;
        bufferingComplete = false;
        totalBytesReceived = 0;
        isPlaying = true;
        
        // Set sample rate
        uint32_t sampleRate = doc["sample_rate"] | 44100;
        i2s_set_sample_rates(I2S_SPK_PORT, sampleRate);
      }
      else if (strcmp(event, "audio_end") == 0) {
        Serial.printf("--- TTS Audio end (%d bytes) ---\n", totalBytesReceived);
        audioComplete = true;
      }
      else if (strcmp(event, "listening") == 0) {
        // Server signals ready for more audio
        if (currentState != STATE_LISTENING) {
          currentState = STATE_LISTENING;
          Serial.println("\n*** Listening - Speak now! ***\n");
        }
      }
      else if (strcmp(event, "error") == 0) {
        Serial.printf("[WS] Error: %s\n", doc["message"].as<const char*>());
      }
      break;
    }
    
    case WStype_BIN:
      // TTS audio data
      if (isPlaying && length > 0) {
        size_t spaceAvailable;
        if (writePos >= readPos) {
          spaceAvailable = AUDIO_BUFFER_SIZE - writePos + readPos - 1;
        } else {
          spaceAvailable = readPos - writePos - 1;
        }
        
        size_t toWrite = min(length, spaceAvailable);
        for (size_t i = 0; i < toWrite; i++) {
          audioRingBuffer[writePos] = payload[i];
          writePos = (writePos + 1) % AUDIO_BUFFER_SIZE;
        }
        
        totalBytesReceived += toWrite;
        
        if (!bufferingComplete && totalBytesReceived >= BUFFERING_THRESHOLD) {
          bufferingComplete = true;
        }
      }
      break;
      
    default:
      break;
  }
}

void sendMicrophoneAudio() {
  int16_t buffer[MIC_BUFFER_SIZE];
  size_t bytesRead = 0;
  
  esp_err_t result = i2s_read(I2S_MIC_PORT, buffer, sizeof(buffer), &bytesRead, 0);
  
  if (result == ESP_OK && bytesRead > 0) {
    // Apply software gain (3x amplification)
    const int GAIN = 3;
    size_t samples = bytesRead / sizeof(int16_t);
    for (size_t i = 0; i < samples; i++) {
      int32_t amplified = (int32_t)buffer[i] * GAIN;
      // Clamp to int16_t range to prevent clipping distortion
      if (amplified > 32767) amplified = 32767;
      if (amplified < -32768) amplified = -32768;
      buffer[i] = (int16_t)amplified;
    }
    
    // Send as binary WebSocket message
    webSocket.sendBIN((uint8_t*)buffer, bytesRead);
  }
}

void playStreamingAudio() {
  if (!bufferingComplete && !audioComplete) {
    return;
  }
  
  size_t available;
  if (writePos >= readPos) {
    available = writePos - readPos;
  } else {
    available = AUDIO_BUFFER_SIZE - readPos + writePos;
  }
  
  const size_t MIN_CHUNK = 512;
  
  if (available >= MIN_CHUNK || (audioComplete && available > 0)) {
    size_t toPlay = min(available, (size_t)1024);
    toPlay = toPlay & ~1;  // Ensure even
    
    if (toPlay > 0) {
      uint8_t tempBuffer[1024];
      for (size_t i = 0; i < toPlay; i++) {
        tempBuffer[i] = audioRingBuffer[readPos];
        readPos = (readPos + 1) % AUDIO_BUFFER_SIZE;
      }
      
      size_t bytesWritten = 0;
      // Use short timeout to allow webSocket.loop() to process heartbeats
      i2s_write(I2S_SPK_PORT, tempBuffer, toPlay, &bytesWritten, pdMS_TO_TICKS(20));
    }
  }
  
  // Check if playback complete
  size_t remaining;
  if (writePos >= readPos) {
    remaining = writePos - readPos;
  } else {
    remaining = AUDIO_BUFFER_SIZE - readPos + writePos;
  }
  
  if (audioComplete && remaining == 0) {
    delay(200);  // Shorter delay for DMA
    i2s_zero_dma_buffer(I2S_SPK_PORT);
    
    isPlaying = false;
    bufferingComplete = false;
    currentState = STATE_LISTENING;
    Serial.println("\n*** Listening - Speak now! ***\n");
  }
}

bool initMicrophoneI2S() {
  Serial.println("Initializing Microphone I2S (24kHz)...");
  
  i2s_config_t config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = MIC_SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = MIC_BUFFER_SIZE,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };
  
  i2s_pin_config_t pins = {
    .bck_io_num = I2S_MIC_SCK,
    .ws_io_num = I2S_MIC_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = I2S_MIC_SD
  };
  
  if (i2s_driver_install(I2S_MIC_PORT, &config, 0, NULL) != ESP_OK) return false;
  if (i2s_set_pin(I2S_MIC_PORT, &pins) != ESP_OK) return false;
  
  Serial.println("Microphone OK!");
  return true;
}

bool initSpeakerI2S() {
  Serial.println("Initializing Speaker I2S...");
  
  i2s_config_t config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = 44100,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 16,
    .dma_buf_len = 1024,
    .use_apll = true,
    .tx_desc_auto_clear = true,
    .fixed_mclk = 0
  };
  
  i2s_pin_config_t pins = {
    .bck_io_num = I2S_SPK_BCLK,
    .ws_io_num = I2S_SPK_LRC,
    .data_out_num = I2S_SPK_DIN,
    .data_in_num = I2S_PIN_NO_CHANGE
  };
  
  if (i2s_driver_install(I2S_SPK_PORT, &config, 0, NULL) != ESP_OK) return false;
  if (i2s_set_pin(I2S_SPK_PORT, &pins) != ESP_OK) return false;
  
  Serial.println("Speaker OK!");
  return true;
}
