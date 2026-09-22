# Motor Overflow – Python → Spike2 trigger setup, start to finish

## 1. What the system does

Every trial of the task sends three **digital markers** from the Black Box
Toolkit USB-TTL module straight into the **Digital Input** connector on the
back of the CED 1401. Spike2 records each marker, with its code, on a Digital
Marker channel called **Trig** alongside the two EMG channels:

| Marker | Code | Moment | Spacing |
| --- | --- | --- | --- |
| a | 1 | participant's finger lands on the X (rest starts) | — |
| b | 2 | trial clock starts (rest ends; this is t = 0 in the task CSV) | 2200 ms after a |
| c | 3 | closing bell (trial ends) | 15 000 ms after b |

Test pulses from the Trigger Setup screen carry code **100**.

After the session a Spike2 script groups the markers into trials, writes the
labels `1a 1b 1c … 4c` (block number + letter) into the file as a `Labels`
channel, rewrites the Trig marker codes to match, and exports a `.mat` file
for MATLAB. It groups by one of two rules, chosen automatically:

- **codes** — when the markers carry 1/2/3 (a mapped cable, Part B): every
  1-2-3 run is a trial. Spacing is only a cross-check.
- **spacing** — when they do not (the straight cable that comes with the
  BBTK: every marker reads the same byte, `AA`): a marker followed by one
  2.2 s later and another 15 s after that is a trial. This is the setup on
  the bench now, and it labels every trial just as well; only the raw Trig
  codes look different before the script has run.

8 trials × 3 markers × 4 blocks = **96 markers per session**, one Spike2 file
per session.

How a marker travels: the BBTK has eight TTL output lines. Lines 1–7 carry the
code (bits 0–6, so codes run 0–127) and line 8 is the **strobe**. The Python
puts the code on lines 1–7 with the strobe HIGH, then, one serial command
later, pulls the strobe LOW. The 1401 reads the data bits when it sees that
low-going edge on Digital Input pin 23 (Data Available). One screen frame
later the strobe goes HIGH again; the code stays on the lines until the next
marker replaces it.

Nothing about the task's timing, touch handling, trial order or questionnaires
depends on the trigger. If the trigger box is missing or fails mid-session the
task carries on and simply records that the markers were not sent.

## 2. File guide

### Py Computer — task folder `Desktop\motor_overflow\`

| File | What it is | Who uses it, when |
| --- | --- | --- |
| `motor_overflow9.py` | The task, including the trigger additions: a Trigger Setup screen before the handedness screen, the three markers per trial, four trigger columns in `_mo_task.csv`, seven in `_mo_session.csv`, and a warning line on the Trial Complete screen if the link drops. Run this for every participant. | RA, every session |
| `bbtk_trigger.py` | The trigger link and the decoder. Imported by the task; must sit in the same folder. Talks to the Black Box Toolkit over USB (`RR` reset, then two-character hex commands that set all eight lines at once: `81` = code 1 with the strobe HIGH, `01` = code 1 with the strobe LOW, `80` = idle). Sends each marker the instant it is asked for and raises the strobe one screen frame later without ever pausing the task. Also holds `decode()`, the rule that turns marker codes back into a/b/c trials — the Spike2 script is a line-by-line copy of it. From a command prompt it can list COM ports (`py bbtk_trigger.py ports`) and check a session's CSV (`py bbtk_trigger.py decode <file>`). | Task (automatically); you, for checks |
| `trigger_test.py` | Bench tool. Sends markers without running the task so you can watch them arrive in Spike2. `--mode hold` = park a data line high for a meter; `--mode single` = 5 test markers; `--mode code --code N` = 5 markers of any code, for checking each data line reaches the right bit; `--mode trial` = a/b/c patterns; `--mode session` = a fake 32-trial session (~12 min) for testing the Spike2 script end to end. `--mock` runs it without hardware. | You, once before piloting; again if the cable is ever in doubt |
| `bbtk_loopback.py` | Quick check that the module is alive and its output reaches the DB25, using a paperclip bridge and no cable. | You, if the cable is in doubt |
| `tests\test_bbtk_trigger.py` | Automated checks of the trigger module: correct bytes in the correct order, strobe width, failure handling, decoder on simulated sessions, command line. | Anyone editing the code |
| `tests\test_study_anchors.py` | Automated checks that the markers in `motor_overflow9.py` sit at exactly the right lines of code, that the timing constants agree, and that nothing else sends a marker. Reads the file; never runs it. | Anyone editing the code |
| `bell.wav`, `nimbl_logo.png`, `ao_video_*.mp4` × 8 | Sound, logo and video stimuli. The videos are supplied per lab machine and are not in the repository. | Task |

The older ME-only version of the task, `motor_execution.py`, is in git history
(`git show 674b2aa:motor_execution.py`); nobody uses it day to day.

### Spike2 PC — folder `spike2\`

| File | What it is | Who uses it, when |
| --- | --- | --- |
| `Motor_Overflow_Config_v3.s2cx` | Sampling configuration. The original `Motor_Overflow_Config.s2cx` with one channel added: channel 32 Trig, a Digital Marker channel. EMG settings untouched. Load it before every session. Made by editing the configuration file directly, so **check the channel in the Sampling Configuration dialog once** (§5) and re-save. | RA, every session |
| `mo_label_export.s2s` | Labelling and export script. Run on a recorded `.smrx` (or a folder of them) after the session. Reads the Trig marker codes, groups them into a/b/c trials, writes a Labels TextMark channel into the file, exports `{name}.mat`, and writes `{name}_labels_report.txt` saying whether all 32 trials were found. Skips files ending `_partial`. | Analyst, after recording |
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
   `bbtk_loopback.py`, `bell.wav`, `nimbl_logo.png`, the eight
   `ao_video_*.mp4` files, and the `tests\` and `spike2\` folders.

If `py` is not recognised on a machine, use `python` in every command below.

### A2. Check the installation

From Command Prompt, in the task folder:

```
cd %USERPROFILE%\Desktop\motor_overflow
py -m unittest discover -s tests -v
```

Expect `OK` at the end (49 tests). This proves the trigger module and the task
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

### B1. The straight cable (what is on the bench, and what works)

The DB25 male-to-female cable supplied with the BBTK, plugged into the rear
digital connector. A pin-for-pin cable does **not** deliver the code: the two
pinouts differ, so the BBTK's ground block (pins 18–25) lands on the 1401's
data bits 0, 2, 4, 6 and on pin 23, and the data bits the BBTK does drive land
on pins the marker does not read. The result is that **every marker arrives
at the right time with the same byte, `AA`**. That is fine: the labelling
script groups them by spacing (§1) and then rewrites the codes to 1/2/3, so
after the script the file is identical to one recorded through a mapped
cable. Nothing needs changing in Python or Spike2 for this.

### B2. Optional: a mapped cable, for codes on the wire

If someone can make an adapter (two DB25 screw-terminal breakout boards and
ten wires; no soldering), the markers carry their codes and the script uses
them directly:

| BBTK line | BBTK DB25 pin | → | 1401 Digital Input pin | Meaning |
| --- | --- | --- | --- | --- |
| 1 | 2 | → | 21 | data bit 0 |
| 2 | 3 | → | 8 | data bit 1 |
| 3 | 4 | → | 20 | data bit 2 |
| 4 | 5 | → | 7 | data bit 3 |
| 5 | 6 | → | 19 | data bit 4 |
| 6 | 7 | → | 6 | data bit 5 |
| 7 | 8 | → | 18 | data bit 6 |
| 8 | 9 | → | 23 | Data Available strobe (idles HIGH, pulses LOW) |
| ground | 25 | → | 13 | signal ground |
| — | — | | 5 → 13 | data bit 7, tied to ground at the 1401 end |

The 1401 column is CED's own table for the rear Digital Input connector
(bit 0 = 21, 1 = 8, 2 = 20, 3 = 7, 4 = 19, 5 = 6, 6 = 18, 7 = 5, ground 13,
strobe 23). The BBTK column is printed on the module: outputs 1–8 on pins
2–9, ground on 18–25.

- Bit 7 (1401 pin 5) **must** be tied to ground. An open TTL input reads as
  1, and every code would arrive as 128 + code.
- Wire every data line even though the task only uses codes 1, 2, 3 and 100.
  The BBTK holds unused lines low; an unconnected 1401 input floats high.

### B3. Optional: an Event channel on the rear connector

The rear connector also carries event inputs (Edit → Preferences → Sampling →
*Use rear connector for events*). With the straight cable, BBTK line 8 — the
strobe, which pulses LOW for 5 ms at every marker — lands on 1401 pin 9, and
lines 1–3 on pins 2–4. If one of those pins is an event input on this
Micro1401, an **Event-** channel (falling edge) titled `Trig` on that port
records the same pulses with no code byte at all, and the script labels it
by spacing exactly as in B1. To find out, put Event channels on every event
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
and choose a mode that shows the marker code as text. With the straight cable
every marker reads `AA` until the script has run, then `01 02 03`; with a
mapped cable they read `01 02 03` (and `64` for test pulses) as they arrive.
Save the configuration again.

## 6. Part D — Bench test (before pilot 999)

Both machines on, cable connected, Spike2 loaded with v3. Command Prompt on the
Py Computer in the task folder. If the BBTK is not on `COM12`, add
`--port COMn` to each command.

"Code" below means what Spike2 shows next to the marker: `AA` on every
marker with the straight cable (B1), the real code with a mapped cable (B2).
Either passes.

| Step | Do | Pass when |
| --- | --- | --- |
| 1 | Spike2: Sample → Start. Py Computer: `py trigger_test.py --mode single` | Exactly 5 markers on Trig, 2 s apart (straight cable: all `AA`; mapped: all `64`) |
| 2 | Start sampling. `py trigger_test.py --mode trial --trials 3` | 3 groups of 3 markers: 2.2 s then 15 s (mapped cable: coded `01 02 03`) |
| 3 | Start sampling. `py trigger_test.py --mode session` (~12 min). Stop; save as `bench_rh_mo.smrx` in a folder of its own | 96 markers on Trig |
| 4 | Spike2: open `mo_label_export.s2s`, Script → Run, answer No, pick that folder | Report: `method: spacing` (straight) or `method: codes` (mapped), `triplets: 32`, `positions: assigned (session, 4 x 8)`, `unmatched: 0`, `.mat` written. Labels channel reads `1a 1b 1c … 4c`; the Trig markers now read `01 02 03` |
| 5 | MATLAB: `load bench_rh_mo.mat` | Variables for FDI_L, FDI_R, Trig, Labels present; `Trig.codes(:,1)` is all 1, 2, 3 |

Mapped cable only — before step 1, check the wiring: `py trigger_test.py
--mode hold` with a meter on the 1401 end (pin 21 vs 13 about 5 V, pin 23 vs
13 about 5 V, pin 5 vs 13 0 V; Ctrl+C drops pin 21), then, sampling,
`--mode code --code 127` → `7F`, `--code 1` → `01`, `--code 64` → `40`. A
code of 128 + the expected value means bit 7 (pin 5) is not grounded; `00`
on every marker means the data lines are not reaching pins 21, 8, 20, 7, 19,
6, 18; a single-line code lighting the wrong bit means two wires are swapped.

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
   per tap (`AA` with the straight cable, `64` with a mapped one). The screen
   counts them.
7. Tap **Continue** and hand over the tablet. The rest of the session is
   unchanged.
8. **Keep Spike2 sampling continuously** through calibration, all four blocks
   and the questionnaires. Do not stop between blocks. Do not press keys in
   Spike2.
9. After the **Task Complete** screen: Sample → Stop. **File → Save As** →
   `{pid}_{hand}_mo.smrx`, e.g. `012_rh_mo.smrx` (`rh`/`lh`, matching the
   Python file names).
10. Close the task; it parks the lines idle and releases the port.

Expected in the file: your test pulses, then 96 task markers in groups of
three (2.2 s, then 15 s apart), and none during calibration, familiarisation,
MIQ-3, questionnaires or Trial Complete screens. With a mapped cable there
may also be one code-0 marker from the moment the task opened the port.

If a Trial Complete screen shows **Trigger link lost — tell the experimenter**,
note the trial and let the session finish; see §9.

**Crash:** stop sampling, save as `{pid}_{hand}_mo_partial.smrx` (the script
skips it). To rerun the same ID, first move that ID's files out of every
`Desktop\motor_overflow\mo_*` folder into an archive folder, then restart the
whole session with a new Spike2 file.

## 8. Part F — After recording: labelling and export

1. Put the session `.smrx` files to process in one folder.
2. Spike2: File → Open → `mo_label_export.s2s` → **Script → Run**.
3. First question: Yes = one file, No = whole folder.
4. When the summary appears, read the Log window and each
   `{name}_labels_report.txt`.

Per file the script produces:

| Output | Content |
| --- | --- |
| Labels channel, saved into the `.smrx` | One TextMark per matched marker. Text `1a 1b 1c 2a … 4c`. Codes `[letter, block, trial, 0]` where letter is 1 = a, 2 = b, 3 = c. Re-running replaces it. |
| Trig codes, rewritten in the `.smrx` | Every labelled marker gets the same `[letter, block, trial, 0]` codes, so the Trig channel reads `01 02 03` on screen whatever it read when recorded (`AA` with the straight cable). Unlabelled markers (test pulses, strays) keep their original code. Event channels have no codes and are left alone. |
| `{name}.mat` | FDI_L, FDI_R, Trig, Labels over the whole file. Trig, as a marker channel, carries `times` and a `codes` matrix (column 1 = 1/2/3, column 2 = block, column 3 = trial after the rewrite). If the export call fails the report says so; export by hand with File → Export → MATLAB Data, same four channels. |
| `{name}_labels_report.txt` | File name, pid/hand, number of Trig markers, which grouping rule was used (`codes` or `spacing`), number of triplets, position status, any unmatched markers, a→b and b→c min/mean/max, triplets outside the expected timing windows, export status. |

Reading the report:

| Report | Meaning | Action |
| --- | --- | --- |
| 32 triplets, positions assigned | every trial labelled | done |
| method: spacing | the markers carried no a/b/c codes (straight cable, or an Event channel); grouped by timing | normal for the straight cable |
| 32 triplets, some unmatched / other codes | test pulses, or the code-0 marker from opening the port | fine |
| 32 triplets, timing faults listed | a trial's spacing was off (usually an AO video frame running late); still labelled | look at that trial's EMG window |
| < 32 triplets, positions UNASSIGNED | a trial lost or gained a marker; labels written as a b c without block/trial | §9 |
| "no Trig channel" / "neither a marker nor an event channel" | wrong configuration loaded or channel renamed | load v3, rename to Trig, re-run |

## 9. Part G — The data and how it joins

### Trigger columns in `{pid}_{hand}_mo_task.csv` (one row per trial)

| Column | Meaning |
| --- | --- |
| `trig_a_ms`, `trig_b_ms`, `trig_c_ms` | when each marker's strobe went LOW, ms on the task's own clock; blank if not sent |
| `trig_ok` | 1 if all three were sent, else 0 |

### Trigger columns in `{pid}_{hand}_mo_session.csv`

| Column | Meaning |
| --- | --- |
| `triggers_enabled` | 1 if the BBTK was connected when Continue was tapped; 0 = session ran without markers |
| `trigger_mock` | 1 = mock run; discard the data |
| `trigger_port` | COM port used |
| `trigger_epoch` | wall-clock time the trigger clock started |
| `n_test_pulses` | how many code-100 markers precede the task markers in the Spike2 file |
| `trig_ab_nominal_ms`, `trig_bc_nominal_ms` | 2200 and 15000 — the spacings the script cross-checks against |

### Joining Spike2 to the CSVs in MATLAB

- After the script, `Trig.codes(:,1)` is 1 = a, 2 = b, 3 = c for every
  labelled marker (whatever the cable), `Trig.codes(:,2)` the block and
  `Trig.codes(:,3)` the trial. `Trig.times(Trig.codes(:,1) == 2)` is every
  trial-clock start. Unlabelled markers keep their recorded code (`170` =
  `AA` with the straight cable, `100` for a mapped-cable test pulse).
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

Each unmatched marker in the report is a broken trial. With a mapped cable
its code says which marker survived; with the straight cable use the timing:
a lone pair about 17.2 s apart means that trial's b was lost. Count the good
triplets before it to find its position. For
certainty, apply the constant offset above to every CSV row's `trig_b_ms`: the
nearest code-2 marker belongs to that row's block and trial. Rows with
`trig_ok = 0` tell you which marker the task itself never sent.

## 10. Part H — Quick problem finder

| Symptom | Fix |
| --- | --- |
| Setup screen: `COM12 not found` | Tap the BBTK's port in the list; or replug and Rescan |
| Setup screen: `in use by another program` | Close the other program (an old task window, a terminal), Rescan |
| Setup screen: `pyserial not installed` | `py -m pip install pyserial` |
| Setup screen: `wrong 'serial' package installed` | `py -m pip uninstall -y serial pyserial` then `py -m pip install pyserial` |
| No markers on Trig for test pulses | Cable in the Digital **Input** connector? Pin 23 connected? Channel 32 Digital Marker present? Sampling started? |
| Every marker is code 0 | Data lines not reaching pins 21, 8, 20, 7, 19, 6, 18 (cable), or strobe arriving before the data (should not happen: the task sends data one command earlier) |
| Every marker is `AA` whatever was sent | The straight cable (Part B1). Not a fault: the script labels by spacing and rewrites the codes. Only a mapped cable (B2) changes what arrives |
| Every marker is the same other code whatever was sent | The data wires are on pins the marker does not read. Send `--mode code --code 127`: if Spike2 still shows the same value, none of the seven lines lands on data bits 0–7 — recheck the map in Part B and that the plug is in the Digital Inputs socket. Then `--code 1`, `2`, `4` … one at a time to see where each line does land |
| Codes come out scrambled (e.g. `--code 1` shows `02`) | Two data wires swapped. The bit that lit tells you which 1401 pin the line actually reached |
| Codes are 128 too big (129, 130, 131, 228) | Pin 21 (bit 7) not tied to ground |
| Codes are wrong in other ways | A data line on the wrong pin — `--mode hold` then `--mode hold --all` with a meter, pin by pin |
| Markers doubled, one code then a 0 | Nothing wrong: the 1401 latched the strobe's rising edge too. Should not happen on a Micro1401/Power1401; the script ignores code 0 anyway |
| Report: 0 triplets although markers are visible | Channel not titled Trig; timing constants changed in one file only; or (mapped cable) the codes in `bbtk_trigger.py` and the script disagree |
| Report: method spacing, some triplets missing, straight cable | A stray or missing pulse; the timing rule has no codes to fall back on. See §9 |
| Task will not start: `ModuleNotFoundError: bbtk_trigger` | `bbtk_trigger.py` is not beside the task |
| Task will not start: `AssertionError` | `REST_MS + BEGIN_MS ≠ 2200` or `TRIAL_SECONDS × 1000 ≠ 15000` — change all three files together or restore the values |
