# Motor Overflow — cheat sheet

## What each file does

| File | What it does |
| --- | --- |
| `motor_overflow9.py` | The study itself. Trigger Setup screen, handedness, participant ID, MIQ-3, metronome familiarisation, reach calibration, the four blocks (ME, then AO/KMI/VMI in a counterbalanced order), MAAS after every block, NASA-TLX, demographics. Sends the three Spike2 markers per trial (codes 1, 2, 3) and writes every CSV. |
| `bbtk_trigger.py` | The link to the Black Box Toolkit USB TTL module: opens the COM port, sends each marker (the code on lines 1–7, then the strobe on line 8 pulled LOW), raises the strobe a frame later, and records a failure instead of crashing. Also the decoder that turns marker codes back into a/b/c trial triplets. Run directly to list ports or decode a file. |
| `trigger_test.py` | Bench tool. Sends markers to Spike2 without running the task: hold a data line high for a meter, 5 test markers, fake trials, or a fake 32-trial session. |
| `bbtk_loopback.py` | Bench self-test with no cable: holds output 1 high and prints what the module sends back when pin 2 is bridged to an input. |
| `spike2\Motor_Overflow_Config_v3.s2cx` | Spike2 sampling configuration: the two EMG channels plus channel 32 `Trig`, a Digital Marker channel read from the rear Digital Input connector. |
| `spike2\mo_label_export.s2s` | Spike2 script to run after a session: groups the `Trig` markers into trials — by code if they carry 1/2/3, by the 2.2 s / 15 s spacing if they all read the same (`AA`, the straight cable) — writes a `Labels` TextMark channel (`1a 1b 1c …`), rewrites the Trig codes to 1/2/3 + block + trial, exports `.mat`, and writes a report saying whether all 32 trials were found. Also accepts an Event channel titled `Trig`. |
| `tests\test_bbtk_trigger.py` | Runs the trigger module against a fake serial port. `TestCmd`: hex commands, codes fit in 7 bits. `TestOpen`: reset then idle (`80`) on open, a silent module is flagged, missing or wrong `serial` package gives a plain-English reason, reopening closes the old port, `MO_TRIGGER_MOCK` selects the fake. `TestPulse`: data goes out before the strobe drops, the strobe is held LOW at least 5 ms then raised with the data kept, a marker while the strobe is LOW raises it first, a write failure is recorded not raised, close is safe to repeat, test pulses are rate-limited and coded 100, hold never touches the strobe. `TestDecoder`: clean sessions, shuffled input, test pulses and code 0 ignored, a dropped b, a stray marker inside a trial, codes decide and spacing only flags, window edges, single-block and empty files. `TestCli`: `decode` on a task CSV and on a two-column time/code file. |
| `tests\test_study_anchors.py` | Reads `motor_overflow9.py` as a syntax tree, never runs it. Checks that markers a and b happen once each in the rest period (a before the touch is logged, b before the metronome starts); that each trial runner sends c exactly once, before its bell and before saving; that no other function sends a marker and `test_pulse` exists only on the setup screen; that nothing sleeps on the marker path; that `flip()` services the strobe after the display flip and every exit closes the port; that `REST_MS + BEGIN_MS` and `TRIAL_SECONDS` agree with `bbtk_trigger.py`; that the setup screen runs before the handedness screen; and that the trigger columns exist in the task and session CSVs. |
| `bell.wav`, `nimbl_logo.png` | Trial bell and welcome-screen logo. Optional: the task runs without either. |
| `ao_video_*.mp4` | The eight action-observation clips (2 models × 2 hands × 2 sizes). Supplied per lab machine; not in the repo. |
| `README.md` | Overview of the study and the repo. |
| `SETUP_INSTRUCTIONS.md` | Cabling, Spike2 configuration, bench test, and running a session, start to finish. |

## Command Prompt

All commands run from the task folder on the Py Computer. If `py` is not
recognised, use `python` instead. If the BBTK is not on COM12, add
`--port COMn` to any `trigger_test.py` command.

```
cd %USERPROFILE%\Desktop\motor_overflow
```

## Setup (once per machine)

| Command | What it does |
| --- | --- |
| `py -m pip install pygame-ce pyserial` | Installs the two packages the task needs. |
| `py -c "import serial; print(serial.Serial)"` | Proves pyserial is the real one. Must print `serial.serialwin32.Serial`. |
| `py -m pip uninstall -y serial pyserial` then `py -m pip install pyserial` | Repairs a machine where the wrong `serial` package got installed. |
| `devmgmt.msc` | Opens Device Manager: find the BBTK's COM port; set its Latency Timer to 1 ms (Port Settings → Advanced). |

## Checks without hardware

| Command | What it does |
| --- | --- |
| `py -m unittest discover -s tests -v` | Runs the 49 automated tests on the trigger module and the task file. Must end `OK`. |
| `py bbtk_trigger.py ports` | Lists every COM port Windows sees, with its description. Run with the BBTK plugged in, then unplugged, to identify it. |
| `py trigger_test.py --mock --mode single` | Runs the bench tool against a fake port and prints every byte it would have sent, with timings. |
| `py bbtk_trigger.py decode mo_task\<pid>_<hand>_mo_task.csv` | Decodes the marker times the task recorded into a/b/c triplets. Expect `triplets: 32`, `unmatched: 0`, `timing: 0 triplet(s) outside`. |
| `py bbtk_trigger.py decode <file>` | Same, on a two-column `time_ms,code` file (e.g. a text export of the Trig channel). |

## Bench tool (hardware, Spike2 sampling)

| Command | What it does |
| --- | --- |
| `py trigger_test.py --mode hold` | Opens the port, pings the BBTK (`BBTK answered` or a warning), then holds data line 1 HIGH until Ctrl+C. LEDs 1 and 8 on the module light (8 is the idle strobe); meter on the 1401 end, pin 21 vs pin 13, reads ~5 V, then 0 V. Spike2 records nothing. |
| `py trigger_test.py --mode hold --all` | Same, but raises all 7 data lines (1401 pins 21, 8, 20, 7, 19, 6, 18). If this reads 5 V on a pin and plain `hold` reads 0 V on pin 21, a line is on the wrong pin. |
| `py trigger_test.py --mode single` | 5 test markers, 2 s apart. Expect 5 markers on Trig (`AA` with the straight cable, `64` with a mapped one). |
| `py trigger_test.py --mode code --code 127` | 5 markers of one chosen code, 1 s apart. 127 raises all seven data lines (Spike2 shows `7F`); `--code 1`, `2`, `4`, `8`, `16`, `32`, `64` raise one line each (`01` … `40`). If the code shown never changes, the data lines are not on the bits the 1401 reads. |
| `py trigger_test.py --mode trial --trials 3` | 3 fake trials: a, +2.2 s b, +15 s c, 5 s gap. Expect 9 markers in three groups (mapped cable: codes 1 2 3). |
| `py trigger_test.py --mode session` | Fake 32-trial session, 4 blocks × 8, about 12 min. Save the Spike2 file and run `mo_label_export.s2s` on it. |
| `Ctrl+C` | Stops any bench run and parks the lines idle. |

## Running the task

| Command | What it does |
| --- | --- |
| `py motor_overflow9.py` | Starts the study. Trigger Setup screen first: status must read `BBTK answered on COMn`. |
| `set MO_TRIGGER_MOCK=1` | Makes the next task run use a fake port (setup screen shows MOCK MODE in red). For dry runs only. |
| `set MO_TRIGGER_MOCK=` | Clears mock mode in this window. Or close the window. |
| `Esc` (in the task) | Quits, parks the lines idle, releases the port. |

## Spike2 PC (no Command Prompt needed)

Once per PC: Edit → Preferences → Sampling → tick **Use rear connector for
events**. Then every session: load `spike2\Motor_Overflow_Config_v3.s2cx`,
Sample → Start, and after the session run `spike2\mo_label_export.s2s`
(Script → Run). Nothing is installed on this machine for the trigger; the
BBTK's driver lives on the Py Computer.

## Cable

The straight DB25 cable supplied with the BBTK, into the Micro1401's rear
digital connector, is what runs the study. Every marker then reads `AA` and
the script labels by spacing. The mapped cable below is optional; it makes
the codes arrive as 1/2/3 on the wire:

| BBTK line (DB25 pin) | 1401 pin | What |
| --- | --- | --- |
| 1 (2), 2 (3), 3 (4), 4 (5) | 21, 8, 20, 7 | data bits 0, 1, 2, 3 |
| 5 (6), 6 (7), 7 (8) | 19, 6, 18 | data bits 4, 5, 6 |
| 8 (9) | 23 | strobe (Data Available), idles HIGH |
| ground (25) | 13 | ground |
| — | 5 → 13 | bit 7 tied to ground |

Two DB25 screw-terminal breakouts and ten wires make the mapped cable
without soldering.
