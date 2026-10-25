#!/usr/bin/env python3
"""Turn a key with a Feetech STS servo through a URT-2.
 
key.py PORT scan | status | free | lock | unlock | goto POS | turn DELTA
key.py --check
"""
import sys
import time
 
import serial
 
BAUD = 1_000_000
SID = 1
TIMEOUT = 0.1                   # s per reply; raise if the USB device server adds latency
LOCKED, UNLOCKED = 2209, 373    # 0..4095 over 360 deg, calibrate with `free` + `status`
SPEED, ACC = 1500, 50           # steps/s, 100 steps/s^2
TORQUE_LIMIT = 600              # per mille of max torque; lower is gentler on the lock
TOL = 30                        # steps short of target counted as jammed
MOVE_TIMEOUT = 3.0
 
# STS register map, words are little-endian
TORQUE_ENABLE, ACCEL, TORQUE_LIM = 40, 41, 48
PRESENT_POS, VOLTAGE, TEMP, MOVING = 56, 62, 63, 66
PING, READ, WRITE = 1, 2, 3
 
 
def packet(sid, instr, params=b""):
    body = bytes([sid, len(params) + 2, instr, *params])
    return b"\xff\xff" + body + bytes([~sum(body) & 0xFF])
 
 
def xfer(ser, sid, instr, params=b""):
    ser.reset_input_buffer()
    ser.write(packet(sid, instr, params))
    head = ser.read(4)
    if len(head) < 4 or head[:2] != b"\xff\xff" or head[2] != sid:
        raise IOError(f"no reply from id {sid}")
    rest = ser.read(head[3])
    body = head[2:] + rest
    if len(rest) != head[3] or ~sum(body[:-1]) & 0xFF != body[-1]:
        raise IOError("bad checksum")
    if rest[0]:
        # bits: 0 voltage, 1 sensor, 2 temperature, 3 current, 5 overload
        print(f"warning: servo error flags 0x{rest[0]:02x}", file=sys.stderr)
    return rest[1:-1]
 
 
def read(ser, addr, n):
    return xfer(ser, SID, READ, [addr, n])
 
 
def write(ser, addr, data):
    xfer(ser, SID, WRITE, [addr, *data])
 
 
def word(v):
    return [v & 0xFF, v >> 8 & 0xFF]
 
 
def position(ser):
    p = int.from_bytes(read(ser, PRESENT_POS, 2), "little")
    return -(p & 0x7FFF) if p & 0x8000 else p
 
 
def move(ser, target):
    write(ser, TORQUE_LIM, word(TORQUE_LIMIT))
    write(ser, TORQUE_ENABLE, [1])
    write(ser, ACCEL, [ACC, *word(target), 0, 0, *word(SPEED)])  # acc, goal pos, goal time, goal speed
    t0 = time.monotonic()
    time.sleep(0.05)
    while read(ser, MOVING, 1)[0] and time.monotonic() - t0 < MOVE_TIMEOUT:
        time.sleep(0.02)
    final = position(ser)
    # ponytail: release after every move so a jammed lock cannot cook the servo;
    # cylinder friction holds the key. Hold torque instead if yours springs back
    write(ser, TORQUE_ENABLE, [0])
    if abs(final - target) > TOL:
        sys.exit(f"jammed at {final}, target {target}")
    print(f"at {final}")
 
 
def main(argv):
    if argv == ["--check"]:
        assert packet(1, PING) == bytes.fromhex("ffff010201fb")
        print("ok")
        return
    if len(argv) < 2:
        sys.exit(__doc__)
    port, cmd, *rest = argv
    # serial_for_url also accepts socket://host:port for a serial-over-TCP server
    with serial.serial_for_url(port, BAUD, timeout=TIMEOUT) as ser:
        if cmd == "scan":
            for sid in range(254):
                try:
                    xfer(ser, sid, PING)
                    print(f"found id {sid}")
                except IOError:
                    pass
        elif cmd == "status":
            print(f"pos {position(ser)}  "
                  f"{read(ser, VOLTAGE, 1)[0] / 10:.1f} V  "
                  f"{read(ser, TEMP, 1)[0]} C")
        elif cmd == "free":
            write(ser, TORQUE_ENABLE, [0])
        elif cmd == "lock":
            move(ser, LOCKED)
        elif cmd == "unlock":
            move(ser, UNLOCKED)
        elif cmd == "goto":
            move(ser, int(rest[0]))
        elif cmd == "turn":
            # ponytail: clamps to one turn; multi-turn needs servo mode 3 and signed targets
            move(ser, min(max(position(ser) + int(rest[0]), 0), 4095))
        else:
            sys.exit(__doc__)
 
 
if __name__ == "__main__":
    main(sys.argv[1:])
