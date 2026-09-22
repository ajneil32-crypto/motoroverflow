"""Checks that the trigger pulses in motor_overflow9.py sit exactly where the
marker specification says, by reading the file's syntax tree.

The study script is never imported: it opens a fullscreen window and uses
Windows-only modules the moment it loads.
"""

import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bbtk_trigger as bt

SCRIPT = os.path.join(ROOT, "motor_overflow9.py")


with open(SCRIPT, encoding="utf-8") as _f:
    SRC = _f.read()
TREE = ast.parse(SRC, SCRIPT)
FUNCS = {n.name: n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)}


def call_name(node):
    """'pulse' for TRIG.pulse(...), 'play_bell' for play_bell(...)."""
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def calls_in(node, name):
    """Call nodes named `name` inside `node`, in source order."""
    found = [n for n in ast.walk(node) if call_name(n) == name]
    return sorted(found, key=lambda n: (n.lineno, n.col_offset))


def pulse_calls(node, label=None):
    out = []
    for c in calls_in(node, "pulse"):
        if not (isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name)
                and c.func.value.id == "TRIG"):
            continue
        if label is None or (c.args and isinstance(c.args[0], ast.Constant)
                             and c.args[0].value == label):
            out.append(c)
    return out


def first_line(calls):
    return min(c.lineno for c in calls)


def module_constant(name):
    for node in TREE.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not assigned at module level")


class TestRestPeriod(unittest.TestCase):
    def test_a_and_b_once_each(self):
        fn = FUNCS["run_rest_period"]
        self.assertEqual(len(pulse_calls(fn, "a")), 1)
        self.assertEqual(len(pulse_calls(fn, "b")), 1)
        self.assertEqual(len(pulse_calls(fn)), 2)

    def test_a_before_trial_start_touch_log(self):
        fn = FUNCS["run_rest_period"]
        a = pulse_calls(fn, "a")[0]
        logs = [c for c in calls_in(fn, "add")
                if any(isinstance(x, ast.Constant) and x.value == "trial start touch"
                       for x in c.args)]
        self.assertEqual(len(logs), 1)
        self.assertLess(a.lineno, logs[0].lineno)

    def test_b_before_start_metronome(self):
        fn = FUNCS["run_rest_period"]
        b = pulse_calls(fn, "b")[0]
        met = calls_in(fn, "start_metronome")
        self.assertEqual(len(met), 1)
        self.assertLess(b.lineno, met[0].lineno)


class TestTrialEnd(unittest.TestCase):
    RUNNERS = ("run_covert_timed_block", "run_ao_block", "run_task_block")

    def test_c_once_before_bell_and_save(self):
        for name in self.RUNNERS:
            with self.subTest(runner=name):
                fn = FUNCS[name]
                cs = pulse_calls(fn, "c")
                self.assertEqual(len(cs), 1)
                self.assertEqual(len(pulse_calls(fn)), 1)
                c = cs[0]
                bells = calls_in(fn, "play_bell")
                self.assertTrue(bells)
                # every bell in a runner is the end-of-trial bell; c precedes it
                self.assertLess(c.lineno, first_line(bells))
                saves = calls_in(fn, "save_task_block")
                self.assertTrue(saves)
                # the AO skip path saves without a pulse; the real save follows c
                after_c = [s for s in saves if s.lineno > c.lineno]
                self.assertEqual(len(after_c), 1)
                self.assertGreater(after_c[0].lineno, first_line(bells))


class TestNoOtherPulses(unittest.TestCase):
    def test_pulse_sites(self):
        allowed = {"run_rest_period": 2, "run_covert_timed_block": 1,
                   "run_ao_block": 1, "run_task_block": 1}
        for name, fn in FUNCS.items():
            n = len(pulse_calls(fn))
            self.assertEqual(n, allowed.get(name, 0), f"{name} has {n} pulse() calls")
        # nothing at module level either
        top = [n for n in TREE.body if not isinstance(n, ast.FunctionDef)]
        for node in top:
            self.assertEqual(pulse_calls(node), [])

    def test_test_pulse_only_in_setup_screen(self):
        for name, fn in FUNCS.items():
            n = len(calls_in(fn, "test_pulse"))
            self.assertEqual(n, 1 if name == "screen_trigger_setup" else 0, name)

    def test_no_sleep_in_pulse_paths(self):
        for name in ("run_rest_period", "run_covert_timed_block", "run_ao_block",
                     "run_task_block", "flip", "screen_trigger_setup"):
            self.assertEqual(calls_in(FUNCS[name], "sleep"), [], name)


class TestHooks(unittest.TestCase):
    def test_flip_services_after_display_flip(self):
        fn = FUNCS["flip"]
        svc = calls_in(fn, "service")
        self.assertEqual(len(svc), 1)
        disp = [c for c in calls_in(fn, "flip")
                if isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Attribute)]
        self.assertEqual(len(disp), 1)
        self.assertGreater(svc[0].lineno, disp[0].lineno)

    def test_check_quit_closes_before_every_exit(self):
        fn = FUNCS["check_quit"]
        closes = calls_in(fn, "close")
        exits = calls_in(fn, "exit")
        self.assertEqual(len(exits), 2)
        self.assertEqual(len(closes), 2)
        for e in exits:
            self.assertTrue(any(c.lineno < e.lineno for c in closes))

    def test_setup_screen_abort_closes(self):
        fn = FUNCS["screen_trigger_setup"]
        self.assertTrue(calls_in(fn, "close"))
        self.assertTrue(calls_in(fn, "open"))


class TestTimingContract(unittest.TestCase):
    def test_constants_match(self):
        rest = module_constant("REST_MS")
        begin = module_constant("BEGIN_MS")
        secs = module_constant("TRIAL_SECONDS")
        self.assertEqual(rest + begin, bt.AB_NOMINAL_MS)
        self.assertEqual(secs * 1000, bt.BC_NOMINAL_MS)

    def test_asserts_present(self):
        src = SRC
        self.assertIn("assert bbtk_trigger.AB_NOMINAL_MS == REST_MS + BEGIN_MS", src)
        self.assertIn("assert bbtk_trigger.BC_NOMINAL_MS == TRIAL_SECONDS * 1000", src)


class TestTopLevelOrder(unittest.TestCase):
    def test_setup_before_handedness(self):
        lines = {}
        for node in TREE.body:
            for c in ast.walk(node):
                nm = call_name(c)
                if nm in ("screen_trigger_setup", "screen_handedness") and isinstance(c.func, ast.Name):
                    lines.setdefault(nm, c.lineno)
        self.assertIn("screen_trigger_setup", lines)
        self.assertIn("screen_handedness", lines)
        self.assertLess(lines["screen_trigger_setup"], lines["screen_handedness"])

    def test_trigger_instance_and_atexit(self):
        src = SRC
        self.assertIn("TRIG = bbtk_trigger.Trigger()", src)
        self.assertIn("atexit.register(TRIG.close)", src)
        self.assertIn("import atexit", src)
        self.assertNotIn("\nimport serial\n", src)


class TestCsvColumns(unittest.TestCase):
    def test_task_row_columns(self):
        fn = FUNCS["save_task_block"]
        keys = [n.slice.value for n in ast.walk(fn)
                if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                and n.value.id == "row" and isinstance(n.slice, ast.Constant)]
        for k in ("trig_a_ms", "trig_b_ms", "trig_c_ms", "trig_ok"):
            self.assertIn(k, keys)

    def test_session_row_columns(self):
        fn = FUNCS["save_session"]
        keys = [n.slice.value for n in ast.walk(fn)
                if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                and n.value.id == "row" and isinstance(n.slice, ast.Constant)]
        for k in ("triggers_enabled", "trigger_mock", "trigger_port", "trigger_epoch",
                  "trigger_answered", "n_test_pulses", "trig_ab_nominal_ms",
                  "trig_bc_nominal_ms"):
            self.assertIn(k, keys)

    def test_trial_complete_warns(self):
        fn = FUNCS["screen_trial_complete"]
        consts = [n.value for n in ast.walk(fn) if isinstance(n, ast.Constant)]
        self.assertIn("Trigger link lost — tell the experimenter.", consts)


if __name__ == "__main__":
    unittest.main()
