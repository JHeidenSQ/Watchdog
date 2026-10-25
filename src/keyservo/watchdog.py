#!/usr/bin/env python3
"""Ping the laser; when it drops, power-cycle it with the key servo and restore
emission and power mode.

watchdog.py --host IP --servo PORT --laser COM3|HOST:PORT [--log FILE]
watchdog.py --simulate [--servo PORT|none]    Enter drops the fake laser
watchdog.py --check
"""
import argparse
import logging
import subprocess
import sys
import threading
import time

import serial

import key
from alcor import Alcor

log = logging.getLogger("watchdog")


def ping(host, timeout=1.0):
    if sys.platform == "win32":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    elif sys.platform == "darwin":
        cmd = ["ping", "-c", "1", "-W", str(int(timeout * 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, round(timeout))), host]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return False
    # Windows exits 0 on "destination host unreachable" from the gateway; only a real echo carries a TTL
    return r.returncode == 0 and b"TTL=" in r.stdout.upper()


def key_cycle(servo, step, off_time):
    """Off: two relative turns, since a weak servo may stall short on the first. On: one turn back"""
    if servo == "none":
        log.info("no servo, skipping key")
        return
    with serial.serial_for_url(servo, key.BAUD, timeout=key.TIMEOUT) as ser:
        for _ in range(2):
            try:
                key.turn(ser, step)
            except key.Jammed as e:
                log.warning("off turn: %s", e)
        time.sleep(off_time)
        try:
            key.turn(ser, -step)
        except key.Jammed as e:
            log.warning("on turn: %s", e)


class Watchdog:
    def __init__(self, reachable, open_laser, cycle, misses=3, boot_timeout=180, retry=5):
        self.reachable, self.open_laser, self.cycle = reachable, open_laser, cycle
        self.misses, self.boot_timeout, self.retry = misses, boot_timeout, retry
        self.state = None  # (emission, mode) last read while healthy
        self.missed = 0

    def step(self):
        if self.reachable():
            self.missed = 0
            try:
                with self.open_laser() as laser:
                    state = laser.emission(), laser.mode()
                if state != self.state:
                    log.info("laser emission=%s mode=%s", *state)
                self.state = state
            except Exception as e:
                log.warning("laser reachable but query failed: %s", e)
            return
        self.missed += 1
        log.warning("no ping reply (%d/%d)", self.missed, self.misses)
        if self.missed >= self.misses:
            self.missed = 0
            self.recover()

    def recover(self):
        log.error("laser lost, power-cycling with key")
        try:
            self.cycle()
        except OSError as e:
            log.error("key servo failed: %s", e)
            return False
        t0 = time.monotonic()
        deadline = t0 + self.boot_timeout
        while not self.reachable():
            if time.monotonic() > deadline:
                log.error("no ping %d s after key cycle, will retry", self.boot_timeout)
                return False
            time.sleep(self.retry)
        log.info("ping back after %.0f s", time.monotonic() - t0)
        if self.state is None:
            log.warning("previous laser state unknown, leaving emission off")
            return True
        emission, mode = self.state
        # the control interface can come up after the network stack, so keep trying until the deadline
        while True:
            try:
                with self.open_laser() as laser:
                    laser.set_mode(mode)  # mode first so emission never starts at the wrong power
                    if emission:
                        laser.set_emission(True)
                log.info("restored emission=%s mode=%s", emission, mode)
                return True
            except Exception as e:
                if time.monotonic() > deadline:
                    log.error("could not restore laser: %s", e)
                    return False
                log.info("laser not ready yet: %s", e)
                time.sleep(self.retry)


class Sim:
    """Fake laser: drop() cuts it off, cycled() boots it after `boot` s in reset state"""

    def __init__(self, boot):
        self.boot, self.up_at = boot, 0.0
        self.on, self.power = True, "full"

    def reachable(self):
        return time.monotonic() >= self.up_at

    def drop(self):
        self.up_at = float("inf")
        log.info("sim: laser dropped")

    def cycled(self):
        self.up_at = time.monotonic() + self.boot
        self.on, self.power = False, "alignment"

    def __enter__(self):
        if not self.reachable():
            raise ConnectionError("sim offline")
        return self

    def __exit__(self, *exc):
        pass

    def emission(self):
        return self.on

    def set_emission(self, on):
        self.on = on

    def mode(self):
        return self.power

    def set_mode(self, mode):
        self.power = mode


def check():
    sim = Sim(boot=0.05)
    w = Watchdog(sim.reachable, lambda: sim, sim.cycled, misses=2, boot_timeout=1, retry=0.01)
    w.step()
    assert w.state == (True, "full")
    sim.drop()
    w.step()
    assert sim.up_at == float("inf")
    w.step()
    assert (sim.on, sim.power) == (True, "full")
    print("ok")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", help="laser IP to ping")
    p.add_argument("--laser", help="laser control link: COM3, /dev/cu.X or HOST:PORT")
    p.add_argument("--servo", default="none", help="servo port, or none to skip the key")
    p.add_argument("--step", type=int, default=1500, help="relative key turn for off; on is -step")
    p.add_argument("--off-time", type=float, default=5, help="s the laser stays off")
    p.add_argument("--interval", type=float, default=10, help="s between pings")
    p.add_argument("--misses", type=int, default=3, help="failed pings before power-cycling")
    p.add_argument("--boot-timeout", type=float, default=180, help="s to wait for the laser after cycling")
    p.add_argument("--simulate", action="store_true", help="fake laser, Enter drops it")
    p.add_argument("--log", help="also log to this file")
    p.add_argument("--check", action="store_true")
    a = p.parse_args()
    if a.check:
        return check()

    handlers = [logging.StreamHandler()] + ([logging.FileHandler(a.log)] if a.log else [])
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")

    def cycle():
        key_cycle(a.servo, a.step, a.off_time)
        if a.simulate:
            sim.cycled()

    if a.simulate:
        sim = Sim(boot=15)
        threading.Thread(target=lambda: [sim.drop() for _ in sys.stdin], daemon=True).start()
        reachable, open_laser = sim.reachable, lambda: sim
        log.info("simulating; press Enter to drop the laser")
    else:
        if not (a.host and a.laser):
            p.error("--host and --laser are required unless --simulate")
        reachable, open_laser = lambda: ping(a.host), lambda: Alcor(a.laser)

    w = Watchdog(reachable, open_laser, cycle, a.misses, a.boot_timeout)
    while True:
        w.step()
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
