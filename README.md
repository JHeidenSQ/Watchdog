# KeyServo

Turns the key switch of a Spark ALCOR laser with a Feetech STS servo (via a URT-2 adapter), and runs a watchdog that power-cycles the laser whenever it drops off the network.

All commands below run from `src/keyservo`.

## Setup

Requires Python 3.9 or newer and `pyserial`.

macOS:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install pyserial
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install pyserial
```

## Finding the servo port

macOS: `ls /dev/cu.*` should list something like `/dev/cu.usbmodem5C842888621`.

Windows: Device Manager, under "Ports (COM & LPT)", shows the URT-2 as e.g. `COM5`. Alternatively run `python -m serial.tools.list_ports`.

The port is called `PORT` below.

```sh
python key.py PORT status
```

## Locking and unlocking

```sh
python key.py PORT lock
python key.py PORT unlock
python key.py PORT turn 1500     # relative to the current position
python key.py PORT turn -1500
```

Absolute positions (`lock`, `unlock`, `goto`) are unreliable because the servo torque is marginal for this key, so the watchdog only uses relative turns.

## Connecting the laser

`alcor.py` speaks the ALCOR binary protocol (User Manual GD0001-14, chapter 13) and needs nothing beyond `pyserial`. The watchdog reads and restores two settings: `emission` and `alignment_mode` (on for alignment, off for full power).

`--laser` is either a serial port (`COM8`, `/dev/cu.usbserial-X`) or `HOST:PORT` for the network interface. A `HOST:PORT` link sends the same frames over raw TCP, which is untested; use the serial port if it misbehaves. Close Spark's GUI while the watchdog runs: a serial port can only be opened by one program at a time, and the watchdog opens it on every poll.

## Running the watchdog

```sh
python watchdog.py --host 192.168.1.50 --laser COM8 --servo COM5 --log watchdog.log
```

What it does, every `--interval` seconds (default 10):

1. Pings `--host`. While the laser answers, it also reads emission state and power mode, so it always knows the last good setting.
2. After `--misses` consecutive failed pings (default 3), it turns the key off with two relative turns of `--step` (default +1500). Two attempts, because a weak servo can stall short on the first; a stall is logged, not fatal.
3. Waits `--off-time` seconds (default 5), then turns the key back by `-step` once.
4. Pings until the laser answers again, up to `--boot-timeout` seconds (default 180).
5. Connects to the laser, sets the previous power mode, and switches emission on if it was on before the drop. Mode is set first so emission never starts at the wrong power. If the control interface is not ready yet, it keeps retrying until the boot timeout.

If the laser does not come back, the next failed pings trigger another cycle. If the watchdog has never seen the laser healthy since it started, it leaves emission off after recovery.

Swap the key direction by passing a negative step, e.g. `--step -1500`. All options: `python watchdog.py --help`.

## Testing

Self-check of the recovery logic, no hardware needed:

```sh
python watchdog.py --check
python key.py --check
```

Simulated laser loss, optionally with the real key servo:

```sh
python watchdog.py --simulate --interval 2              # no servo, logs only
python watchdog.py --simulate --interval 2 --servo COM5 # real key turns
```

Press Enter to make the fake laser drop. The watchdog then cycles the key, the fake laser comes back after 15 s with emission off and alignment mode, and the watchdog restores the state it saw before the drop (emission on, full power).

End-to-end test with the real laser: start the watchdog normally, then unplug the laser's network cable. Once the key has cycled, plug the cable back in and watch the log restore emission and mode.

## Running in the background

### Windows

Use Task Scheduler so the watchdog starts at log-on and restarts with the PC. In an administrator PowerShell, with the paths adjusted:

```powershell
schtasks /Create /TN KeyServoWatchdog /SC ONLOGON /RL HIGHEST /TR "C:\KeyServo\src\keyservo\.venv\Scripts\pythonw.exe C:\KeyServo\src\keyservo\watchdog.py --host 192.168.1.50 --laser COM8 --servo COM5 --log C:\KeyServo\watchdog.log"
schtasks /Run /TN KeyServoWatchdog
```

`pythonw.exe` runs without a console window, so the log file is the only output. In the task's properties, untick "Stop the task if it runs longer than 3 days", and set the PC's power plan so it never sleeps. Stop it with `schtasks /End /TN KeyServoWatchdog`.

### macOS

Save as `~/Library/LaunchAgents/com.keyservo.watchdog.plist`, with the paths adjusted:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.keyservo.watchdog</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/me/KeyServo/src/keyservo/.venv/bin/python</string>
    <string>/Users/me/KeyServo/src/keyservo/watchdog.py</string>
    <string>--host</string><string>192.168.1.50</string>
    <string>--laser</string><string>/dev/cu.usbserial-X</string>
    <string>--servo</string><string>/dev/cu.usbmodem5C842888621</string>
    <string>--log</string><string>/Users/me/KeyServo/watchdog.log</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
```

Then `launchctl load ~/Library/LaunchAgents/com.keyservo.watchdog.plist`; `launchctl unload` with the same path stops it.
