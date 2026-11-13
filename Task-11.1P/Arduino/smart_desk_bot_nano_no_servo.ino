#include <Wire.h>
#include <BH1750.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

const byte LIGHT_PWM_PIN = 5;

const byte BUTTON_PIN = 2;

const unsigned long SENSOR_INTERVAL_MS = 500;
const unsigned long SENSOR_RETRY_MS = 10000;
const unsigned long STATUS_INTERVAL_MS = 2000;
const unsigned long LIGHT_FADE_INTERVAL_MS = 15;

const unsigned long PI_TIMEOUT_MS = 5000;

const int SENSOR_FAIL_BRIGHTNESS = 35;

const float LUX_LEVEL_1 = 10.0;
const float LUX_LEVEL_2 = 30.0;
const float LUX_LEVEL_3 = 70.0;
const float LUX_LEVEL_4 = 120.0;
const float LUX_LEVEL_5 = 200.0;
const float LUX_LEVEL_6 = 300.0;

const unsigned long DEBOUNCE_MS = 40;

const byte SERIAL_COMMAND_BUFFER_SIZE = 80;

BH1750 lightMeter;

enum LightMode {
  AUTO_MODE,
  MANUAL_MODE
};

LightMode lightMode = AUTO_MODE;

bool sensorAvailable = false;
bool hasFilteredLux = false;
bool lightIsOn = false;

float currentLux = NAN;

int brightnessTrim = 0;
int manualBrightness = 0;

int currentBrightness = 0;
int targetBrightness = 0;

byte consecutiveSensorErrors = 0;

unsigned long lastSensorRead = 0;
unsigned long lastSensorRetry = 0;
unsigned long lastStatusSend = 0;
unsigned long lastFadeUpdate = 0;

unsigned long lastHeartbeat = 0;

bool heartbeatReceived = false;

bool lastButtonReading = HIGH;
bool stableButtonState = HIGH;

unsigned long lastButtonChange = 0;

char inputBuffer[SERIAL_COMMAND_BUFFER_SIZE];
byte inputIndex = 0;

int clampPercentage(int value) {
  return constrain(value, 0, 100);
}

bool parseIntegerValue(const char *input, long &result) {
  if (input == nullptr || *input == '\0') {
    return false;
  }

  char *endPointer;

  long value = strtol(input, &endPointer, 10);

  if (endPointer == input) {
    return false;
  }

  while (*endPointer == ' ' || *endPointer == '\t') {
    endPointer++;
  }

  if (*endPointer != '\0') {
    return false;
  }

  result = value;
  return true;
}

int brightnessFromLux(float lux) {

  if (!sensorAvailable || isnan(lux)) {
    return SENSOR_FAIL_BRIGHTNESS;
  }

  if (lux <= LUX_LEVEL_1) {
    return 100;
  }

  if (lux <= LUX_LEVEL_2) {
    return 85;
  }

  if (lux <= LUX_LEVEL_3) {
    return 70;
  }

  if (lux <= LUX_LEVEL_4) {
    return 50;
  }

  if (lux <= LUX_LEVEL_5) {
    return 30;
  }

  if (lux <= LUX_LEVEL_6) {
    return 15;
  }

  return 0;
}

const char *getLightModeName() {

  if (lightMode == MANUAL_MODE) {
    return "MANUAL";
  }

  if (brightnessTrim != 0) {
    return "AUTO_ADJUSTED";
  }

  return "AUTO";
}

void calculateTargetBrightness() {

  if (lightMode == MANUAL_MODE) {
    targetBrightness = manualBrightness;
    return;
  }

  int automaticBrightness =
    brightnessFromLux(currentLux);

  targetBrightness = clampPercentage(
    automaticBrightness + brightnessTrim
  );
}

void writeLightPWM() {

  int pwmValue = map(
    currentBrightness,
    0,
    100,
    0,
    255
  );

  analogWrite(
    LIGHT_PWM_PIN,
    pwmValue
  );

  lightIsOn = currentBrightness > 0;
}

void updateLightOutput(bool immediate = false) {

  calculateTargetBrightness();

  unsigned long now = millis();

  if (immediate) {
    currentBrightness = targetBrightness;
    writeLightPWM();
    lastFadeUpdate = now;
    return;
  }

  if (now - lastFadeUpdate < LIGHT_FADE_INTERVAL_MS) {
    return;
  }

  lastFadeUpdate = now;

  if (currentBrightness < targetBrightness) {
    currentBrightness += 3;

    if (currentBrightness > targetBrightness) {
      currentBrightness = targetBrightness;
    }
  }

  else if (currentBrightness > targetBrightness) {
    currentBrightness -= 3;

    if (currentBrightness < targetBrightness) {
      currentBrightness = targetBrightness;
    }
  }

  writeLightPWM();
}

void setManualBrightness(int percentage) {

  lightMode = MANUAL_MODE;

  manualBrightness = clampPercentage(percentage);

  updateLightOutput(true);

  Serial.print("OK,LIGHT,LEVEL,");
  Serial.println(currentBrightness);
}

void restoreAutomaticLighting() {

  lightMode = AUTO_MODE;
  brightnessTrim = 0;

  updateLightOutput(true);

  Serial.println("OK,LIGHT,AUTO");
}

void adjustAutomaticBrightness(int adjustment) {

  lightMode = AUTO_MODE;

  brightnessTrim = constrain(
    brightnessTrim + adjustment,
    -100,
    100
  );

  updateLightOutput(true);

  Serial.print("OK,LIGHT,LEVEL,");
  Serial.println(currentBrightness);
}

bool initializeLightSensor() {

  bool success = lightMeter.begin(
    BH1750::CONTINUOUS_HIGH_RES_MODE
  );

  if (!success) {
    sensorAvailable = false;
    currentLux = NAN;
    hasFilteredLux = false;

    Serial.println("ERR,BH1750,NOT_FOUND");

    return false;
  }

  sensorAvailable = true;
  consecutiveSensorErrors = 0;

  float initialReading = lightMeter.readLightLevel();

  if (!isnan(initialReading) && initialReading >= 0) {

    currentLux = initialReading;
    hasFilteredLux = true;

    Serial.println("OK,BH1750,READY");

  } else {

    currentLux = NAN;
    hasFilteredLux = false;

    Serial.println("ERR,BH1750_INITIAL_READ");
  }

  return true;
}

void readLightSensor() {

  unsigned long now = millis();

  if (!sensorAvailable) {

    if (now - lastSensorRetry >= SENSOR_RETRY_MS) {

      lastSensorRetry = now;

      if (initializeLightSensor()) {
        Serial.println("EVENT,BH1750,RECOVERED");
      }
    }

    return;
  }

  if (now - lastSensorRead < SENSOR_INTERVAL_MS) {
    return;
  }

  lastSensorRead = now;

  float reading = lightMeter.readLightLevel();

  if (isnan(reading) || reading < 0) {

    consecutiveSensorErrors++;

    Serial.println("ERR,BH1750_READ");

    if (consecutiveSensorErrors >= 3) {

      sensorAvailable = false;
      currentLux = NAN;
      hasFilteredLux = false;

      Serial.println(
        "EVENT,BH1750,FAILED,FALLBACK_BRIGHTNESS"
      );

      updateLightOutput(false);
    }

    return;
  }

  consecutiveSensorErrors = 0;

  if (!hasFilteredLux) {

    currentLux = reading;
    hasFilteredLux = true;

  } else {

    currentLux =
      (0.70f * currentLux) +
      (0.30f * reading);
  }

  updateLightOutput(false);
}

void sendStatus() {

  Serial.print("DATA,");

  if (sensorAvailable && !isnan(currentLux)) {
    Serial.print(currentLux, 1);
  } else {
    Serial.print("SENSOR_ERROR");
  }

  Serial.print(",");

  Serial.print(lightIsOn ? "ON" : "OFF");

  Serial.print(",");

  Serial.print(getLightModeName());

  Serial.print(",");

  if (!sensorAvailable) {
    Serial.print("SENSOR_ERROR");
  } else if (currentLux < 80.0) {
    Serial.print("DARK");
  } else {
    Serial.print("BRIGHT");
  }

  Serial.print(",");

  Serial.print(-1);

  Serial.print(",");

  Serial.print("NOT_USED");

  Serial.print(",");

  Serial.println(currentBrightness);
}

void processCommand(char *command) {

  while (*command == ' ' || *command == '\t') {
    command++;
  }

  size_t length = strlen(command);

  while (
    length > 0 &&
    (
      command[length - 1] == ' ' ||
      command[length - 1] == '\t'
    )
  ) {
    command[--length] = '\0';
  }

  for (size_t i = 0; i < length; i++) {

    if (command[i] >= 'a' && command[i] <= 'z') {
      command[i] -= 32;
    }
  }

  if (length == 0) {
    return;
  }

  lastHeartbeat = millis();
  heartbeatReceived = true;

  if (strcmp(command, "PING") == 0) {
    Serial.println("PONG");
  }

  else if (strcmp(command, "LIGHT AUTO") == 0) {
    restoreAutomaticLighting();
  }

  else if (strcmp(command, "LIGHT ON") == 0) {
    setManualBrightness(100);
  }

  else if (strcmp(command, "LIGHT OFF") == 0) {
    setManualBrightness(0);
  }

  else if (strncmp(command, "LIGHT LEVEL ", 12) == 0) {

    long percentage;

    if (!parseIntegerValue(command + 12, percentage)) {
      Serial.println("ERR,LIGHT,INVALID_LEVEL");
    }

    else if (percentage < 0 || percentage > 100) {
      Serial.println("ERR,LIGHT,LEVEL_OUT_OF_RANGE");
    }

    else {
      setManualBrightness((int)percentage);
    }
  }

  else if (strncmp(command, "LIGHT ADJUST ", 13) == 0) {

    long adjustment;

    if (!parseIntegerValue(command + 13, adjustment)) {
      Serial.println("ERR,LIGHT,INVALID_ADJUSTMENT");
    }

    else if (adjustment < -100 || adjustment > 100) {
      Serial.println("ERR,LIGHT,ADJUSTMENT_OUT_OF_RANGE");
    }

    else {
      adjustAutomaticBrightness((int)adjustment);
    }
  }

  else if (strcmp(command, "STATUS") == 0) {
    sendStatus();
  }

  else {
    Serial.println("ERR,UNKNOWN_COMMAND");
  }
}

void readSerialCommands() {

  while (Serial.available() > 0) {

    char incoming = Serial.read();

    if (incoming == '\n') {

      inputBuffer[inputIndex] = '\0';

      processCommand(inputBuffer);

      inputIndex = 0;
    }

    else if (incoming != '\r') {

      if (inputIndex < SERIAL_COMMAND_BUFFER_SIZE - 1) {

        inputBuffer[inputIndex++] = incoming;

      } else {

        inputIndex = 0;

        Serial.println("ERR,COMMAND_TOO_LONG");
      }
    }
  }
}

void checkButton() {

  bool reading = digitalRead(BUTTON_PIN);

  if (reading != lastButtonReading) {

    lastButtonChange = millis();
    lastButtonReading = reading;
  }

  if (millis() - lastButtonChange >= DEBOUNCE_MS) {

    if (reading != stableButtonState) {

      stableButtonState = reading;

      if (stableButtonState == LOW) {
        Serial.println("EVENT,BUTTON_PRESSED");
      }
    }
  }
}

void checkPiConnection() {

  if (
    heartbeatReceived &&
    millis() - lastHeartbeat > PI_TIMEOUT_MS
  ) {

    heartbeatReceived = false;

    lightMode = AUTO_MODE;
    brightnessTrim = 0;

    updateLightOutput(false);

    Serial.println(
      "EVENT,PI_DISCONNECTED,AUTO_LIGHT_RESTORED"
    );
  }
}

void setup() {

  Serial.begin(115200);

  pinMode(LIGHT_PWM_PIN, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  analogWriteResolution(8);

  analogWrite(LIGHT_PWM_PIN, 0);

  Wire.begin();

  lightMode = AUTO_MODE;
  brightnessTrim = 0;

  initializeLightSensor();

  updateLightOutput(true);

  Serial.println("SMART_DESK_BOT_NANO_READY");

  sendStatus();
}

void loop() {

  readSerialCommands();

  readLightSensor();

  checkButton();

  checkPiConnection();

  updateLightOutput(false);

  if (millis() - lastStatusSend >= STATUS_INTERVAL_MS) {

    lastStatusSend = millis();

    sendStatus();
  }
}
