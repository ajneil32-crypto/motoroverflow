"""Tests for bbtk_trigger: the Trigger class with a fake clock and fake serial
port, the spacing decoder against a simulated session, and the command-line decode.

Run from the study folder:  py -m unittest discover -s tests -v
"""

import csv
import io
import os
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bbtk_trigger as bt

UP, DOWN = b"80", b"00"


class FakeClock:
    def __init__(self, start=100.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance_ms(self, ms):
        self.t += ms / 1000.0


def make_trigger(clock=None, **serial_kwargs):
    clock = clock or FakeClock()
    made = []

    def factory(port, baud, timeout=None, write_timeout=None):
        ser = bt.FakeSerial(port, baud, timeout, write_timeout, clock=clock, **serial_kwargs)
        made.append(ser)
        return ser

    trig = bt.Trigger(clock=clock, serial_factory=factory)
    return trig, clock, made


def payloads(ser):
    return [b for _, b in ser.writes]


class TestOpen(unittest.TestCase):
    def test_open_resets_then_idles(self):
        trig, clock, made = make_trigger()
        ok, msg = trig.open("COM9")
        self.assertTrue(ok, msg)
        self.assertTrue(trig.enabled)
        self.assertEqual(trig.port, "COM9")
        self.assertEqual(payloads(made[0]), [b"RR", b"##", UP])
        self.assertTrue(trig.answered)
        self.assertIn("answered", msg)
        self.assertEqual(made[0].baudrate, 115200)
        self.assertEqual(made[0].timeout, 1)
        self.assertEqual(made[0].write_timeout, 0.05)

    def test_silent_module_opens_but_is_flagged(self):
        trig, clock, made = make_trigger(silent=True)
        ok, msg = trig.open("COM9")
        self.assertTrue(ok)
        self.assertTrue(trig.enabled)
        self.assertFalse(trig.answered)
        self.assertIn("did not answer", msg)
        self.assertEqual(payloads(made[0]), [b"RR", b"##", UP])
        self.assertIsNotNone(trig.pulse("a"))

    def test_failed_open_gives_reason(self):
        trig, clock, made = make_trigger(fail_open=True)
        ok, msg = trig.open("COM9")
        self.assertFalse(ok)
        self.assertFalse(trig.enabled)
        self.assertIn("COM9", msg)
        self.assertIsNone(trig.pulse("a"))

    def test_missing_pyserial_is_reported(self):
        trig = bt.Trigger(clock=FakeClock())
        trig.mock = False
        trig._factory = None
        real_import = __import__

        def no_serial(name, *a, **k):
            if name == "serial" or name.startswith("serial."):
                raise ImportError("no module named serial")
            return real_import(name, *a, **k)

        import builtins
        builtins.__import__ = no_serial
        try:
            ok, msg = trig.open("COM9")
        finally:
            builtins.__import__ = real_import
        self.assertFalse(ok)
        self.assertIn("pyserial not installed", msg)

    def test_wrong_serial_package_is_reported(self):
        # the PyPI package "serial" shadows pyserial and has no Serial class
        import types
        trig = bt.Trigger(clock=FakeClock())
        trig.mock = False
        trig._factory = None
        real = sys.modules.get("serial")
        sys.modules["serial"] = types.ModuleType("serial")
        try:
            ok, msg = trig.open("COM9")
        finally:
            if real is None:
                del sys.modules["serial"]
            else:
                sys.modules["serial"] = real
        self.assertFalse(ok)
        self.assertIn("wrong 'serial' package", msg)

    def test_open_reasons_are_plain_english(self):
        cases = (
            ("could not open port 'COM12': FileNotFoundError(2, 'The system cannot find the file specified.', None, 2)",
             "COM12 not found"),
            ("could not open port 'COM12': PermissionError(13, 'Access is denied.', None, 5)",
             "in use by another program"),
            ("Write timeout", "did not answer"),
            ("x" * 200, "..."),
        )
        for raw, expect in cases:
            msg = bt._plain_reason("COM12", OSError(raw))
            self.assertIn(expect, msg, raw[:40])
            self.assertLessEqual(len(msg), 80, msg)

    def test_reopen_closes_previous_port(self):
        trig, clock, made = make_trigger()
        trig.open("COM1")
        trig.open("COM2")
        self.assertFalse(made[0].is_open)
        self.assertTrue(made[1].is_open)
        self.assertEqual(trig.port, "COM2")

    def test_mock_env_uses_fake_serial(self):
        old = os.environ.get(bt.MOCK_ENV)
        os.environ[bt.MOCK_ENV] = "1"
        try:
            trig = bt.Trigger()
            self.assertTrue(trig.mock)
            ok, _ = trig.open("COM12")
            self.assertTrue(ok)
            self.assertEqual(trig.list_ports(), [("MOCK", "FakeSerial")])
            trig.close()
        finally:
            if old is None:
                del os.environ[bt.MOCK_ENV]
            else:
                os.environ[bt.MOCK_ENV] = old


class TestPulse(unittest.TestCase):
    def test_pulse_puts_line_up_then_down(self):
        trig, clock, made = make_trigger()
        trig.open("COM9")
        clock.advance_ms(1234.56)
        t = trig.pulse("a")
        self.assertEqual(t, 1234.6)
        self.assertEqual(payloads(made[0])[-2:], [UP, DOWN])
        self.assertEqual(trig.counts["a"], 1)

    def test_pulse_when_disabled(self):
        trig, clock, made = make_trigger()
        self.assertIsNone(trig.pulse("a"))
        self.assertEqual(made, [])

    def test_service_holds_line_down_then_puts_it_up(self):
        trig, clock, made = make_trigger()
        trig.open("COM9")
        trig.pulse("a")
        n = len(made[0].writes)
        clock.advance_ms(4.9)
        trig.service()
        self.assertEqual(len(made[0].writes), n)
        clock.advance_ms(0.1)
        trig.service()
        self.assertEqual(payloads(made[0])[-1], UP)
        n = len(made[0].writes)
        trig.service()
        trig.service()
        self.assertEqual(len(made[0].writes), n, "idle service must not write")

    def test_pulse_while_line_down_puts_it_up_first(self):
        trig, clock, made = make_trigger()
        trig.open("COM9")
        trig.pulse("a")
        clock.advance_ms(1)
        trig.pulse("b")
        # a: up, down; b: up (line back up), down (a fresh fall)
        self.assertEqual(payloads(made[0])[-4:], [UP, DOWN, UP, DOWN])
        clock.advance_ms(bt.PULSE_MIN_MS + 1)
        trig.service()
        self.assertEqual(payloads(made[0])[-1], UP)

    def test_write_failure_is_recorded_not_raised(self):
        # RR, ##, 80 on open, then one good marker (2 writes); the sixth write fails
        trig, clock, made = make_trigger(fail_after_writes=5)
        trig.open("COM9")
        self.assertIsNotNone(trig.pulse("a"))
        clock.advance_ms(10)
        trig.service()             # this write fails
        self.assertTrue(trig.failed)
        self.assertFalse(trig.enabled)
        self.assertIn("link lost", trig.error)
        self.assertIn("simulated", trig.error_detail)
        n = len(made[0].writes)
        self.assertIsNone(trig.pulse("b"))
        trig.service()
        self.assertEqual(len(made[0].writes), n)
        trig.close()               # must not raise either

    def test_failure_between_the_two_writes(self):
        # the up write goes out, the down write fails: nothing is counted
        trig, clock, made = make_trigger(fail_after_writes=4)
        trig.open("COM9")
        self.assertIsNone(trig.pulse("a"))
        self.assertTrue(trig.failed)
        self.assertNotIn("a", trig.counts)

    def test_close_puts_line_up_and_is_idempotent(self):
        trig, clock, made = make_trigger()
        trig.open("COM9")
        trig.pulse("a")
        trig.close()
        self.assertEqual(payloads(made[0])[-1], UP)
        self.assertFalse(made[0].is_open)
        self.assertFalse(trig.enabled)
        trig.close()
        trig.close()
        self.assertIsNone(trig.pulse("a"))

    def test_test_pulse_rate_limit(self):
        trig, clock, made = make_trigger()
        trig.open("COM9")
        self.assertTrue(trig.test_pulse())
        self.assertEqual(payloads(made[0])[-2:], [UP, DOWN])
        clock.advance_ms(999)
        self.assertFalse(trig.test_pulse())
        clock.advance_ms(1)
        self.assertTrue(trig.test_pulse())
        self.assertEqual(trig.n_test, 2)
        self.assertEqual(trig.counts["test"], 2)

# --- decoder ------------------------------------------------------------------

def simulate_session(seed, n_blocks=4, n_trials=8, ao_block=None):
    """Marker times (ms) for a session with the jitter the real task shows.
    Returns (times, expected_triplets) where expected is a list of (a, b, c)
    times."""
    rng = random.Random(seed)
    t = rng.uniform(5000, 20000)
    times, expected = [], []
    for blk in range(n_blocks):
        for tr in range(n_trials):
            a = t
            b = a + bt.AB_NOMINAL_MS + rng.uniform(0, 34)
            late = rng.uniform(0, 150) if blk == ao_block else rng.uniform(0, 17)
            c = b + bt.BC_NOMINAL_MS + late
            times += [a, b, c]
            expected.append((a, b, c))
            t = c + rng.uniform(3000, 60000)
        t += rng.uniform(60000, 300000)
    return times, expected


class TestDecoder(unittest.TestCase):
    def test_clean_session(self):
        for seed in (1, 2, 3):
            times, expected = simulate_session(seed, ao_block=seed % 4)
            self.assertEqual(len(times), 96)
            triplets, unmatched = bt.decode(times)
            self.assertEqual(triplets, expected)
            self.assertEqual(unmatched, [])
            pos = bt.assign_positions(triplets)
            for k, (blk, tr) in enumerate(pos):
                self.assertEqual((blk, tr), (k // 8 + 1, k % 8 + 1))

    def test_shuffled_input_is_sorted(self):
        times, expected = simulate_session(7)
        random.Random(0).shuffle(times)
        triplets, unmatched = bt.decode(times)
        self.assertEqual(triplets, expected)

    def test_test_pulses_unmatched(self):
        times, expected = simulate_session(4)
        first = times[0]
        stray = [first - 30000, first - 29000, first - 28000]
        triplets, unmatched = bt.decode(stray + times)
        self.assertEqual(triplets, expected)
        self.assertEqual(unmatched, stray)

    def test_dropped_b(self):
        times, expected = simulate_session(5)
        k = 13
        a, b, c = expected[k]
        times.remove(b)
        triplets, unmatched = bt.decode(times)
        self.assertEqual(len(triplets), 31)
        self.assertEqual(unmatched, [a, c])
        self.assertEqual(triplets, expected[:k] + expected[k + 1:])
        self.assertTrue(all(p == (0, 0) for p in bt.assign_positions(triplets)))
        self.assertFalse(bt.positions_assigned(triplets))

    def test_stray_2200_before_a(self):
        times, expected = simulate_session(8)
        stray = expected[3][0] - bt.AB_NOMINAL_MS
        triplets, unmatched = bt.decode(times + [stray])
        self.assertEqual(unmatched, [stray])
        self.assertEqual(triplets, expected)

    def test_window_edges(self):
        ab_lo, ab_hi = bt.AB_WINDOW_MS
        bc_lo, bc_hi = bt.BC_WINDOW_MS
        for ab, bc, ok in ((ab_lo, bc_lo, True), (ab_hi, bc_hi, True),
                           (ab_lo - 1, bc_lo, False), (ab_hi + 1, bc_hi, False),
                           (ab_lo, bc_lo - 1, False), (ab_hi, bc_hi + 1, False)):
            triplets, unmatched = bt.decode([1000, 1000 + ab, 1000 + ab + bc])
            self.assertEqual(len(triplets), 1 if ok else 0, (ab, bc))
            self.assertEqual(len(unmatched), 0 if ok else 3, (ab, bc))

    def test_single_block_file(self):
        times, expected = simulate_session(9, n_blocks=1)
        triplets, unmatched = bt.decode(times)
        self.assertEqual(triplets, expected)
        self.assertEqual(bt.assign_positions(triplets), [(0, k + 1) for k in range(8)])
        self.assertTrue(bt.positions_assigned(triplets))

    def test_empty(self):
        self.assertEqual(bt.decode([]), ([], []))
        self.assertEqual(bt.assign_positions([]), [])


class TestCli(unittest.TestCase):
    def test_decode_task_csv(self):
        times, expected = simulate_session(11)
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "999_rh_mo_task.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["participant_id", "condition", "trial_index",
                        "trig_a_ms", "trig_b_ms", "trig_c_ms", "trig_ok"])
            for k, (a, b, c) in enumerate(expected):
                w.writerow(["999", "ME", k % 8 + 1, a, b, c, 1])
            # a skipped AO trial leaves the columns blank
            w.writerow(["999", "AO", 1, "", "", "", 0])
        self.assertEqual(bt.load_marks(path), times)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = bt.main(["decode", path])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("triplets:    32", text)
        self.assertIn("unmatched:   0", text)
        self.assertIn("positions:   assigned", text)
        self.assertIn("1a 1b 1c", text)
        self.assertIn("4c", text)

    def test_decode_two_column(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "marks.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("time_ms,code\n500,170\n1000,170\n3200,170\n18200,170\n50000,170\n")
        self.assertEqual(bt.load_marks(path), [500, 1000, 3200, 18200, 50000])
        out = io.StringIO()
        with redirect_stdout(out):
            bt.main(["decode", path])
        text = out.getvalue()
        self.assertIn("triplets:    1", text)
        self.assertIn("unmatched:   2", text)
        self.assertIn("UNASSIGNED", text)


if __name__ == "__main__":
    unittest.main()
