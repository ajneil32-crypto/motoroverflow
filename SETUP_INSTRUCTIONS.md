# Motor Overflow – Python → Spike2 trigger setup, start to finish

## 1. What the system does

Every trial of the task sends three **digital markers** from the Black Box
Toolkit USB-TTL module straight into the **Digital Input** connector on the
back of the CED 1401. Spike2 records each marker on a Digital Marker channel
called **Trig** alongside the two EMG channels:

| Marker | Moment | Spacing |
| --- | --- | --- |
| a | participant's finger lands on the X (rest starts) | — |
| b | trial clock starts (rest ends; this is t = 0 in the task CSV) | 2200 ms after a |
| c | closing bell (trial ends) | 15 000 ms after b |

The cable carries only the timing, not a code, so every marker (including the
test pulses from the Trigger Setup screen) reads the same byte, `AA`. The
markers are told apart by their spacing: after the session a Spike2 script
finds every marker followed by one 2.2 s later and another 15 s after that,
writes the labels `1a 1b 1c … 4c` (block number + letter) into the file as a
`Labels` channel.

8 trials × 3 markers × 4 blocks = **96 markers per session**, one Spike2 file
per session.

How a marker travels: BBTK output line 8 is wired to the 1401's Data
Available input (Digital Input pin 23). The line idles up; the Python sends
`80` (line up) then `00` (line down), and the 1401 records a marker at that
fall. One screen frame later the line goes back up, ready for the next marker.

Nothing about the task's timing, touch handling, trial order or questionnaires
depends on the trigger. If the trigger box is missing or fails mid-session the
task carries on and simply records that the markers were not sent.

## 2. File guide

### Py Computer — task folder `Desktop\motor_overflow\`

| File | What it is | Who uses it, when |
| --- | --- | --- |
| `motor_overflow9.py` | The task, including the trigger additions: a Trigger Setup screen before the handedness screen, the three markers per trial, four trigger columns in `_mo_task.csv`, seven in `_mo_session.csv`, and a warning line on the Trial Complete screen if the link drops. Run this for every participant. | RA, every session |
| `bbtk_trigger.py` | The trigger link and the decoder. Imported by the task; must sit in the same folder. Talks to the Black Box Toolkit over USB (`RR` reset, then `80` = line up, `00` = line down). Sends each marker the instant it is asked for and puts the line back up one screen frame later without ever pausing the task. Also holds `decode()`, the rule that groups marker times into a/b/c trials by their spacing — the Spike2 script is a line-by-line copy of it. From a command prompt it can list COM ports (`py bbtk_trigger.py ports`) and check a session's CSV (`py bbtk_trigger.py decode <file>`). | Task (automatically); you, for checks |
| `trigger_test.py` | Bench tool. Sends markers without running the task so you can watch them arrive in Spike2. `--mode single` = 5 test markers; `--mode trial` = a/b/c patterns; `--mode session` = a fake 32-trial session (~12 min) for testing the Spike2 script end to end. `--mock` runs it without hardware. | You, once before piloting; again if the cable is ever in doubt |
| `tests\test_bbtk_trigger.py` | Automated checks of the trigger module: correct bytes in the correct order, pulse width, failure handling, decoder on simulated sessions, command line. | Anyone editing the code |
| `tests\test_study_anchors.py` | Automated checks that the markers in `motor_overflow9.py` sit at exactly the right lines of code, that the timing constants agree, and that nothing else sends a marker. Reads the file; never runs it. | Anyone editing the code |
| `bell.wav`, `nimbl_logo.png`, `ao_video_*.mp4` × 8 | Sound, logo and video stimuli. The videos are supplied per lab machine and are not in the repository. | Task |

The older ME-only version of the task, `motor_execution.py`, is in git history
(`git show 674b2aa:motor_execution.py`); nobody uses it day to day.

### Spike2 PC — folder `spike2\`

| File | What it is | Who uses it, when |
| --- | --- | --- |
| `Motor_Overflow_Config_v3.s2cx` | Sampling configuration. The original `Motor_Overflow_Config.s2cx` with one channel added: channel 32 Trig, a Digital Marker channel. EMG settings untouched. Load it before every session. Made by editing the configuration file directly, so **check the channel in the Sampling Configuration dialog once** (§5) and re-save. | RA, every session |
| `mo_label_export.s2s` | Labelling script. Run on a recorded `.smrx` (or a folder of them) after the session. Reads the Trig marker times, groups them into a/b/c trials by spacing, writes a Labels TextMark channel into the file, and writes `{name}_labels_report.txt` saying whether all 32 trials were found. Skips files ending `_partial`. | Analyst, after recording |
| `..\Motor_Overflow_Config.s2cx` (on the Desktop, one level above the task folder) | The original configuration, EMG only. Never modified; the fallback if v3 will not load — add the Digital Marker channel to it by hand as in §5. | Only if v3 fails |

## 3. Part A — Py Computer: Python setup

### A1. Install (once per machine)

1. Python 3.14 for Windows from python.org, with "Add to PATH" and the `py`
   launcher ticked.
2. Open **Command Prompt** and install the packages the task needs:

   ```
   py -m pip install pygame-ce opencv-python pyserial
   ```

   Never install a package called plain `serial` — it is unrelated and breaks
   pyserial.
3. Put the task folder on the Desktop: `Desktop\motor_overflow\` containing
   `motor_overflow9.py`, `bbtk_trigger.py`, `trigger_test.py`,
   `bell.wav`, `nimbl_logo.png`, the eight
   `ao_video_*.mp4` files, and the `tests\` and `spike2\` folders.

If `py` is not recognised on a machine, use `python` in every command below.

### A2. Check the installation

From Command Prompt, in the task folder:

```
cd %USERPROFILE%\Desktop\motor_overflow
py -m unittest discover -s tests -v
```

Expect `OK` at the end (43 tests). This proves the trigger module and the task
file are intact; it does not need the hardware.

### A3. Find the BBTK's COM port

Plug the Black Box Toolkit USB-TTL module into the Py Computer. Then either:

- **Device Manager → Ports (COM & LPT)** — look for the USB serial / Black Box
  entry, e.g. `COM12`; or
- `py bbtk_trigger.py ports` — prints every port with its description.

The task tries `COM12` first. If the BBTK is on another port the Trigger Setup
screen lists the ports as buttons; tap the right one. You do not have to edit
anything.

While in Device Manager, open the port's **Port Settings → Advanced** and set
the **Latency Timer** to 1 ms. Otherwise the USB driver can hold the two
commands of a marker back for up to 16 ms.

### A4. Dry run without hardware (optional)

```
set MO_TRIGGER_MOCK=1
py motor_overflow9.py
```

The setup screen shows **MOCK MODE** in red. Run through a session with the
mouse using a spare participant ID (e.g. 998). Afterwards:

```
py bbtk_trigger.py decode mo_task\998_rh_mo_task.csv
```

must print `triplets: 32`, `unmatched: 0`, `positions: assigned`. Then delete
that ID's files from every `mo_*` folder, or the ID is blocked next time.

## 4. Part B — Hardware

```
Py Computer USB-A → BBTK USB-TTL (USB-B) → DB25 cable → CED Micro1401 rear digital connector
```

Signals flow from the BBTK into the 1401. Nothing else links the two
computers; the 1401 is the only shared clock. EMG runs 1902 → 1401 exactly as
before and the trigger cable does not touch it.

### B1. The cable

The DB25 male-to-female cable supplied with the BBTK, plugged into the rear
Digital Inputs connector. The two pinouts differ, so only the strobe gets
through: BBTK line 8 reaches pin 23 (Data Available), while the BBTK's ground
block lands on some of the 1401's data bits and its data lines land on pins
the marker does not read. The result is that **every marker arrives at the
right time with the same byte, `AA`**. That is expected: the labelling script
groups the markers by spacing (§1). Nothing needs changing in Python or
Spike2 for this.

### B2. Optional: an Event channel on the rear connector

The rear connector also carries event inputs (Edit → Preferences → Sampling →
*Use rear connector for events*). BBTK line 8 — the
strobe, which pulses LOW for 5 ms at every marker — lands on 1401 pin 9, and
lines 1–3 on pins 2–4. If one of those pins is an event input on this
Micro1401, an **Event-** channel (falling edge) titled `Trig` on that port
records the same pulses with no code byte at all, and the script labels it
by spacing exactly as for the Digital Marker channel. To find out, put Event channels on every event
port, run `py trigger_test.py --mode single`, and see which one ticks. If one
does, use it instead of the Digital Marker channel; nothing else changes.

## 5. Part C — Spike2 PC: configuration

Copy the `spike2\` folder anywhere on the recording PC.

### C1. Preferences (once per PC)

**Edit → Preferences → Sampling**: tick **Use rear connector for events**.

### C2. Load the supplied configuration

1. Spike2 → **File → Load Configuration…** → `Motor_Overflow_Config_v3.s2cx`.
2. **Sample → Sampling Configuration…** → **Channels** tab. Confirm:

   | Ch | Title | Type | Port | Rate |
   | --- | --- | --- | --- | --- |
   | 1 | FDI_L | Waveform | ADC 0 (1902) | 2000 Hz |
   | 2 | FDI_R | Waveform | ADC 2 (1902) | 2000 Hz |
   | 31 | Keyboard | Marker | — | — |
   | 32 | Trig | Digital Marker | rear Digital Input, bits 7–0 | max 100/s |

3. If channel 32 is missing (v3 did not carry it over), add it: **New Channel
   → Digital Marker**, title `Trig`, maximum event rate `100`. Digital marker
   is always channel 32. Nothing else to set: on the Micro1401 and Power1401
   the marker is read from data bits 7–0 whenever pin 23 sees a low-going
   pulse.
4. **File → Save Configuration** back over `Motor_Overflow_Config_v3.s2cx`.

The maximum event rate only sizes a buffer. The real rate is under one marker
a second (three per 17 s trial, one a second at most for test pulses).

### Display

After the first sampling run, right-click the **Trig** channel → **Draw Mode**
and choose a mode that shows the marker code as text, so each marker is
visible as `AA`. After the script has run, the Labels channel carries the
`1a 1b 1c` text. Save the configuration again.

## 6. Part D — Bench test (before pilot 999)

Both machines on, cable connected, Spike2 loaded with v3. Command Prompt on the
Py Computer in the task folder. If the BBTK is not on `COM12`, add
`--port COMn` to each command.

| Step | Do | Pass when |
| --- | --- | --- |
| 1 | Spike2: Sample → Start. Py Computer: `py trigger_test.py --mode single` | Exactly 5 markers on Trig, 2 s apart, all `AA` |
| 2 | Start sampling. `py trigger_test.py --mode trial --trials 3` | 3 groups of 3 markers: 2.2 s then 15 s |
| 3 | Start sampling. `py trigger_test.py --mode session` (~12 min). Stop; save as `bench_rh_mo.smrx` in a folder of its own | 96 markers on Trig |
| 4 | Spike2: open `mo_label_export.s2s`, Script → Run, answer No, pick that folder | Report: `triplets: 32`, `positions: assigned (session, 4 x 8)`, `unmatched: 0`. Labels channel reads `1a 1b 1c … 4c` |

Step 4 is where the script's `VERIFY` lines get exercised. If it stops with an
error, the line number points at a call to check against Spike2 Help (press F1
on the function name); fix it, then delete the `UNTESTED` line from the script
header.

## 7. Part E — Running a session

1. Power the 1401 and 1902. Plug the BBTK into the Py Computer.
2. Spike2: File → Load Configuration → `Motor_Overflow_Config_v3.s2cx`.
3. Py Computer: `py motor_overflow9.py`. The **Trigger Setup** screen appears
   first.
4. The status line must be green: **BBTK answered on COMn — now check
   Spike2**. If red, it says why (`COM12 not found`, `in use by another
   program`, `pyserial not installed`…) — tap the correct port button, or fix
   the cause and tap **Rescan**.
5. **Start Spike2 sampling now.**
6. Tap **Test Pulse** two or three times, a second apart. One marker on Trig
   per tap, reading `AA`. The screen counts them.
7. Tap **Continue** and hand over the tablet. The rest of the session is
   unchanged.
8. **Keep Spike2 sampling continuously** through calibration, all four blocks
   and the questionnaires. Do not stop between blocks. Do not press keys in
   Spike2.
9. After the **Task Complete** screen: Sample → Stop. **File → Save As** →
   `{pid}_{hand}_mo.smrx`, e.g. `012_rh_mo.smrx` (`rh`/`lh`, matching the
   Python file names).
10. Close the task; it puts the line back up and releases the port.

Expected in the file: your test pulses, then 96 task markers in groups of
three (2.2 s, then 15 s apart), and none during calibration, familiarisation,
MIQ-3, questionnaires or Trial Complete screens. There may also be one
marker from the moment the task opened the port; the script leaves it
unmatched.

If a Trial Complete screen shows **Trigger link lost — tell the experimenter**,
note the trial and let the session finish; see §9.

**Crash:** stop sampling, save as `{pid}_{hand}_mo_partial.smrx` (the script
skips it). To rerun the same ID, first move that ID's files out of every
`Desktop\motor_overflow\mo_*` folder into an archive folder, then restart the
whole session with a new Spike2 file.

## 8. Part F — After recording: labelling

1. Put the session `.smrx` files to process in one folder.
2. Spike2: File → Open → `mo_label_export.s2s` → **Script → Run**.
3. First question: Yes = one file, No = whole folder.
4. When the summary appears, read the Log window and each
   `{name}_labels_report.txt`.

Per file the script produces:

| Output | Content |
| --- | --- |
| Labels channel, saved into the `.smrx` | One TextMark per matched marker. Text `1a 1b 1c 2a … 4c`. Codes `[letter, block, trial, 0]` where letter is 1 = a, 2 = b, 3 = c. Re-running replaces it. |
| `{name}_labels_report.txt` | File name, pid/hand, number of Trig markers, number of triplets, position status, any unmatched markers, a→b and b→c min/mean/max. |

Reading the report:

| Report | Meaning | Action |
| --- | --- | --- |
| 32 triplets, positions assigned | every trial labelled | done |
| 32 triplets, some unmatched | test pulses, or the marker from opening the port | fine |
| < 32 triplets, positions UNASSIGNED | a marker was lost, or a trial's spacing fell outside the windows (e.g. an AO video frame running very late); labels written as a b c without block/trial | §9 |
| "no Trig channel" / "neither a marker nor an event channel" | wrong configuration loaded or channel renamed | load v3, rename to Trig, re-run |

## 9. Part G — The data and how it joins

### Trigger columns in `{pid}_{hand}_mo_task.csv` (one row per trial)

| Column | Meaning |
| --- | --- |
| `trig_a_ms`, `trig_b_ms`, `trig_c_ms` | when each marker's line went down, ms on the task's own clock; blank if not sent |
| `trig_ok` | 1 if all three were sent, else 0 |

### Trigger columns in `{pid}_{hand}_mo_session.csv`

| Column | Meaning |
| --- | --- |
| `triggers_enabled` | 1 if the BBTK was connected when Continue was tapped; 0 = session ran without markers |
| `trigger_mock` | 1 = mock run; discard the data |
| `trigger_port` | COM port used |
| `trigger_epoch` | wall-clock time the trigger clock started |
| `n_test_pulses` | how many test-pulse markers precede the task markers in the Spike2 file |
| `trig_ab_nominal_ms`, `trig_bc_nominal_ms` | 2200 and 15000 — the spacings the script groups by |

### Joining Spike2 to the CSVs

- Each item on the Labels channel carries codes `[letter, block, trial, 0]`:
  code 1 is 1 = a, 2 = b, 3 = c. Every `b` item is a trial-clock start. Trig
  itself holds every raw marker, all `AA`, including test pulses and strays.
- Labels code 2 (block 1–4) = `condition_position` in `_mo_task.csv`;
  `cond_1 … cond_4` in `_mo_session.csv` give the condition name
  (ME/KMI/VMI/AO).
- Labels code 3 (trial 1–8) = `trial_index`.
- `pid` and `hand` come from the file name.
- EMG windows: rest baseline = a→b; trial = b→c.
- Spike2 seconds and Python `trig_*_ms` differ by one constant per session.
  Fit it as the median of `spike2_b − trig_b_ms/1000` over matched triplets;
  residuals are a few ms.

### Recovering when positions are unassigned

Each unmatched marker in the report is a broken trial. Use the timing to
work out which marker survived: a lone pair about 17.2 s apart means that trial's b was lost. Count the good
triplets before it to find its position. For
certainty, apply the constant offset above to every CSV row's `trig_b_ms`: the
nearest Trig marker belongs to that row's block and trial. Rows with
`trig_ok = 0` tell you which marker the task itself never sent.

## 10. Part H — Quick problem finder

| Symptom | Fix |
| --- | --- |
| Setup screen: `COM12 not found` | Tap the BBTK's port in the list; or replug and Rescan |
| Setup screen: `in use by another program` | Close the other program (an old task window, a terminal), Rescan |
| Setup screen: `pyserial not installed` | `py -m pip install pyserial` |
| Setup screen: `wrong 'serial' package installed` | `py -m pip uninstall -y serial pyserial` then `py -m pip install pyserial` |
| No markers on Trig for test pulses | Cable in the Digital **Input** connector? Pin 23 connected? Channel 32 Digital Marker present? Sampling started? |
| Every marker is `AA` | Expected: the cable carries only the strobe (Part B1). The script labels by spacing |
| Report: 0 triplets although markers are visible | Channel not titled Trig, or timing constants changed in one file only. The Log prints the first gaps next to the windows they must fall in |
| Report: some triplets missing | A stray or missing pulse. See §9 |
| Task will not start: `ModuleNotFoundError: bbtk_trigger` | `bbtk_trigger.py` is not beside the task |
| Task will not start: `AssertionError` | `REST_MS + BEGIN_MS ≠ 2200` or `TRIAL_SECONDS × 1000 ≠ 15000` — change all three files together or restore the values |
