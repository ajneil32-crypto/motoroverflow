# Motor Overflow — cheat sheet

## What each file does

| File | What it does |
| --- | --- |
| `motor_overflow9.py` | The study itself. Trigger Setup screen, handedness, participant ID, MIQ-3, metronome familiarisation, reach calibration, the four blocks (ME, then AO/KMI/VMI in a counterbalanced order), MAAS after every block, NASA-TLX, demographics. Sends the three Spike2 markers per trial (a, b, c) and writes every CSV. |
| `bbtk_trigger.py` | The link to the Black Box Toolkit USB TTL module: opens the COM port, sends each marker (line 8, the strobe, up then down: `80` `00`), puts it back up a frame later, and records a failure instead of crashing. Also the decoder that groups marker times into a/b/c trial triplets by their spacing. Run directly to list ports or decode a file. |
| `trigger_test.py` | Bench tool. Sends markers to Spike2 without running the task: 5 test markers, fake trials, or a fake 32-trial session. |
| `spike2\Motor_Overflow_Config_v3.s2cx` | Spike2 sampling configuration: the two EMG channels plus channel 32 `Trig`, a Digital Marker channel read from the rear Digital Input connector. |
| `spike2\mo_label_export.s2s` | Spike2 script to run after a session: groups the `Trig` markers (all `AA`) into trials by their 2.2 s / 15 s spacing, writes a `Labels` TextMark channel (`1a 1b 1c …`) into the `.smrx`, and writes a report saying whether all 32 trials were found. Also accepts an Event channel titled `Trig`. |
| `tests\test_bbtk_trigger.py` | Runs the trigger module against a fake serial port. `TestOpen`: reset then idle (`80`) on open, a silent module is flagged, missing or wrong `serial` package gives a plain-English reason, reopening closes the old port, `MO_TRIGGER_MOCK` selects the fake. `TestPulse`: each marker is line up then down (`80` `00`), the line is held down at least 5 ms then put back up, a marker while the line is down puts it up first, a write failure is recorded not raised, close is safe to repeat, test pulses are rate-limited. `TestDecoder`: clean sessions, shuffled input, test pulses unmatched, a dropped b, a stray marker 2.2 s before an a, window edges, single-block and empty files. `TestCli`: `decode` on a task CSV and on a two-column time/code file. |
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
| `py -m unittest discover -s tests -v` | Runs the 43 automated tests on the trigger module and the task file. Must end `OK`. |
| `py bbtk_trigger.py ports` | Lists every COM port Windows sees, with its description. Run with the BBTK plugged in, then unplugged, to identify it. |
| `py trigger_test.py --mock --mode single` | Runs the bench tool against a fake port and prints every byte it would have sent, with timings. |
| `py bbtk_trigger.py decode mo_task\<pid>_<hand>_mo_task.csv` | Decodes the marker times the task recorded into a/b/c triplets. Expect `triplets: 32`, `unmatched: 0`. |
| `py bbtk_trigger.py decode <file>` | Same, on a file whose first column is the time in ms (e.g. a text export of the Trig channel). |

## Bench tool (hardware, Spike2 sampling)

| Command | What it does |
| --- | --- |
| `py trigger_test.py --mode single` | 5 test markers, 2 s apart. Expect 5 markers on Trig, all `AA`. |
| `py trigger_test.py --mode trial --trials 3` | 3 fake trials: a, +2.2 s b, +15 s c, 5 s gap. Expect 9 markers in three groups. |
| `py trigger_test.py --mode session` | Fake 32-trial session, 4 blocks × 8, about 12 min. Save the Spike2 file and run `mo_label_export.s2s` on it. |
| `Ctrl+C` | Stops any bench run and puts the line back up. |

## Running the task

| Command | What it does |
| --- | --- |
| `py motor_overflow9.py` | Starts the study. Trigger Setup screen first: status must read `BBTK answered on COMn`. |
| `set MO_TRIGGER_MOCK=1` | Makes the next task run use a fake port (setup screen shows MOCK MODE in red). For dry runs only. |
| `set MO_TRIGGER_MOCK=` | Clears mock mode in this window. Or close the window. |
| `Esc` (in the task) | Quits, puts the line back up, releases the port. |

## Spike2 PC (no Command Prompt needed)

Once per PC: Edit → Preferences → Sampling → tick **Use rear connector for
events**. Then every session: load `spike2\Motor_Overflow_Config_v3.s2cx`,
Sample → Start, and after the session run `spike2\mo_label_export.s2s`
(Script → Run). Nothing is installed on this machine for the trigger; the
BBTK's driver lives on the Py Computer.

## Cable

The straight DB25 cable supplied with the BBTK, into the Micro1401's rear
Digital Inputs connector. Only the strobe (BBTK line 8, pin 9) reaches the
1401 (pin 23), so every marker reads `AA` and the script labels them by
spacing.
