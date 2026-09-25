"""Bench tool: sends digital markers to Spike2 through the BBTK without running
the task. Run from a Command Prompt in this folder while Spike2 is sampling.

    py trigger_test.py [--port COM12] [--mock] [--mode single|trial|session] [--trials N]

single   5 test markers, 2 s apart. Look for 5 markers on Trig.
trial    N trials (default 3): a, +2200 ms b, +15000 ms c, then
         a 5 s gap.
session  32 trials in 4 blocks of 8, 3 s between trials, 10 s between blocks
         (about 12 minutes). Save the Spike2 file and run mo_label_export.s2s
         on it: the report must show 32 triplets with positions assigned.

Ctrl+C stops it and puts the line up. --mock uses the fake port and prints
every write at the end so the timing can be checked without hardware.
"""

import argparse
import os
import sys
import time

import bbtk_trigger as bt


def wait_ms(trig, ms, t0):
    """Sleep in 1 ms steps, servicing the trigger so the line goes back up on time."""
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
    what = f"{label}{tag}"
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
    ap.add_argument("--mode", choices=("single", "trial", "session"), default="single")
    ap.add_argument("--trials", type=int, default=3, help="trials for --mode trial")
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

    n_expected = {"single": 5, "trial": 3 * args.trials,
                  "session": 3 * bt.TRIALS_PER_BLOCK * bt.BLOCKS_PER_SESSION}[args.mode]
    print(f"mode {args.mode}: {n_expected} markers. Ctrl+C to stop.")
    fake = trig._ser if trig.mock else None      # kept so the log survives close()
    start = time.perf_counter()
    try:
        if args.mode == "single":
            run_single(trig, start)
        elif args.mode == "trial":
            run_trials(trig, start, args.trials, 5000)
        else:
            run_session(trig, start)
        # let the line go back up before the port closes
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
