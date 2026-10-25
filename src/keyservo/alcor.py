"""Spark Lasers ALCOR over RS-232/USB serial, protocol per ALCOR User Manual GD0001-14 ch. 13."""

import struct

import serial

PORT = "COM8"


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def frame(rid: int, cmd: int, data: bytes = b"") -> bytes:
    head = struct.pack("<BBIH", 0x53, rid, cmd, len(data))
    packet = head + struct.pack("<H", crc16(head))
    if data:
        packet += data + struct.pack("<H", crc16(data))
    return packet


ERROR_BITS = {
    0: "internal error",
    1: "interlock circuit open",
    2: "seeder frequency out of range",
    3: "internal communication bus error",
    4: "seeder temperature out of range",
    5: "amplifier temperature out of range",
    11: "seeder output power below threshold",
    12: "seeder diode power below threshold",
    13: "amplifier output power below threshold",
    18: "pump diode temperature out of range",
    32: "laser head temperature out of range",
    37: "error power level",
    38: "seeder current out of range",
}


class Alcor:
    WRITE = 0x80000000
    PING, ERRORS, WARNINGS, CLEAR_ERRORS = 0x00, 0x01, 0x02, 0x03
    EMISSION, GDD, POWER_MODE, GDD_RANGE = 0x04, 0x07, 0x09, 0x0A
    SHUTTER, AOM_LEVEL = 0x11, 0x1E
    SERIAL_NUMBER = 0x400E
    # not in the manual, taken from the Spark Lasers GUI 2.3.6 command table
    WL_MAX, WL_MIN, WL_SETPOINT, WL_ACTUAL = 0x12000120, 0x12000121, 0x12000122, 0x12000127

    def __init__(self, port: str = PORT):
        # vendor GUI allows 5 x 500 ms for writes such as emission on/off
        # serial_for_url also takes socket://host:port for the TCP interface
        self.ser = serial.serial_for_url(port, 115200, timeout=3.0)
        self._rid = 0

    def close(self):
        self.ser.close()

    def query(self, cmd: int, data: bytes = b"") -> bytes:
        self._rid = (self._rid + 1) & 0xFF
        self.ser.reset_input_buffer()
        self.ser.write(frame(self._rid, cmd, data))
        head = self.ser.read(7)
        if len(head) != 7:
            raise TimeoutError(f"no answer to {cmd:#010x}")
        magic, rid, err, n, crc = struct.unpack("<BBBHH", head)
        if magic != 0x53 or rid != self._rid or crc != crc16(head[:5]):
            raise IOError(f"corrupt answer header {head.hex(' ')}")
        if err:
            raise IOError(f"{cmd:#010x} rejected, protocol error {err:#04x}")
        body = self.ser.read(n + 2) if n else b""
        if n and (len(body) != n + 2 or crc16(body[:n]) != int.from_bytes(body[n:], "little")):
            raise IOError(f"corrupt answer data {body.hex(' ')}")
        return body[:n]

    def get(self, cmd: int, fmt: str):
        values = struct.unpack("<" + fmt, self.query(cmd))
        return values[0] if len(values) == 1 else values

    def set(self, cmd: int, fmt: str, *values):
        self.query(self.WRITE | cmd, struct.pack("<" + fmt, *values))

    def ping(self) -> bool:
        try:
            self.query(self.PING)
            return True
        except (TimeoutError, IOError):
            return False

    @property
    def serial_number(self) -> int:
        return self.get(self.SERIAL_NUMBER, "I")

    @property
    def errors(self) -> list[str]:
        bits = self.get(self.ERRORS, "Q")
        return [ERROR_BITS.get(i, f"bit {i}") for i in range(64) if bits >> i & 1]

    @property
    def busy(self) -> bool:
        return bool(self.get(self.WARNINGS, "Q") >> 21 & 1)

    def clear_errors(self):
        self.query(self.WRITE | self.CLEAR_ERRORS)

    @property
    def emission(self) -> bool:
        return bool(self.get(self.EMISSION, "B"))

    @emission.setter
    def emission(self, on: bool):
        self.set(self.EMISSION, "B", int(on))

    @property
    def alignment_mode(self) -> bool:
        return bool(self.get(self.POWER_MODE, "B"))

    @alignment_mode.setter
    def alignment_mode(self, on: bool):
        self.set(self.POWER_MODE, "B", int(on))

    @property
    def gdd(self) -> float:
        return self.get(self.GDD, "f")

    @gdd.setter
    def gdd(self, fs2: float):
        lo, hi = self.gdd_range
        if not lo <= fs2 <= hi:
            raise ValueError(f"GDD {fs2} fs^2 outside [{lo}, {hi}]")
        self.set(self.GDD, "f", fs2)

    @property
    def gdd_range(self) -> tuple[float, float]:
        return self.get(self.GDD_RANGE, "2f")

    @property
    def wavelength(self) -> float:
        return self.get(self.WL_SETPOINT, "f")

    @wavelength.setter
    def wavelength(self, nm: float):
        lo, hi = self.wavelength_range
        if not lo <= nm <= hi:
            raise ValueError(f"wavelength {nm} nm outside [{lo}, {hi}]")
        self.set(self.WL_SETPOINT, "f", nm)

    @property
    def wavelength_range(self) -> tuple[float, float]:
        return self.get(self.WL_MIN, "f"), self.get(self.WL_MAX, "f")

    @property
    def wavelength_actual(self) -> float:
        return self.get(self.WL_ACTUAL, "f")

    @property
    def shutter(self) -> bool:
        return bool(self.get(self.SHUTTER, "B"))

    @shutter.setter
    def shutter(self, on: bool):
        self.set(self.SHUTTER, "B", int(on))

    @property
    def aom_level(self) -> float:
        return self.get(self.AOM_LEVEL, "f")

    @aom_level.setter
    def aom_level(self, percent: float):
        if not 0 <= percent <= 100:
            raise ValueError(f"AOM level {percent} % outside [0, 100]")
        self.set(self.AOM_LEVEL, "f", percent)
