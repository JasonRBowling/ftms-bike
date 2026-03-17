# ftms-bike
Turn a Raspberry Pi into a Bluetooth Low Energy (BLE) cycling sensor for "dumb" exercise bikes so you can use it in games. Uses a hall effect sensor to detect crank revolutions and broadcasts cadence, power, and speed to fitness apps like Zwift, MyWhoosh, and Kinomap via the FTMS (Fitness Machine Service) standard. Tested on Raspberry Pi Bullseye, with Kinomap and MyWhoosh confirmed working well. 

Small script watches GPIO pin to be pulled to ground by Hall effect sensor, fires MQTT message each time the crank arm passes the sensor. Main program computes power curve from cadence using an adjustable curve. This is about as simple as it gets, but is extremely inexpensive and is quite enjoyable to ride with in the games.

# Pi Crank FTMS

Turn a Raspberry Pi into a Bluetooth Low Energy (BLE) fitness sensor for your bike. This project reads crank revolutions from a hall effect sensor and broadcasts cadence, power, and speed to fitness apps like Zwift, MyWhoosh, Kinomap, and other FTMS-compatible applications.

## Features

- **BLE FTMS Peripheral** - Broadcasts as a standard Fitness Machine Service device
- **Real-time Cadence** - Calculated from crank revolution timing with EMA smoothing
- **Estimated Power** - Derived from cadence using the formula: `P = 0.000274 × C³`
- **Estimated Speed** - Derived from cadence: `S = C × 0.35 km/h`
- **Auto-start on Boot** - Systemd services start everything automatically
- **Debounced Input** - Hardware and software debouncing for reliable magnet detection

## Tested Apps

| App | Status |
|-----|--------|
| MyWhoosh | ✅ Working |
| Kinomap | ✅ Working |
| Zwift | Should work (FTMS standard) |
| TrainerRoad | Should work (FTMS standard) |

## Hardware Requirements

- Raspberry Pi 4 (tested on Pi OS Bullseye)
- Hall effect sensor (e.g., A3144, OH137, SS49E)
- Small magnet attached to crank arm
- Jumper wires

### Wiring

| Hall Sensor | Raspberry Pi |
|-------------|--------------|
| VCC | 3.3V (Pin 1) |
| GND | Ground (Pin 6) |
| OUT | GPIO 17 (Pin 11) |

I used a 10k external pullup resistor. When the magnet passes the sensor, the output is pulled LOW.

## What's Included

| File | Description |
|------|-------------|
| `magnet_sensor_mqtt.py` | Reads hall effect sensor and publishes crank events to MQTT |
| `ftms_bridge.py` | Subscribes to MQTT and broadcasts BLE FTMS data |
| `magnet-sensor.service` | Systemd service for the sensor script |
| `ftms-bridge.service` | Systemd service for the BLE bridge |
| `install_services.sh` | One-click installer for all services |
| `LICENSE` | MIT License |

## Installation

### Prerequisites

```bash
sudo apt-get update
sudo apt-get install -y python3-pip mosquitto mosquitto-clients bluez
pip3 install paho-mqtt dbus-next RPi.GPIO
```

### BlueZ Configuration

1. Enable experimental features in BlueZ:
   ```bash
   sudo nano /lib/systemd/system/bluetooth.service
   ```
   Change the `ExecStart` line to:
   ```
   ExecStart=/usr/lib/bluetooth/bluetoothd --experimental
   ```

2. Restart Bluetooth:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart bluetooth
   ```

### Install FTMS Services

1. Clone or copy all files to `/home/pi/ftms/`

2. Run the installer:
   ```bash
   cd /home/pi/ftms
   sudo bash install_services.sh
   ```

The installer will:
- Check all prerequisites
- Install Python dependencies if missing
- Copy service files to systemd
- Enable and start all services
- Configure auto-start on boot

## Usage

After installation, the services start automatically on boot. Your Pi will appear as **"PiCrankFTMS"** in your fitness app's Bluetooth device list.

### Useful Commands

```bash
# View live logs from both services
journalctl -u magnet-sensor -u ftms-bridge -f

# Check service status
sudo systemctl status magnet-sensor ftms-bridge

# Restart services
sudo systemctl restart magnet-sensor ftms-bridge

# Stop services
sudo systemctl stop magnet-sensor ftms-bridge

# Monitor MQTT messages
mosquitto_sub -h localhost -t sensors/crank -v
```

### Environment Variables

Both scripts support configuration via environment variables. Edit the service files to customize:

**Magnet Sensor:**
| Variable | Default | Description |
|----------|---------|-------------|
| `MQTT_HOST` | localhost | MQTT broker host |
| `MQTT_PORT` | 1883 | MQTT broker port |
| `GPIO_PIN` | 17 | BCM pin number |
| `DEBOUNCE_MS` | 50 | Hardware debounce time |
| `MIN_INTERVAL_MS` | 100 | Minimum ms between events (max 600 RPM) |
| `DEBUG` | 0 | Set to 1 for verbose logging |

**FTMS Bridge:**
| Variable | Default | Description |
|----------|---------|-------------|
| `MQTT_HOST` | localhost | MQTT broker host |
| `MQTT_PORT` | 1883 | MQTT broker port |
| `BLE_NAME` | PiCrankFTMS | Bluetooth device name |
| `DEBUG` | 0 | Set to 1 for verbose logging |

## How It Works

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│   Magnet    │      │   Hall      │      │  Raspberry  │      │  Fitness    │
│   on Crank  │──▶   │   Sensor    │──▶   │     Pi      │──▶   │    App      │
└─────────────┘      └─────────────┘      └─────────────┘      └─────────────┘
                                                │
                          ┌─────────────────────┼─────────────────────┐
                          │                     │                     │
                          ▼                     ▼                     ▼
                    ┌───────────┐        ┌───────────┐        ┌───────────┐
                    │   GPIO    │        │   MQTT    │        │    BLE    │
                    │  Polling  │───▶    │  Broker   │───▶    │   FTMS    │
                    └───────────┘        └───────────┘        └───────────┘
                   magnet_sensor_mqtt.py    mosquitto        ftms_bridge.py
```

1. **Magnet passes sensor** → GPIO pin goes LOW (falling edge)
2. **Sensor script** detects edge, calculates RPM, publishes to MQTT topic `sensors/crank`
3. **Bridge script** receives MQTT message, calculates cadence/power/speed
4. **BLE FTMS** broadcasts data to connected fitness app

## Formulas

| Metric | Formula | Notes |
|--------|---------|-------|
| Cadence | `60 / interval` | EMA smoothed over 3 samples |
| Power | `0.000274 × C³` | Capped at 1000W |
| Speed | `C × 0.35` | km/h |

## Troubleshooting

### BLE not advertising
```bash
# Check Bluetooth service
sudo systemctl status bluetooth

# Ensure experimental mode is enabled
sudo bluetoothctl show | grep Experimental
```

### No MQTT messages
```bash
# Check if mosquitto is running
sudo systemctl status mosquitto

# Test publishing manually
mosquitto_pub -h localhost -t sensors/crank -m "1"
```

### Services not starting
```bash
# Check service logs
journalctl -u magnet-sensor -n 50
journalctl -u ftms-bridge -n 50
```

### Sensor not detecting magnet
- Verify wiring (VCC, GND, OUT)
- Check GPIO pin number matches configuration
- Test magnet polarity - try flipping it
- Reduce gap between magnet and sensor

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Acknowledgments

Built with assistance from Claude (Anthropic). This project uses:
- [dbus-next](https://github.com/altdesktop/python-dbus-next) for BLE GATT server
- [paho-mqtt](https://github.com/eclipse/paho.mqtt.python) for MQTT communication
- [RPi.GPIO](https://pypi.org/project/RPi.GPIO/) for GPIO access
