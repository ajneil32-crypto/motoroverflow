"""Bench tool: sends digital markers to Spike2 through the BBTK without running
the task. Run from a Command Prompt in this folder while Spike2 is sampling.

    py trigger_test.py [--port COM12] [--mock] [--mode single|trial|session|hold|code] [--trials N] [--all] [--code N]

hold     data line 1 held HIGH until Ctrl+C, then dropped; the strobe stays
         HIGH so Spike2 records nothing. Put a multimeter on the 1401 end of
         the cable: pin 21 vs pin 13 reads about 5 V while held, 0 V after.
         With --all every data line (1401 pins 21, 8, 20, 7, 19, 6, 18) is
         raised, so a
         reading of 5 V on --all but 0 V without it means the cable is tapped
         on the wrong pin. Pin 23 (strobe) reads about 5 V throughout.
single   5 test markers (code 100), 2 s apart. Look for 5 markers on Trig.
code     5 markers with the code given by --code (0-127), 1 s apart. For
         finding out what the 1401 makes of each data line: --code 127 raises
         all seven, --code 1, 2, 4, 8, 16, 32, 64 raise one line each. Spike2
         must show the same number (in hex: 7F, 01, 02, 04, 08, 10, 20, 40).
trial    N trials (default 3): a (1), +2200 ms b (2), +15000 ms c (3), then
         a 5 s gap.
session  32 trials in 4 blocks of 8, 3 s between trials, 10 s between blocks
         (about 12 minutes). Save the Spike2 file and run mo_label_export.s2s
         on it: the report must show 32 triplets with positions assigned.

Ctrl+C stops it and parks the lines idle. --mock uses the fake port and prints
every write at the end so the timing can be checked without hardware.
"""

import argparse
import os
import sys
import time

import bbtk_trigger as bt


def wait_ms(trig, ms, t0):
    """Sleep in 1 ms steps, servicing the trigger so the line drops on time."""
    end = t0 + ms / 1000.0
    while True:
        trig.service()
        now = time.perf_counter()
        if now >= end:
            return
        time.sleep(min(0.001, end - now))


def send(trig, label, start, tag=""):
    t = trig.pulse(label)
    stamp = (time.perf_counter() - start) * 1000.0
    what = f"{label} (code {bt.CODES[label]}){tag}"
    if t is None:
        print(f"{stamp:10.1f} ms  {what}  FAILED: {trig.error}")
    else:
        print(f"{stamp:10.1f} ms  {what}")
    return time.perf_counter()


def run_single(trig, start):
    for i in range(5):
        t = send(trig, "test", start, f" {i + 1}")
        if i < 4:
            wait_ms(trig, 2000, t)


def run_code(trig, start, code):
    for i in range(5):
        t = trig.pulse("code", code)
        stamp = (time.perf_counter() - start) * 1000.0
        if t is None:
            print(f"{stamp:10.1f} ms  code {code} (hex {code:02X})  FAILED: {trig.error}")
        else:
            print(f"{stamp:10.1f} ms  code {code} (hex {code:02X})  {i + 1}")
        if i < 4:
            wait_ms(trig, 1000, time.perf_counter())


def run_hold(trig, all_lines):
    what = "all 7 data lines (1401 pins 21, 8, 20, 7, 19, 6, 18)" if all_lines else "data line 1 (BBTK pin 2 -> 1401 pin 21)"
    if not trig.hold(True, all_lines):
        print(f"hold FAILED: {trig.error}")
        return
    print(f"{what} HIGH. Red LED(s) on the BBTK lit; meter on the 1401 end, pin 21 vs pin 13: about 5 V.")
    print("Strobe (line 8 -> pin 23) stays HIGH, so Spike2 shows nothing. Ctrl+C drops the data lines.")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    ok = trig.hold(False)
    print("LOW." + ("" if ok else f" (drop FAILED: {trig.error})") + " Meter: expect 0 V on pin 21; LED 8 (strobe) stays lit.")


def run_trials(trig, start, n_trials, gap_ms, block=None):
    for k in range(n_trials):
        tag = f" block {block}, trial {k + 1}" if block else f" trial {k + 1}"
        t = send(trig, "a", start, tag)
        wait_ms(trig, bt.AB_NOMINAL_MS, t)
        t = send(trig, "b", start, tag)
        wait_ms(trig, bt.BC_NOMINAL_MS, t)
        t = send(trig, "c", start, tag)
        if k < n_trials - 1:
            wait_ms(trig, gap_ms, t)
    return t


def run_session(trig, start):
    for blk in range(1, bt.BLOCKS_PER_SESSION + 1):
        t = run_trials(trig, start, bt.TRIALS_PER_BLOCK, 3000, block=blk)
        if blk < bt.BLOCKS_PER_SESSION:
            print(f"--- end of block {blk}, 10 s pause ---")
            wait_ms(trig, 10000, t)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=bt.DEFAULT_PORT)
    ap.add_argument("--mock", action="store_true", help="no hardware; use the fake port")
    ap.add_argument("--mode", choices=("single", "trial", "session", "hold", "code"), default="single")
    ap.add_argument("--trials", type=int, default=3, help="trials for --mode trial")
    ap.add_argument("--all", action="store_true", help="--mode hold: raise all 7 data lines, not just line 1")
    ap.add_argument("--code", type=int, default=bt.CODE_MAX, help=f"--mode code: the code to send, 0-{bt.CODE_MAX} (default all seven lines)")
    args = ap.parse_args()

    if args.mock:
        os.environ[bt.MOCK_ENV] = "1"
    trig = bt.Trigger()
    ok, msg = trig.open(args.port)
    print(("mock port" if trig.mock else "port") + f": {msg}")
    if ok and not trig.answered:
        print("WARNING: the module did not answer the ## ping. Markers may not be going out.")
    if not ok:
        print("ports seen:", trig.list_ports() or "none")
        return 1

    if not 0 <= args.code <= bt.CODE_MAX:
        print(f"--code must be 0-{bt.CODE_MAX}")
        return 2
    n_expected = {"single": 5, "trial": 3 * args.trials, "hold": 0, "code": 5,
                  "session": 3 * bt.TRIALS_PER_BLOCK * bt.BLOCKS_PER_SESSION}[args.mode]
    print(f"mode {args.mode}: {n_expected} markers. Ctrl+C to stop.")
    fake = trig._ser if trig.mock else None      # kept so the log survives close()
    start = time.perf_counter()
    try:
        if args.mode == "hold":
            run_hold(trig, args.all)
        elif args.mode == "single":
            run_single(trig, start)
        elif args.mode == "code":
            run_code(trig, start, args.code)
        elif args.mode == "trial":
            run_trials(trig, start, args.trials, 5000)
        else:
            run_session(trig, start)
        # let the last strobe rise before the port closes
        wait_ms(trig, bt.PULSE_MIN_MS + 5, time.perf_counter())
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        trig.close()

    if trig.failed:
        print(f"link failed during the run: {trig.error}")
    if fake is not None:
        print("\nFakeSerial write log (ms since epoch, bytes):")
        for t_ms, data in fake.writes:
            print(f"{t_ms - trig.epoch * 1000.0:10.1f}  {data!r}")
        print(f"{len(fake.writes)} writes")
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
