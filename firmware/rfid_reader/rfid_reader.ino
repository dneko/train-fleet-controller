// ESP32 Dev Module + ELECHOUSE PN532 V3. Switch 1 ON, switch 2 OFF.
// VCC -> 3V3, GND -> GND, SDA -> GPIO21, SCL -> GPIO22.
// Install Seeed Studio PN532-Arduino 1.1.0+. No secrets.h or NTP needed.
#include <Wire.h>
#include <PN532_I2C.h>
#include <PN532.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include <esp_system.h>

// EDIT THESE SETTINGS BEFORE UPLOADING:
const char *WIFI_SSID = "YOUR_WIFI_NAME";
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char *READER_TOKEN = "YOUR_SHARED_SECRET"; // Same as TRAIN_READER_TOKEN on Mac.
const char *CONTROLLER_ID = "train-fleet";
const char *SERVER_URL = ""; // Automatic discovery; optional http://MAC_IP:8765/events.

// Most classic ESP32 Dev Module boards have a controllable LED on GPIO2.
// Change if your board uses another pin. -1 disables identification.
constexpr int IDENTIFY_LED_PIN = 2;
constexpr bool LED_ACTIVE_LOW = false;
constexpr uint32_t STATUS_INTERVAL_MS = 100; // Nominal 10 reports per second.

PN532_I2C readerConnection(Wire);
PN532 reader(readerConnection);
char nodeId[32]; // Permanent hardware-derived identity; gate assignment is on the Mac.
char bootId[17];
String lastUid;
uint32_t lastSeen = 0;
uint32_t sequence = 0;
volatile bool readerReady = false;
volatile uint32_t lastReaderPoll = 0;
volatile uint32_t identifyRequested = 0;
volatile uint32_t identifyAck = 0;
volatile uint32_t identifyUntil = 0;
volatile bool segmentOccupied = false;
struct Event { char payload[280]; uint32_t detectedMs; };
QueueHandle_t outgoing;

String findController() {
  Serial.println("Looking for train controller...");
  int count = MDNS.queryService("trainrfid", "tcp");
  String url;
  int matches = 0;
  for (int i = 0; i < count; ++i) {
    if (MDNS.txt(i, "controller") != CONTROLLER_ID || MDNS.txt(i, "version") != "1") continue;
    IPAddress address = MDNS.address(i);
    if (address == IPAddress(0, 0, 0, 0) || MDNS.port(i) == 0) continue;
    ++matches;
    url = String("http://") + address.toString() + ":" + String(MDNS.port(i));
  }
  if (matches > 1) {
    Serial.println("Multiple matching controllers. Close extras or use a unique CONTROLLER_ID.");
    return "";
  }
  if (matches == 1) Serial.printf("Controller found: %s\n", url.c_str());
  else Serial.println("No controller found yet. Check Mac app, Wi-Fi, and firewall.");
  return url;
}

int postJson(HTTPClient &http, WiFiClient &client, const String &url, const char *payload, bool status) {
  http.setConnectTimeout(300);
  http.setTimeout(300);
  http.setReuse(true);
  if (!http.begin(client, url)) return -1;
  const char *headers[] = {"X-Identify-Id", "X-Identify-Ms", "X-Segment-Occupied"};
  if (status) http.collectHeaders(headers, 3);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", String("Bearer ") + READER_TOKEN);
  int code = http.POST((uint8_t *)payload, strlen(payload));
  if (status && code == 200) {
    String occupied = http.header("X-Segment-Occupied");
    if (occupied == "1" || occupied == "0") segmentOccupied = occupied == "1";
    uint32_t command = (uint32_t)http.header("X-Identify-Id").toInt();
    uint32_t duration = (uint32_t)http.header("X-Identify-Ms").toInt();
    // The server returns remaining time, not a fresh 10 seconds on each heartbeat.
    if (command && duration && command != identifyAck) {
      if (duration > 10000) duration = 10000;
      identifyUntil = millis() + duration;
      identifyRequested = command;
    }
  }
  if (code > 0) http.getString(); // Consume response before reusing the connection.
  http.end();
  return code;
}

void networkTask(void *) {
  WiFiClient client;
  HTTPClient http; // Keep alive across reports; destructor would close the socket.
  bool connected = false;
  bool mdnsReady = false;
  uint32_t lastReconnect = 0;
  uint32_t lastDiscovery = millis() - 5000UL;
  uint32_t lastStatus = millis() - STATUS_INTERVAL_MS;
  int failures = 0;
  String base = SERVER_URL;
  if (base.endsWith("/events")) base.remove(base.length() - 7);
  while (base.endsWith("/")) base.remove(base.length() - 1);
  const bool automatic = base.isEmpty();
  for (;;) {
    bool online = WiFi.status() == WL_CONNECTED;
    if (online != connected) {
      connected = online;
      client.stop();
      if (online) {
        Serial.printf("Wi-Fi connected: %s\n", WiFi.localIP().toString().c_str());
        lastDiscovery = millis() - 5000UL;
      } else {
        Serial.println("Wi-Fi disconnected; reconnecting...");
        if (automatic) base = "";
        if (mdnsReady) MDNS.end();
        mdnsReady = false;
      }
    }
    if (!online) {
      if (millis() - lastReconnect > 5000) {
        lastReconnect = millis();
        Serial.println("Waiting for 2.4 GHz Wi-Fi...");
        WiFi.reconnect();
      }
      // Do not replay detections after a network outage using misleading receipt times.
      Event discarded;
      while (xQueueReceive(outgoing, &discarded, 0) == pdTRUE) Serial.println("EVENT LOST: Wi-Fi offline");
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }
    // Discover only when needed; a blocking mDNS query must not interrupt healthy 10 Hz status.
    if (automatic && base.isEmpty() && millis() - lastDiscovery >= 5000UL) {
      if (!mdnsReady) mdnsReady = MDNS.begin(nodeId);
      if (mdnsReady) base = findController();
      lastDiscovery = millis();
    }
    if (!base.isEmpty() && millis() - lastStatus >= STATUS_INTERVAL_MS) {
      lastStatus = millis();
      char payload[320];
      snprintf(payload, sizeof(payload),
        "{\"node\":\"%s\",\"pn532\":%s,\"rssi\":%d,\"uptime_ms\":%lu,\"poll_age_ms\":%lu,\"identify_ack\":%lu,\"led\":\"%s\"}",
        nodeId, readerReady ? "true" : "false", WiFi.RSSI(), (unsigned long)millis(),
        (unsigned long)(millis() - lastReaderPoll), (unsigned long)identifyAck,
        IDENTIFY_LED_PIN >= 0 ? "blink" : "none");
      int code = postJson(http, client, base + "/status", payload, true);
      if (code == 200) failures = 0;
      else {
        ++failures;
        if (failures == 1) Serial.printf("Status failed (%d). Check controller and shared token.\n", code);
        client.stop();
        if (failures >= 3 && automatic) {
          base = "";
          lastDiscovery = millis() - 5000UL;
          failures = 0;
        }
      }
    }
    // Limit event sending per iteration so heartbeat reports cannot be starved.
    Event event;
    if (xQueueReceive(outgoing, &event, 0) == pdTRUE) {
      if (base.isEmpty() || millis() - event.detectedMs > 1000UL) {
        Serial.println("EVENT LOST: controller unavailable or detection delayed");
      } else {
        int code = postJson(http, client, base + "/events", event.payload, false);
        if (code == 202) Serial.println("Tag delivered to Mac");
        else Serial.printf("EVENT LOST: delivery failed (%d)\n", code);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

void setup() {
  Serial.begin(115200);
  uint64_t chip = ESP.getEfuseMac();
  snprintf(nodeId, sizeof(nodeId), "esp32-%04lx%08lx", (unsigned long)(chip >> 32), (unsigned long)chip);
  snprintf(bootId, sizeof(bootId), "%08lx%08lx", (unsigned long)esp_random(), (unsigned long)esp_random());
  Serial.printf("Sensor ID: %s (assign its gate on the Mac)\n", nodeId);
  if (IDENTIFY_LED_PIN >= 0) {
    pinMode(IDENTIFY_LED_PIN, OUTPUT);
    digitalWrite(IDENTIFY_LED_PIN, LED_ACTIVE_LOW ? HIGH : LOW);
  }
  Wire.begin(21, 22);
  reader.begin();
  outgoing = xQueueCreate(32, sizeof(Event));
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  if (!outgoing || xTaskCreate(networkTask, "network", 8192, nullptr, 1, nullptr) != pdPASS) {
    while (true) { Serial.println("Cannot start network task"); delay(2000); }
  }
}

void loop() {
  uint32_t now = millis();
  if (IDENTIFY_LED_PIN >= 0) {
    bool active = identifyRequested && (int32_t)(identifyUntil - now) > 0;
    // Identify temporarily overrides occupancy, then restores its latest state.
    // Network failures preserve the last Mac command; no timer releases a segment.
    bool lit = active ? ((now / 200) % 2 == 0) : segmentOccupied;
    digitalWrite(IDENTIFY_LED_PIN, (lit != LED_ACTIVE_LOW) ? HIGH : LOW);
    if (active) identifyAck = identifyRequested;
  }
  // Recheck hardware every two seconds and recover if the PN532 is reconnected.
  static uint32_t lastHealth = millis() - 2000UL;
  if (now - lastHealth >= 2000UL) {
    lastHealth = now;
    bool found = reader.getFirmwareVersion() != 0;
    if (found && !readerReady) {
      found = reader.SAMConfig();
      if (found) reader.setPassiveActivationRetries(0x00);
    }
    if (found != readerReady) Serial.println(found ? "PN532 ready" : "PN532 missing");
    readerReady = found;
  }
  lastReaderPoll = millis();
  if (!readerReady) { delay(5); return; }
  uint8_t uid[10], length = 0;
  if (!reader.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &length, 30)) return;
  uint32_t detected = millis();
  String current;
  for (uint8_t i = 0; i < length; ++i) {
    if (uid[i] < 16) current += "0";
    current += String(uid[i], HEX);
  }
  current.toUpperCase();
  if (current != lastUid || detected - lastSeen > 1000) {
    Event event;
    event.detectedMs = detected;
    snprintf(event.payload, sizeof(event.payload),
      "{\"uid\":\"%s\",\"node\":\"%s\",\"boot\":\"%s\",\"seq\":%lu,\"t_ms\":%lu,\"unix_ms\":null}",
      current.c_str(), nodeId, bootId, (unsigned long)++sequence, (unsigned long)detected);
    Serial.println(event.payload);
    if (xQueueSend(outgoing, &event, 0) != pdTRUE) Serial.println("EVENT LOST: queue full");
  }
  lastUid = current;
  lastSeen = detected;
}
