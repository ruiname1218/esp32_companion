/*
 * ESP32-S3 Voice Assistant - Real-time Conversation Mode
 * 
 * Features:
 * - WiFi Provisioning via Captive Portal
 * - Real-time voice conversation with VAD
 * - NVS storage for persistent configuration
 * 
 * Hardware:
 * INMP441 (Mic):     SCK->GPIO4, WS->GPIO5, SD->GPIO6
 * MAX98357A (Spk):   BCLK->GPIO15, LRC->GPIO16, DIN->GPIO17
 * Button:            GPIO0 (Boot button for provisioning mode)
 */

#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <driver/i2s.h>
#include "wifi_portal.h"
#include "config.h"  // For SERVER_HOST and SERVER_PORT

// Button for provisioning mode
#define PROVISION_BUTTON 0  // Boot button (GPIO0)
#define BUTTON_HOLD_TIME 3000  // 3 seconds to enter provisioning mode

// AP Mode settings
const char* AP_SSID = "Magoo-Setup";
const char* AP_PASS = "";  // Open network for easy access

// Preferences (NVS) - WiFi credentials only
Preferences preferences;
String savedSSID = "";
String savedPassword = "";
bool provisioningMode = false;

// Web server for Captive Portal
WebServer webServer(80);
DNSServer dnsServer;
const byte DNS_PORT = 53;

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
  delay(1000);
  
  // Initialize button
  pinMode(PROVISION_BUTTON, INPUT_PULLUP);
  
  Serial.println();
  Serial.println("================================================");
  Serial.println("  Magoo - AI Voice Companion");
  Serial.println("================================================");
  Serial.printf("Free heap: %d bytes\n", ESP.getFreeHeap());
  Serial.printf("PSRAM: %d bytes\n", ESP.getPsramSize());
  
  // Load saved configuration from NVS
  loadConfiguration();
  
  // Check if button is held for provisioning mode
  Serial.println("Hold BOOT button for 3 seconds to enter setup mode...");
  unsigned long buttonStart = millis();
  while (digitalRead(PROVISION_BUTTON) == LOW) {
    if (millis() - buttonStart > BUTTON_HOLD_TIME) {
      Serial.println("Entering WiFi Setup Mode!");
      provisioningMode = true;
      break;
    }
    delay(100);
  }
  
  // Check if we have saved WiFi credentials
  if (savedSSID.length() == 0 || provisioningMode) {
    Serial.println("No WiFi configured or setup requested");
    startProvisioningMode();
    return;  // Don't continue setup, run portal in loop
  }
  
  // Allocate audio buffers in PSRAM
  audioRingBuffer = (uint8_t*)ps_malloc(AUDIO_BUFFER_SIZE);
  if (!audioRingBuffer) {
    Serial.println("ERROR: Failed to allocate audio buffer!");
    while(1) delay(1000);
  }
  memset(audioRingBuffer, 0, AUDIO_BUFFER_SIZE);
  
  // Connect to saved WiFi
  if (!connectToWiFi()) {
    Serial.println("WiFi connection failed, entering setup mode");
    startProvisioningMode();
    return;
  }
  
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
  // Provisioning mode - run Captive Portal
  if (provisioningMode) {
    dnsServer.processNextRequest();
    webServer.handleClient();
    delay(1);
    return;
  }
  
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

// ================== WiFi Provisioning Functions ==================

void loadConfiguration() {
  preferences.begin("magoo", true);  // Read-only
  savedSSID = preferences.getString("ssid", "");
  savedPassword = preferences.getString("password", "");
  preferences.end();
  
  Serial.println("Configuration loaded from NVS:");
  Serial.printf("  SSID: %s\n", savedSSID.c_str());
  Serial.printf("  Server: %s:%d (from config.h)\n", SERVER_HOST, SERVER_PORT);
}

void saveConfiguration(String ssid, String password) {
  preferences.begin("magoo", false);  // Read-write
  preferences.putString("ssid", ssid);
  preferences.putString("password", password);
  preferences.end();
  
  Serial.println("WiFi configuration saved to NVS");
}

bool connectToWiFi() {
  Serial.print("Connecting to WiFi: ");
  Serial.println(savedSSID);
  
  WiFi.mode(WIFI_STA);
  WiFi.begin(savedSSID.c_str(), savedPassword.c_str());
  
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
    return true;
  } else {
    Serial.println("\nWiFi connection failed!");
    return false;
  }
}

void startProvisioningMode() {
  provisioningMode = true;
  
  Serial.println("\n========================================");
  Serial.println("  WiFi Setup Mode");
  Serial.println("========================================");
  Serial.println("Connect to WiFi: Magoo-Setup");
  Serial.println("Then open any website to configure");
  Serial.println("========================================\n");
  
  // Start AP Mode
  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASS);
  delay(100);
  
  IPAddress apIP = WiFi.softAPIP();
  Serial.print("AP IP address: ");
  Serial.println(apIP);
  
  // Start DNS server for Captive Portal
  dnsServer.start(DNS_PORT, "*", apIP);
  
  // Setup web server routes
  webServer.on("/", HTTP_GET, handleRoot);
  webServer.on("/scan", HTTP_GET, handleScan);
  webServer.on("/save", HTTP_POST, handleSave);
  webServer.on("/generate_204", HTTP_GET, handleRoot);  // Android captive portal
  webServer.on("/fwlink", HTTP_GET, handleRoot);  // Microsoft captive portal
  webServer.onNotFound(handleRoot);  // Redirect all to root
  
  webServer.begin();
  Serial.println("Captive Portal started");
}

void handleRoot() {
  webServer.send(200, "text/html", PORTAL_HTML);
}

void handleScan() {
  int n = WiFi.scanNetworks();
  String json = "{\"networks\":[";
  
  for (int i = 0; i < n; i++) {
    if (i > 0) json += ",";
    json += "{\"ssid\":\"" + WiFi.SSID(i) + "\",\"rssi\":" + String(WiFi.RSSI(i)) + "}";
  }
  json += "]}";
  
  webServer.send(200, "application/json", json);
  WiFi.scanDelete();
}

void handleSave() {
  if (webServer.hasArg("plain")) {
    StaticJsonDocument<256> doc;
    DeserializationError error = deserializeJson(doc, webServer.arg("plain"));
    
    if (error) {
      webServer.send(400, "application/json", "{\"success\":false,\"message\":\"Invalid JSON\"}");
      return;
    }
    
    String ssid = doc["ssid"] | "";
    String password = doc["password"] | "";
    
    if (ssid.length() == 0) {
      webServer.send(400, "application/json", "{\"success\":false,\"message\":\"SSID is required\"}");
      return;
    }
    
    // Save WiFi configuration
    saveConfiguration(ssid, password);
    
    webServer.send(200, "application/json", "{\"success\":true}");
    
    // Restart after short delay
    delay(1000);
    ESP.restart();
  } else {
    webServer.send(400, "application/json", "{\"success\":false,\"message\":\"No data\"}");
  }
}

// ================== WebSocket Functions ==================


// Connect to WebSocket server with Device ID (MAC)
void connectWebSocket() {
  // Get MAC Address as Device ID
  String mac = WiFi.macAddress();
  mac.replace(":", ""); // Remove colons to make it cleaner
  
  Serial.printf("Connecting to WebSocket: %s:%d\n", SERVER_HOST, SERVER_PORT);
  Serial.printf("Device ID: %s\n", mac.c_str());
  
  // Construct path with query param
  String path = "/ws?device_id=" + mac;
  
  #ifdef USE_SSL
    // SSL/WSS connection for Deno Deploy / production
    Serial.println("[WS] Using SSL (wss://)");
    webSocket.beginSSL(SERVER_HOST, SERVER_PORT, path.c_str());
  #else
    // Plain WebSocket for local development
    Serial.println("[WS] Using plain WebSocket (ws://)");
    webSocket.begin(SERVER_HOST, SERVER_PORT, path.c_str());
  #endif
  
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
    // Apply software gain (5x amplification for better sensitivity)
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
