"""Digital-marker link between the Motor Overflow task and Spike2.

The task script sends one coded marker on the Black Box Toolkit TTL lines at
three moments in every trial: when the finger first lands on the X (a, code
1), when the trial clock starts (b, code 2) and when the closing bell rings
(c, code 3). The lines run straight into the CED 1401's rear Digital Input
connector and Spike2 records each marker, with its code, on a Digital Marker
channel. The 1401 reads the data bits when it sees a low-going pulse on
Digital Input pin 23 (Data Available):

    BBTK lines 1-7  ->  1401 digital input bits 0-6   (the code, 0-127)
    BBTK line 8     ->  1401 pin 23, Data Available    (the strobe, active LOW)

A marker is sent in two serial writes with nothing in between: first the code
with the strobe HIGH, then the same code with the strobe LOW. The gap between
the two writes (the time the first command takes to reach the module, at
least 0.17 ms) is the settle time the 1401 needs before the strobe edge.
service(), which the task calls once per frame, raises the strobe again once
it has been LOW for PULSE_MIN_MS. The code is left on the data lines until
the next marker changes it, so the data is stable across both strobe edges.

Nothing in the pulse path ever waits. A serial failure switches the link off
and is written down; it never raises into the task.

When every marker carries its code, the a/b/c labelling in Spike2 needs no
guessing: the decoder at the bottom of this file groups consecutive a, b, c
markers into trials and only uses the expected spacings (a->b about 2.2 s,
b->c about 15 s) as a cross-check. When the codes carry nothing (a straight
DB25 cable delivers the strobe but not the data bits, so every marker reads
the same byte, typically AA) the decoder falls back to grouping by spacing
alone, exactly as the original one-line design did. The choice is automatic.
spike2/mo_label_export.s2s is a port of both paths.

Needs only the standard library plus pyserial (for real hardware). Set the
environment variable MO_TRIGGER_MOCK=1 to run without hardware.

Command line:
    py bbtk_trigger.py ports            list COM ports
    py bbtk_trigger.py decode <file>    decode marker times/codes, print a report
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
AB_WINDOW_MS  = (2150, 2350)  # expected a->b spacing (cross-check only)
BC_WINDOW_MS  = (14950, 15250)# expected b->c spacing (AO frame loop can run late)
PULSE_MIN_MS  = 5             # strobe held LOW at least this long before it is raised
TEST_PULSE_GAP_MS = 1000      # test pulses rate-limited
DEFAULT_PORT  = "COM12"
TRIALS_PER_BLOCK = 8
BLOCKS_PER_SESSION = 4
MOCK_ENV = "MO_TRIGGER_MOCK"  # "1" -> FakeSerial

# Marker codes. The Spike2 script declares the same three; the .s2s Labels
# channel and the .mat export carry them unchanged.
CODES = {"a": 1, "b": 2, "c": 3, "test": 100}
CODE_MAX = 127                # 7 data bits: BBTK lines 1-7 -> 1401 bits 0-6

# Protocol per "The Black Box ToolKit USB TTL Module v1 Guide" (USBTTLv1r18):
# 115200 8N1, commands are two ASCII characters in capitals, outputs latch.
# A two-character hex command sets all 8 output lines at once: bit n of the
# value drives line n+1.
BAUD = 115200
CMD_RESET = b"RR"   # reset the module and clear every output line
CMD_PING  = b"##"   # the module answers PING_REPLY if it is alive and in sync
PING_REPLY = b"XX"
STROBE_MASK = 0x80  # line 8 = the Data Available strobe, HIGH when idle
DATA_MASK   = 0x7F  # lines 1-7 = the code
RESET_WAIT_S = 0.1  # settle after RR before the first command


def cmd(value):
    """The two-character command that puts `value` (0-255) on the 8 lines."""
    return f"{value & 0xFF:02X}".encode("ascii")


CMD_IDLE = cmd(STROBE_MASK)   # "80": no data, strobe HIGH


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
    """The BBTK lines. open() once, pulse() at each marker, service() every frame."""

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
        self._data = 0           # the code currently on lines 1-7
        self._strobe_low = False
        self._low_at = 0.0
        self._last_test = None
        self.n_test = 0
        self.counts = {}

    # --- lifetime -------------------------------------------------------------

    def open(self, port):
        """Open `port`, reset the module and park the lines idle (no data,
        strobe HIGH). Returns (ok, message). Never raises.

        After the reset the module is pinged (## -> XX). No answer still
        opens the link, because markers may well still go out, but `answered`
        is False and the message says so. The reset wait and the ping read
        (at most the 1 s port timeout) are the only blocking waits anywhere
        in this module; they happen on the setup screen, never in a trial.

        RR drops every line, strobe included, so the 1401 may latch one code-0
        marker while the port is being opened. The decoder ignores code 0."""
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
            ser.write(CMD_IDLE)
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
        self._data = 0
        self._strobe_low = False
        self.answered = PING_REPLY in reply
        if self.answered:
            return True, f"BBTK answered on {port}"
        return True, (f"{port} opened but the BBTK did not answer - wrong port, "
                      "or module out of sync: unplug it, plug it back in, tap Rescan")

    def close(self):
        """Park the lines idle and close the port. Safe to call any number of times."""
        ser, self._ser = self._ser, None
        self.enabled = False
        if ser is None:
            return
        try:
            if getattr(ser, "is_open", True):
                try:
                    ser.write(CMD_IDLE)
                    ser.flush()
                except Exception:
                    pass
                ser.close()
        except Exception:
            pass
        self._data = 0
        self._strobe_low = False

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

    def pulse(self, label, code=None):
        """Send marker `code` (default CODES[label]). Returns ms since epoch
        of the strobe edge, or None if nothing was sent.

        Two writes, back to back: the code with the strobe HIGH, then the
        code with the strobe LOW. If the strobe is still LOW from the
        previous marker the first write raises it, so the second is a fresh
        falling edge with the new code already settled."""
        if code is None:
            if label not in CODES:
                raise ValueError(f"unknown marker label {label!r}; give a code")
            code = CODES[label]
        if not 0 <= code <= CODE_MAX:
            raise ValueError(f"code must be 0-{CODE_MAX}, got {code}")
        if not self.enabled or self._ser is None:
            return None
        if not self._write(cmd(code | STROBE_MASK)):
            return None
        if not self._write(cmd(code)):
            return None
        now = self._clock()
        self._data = code
        self._strobe_low = True
        self._low_at = now
        self.counts[label] = self.counts.get(label, 0) + 1
        return round((now - self.epoch) * 1000.0, 1)

    def service(self):
        """Call once per frame: raises the strobe once it has been LOW long
        enough. The code stays on the data lines."""
        if not self._strobe_low:
            return
        if (self._clock() - self._low_at) * 1000.0 >= PULSE_MIN_MS:
            if self.enabled and self._ser is not None:
                self._write(cmd(self._data | STROBE_MASK))
            self._strobe_low = False

    def test_pulse(self):
        """A marker (code CODES['test']) for the setup screen, no more than
        one per second."""
        now = self._clock()
        if self._last_test is not None and (now - self._last_test) * 1000.0 < TEST_PULSE_GAP_MS:
            return False
        if self.pulse("test") is None:
            return False
        self._last_test = now
        self.n_test += 1
        return True

    def hold(self, high, all_lines=False):
        """Bench use only (trigger_test.py --mode hold): park line 1, or every
        data line, HIGH or LOW so a meter can be put on the far end of the
        cable. The strobe stays HIGH throughout, so Spike2 records nothing.
        service() leaves a held line alone. Returns True if the bytes were
        sent. Never call this from the task."""
        if not self.enabled or self._ser is None:
            return False
        self._strobe_low = False
        self._data = (DATA_MASK if all_lines else 0x01) if high else 0
        return self._write(cmd(self._data | STROBE_MASK))

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

def decode_method(marks):
    """'codes' if any marker carries an a/b/c code, else 'spacing'."""
    abc = (CODES["a"], CODES["b"], CODES["c"])
    return "codes" if any(int(c) in abc for _, c in marks) else "spacing"


def decode(marks):
    """Group (time_ms, code) markers into (a, b, c) triplets of times.

    Chooses decode_by_codes or decode_by_spacing with decode_method. Both
    return (triplets, unmatched) where unmatched is the list of (time, code)
    markers that did not form a trial."""
    if decode_method(marks) == "codes":
        return decode_by_codes(marks)
    return decode_by_spacing(marks)


def decode_by_codes(marks):
    """Three consecutive markers coded a, b, c (1, 2, 3) make one trial.
    Codes outside 1-3 (test pulses, the code 0 the 1401 may latch when the
    port is opened) are dropped before grouping. A marker that does not start
    a triplet is skipped on its own, so one stray or missing marker costs one
    trial, not every label after it."""
    ca, cb, cc = CODES["a"], CODES["b"], CODES["c"]
    m = sorted((float(t), int(c)) for t, c in marks if int(c) in (ca, cb, cc))
    i = 0
    triplets = []
    unmatched = []
    while i < len(m):
        if (i + 2 < len(m) and m[i][1] == ca and m[i + 1][1] == cb
                and m[i + 2][1] == cc):
            triplets.append((m[i][0], m[i + 1][0], m[i + 2][0]))
            i += 3
        else:
            unmatched.append(m[i])
            i += 1
    return triplets, unmatched


def decode_by_spacing(marks):
    """Codes ignored: a marker followed by one AB_WINDOW_MS later and another
    BC_WINDOW_MS after that makes one trial. Test pulses fall out as
    unmatched because nothing follows them at 2.2 s. Same skip-one rule as
    decode_by_codes."""
    m = sorted((float(t), int(c)) for t, c in marks)
    ab_lo, ab_hi = AB_WINDOW_MS
    bc_lo, bc_hi = BC_WINDOW_MS
    i = 0
    triplets = []
    unmatched = []
    while i < len(m):
        if (i + 2 < len(m)
                and ab_lo <= m[i + 1][0] - m[i][0] <= ab_hi
                and bc_lo <= m[i + 2][0] - m[i + 1][0] <= bc_hi):
            triplets.append((m[i][0], m[i + 1][0], m[i + 2][0]))
            i += 3
        else:
            unmatched.append(m[i])
            i += 1
    return triplets, unmatched


def timing_faults(triplets):
    """Triplets whose a->b or b->c spacing falls outside the expected windows.
    Returns a list of (index, ab_ms, bc_ms). A fault does not unlabel the
    trial; it is reported so someone can look at it."""
    out = []
    for k, (a, b, c) in enumerate(triplets):
        ab, bc = b - a, c - b
        if not (AB_WINDOW_MS[0] <= ab <= AB_WINDOW_MS[1]
                and BC_WINDOW_MS[0] <= bc <= BC_WINDOW_MS[1]):
            out.append((k, ab, bc))
    return out


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


def report(marks):
    """Decode and return the report as a list of text lines."""
    method = decode_method(marks)
    triplets, unmatched = decode(marks)
    faults = timing_faults(triplets)
    positions = assign_positions(triplets)
    abc = set(CODES[k] for k in "abc")
    other = {}
    for _, c in marks:
        if int(c) not in abc:
            other[int(c)] = other.get(int(c), 0) + 1
    ab = [b - a for a, b, _ in triplets]
    bc = [c - b for _, b, c in triplets]
    lines = [
        f"markers:     {len(marks)}",
        f"method:      {'codes' if method == 'codes' else 'spacing (no a/b/c codes found)'}",
        f"triplets:    {len(triplets)}",
        f"unmatched:   {len(unmatched)}",
    ]
    if unmatched:
        lines.append("unmatched (ms/code): " + ", ".join(f"{t:.1f}/{c}" for t, c in unmatched))
    if other and method == "codes":
        lines.append("ignored codes: " + ", ".join(f"{c} x{n}" for c, n in sorted(other.items())))
    for name, vals in (("a->b", ab), ("b->c", bc)):
        st = _stats(vals)
        if st:
            lines.append(f"{name} ms:    min {st[0]:.1f}  mean {st[1]:.1f}  max {st[2]:.1f}")
    lines.append(f"timing:      {len(faults)} triplet(s) outside the expected windows")
    for k, abv, bcv in faults:
        lines.append(f"  triplet {k + 1}: a->b {abv:.1f} ms, b->c {bcv:.1f} ms")
    if positions_assigned(triplets):
        kind = "session (4 blocks x 8)" if len(triplets) == 32 else "single block (8)"
        lines.append(f"positions:   assigned, {kind}")
        lines.append("labels:      " + " ".join(
            (f"{b}{l}" if b else f"{l}{t}") for (b, t) in positions for l in "abc"))
    else:
        lines.append("positions:   UNASSIGNED (expected 32 or 8 triplets)")
    return lines


def load_marks(path):
    """(time_ms, code) markers from either a *_mo_task.csv (trig_a_ms,
    trig_b_ms, trig_c_ms flattened in row order, coded 1/2/3) or a two-column
    file of `time_ms,code` such as a Spike2 text export of the Trig channel."""
    with open(path, newline="", encoding="utf-8") as f:
        text = f.read()
    rows = list(csv.reader(text.splitlines()))
    if rows and "trig_a_ms" in rows[0]:
        header = rows[0]
        cols = [(header.index(f"trig_{k}_ms"), CODES[k]) for k in "abc"]
        marks = []
        for row in rows[1:]:
            for j, code in cols:
                if j < len(row) and row[j].strip():
                    marks.append((float(row[j]), code))
        return marks
    marks = []
    for row in rows:
        if len(row) < 2:
            continue
        try:
            marks.append((float(row[0].strip()), int(float(row[1].strip()))))
        except ValueError:
            continue    # header or comment line
    return marks


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
