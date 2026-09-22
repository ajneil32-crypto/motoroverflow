"""Bench self-test of the BBTK with no cable: does output 1 reach the DB25?

    py bbtk_loopback.py [--port COM12]

Holds data line 1 (output 1, pin 2) HIGH, with the strobe line 8 HIGH as it
always is when idle, and prints every byte the module sends until Ctrl+C. With the gender changer on the module, push a U-shaped paperclip into
holes 2 and 10 (output 1 -> input 1). If pin 2 carries the 5 V, the green
input-1 LED lights and the module reports the input change here as a hex
pair. Lift the paperclip and it reports the change back. Nothing printed and
no green LED while bridged means the output is not reaching the pin.
"""

import argparse
import sys
import time

import bbtk_trigger as bt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=bt.DEFAULT_PORT)
    args = ap.parse_args()

    trig = bt.Trigger()
    ok, msg = trig.open(args.port)
    print("port:", msg)
    if not ok:
        return 1
    ser = trig._ser
    ser.timeout = 0.05
    if not trig.hold(True):
        print("hold FAILED:", trig.error)
        return 1
    print("output 1 (pin 2) HIGH, red LEDs 1 and 8 lit (8 is the idle strobe). Bridge holes 2 and 10 now. Ctrl+C to stop.")
    t0 = time.perf_counter()
    try:
        while True:
            data = ser.read(64)
            if data:
                print(f"{(time.perf_counter() - t0) * 1000:9.0f} ms  module sent {data!r}")
    except KeyboardInterrupt:
        pass
    finally:
        trig.close()
    print("\nidle, closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
