#!/usr/bin/env python3
"""
Hall Effect Sensor Crank Detector for Raspberry Pi

This script monitors a hall effect sensor connected to GPIO pin 17 to detect
crank revolutions (magnet passing). When detected, it publishes a message
to the MQTT topic 'sensors/crank'.

=============================================================================
HARDWARE SETUP
=============================================================================

Hall Effect Sensor (e.g., A3144, OH137, SS49E):
    - VCC → 3.3V (Pin 1) or 5V (Pin 2)
    - GND → Ground (Pin 6)
    - OUT → GPIO 17 (Pin 11)
    
    Note: Most hall effect sensors are open-collector/open-drain output,
    so they need a pull-up resistor. The Pi's internal pull-up is used.
    
    When magnet is present: Output goes LOW (pulled to ground)
    When magnet is absent:  Output goes HIGH (pull-up)

=============================================================================
INSTALLATION REQUIREMENTS
=============================================================================

System packages (apt-get):
    sudo apt-get update
    sudo apt-get install -y python3-pip python3-gpiozero python3-lgpio

Python packages (pip):
    pip3 install paho-mqtt RPi.GPIO

    Note: On Raspberry Pi OS Bullseye, lgpio is the recommended backend.
    If you have issues, try: pip3 install lgpio

Usage:
    sudo python3 crank_sensor.py

    Optional environment variables:
        MQTT_HOST      - MQTT broker host (default: localhost)
        MQTT_PORT      - MQTT broker port (default: 1883)
        GPIO_PIN       - GPIO pin number BCM (default: 17)
        DEBOUNCE_MS    - Debounce time in milliseconds (default: 50)
        MIN_INTERVAL_MS - Minimum interval between events in ms (default: 100)
        DEBUG          - Set to 1 for verbose output (default: 0)

=============================================================================
DEBOUNCE STRATEGY
=============================================================================

This script uses multiple debounce techniques:

1. Hardware debounce: Use a 0.1µF capacitor between OUT and GND if needed

2. Software debounce (implemented here):
   - Minimum interval between events (default 100ms = max 600 RPM)
   - State-based detection (only trigger on HIGH→LOW transition)
   - Configurable debounce time for edge detection

3. Timing validation:
   - Ignores events that occur faster than physically possible
   - At 400 RPM = 6.67 revolutions/second = 150ms per revolution
   - Default minimum interval of 100ms allows up to 600 RPM with margin

=============================================================================
"""

import os
import sys
import time
import logging
import signal
from datetime import datetime
from typing import Optional

# Try to import GPIO library
try:
    import RPi.GPIO as GPIO
    GPIO_LIBRARY = "RPi.GPIO"
except ImportError:
    try:
        from gpiozero import Button
        GPIO_LIBRARY = "gpiozero"
    except ImportError:
        print("ERROR: No GPIO library found!")
        print("Please install: sudo apt-get install python3-rpi.gpio")
        print("            or: pip3 install RPi.GPIO")
        sys.exit(1)

# MQTT
import paho.mqtt.client as mqtt

# =============================================================================
# CONFIGURATION
# =============================================================================

MQTT_HOST = os.environ.get('MQTT_HOST', 'localhost')
MQTT_PORT = int(os.environ.get('MQTT_PORT', '1883'))
MQTT_TOPIC = 'sensors/crank'

GPIO_PIN = int(os.environ.get('GPIO_PIN', '17'))
DEBOUNCE_MS = int(os.environ.get('DEBOUNCE_MS', '50'))
MIN_INTERVAL_MS = int(os.environ.get('MIN_INTERVAL_MS', '100'))  # Max ~600 RPM

DEBUG_MODE = os.environ.get('DEBUG', '0') == '1'

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# =============================================================================
# CRANK SENSOR CLASS
# =============================================================================

class CrankSensor:
    """Monitors hall effect sensor and publishes crank events to MQTT."""
    
    def __init__(self, gpio_pin: int, debounce_ms: int, min_interval_ms: int,
                 mqtt_host: str, mqtt_port: int, mqtt_topic: str):
        self.gpio_pin = gpio_pin
        self.debounce_ms = debounce_ms
        self.min_interval_ms = min_interval_ms
        self.min_interval_sec = min_interval_ms / 1000.0
        
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_topic = mqtt_topic
        
        self.mqtt_client: Optional[mqtt.Client] = None
        self.mqtt_connected = False
        
        self.last_event_time: float = 0
        self.event_count: int = 0
        self.ignored_count: int = 0
        self.running: bool = False
        
        # For RPM calculation display
        self.recent_intervals: list = []
        
    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """MQTT connection callback."""
        if rc == 0:
            self.mqtt_connected = True
            logger.info(f"[MQTT] Connected to broker at {self.mqtt_host}:{self.mqtt_port}")
        else:
            logger.error(f"[MQTT] Connection failed with code: {rc}")
    
    def _on_mqtt_disconnect(self, client, userdata, rc):
        """MQTT disconnection callback."""
        self.mqtt_connected = False
        logger.warning(f"[MQTT] Disconnected (rc={rc})")
    
    def _setup_mqtt(self):
        """Initialize MQTT client."""
        logger.info(f"[MQTT] Connecting to {self.mqtt_host}:{self.mqtt_port}...")
        
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self._on_mqtt_connect
        self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
        
        try:
            self.mqtt_client.connect(self.mqtt_host, self.mqtt_port, keepalive=60)
            self.mqtt_client.loop_start()
        except Exception as e:
            logger.error(f"[MQTT] Failed to connect: {e}")
            raise
    
    def _setup_gpio_rpigpio(self):
        """Setup GPIO using RPi.GPIO library."""
        logger.info(f"[GPIO] Setting up pin {self.gpio_pin} using RPi.GPIO")
        
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        
        # Setup pin with pull-up resistor
        GPIO.setup(self.gpio_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        
        # Add edge detection with debounce
        # We detect FALLING edge (HIGH→LOW) when magnet approaches
        GPIO.add_event_detect(
            self.gpio_pin,
            GPIO.FALLING,
            callback=self._on_magnet_detected,
            bouncetime=self.debounce_ms
        )
        
        logger.info(f"[GPIO] Pin {self.gpio_pin} configured:")
        logger.info(f"[GPIO]   - Pull-up: ENABLED")
        logger.info(f"[GPIO]   - Edge detect: FALLING")
        logger.info(f"[GPIO]   - Hardware debounce: {self.debounce_ms}ms")
    
    def _on_magnet_detected(self, channel):
        """Callback when magnet is detected (falling edge)."""
        current_time = time.monotonic()
        
        # Software debounce: check minimum interval
        interval = current_time - self.last_event_time
        
        if interval < self.min_interval_sec:
            self.ignored_count += 1
            if DEBUG_MODE:
                logger.debug(f"[SENSOR] Ignored bounce (interval={interval*1000:.1f}ms < {self.min_interval_ms}ms)")
            return
        
        # Valid event!
        self.event_count += 1
        self.last_event_time = current_time
        
        # Calculate instantaneous RPM for display
        if interval > 0 and interval < 10:  # Sanity check
            instant_rpm = 60.0 / interval
            self.recent_intervals.append(interval)
            # Keep only last 5 intervals for averaging
            if len(self.recent_intervals) > 5:
                self.recent_intervals.pop(0)
            avg_interval = sum(self.recent_intervals) / len(self.recent_intervals)
            avg_rpm = 60.0 / avg_interval
        else:
            instant_rpm = 0
            avg_rpm = 0
        
        # Publish to MQTT
        if self.mqtt_client and self.mqtt_connected:
            try:
                self.mqtt_client.publish(self.mqtt_topic, "1", qos=0)
                logger.info(f"[CRANK] Event #{self.event_count}: "
                           f"interval={interval*1000:.0f}ms, "
                           f"instant={instant_rpm:.0f}RPM, "
                           f"avg={avg_rpm:.0f}RPM")
            except Exception as e:
                logger.error(f"[MQTT] Publish failed: {e}")
        else:
            logger.warning(f"[CRANK] Event #{self.event_count} - MQTT not connected!")
    
    def start(self):
        """Start the sensor monitoring."""
        logger.info("╔═══════════════════════════════════════════════════════════════╗")
        logger.info("║       Crank Sensor - Hall Effect Detector                     ║")
        logger.info("╚═══════════════════════════════════════════════════════════════╝")
        logger.info("")
        logger.info("Configuration:")
        logger.info(f"  GPIO Pin:        {self.gpio_pin} (BCM)")
        logger.info(f"  Debounce:        {self.debounce_ms}ms")
        logger.info(f"  Min Interval:    {self.min_interval_ms}ms (max {60000/self.min_interval_ms:.0f} RPM)")
        logger.info(f"  MQTT Broker:     {self.mqtt_host}:{self.mqtt_port}")
        logger.info(f"  MQTT Topic:      {self.mqtt_topic}")
        logger.info(f"  GPIO Library:    {GPIO_LIBRARY}")
        logger.info("")
        
        # Setup MQTT
        self._setup_mqtt()
        
        # Wait for MQTT connection
        timeout = 5.0
        start = time.time()
        while not self.mqtt_connected and (time.time() - start) < timeout:
            time.sleep(0.1)
        
        if not self.mqtt_connected:
            logger.warning("[MQTT] Connection timeout - continuing anyway")
        
        # Setup GPIO
        if GPIO_LIBRARY == "RPi.GPIO":
            self._setup_gpio_rpigpio()
        else:
            logger.error("gpiozero implementation not included - please use RPi.GPIO")
            sys.exit(1)
        
        self.running = True
        
        logger.info("")
        logger.info("═══════════════════════════════════════════════════════════════")
        logger.info("Sensor active! Waiting for crank rotations...")
        logger.info("Press Ctrl+C to exit.")
        logger.info("═══════════════════════════════════════════════════════════════")
        logger.info("")
        
        # Main loop - just keep the program running
        # The actual detection happens in the callback
        try:
            while self.running:
                time.sleep(0.1)
                
                # Periodic status (every 30 seconds)
                if self.event_count > 0 and int(time.time()) % 30 == 0:
                    time.sleep(0.1)  # Prevent multiple prints
                    
        except KeyboardInterrupt:
            pass
    
    def stop(self):
        """Stop the sensor monitoring."""
        logger.info("")
        logger.info("Shutting down...")
        self.running = False
        
        # Cleanup GPIO
        if GPIO_LIBRARY == "RPi.GPIO":
            GPIO.remove_event_detect(self.gpio_pin)
            GPIO.cleanup(self.gpio_pin)
        
        # Cleanup MQTT
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        
        logger.info("")
        logger.info("╔═══════════════════════════════════════════════════════════════╗")
        logger.info("║                      SESSION SUMMARY                          ║")
        logger.info("╠═══════════════════════════════════════════════════════════════╣")
        logger.info(f"║  Total crank events:    {self.event_count:>6}                            ║")
        logger.info(f"║  Ignored (debounce):    {self.ignored_count:>6}                            ║")
        logger.info("╚═══════════════════════════════════════════════════════════════╝")


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Main entry point."""
    sensor = CrankSensor(
        gpio_pin=GPIO_PIN,
        debounce_ms=DEBOUNCE_MS,
        min_interval_ms=MIN_INTERVAL_MS,
        mqtt_host=MQTT_HOST,
        mqtt_port=MQTT_PORT,
        mqtt_topic=MQTT_TOPIC
    )
    
    # Handle signals for clean shutdown
    def signal_handler(signum, frame):
        sensor.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        sensor.start()
    finally:
        sensor.stop()


if __name__ == '__main__':
    main()
