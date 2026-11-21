from __future__ import annotations

import time


def _crc8(data) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def decode_sht3x(raw):
    """Six bytes from an SHT3x -> (temperature °C, humidity %), or None on a
    checksum failure."""
    if len(raw) != 6 or _crc8(raw[0:2]) != raw[2] or _crc8(raw[3:5]) != raw[5]:
        return None
    t = -45.0 + 175.0 * ((raw[0] << 8) | raw[1]) / 65535.0
    h = 100.0 * ((raw[3] << 8) | raw[4]) / 65535.0
    return t, h


class Sht3x:
    """SHT3x temperature/humidity sensor on I2C. Untested on the device.
    On QNX the process must run as root for I2C access."""

    def __init__(self, bus: int = 1, address: int = 0x44):
        import smbus

        self._bus = smbus.SMBus(bus)
        self._address = address

    def read(self):
        try:
            self._bus.write_i2c_block_data(self._address, 0x24, [0x00])
            time.sleep(0.02)
            raw = self._bus.read_i2c_block_data(self._address, 0x00, 6)
        except OSError:
            return None
        return decode_sht3x(list(raw))


def open_sensor():
    """The sensor if one is reachable, else None (the climate feature turns off)."""
    try:
        sensor = Sht3x()
        return sensor if sensor.read() is not None else None
    except Exception:
        return None
