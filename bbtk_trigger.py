"""Digital-marker link between the Motor Overflow task and Spike2.

The task sends one marker on the Black Box Toolkit TTL module at three moments
in every trial: when the finger first lands on the X (a), when the trial clock
starts (b) and when the closing bell rings (c). The module's DB25 cable runs
straight into the CED 1401's rear Digital Input connector, and Spike2 records
each marker on a Digital Marker channel.

That straight cable carries only the strobe. BBTK line 8 lands on 1401 pin 23
(Data Available) and the 1401 records a marker each time that line falls. The
data lines do not reach the bits the 1401 reads, so every marker has the same
code (typically AA). That means markers are told apart by their order and
spacing: a, then b about 2.2 s later, then c about 15 s after that. The
decoder at the bottom of this file does that grouping, and
spike2/mo_label_export.s2s is a line-for-line port of it.

A marker is two serial writes with nothing in between: line up ("80"), then
line down ("00"). The fall is the marker. service(), which the task calls once
per frame, puts the line back up once it has been down for PULSE_MIN_MS, ready
for the next marker.

Nothing in the pulse path ever waits. A serial failure switches the link off
and is written down; it never raises into the task.

Needs only the standard library plus pyserial (for real hardware). Set the
environment variable MO_TRIGGER_MOCK=1 to run without hardware.

Command line:
    py bbtk_trigger.py ports            list COM ports
    py bbtk_trigger.py decode <file>    decode marker times, print a report
"""

import csv
import os
import sys
import time
from datetime import datetime

# Timing contract. The study script asserts that its own REST_MS + BEGIN_MS and
# TRIAL_SECONDS * 1000 equal these two numbers, and the Spike2 labelling script
# declares the same windows. Change them together or not at all.
AB_NOMINAL_MS = 2200          # must equal REST_MS + BEGIN_MS in motor_overflow9.py
BC_NOMINAL_MS = 15000         # must equal TRIAL_SECONDS * 1000
AB_WINDOW_MS  = (2150, 2350)  # expected a->b spacing
BC_WINDOW_MS  = (14950, 15250)# expected b->c spacing (AO frame loop can run late)
PULSE_MIN_MS  = 5             # line held down at least this long before it goes back up
TEST_PULSE_GAP_MS = 1000      # test pulses rate-limited
DEFAULT_PORT  = "COM12"
TRIALS_PER_BLOCK = 8
BLOCKS_PER_SESSION = 4
MOCK_ENV = "MO_TRIGGER_MOCK"  # "1" -> FakeSerial

# Protocol per "The Black Box ToolKit USB TTL Module v1 Guide" (USBTTLv1r18):
# 115200 8N1, commands are two ASCII characters in capitals, outputs latch.
BAUD = 115200
CMD_RESET  = b"RR"  # reset the module and clear every output line
CMD_PING   = b"##"  # the module answers PING_REPLY if it is alive and in sync
PING_REPLY = b"XX"
LINE_UP    = b"80"  # line 8 (the strobe) up: idle
LINE_DOWN  = b"00"  # line 8 down: the 1401 records a marker
RESET_WAIT_S = 0.1  # settle after RR before the first command


def _plain_reason(port, exc):
    """A short, plain-English reason an open() failed, for the setup screen.
    The full exception text is kept separately in Trigger.error_detail."""
    text = str(exc)
    low = text.lower()
    if "filenotfound" in low or "cannot find" in low or "no such file" in low:
        return f"{port} not found. Check the USB cable, or tap the port below."
    if "permission" in low or "access is denied" in low:
        return f"{port} is in use by another program. Close it and tap Rescan."
    if "timeout" in low:
        return f"{port} did not answer. Unplug the BBTK, plug it back in, tap Rescan."
    text = text.split("\n")[0].strip()
    if len(text) > 70:
        text = text[:67].rstrip() + "..."
    return f"{port}: {text}" if text else f"{port}: {type(exc).__name__}"


class FakeSerial:
    """Stands in for serial.Serial in tests and in mock mode.

    Every write is kept in `writes` as (perf_counter ms, bytes) so a test, or a
    person checking a mock run, can see exactly what would have reached the
    module and when."""

    def __init__(self, port=None, baudrate=BAUD, timeout=None, write_timeout=None,
                 fail_open=False, fail_after_writes=None, clock=time.perf_counter,
                 silent=False):
        if fail_open:
            raise OSError(f"could not open port {port!r}")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.is_open = True
        self.writes = []
        self._fail_after = fail_after_writes
        self._clock = clock
        self._silent = silent      # True: never answer the ping, like a dead module
        self._inbox = b""

    def write(self, data):
        if not self.is_open:
            raise OSError("port is closed")
        if self._fail_after is not None and len(self.writes) >= self._fail_after:
            raise OSError("simulated write failure")
        self.writes.append((self._clock() * 1000.0, bytes(data)))
        if bytes(data) == CMD_PING and not self._silent:
            self._inbox += PING_REPLY
        return len(data)

    def read(self, size=1):
        out, self._inbox = self._inbox[:size], self._inbox[size:]
        return out

    def reset_input_buffer(self):
        self._inbox = b""

    def flush(self):
        pass

    def close(self):
        self.is_open = False


class Trigger:
    """The BBTK strobe line. open() once, pulse() at each marker, service() every frame."""

    def __init__(self, clock=time.perf_counter, serial_factory=None):
        self._clock = clock
        self.mock = os.environ.get(MOCK_ENV) == "1"
        if serial_factory is not None:
            self._factory = serial_factory
        elif self.mock:
            self._factory = FakeSerial
        else:
            self._factory = None      # resolved to serial.Serial in open()
        self._ser = None
        self.enabled = False
        self.failed = False
        self.error = ""          # short reason, fit for the setup screen
        self.error_detail = ""   # the full exception text, for the log
        self.port = None
        self.answered = False    # the module replied to the ping in open()
        self.epoch = clock()
        self.epoch_wallclock = datetime.now().isoformat(timespec="milliseconds")
        self._down = False       # line currently down after a marker
        self._down_at = 0.0
        self._last_test = None
        self.n_test = 0
        self.counts = {}

    # --- lifetime -------------------------------------------------------------

    def open(self, port):
        """Open `port`, reset the module and put the line up. Returns
        (ok, message). Never raises.

        After the reset the module is pinged (## -> XX). No answer still
        opens the link, because markers may well still go out, but `answered`
        is False and the message says so. The reset wait and the ping read
        (at most the 1 s port timeout) are the only blocking waits anywhere
        in this module; they happen on the setup screen, never in a trial.

        RR drops every line, strobe included, so the 1401 may record one
        marker while the port is being opened. Nothing follows it at the a->b
        spacing, so the decoder leaves it unmatched."""
        self.close()
        factory = self._factory
        if factory is None:
            try:
                import serial
            except ImportError:
                self.enabled = False
                self.error = "pyserial not installed (py -m pip install pyserial)"
                return False, self.error
            # An unrelated PyPI package is also called "serial" and, if
            # installed, overwrites pyserial's files. Say so rather than crash.
            factory = getattr(serial, "Serial", None)
            if factory is None:
                self.enabled = False
                self.error = ("wrong 'serial' package installed - run: "
                              "py -m pip uninstall -y serial pyserial && py -m pip install pyserial")
                return False, self.error
        try:
            ser = factory(port, BAUD, timeout=1, write_timeout=0.05)
            ser.write(CMD_RESET)
            time.sleep(RESET_WAIT_S)
            # The module reports input-line changes unasked; throw away
            # anything already waiting so the ping reply is read cleanly.
            if hasattr(ser, "reset_input_buffer"):
                ser.reset_input_buffer()
            ser.write(CMD_PING)
            reply = ser.read(2) if hasattr(ser, "read") else b""
            ser.write(LINE_UP)
        except Exception as exc:
            self.enabled = False
            self.error_detail = f"{type(exc).__name__}: {exc}"
            self.error = _plain_reason(port, exc)
            return False, self.error
        self._ser = ser
        self.port = port
        self.enabled = True
        self.failed = False
        self.error = ""
        self._down = False
        self.answered = PING_REPLY in reply
        if self.answered:
            return True, f"BBTK answered on {port}"
        return True, (f"{port} opened but the BBTK did not answer - wrong port, "
                      "or module out of sync: unplug it, plug it back in, tap Rescan")

    def close(self):
        """Put the line up and close the port. Safe to call any number of times."""
        ser, self._ser = self._ser, None
        self.enabled = False
        if ser is None:
            return
        try:
            if getattr(ser, "is_open", True):
                try:
                    ser.write(LINE_UP)
                    ser.flush()
                except Exception:
                    pass
                ser.close()
        except Exception:
            pass
        self._down = False

    # --- markers --------------------------------------------------------------

    def _write(self, data):
        # Any failure here ends the link for the rest of the session. It is
        # recorded so the trial rows and the trial-complete screen can say so.
        try:
            self._ser.write(data)
            return True
        except Exception as exc:
            self.enabled = False
            self.failed = True
            self.error_detail = f"{type(exc).__name__}: {exc}"
            self.error = f"link lost on {self.port}: {type(exc).__name__}"
            return False

    def pulse(self, label=""):
        """Send one marker. Returns ms since epoch of the fall, or None if
        nothing was sent. `label` (a, b, c, test) is only counted.

        Two writes, back to back: line up, then line down. If the line is
        still down from the previous marker the first write puts it back up,
        so the second is always a fresh fall."""
        if not self.enabled or self._ser is None:
            return None
        if not self._write(LINE_UP):
            return None
        if not self._write(LINE_DOWN):
            return None
        now = self._clock()
        self._down = True
        self._down_at = now
        self.counts[label] = self.counts.get(label, 0) + 1
        return round((now - self.epoch) * 1000.0, 1)

    def service(self):
        """Call once per frame: puts the line back up once it has been down
        long enough."""
        if not self._down:
            return
        if (self._clock() - self._down_at) * 1000.0 >= PULSE_MIN_MS:
            if self.enabled and self._ser is not None:
                self._write(LINE_UP)
            self._down = False

    def test_pulse(self):
        """A marker for the setup screen, no more than one per second."""
        now = self._clock()
        if self._last_test is not None and (now - self._last_test) * 1000.0 < TEST_PULSE_GAP_MS:
            return False
        if self.pulse("test") is None:
            return False
        self._last_test = now
        self.n_test += 1
        return True

    # --- ports ----------------------------------------------------------------

    def list_ports(self):
        if self.mock:
            return [("MOCK", "FakeSerial")]
        try:
            from serial.tools import list_ports
        except ImportError:
            return []
        try:
            return [(p.device, p.description) for p in list_ports.comports()]
        except Exception:
            return []


# --- decoder ----------------------------------------------------------------
# Single source of truth. spike2/mo_label_export.s2s repeats this line for line.

def decode(times):
    """Group marker times (ms) into (a, b, c) triplets by spacing.

    A marker followed by one AB_WINDOW_MS later and another BC_WINDOW_MS
    after that makes one trial. Returns (triplets, unmatched), where unmatched
    is the sorted list of times that did not form a trial. A marker that does
    not start a triplet is skipped on its own, so one stray or missing marker
    costs one trial, not every label after it. Test pulses fall out as
    unmatched because nothing follows them at 2.2 s."""
    m = sorted(float(t) for t in times)
    ab_lo, ab_hi = AB_WINDOW_MS
    bc_lo, bc_hi = BC_WINDOW_MS
    i = 0
    triplets = []
    unmatched = []
    while i < len(m):
        if (i + 2 < len(m)
                and ab_lo <= m[i + 1] - m[i] <= ab_hi
                and bc_lo <= m[i + 2] - m[i + 1] <= bc_hi):
            triplets.append((m[i], m[i + 1], m[i + 2]))
            i += 3
        else:
            unmatched.append(m[i])
            i += 1
    return triplets, unmatched


def assign_positions(triplets):
    """(block, trial) for each triplet, by count. Block 1-4 for a full session
    file, block 0 for a single-block file, all zeros when the count is neither
    (the report then says the positions were not assigned)."""
    n = len(triplets)
    if n == TRIALS_PER_BLOCK * BLOCKS_PER_SESSION:
        return [(k // TRIALS_PER_BLOCK + 1, k % TRIALS_PER_BLOCK + 1) for k in range(n)]
    if n == TRIALS_PER_BLOCK:
        return [(0, k + 1) for k in range(n)]
    return [(0, 0)] * n


def positions_assigned(triplets):
    n = len(triplets)
    return n in (TRIALS_PER_BLOCK * BLOCKS_PER_SESSION, TRIALS_PER_BLOCK)


def _stats(values):
    if not values:
        return None
    return min(values), sum(values) / len(values), max(values)


def report(times):
    """Decode and return the report as a list of text lines."""
    triplets, unmatched = decode(times)
    positions = assign_positions(triplets)
    ab = [b - a for a, b, _ in triplets]
    bc = [c - b for _, b, c in triplets]
    lines = [
        f"markers:     {len(times)}",
        f"triplets:    {len(triplets)}",
        f"unmatched:   {len(unmatched)}",
    ]
    if unmatched:
        lines.append("unmatched (ms): " + ", ".join(f"{t:.1f}" for t in unmatched))
    for name, vals in (("a->b", ab), ("b->c", bc)):
        st = _stats(vals)
        if st:
            lines.append(f"{name} ms:    min {st[0]:.1f}  mean {st[1]:.1f}  max {st[2]:.1f}")
    if positions_assigned(triplets):
        kind = "session (4 blocks x 8)" if len(triplets) == 32 else "single block (8)"
        lines.append(f"positions:   assigned, {kind}")
        lines.append("labels:      " + " ".join(
            (f"{b}{l}" if b else f"{l}{t}") for (b, t) in positions for l in "abc"))
    else:
        lines.append("positions:   UNASSIGNED (expected 32 or 8 triplets)")
    return lines


def load_marks(path):
    """Marker times (ms) from either a *_mo_task.csv (trig_a_ms, trig_b_ms,
    trig_c_ms flattened in row order) or a file whose first column is the
    time in ms, such as a Spike2 text export of the Trig channel. Any other
    columns (the marker code, which is always the same) are ignored."""
    with open(path, newline="", encoding="utf-8") as f:
        text = f.read()
    rows = list(csv.reader(text.splitlines()))
    if rows and "trig_a_ms" in rows[0]:
        header = rows[0]
        cols = [header.index(f"trig_{k}_ms") for k in "abc"]
        times = []
        for row in rows[1:]:
            for j in cols:
                if j < len(row) and row[j].strip():
                    times.append(float(row[j]))
        return times
    times = []
    for row in rows:
        if not row:
            continue
        try:
            times.append(float(row[0].strip()))
        except ValueError:
            continue    # header or comment line
    return times


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("ports", "decode"):
        print("usage: py bbtk_trigger.py ports | decode <file>")
        return 2
    if argv[0] == "ports":
        ports = Trigger().list_ports()
        if not ports:
            print("no COM ports found (is pyserial installed? is the BBTK plugged in?)")
        for dev, desc in ports:
            print(f"{dev}\t{desc}")
        return 0
    if len(argv) < 2:
        print("usage: py bbtk_trigger.py decode <file>")
        return 2
    marks = load_marks(argv[1])
    for line in report(marks):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
