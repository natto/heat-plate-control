import RPi.GPIO as GPIO
import time

RELAY_PIN = 12

GPIO.setmode(GPIO.BCM)
GPIO.setup(RELAY_PIN, GPIO.OUT)

try:
    print("Fan ON for 5 seconds")
    GPIO.output(RELAY_PIN, GPIO.LOW)  # Relay ON
    time.sleep(5)

    print("Fan OFF")
    GPIO.output(RELAY_PIN, GPIO.HIGH)  # Relay OFF

finally:
    GPIO.cleanup()

