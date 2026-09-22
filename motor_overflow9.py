

import sys
import os
import csv
import glob
import winreg
import array
import shutil
import tempfile
import itertools
import random
from datetime import datetime
import math
import cv2
import atexit

import pygame

import bbtk_trigger

try:
    from pygame._sdl2 import touch as _sdl2_touch
except ImportError:
    _sdl2_touch = None


_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes
    import ctypes.wintypes as wt

    WM_TOUCH     = 0x0240
    GWLP_WNDPROC = -4

    TWF_FINETOUCH = 0x00000001
    TWF_WANTPALM  = 0x00000002

    TOUCHEVENTF_DOWN    = 0x0002
    TOUCHEVENTF_UP      = 0x0004
    TOUCHEVENTF_PRIMARY = 0x0010
    TOUCHEVENTF_PALM    = 0x0080

    TOUCHINPUTMASKF_CONTACTAREA = 0x0004

    _ULONG_PTR = ctypes.c_size_t
    _LRESULT   = ctypes.c_ssize_t

    class _TOUCHINPUT(ctypes.Structure):
        _fields_ = [
            ("x",           wt.LONG),
            ("y",           wt.LONG),
            ("hSource",     wt.HANDLE),
            ("dwID",        wt.DWORD),
            ("dwFlags",     wt.DWORD),
            ("dwMask",      wt.DWORD),
            ("dwTime",      wt.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
            ("cxContact",   wt.DWORD),
            ("cyContact",   wt.DWORD),
        ]

    _user32 = ctypes.windll.user32
    _user32.GetTouchInputInfo.argtypes = [wt.HANDLE, wt.UINT,
                                          ctypes.POINTER(_TOUCHINPUT), ctypes.c_int]
    _user32.GetTouchInputInfo.restype  = wt.BOOL
    _user32.IsTouchWindow.argtypes     = [wt.HWND, ctypes.POINTER(wt.ULONG)]
    _user32.IsTouchWindow.restype      = wt.BOOL
    _user32.RegisterTouchWindow.argtypes   = [wt.HWND, wt.ULONG]
    _user32.RegisterTouchWindow.restype    = wt.BOOL
    _user32.UnregisterTouchWindow.argtypes = [wt.HWND]
    _user32.UnregisterTouchWindow.restype  = wt.BOOL
    _user32.CallWindowProcW.argtypes = [_LRESULT, wt.HWND, wt.UINT,
                                        wt.WPARAM, wt.LPARAM]
    _user32.CallWindowProcW.restype  = _LRESULT

    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class _ContactGeometry:
    """How big each touch is, and whether Windows called it a palm, remembered
    against the same finger number pygame uses."""

    MAX_RECORDS = 128

    def __init__(self):
        self.installed  = False
        self.error      = "not attempted"
        self.sdl_flags  = None
        self.saw_area   = False
        self.saw_palm   = False
        self._hwnd      = None
        self._old_proc  = None
        self._proc      = None
        self._records   = {}

    def install(self):
        if not _IS_WINDOWS:
            self.error = "not Windows"
            return False
        try:
            self._hwnd = pygame.display.get_wm_info()["window"]
        except Exception as e:
            self.error = f"pygame would not tell us which window it made: {e}"
            return False
        self.sdl_flags = self.touch_window_flags()
        try:
            self._proc      = _WNDPROC(self._wndproc)
            setter          = getattr(_user32, "SetWindowLongPtrW", _user32.SetWindowLongW)
            setter.argtypes = [wt.HWND, ctypes.c_int, _WNDPROC]
            setter.restype  = _LRESULT
            self._old_proc  = setter(self._hwnd, GWLP_WNDPROC, self._proc)
            if not self._old_proc:
                self.error = "Windows would not let us listen in on the window"
                return False
        except Exception as e:
            self.error = f"could not listen in on the window: {e}"
            return False
        self.installed = True
        self.error     = ""
        return True

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TOUCH:
            try:
                self._read(wparam, lparam)
            except Exception:
                pass
        return _user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)

    def _read(self, wparam, lparam):
        n = wparam & 0xFFFF
        if n <= 0:
            return
        buf = (_TOUCHINPUT * n)()
        handle = ctypes.c_void_p(lparam & 0xFFFFFFFFFFFFFFFF)
        if not _user32.GetTouchInputInfo(handle, n, buf, ctypes.sizeof(_TOUCHINPUT)):
            return
        for ti in buf:
            has_area = bool(ti.dwMask & TOUCHINPUTMASKF_CONTACTAREA)
            palm     = bool(ti.dwFlags & TOUCHEVENTF_PALM)
            w = ti.cxContact / 100.0 if has_area else None
            h = ti.cyContact / 100.0 if has_area else None
            if has_area:
                self.saw_area = True
            if palm:
                self.saw_palm = True
            rec = self._records.get(ti.dwID)
            if rec is None or (ti.dwFlags & TOUCHEVENTF_DOWN):
                rec = self._records[ti.dwID] = {"w": w, "h": h, "max_area": None,
                                                "palm": False, "primary": False}
                if len(self._records) > self.MAX_RECORDS:
                    self._records.pop(next(iter(self._records)), None)
            if has_area:
                rec["w"], rec["h"] = w, h
                area = w * h
                rec["max_area"] = area if rec["max_area"] is None else max(rec["max_area"], area)
            rec["palm"]    = rec["palm"] or palm
            rec["primary"] = bool(ti.dwFlags & TOUCHEVENTF_PRIMARY)

    def get(self, finger_id):
        try:
            return self._records.get(int(finger_id))
        except (TypeError, ValueError):
            return None

    def touch_window_flags(self):
        if not _IS_WINDOWS or self._hwnd is None:
            return None
        flags = wt.ULONG(0)
        if not _user32.IsTouchWindow(self._hwnd, ctypes.byref(flags)):
            return None
        return int(flags.value)

    def set_windows_palm_rejection(self, enabled):
        if not self.installed:
            return False
        flags = TWF_FINETOUCH if enabled else (TWF_FINETOUCH | TWF_WANTPALM)
        _user32.UnregisterTouchWindow(self._hwnd)
        return bool(_user32.RegisterTouchWindow(self._hwnd, wt.ULONG(flags)))


CONTACT_GEOMETRY = _ContactGeometry()


def get_desktop():
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        )
        path, _ = winreg.QueryValueEx(key, "Desktop")
        return os.path.expandvars(path)
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Desktop")

DESKTOP = get_desktop()

STUDY_ROOT = os.path.join(DESKTOP, "motor_overflow")


BACKGROUND     = (  0,   0,   0)
TEXT           = (224, 225, 226)
TEXT_SECONDARY = (150, 150, 155)
ACCENT         = (216, 138,  23)
ERROR          = (220,  50,  50)
OK             = ( 60, 200, 100)
ZONE_FILL      = ( 80, 140, 220,  62)
ZONE_EDGE      = ( 80, 140, 220, 150)

FPS               = 60
CROSS_RADIUS      = 26
CIRCLE_RADIUS     = 26
CROSS_HIT_RADIUS  = 61
CIRCLE_HIT_RADIUS = 61

CALIBRATION_EXCLUSION_RADIUS = 80

REACH_CONE_AXIS_BELOW_HORIZ_DEG = 15
REACH_CONE_HALF_WIDTH_DEG       = 45
REACH_CONE_AXIS_DEG             = None

SHOW_TOUCH_ZONES = True

NUM_KEYS = {getattr(pygame, f"K_{pad}{i}"): i for i in range(8) for pad in ("", "KP")}

FIXED_CONDITIONS    = ["ME"]
ROTATING_CONDITIONS = ["AO", "KMI", "VMI"]
CONDITIONS  = FIXED_CONDITIONS + ROTATING_CONDITIONS
TASK_ORDERS = list(itertools.permutations(ROTATING_CONDITIONS))

SIZES = ["Small", "Large"]

TRIALS_PER_CONDITION = 8
TRIAL_SECONDS        = 15

MAX_SAME_SIZE_RUN = 2

METRONOME_BPM  = 40
BEAT_MS        = int(round(60000 / METRONOME_BPM))
TRIAL_BEATS    = TRIAL_SECONDS * 1000 // BEAT_MS


def get_task_order(pid):
    idx = (int(pid) - 1) % len(TASK_ORDERS)
    return FIXED_CONDITIONS + list(TASK_ORDERS[idx]), idx


def _max_run(seq):
    best = run = 1
    for a, b in zip(seq, seq[1:]):
        run  = run + 1 if a == b else 1
        best = max(best, run)
    return best


def get_trial_sequences(pid):
    half  = TRIALS_PER_CONDITION // 2
    seqs  = {}
    for i, cond in enumerate(CONDITIONS):
        rng = random.Random(int(pid) * 10 + i)
        seq = [SIZES[0]] * half + [SIZES[1]] * half
        for _attempt in range(1000):
            rng.shuffle(seq)
            if _max_run(seq) <= MAX_SAME_SIZE_RUN:
                break
        seqs[cond] = list(seq)
    return seqs


_HERE = os.path.dirname(os.path.abspath(__file__))
AO_HAND_MODELS = ["a", "b"]
AO_VIDEO_PATHS = {
    (model, hand, size): os.path.join(_HERE, f"ao_video_{model}_{hand}_{size.lower()}.mp4")
    for model in AO_HAND_MODELS
    for hand in ("rh", "lh")
    for size in ("Small", "Large")
}

ENTER = (pygame.K_RETURN, pygame.K_KP_ENTER)


MIQ3_INSTRUCTIONS = (
    "This questionnaire assesses your ability to imagine movements.",
    "For each item you will physically perform a movement, then imagine it.",
    "You then rate how easy or difficult that mental task was, on the scale shown.",
    "Ask the experimenter any questions now, then tell them you are ready.",
)
ITEMS = [
    ( 1, "Knee lift",    "kinesthetic",     "knee_lift"),
    ( 2, "Jump",         "internal_visual", "jump"),
    ( 3, "Arm movement", "external_visual", "arm_movement"),
    ( 4, "Waist Bend",   "kinesthetic",     "waist_bend"),
    ( 5, "Knee lift",    "internal_visual", "knee_lift"),
    ( 6, "Jump",         "external_visual", "jump"),
    ( 7, "Arm movement", "kinesthetic",     "arm_movement"),
    ( 8, "Waist Bend",   "internal_visual", "waist_bend"),
    ( 9, "Knee lift",    "external_visual", "knee_lift"),
    (10, "Jump",         "kinesthetic",     "jump"),
    (11, "Arm movement", "internal_visual", "arm_movement"),
    (12, "Waist Bend",   "external_visual", "waist_bend"),
]

VISUAL_LABELS = [
    "Very hard to see", "Hard to see", "Somewhat hard to see",
    "Neutral",
    "Somewhat easy to see", "Easy to see", "Very easy to see",
]

KINESTHETIC_LABELS = [
    "Very hard to feel", "Hard to feel", "Somewhat hard to feel",
    "Neutral",
    "Somewhat easy to feel", "Easy to feel", "Very easy to feel",
]

IMG_TYPE = {
    "kinesthetic":     ("Kinesthetic Imagery",    KINESTHETIC_LABELS),
    "internal_visual": ("Internal Visual Imagery", VISUAL_LABELS),
    "external_visual": ("External Visual Imagery", VISUAL_LABELS),
}

MOVEMENT_INSTRUCTIONS = {
    "knee_lift": (
        "Stand with your feet and legs together and your arms at your sides.",
        "Raise your right knee as high as possible so that you are starting on your left leg "
        "with your right leg flexed (bent) at the knee. Now lower your right leg so you are "
        "once again standing on two feet. The action is performed slowly.",
    ),
    "jump": (
        "Stand with your feet and legs together and your arms at your sides.",
        "Bend down low and then jump straight up in the air as high as possible with both arms "
        "extended above your head. Land with both feet apart and lower your arms to your sides.",
    ),
    "arm_movement": (
        "Extend the arm of your non-dominant hand straight out to your side so that it is "
        "parallel to the ground, palm down.",
        "Move your arm forward until it is directly in front of your body (still parallel to "
        "the ground). Keep your arm extended during the movement and make the movement slowly.",
    ),
    "waist_bend": (
        "Stand with your feet slightly apart and your arms fully extended above your head.",
        "Slowly bend forward at the waist and try to touch your toes with your fingertips "
        "(or, if possible, touch the floor with your fingertips or your hands). Now return to "
        "the starting position, standing erect with your arms extended above your head.",
    ),
}

MENTAL_TASK = {
    "kinesthetic": (
        "Assume the starting position. Attempt to feel yourself making the movement just "
        "performed without actually doing it. Now rate the ease/difficulty with which you "
        "were able to do this mental task."
    ),
    "internal_visual": (
        "Assume the starting position. Attempt to see yourself making the movement just "
        "observed from an internal perspective (i.e., from a 1st person perspective, as if "
        "you are actually inside yourself performing and seeing the action through your own "
        "eyes). Now rate the ease/difficulty with which you were able to do this mental task."
    ),
    "external_visual": (
        "Assume the starting position. Attempt to see yourself making the movement just "
        "observed from an external perspective (i.e., from a 3rd person perspective, as if "
        "watching yourself on DVD). Now rate the ease/difficulty with which you were able to "
        "do this mental task."
    ),
}

MAAS_ITEMS = [
    "I was finding it difficult to stay focused on what was happening.",
    "I was doing something without paying attention.",
    "I was preoccupied with the future or the past.",
    "I was doing something automatically, without being aware of what I was doing.",
    "I was rushing through something without being really attentive to it.",
]

MAAS_MIN = 0
MAAS_MAX = 6

MAAS_ANCHORS = {
    0: "not at all",
    3: "somewhat",
    6: "very much",
}

NASA_TLX_SCALES = [
    ("Mental Demand",   "Low",  "High"),
    ("Physical Demand", "Low",  "High"),
    ("Temporal Demand", "Low",  "High"),
    ("Performance",     "Poor", "Good"),
    ("Effort",          "Low",  "High"),
    ("Frustration",     "Low",  "High"),
]

CULTURE_OPTIONS = [
    ("African/Black",
     "including African-American, African-Canadian, Caribbean",
     "african_black"),
    ("East Asian",
     "e.g., Chinese, Taiwanese, Japanese, Korean, etc.",
     "east_asian"),
    ("European/White",
     "",
     "european_white"),
    ("Indo-Caribbean, Indo-African, Indo-Fijian, West-Indian",
     "",
     "indo_caribbean"),
    ("Latin, South or Central American",
     "",
     "latin_american"),
    ("Polynesian",
     "e.g., Samoans, Tongan, Niuean, Cook Island Maori, Tahitian Maaohi, "
     "Hawaiian Ma’oli, Marquesan, New Zealand Maori",
     "polynesian"),
    ("South Asian",
     "e.g., Afghan, Nepali, Tamil, Bangladeshi, Pakistani, Indian, Sri Lankan, "
     "Punjabi",
     "south_asian"),
    ("Southeast Asian",
     "e.g., Vietnamese, Thai, Cambodian, Malaysian, Filipino/a, Laotian, "
     "Singaporean, Indonesian",
     "southeast_asian"),
    ("West Asian",
     "e.g., Iraqi, Jordanian, Palestinian, Saudi, Syrian, Yemeni, Armenian, "
     "Iranian, Israeli, Turkish",
     "west_asian"),
    ("Indigenous within Canada",
     "e.g., First Nation, Métis, Inuit",
     "indigenous_canada"),
]

CULTURE_QUESTION = "What is your cultural background? Choose all that apply."

CULTURE_SELF_KEY    = "self_identify"
CULTURE_DECLINE_KEY = "prefer_not_to_answer"

GENDER_QUESTION = "With which gender do you most identify?"

GENDER_OPTIONS = [
    ("Woman",                           "", "woman"),
    ("Man",                             "", "man"),
    ("Transgender woman | Trans woman", "", "trans_woman"),
    ("Transgender man | Trans man",     "", "trans_man"),
    ("Non-binary",                      "", "non_binary"),
    ("Allo-binary",                     "", "allo_binary"),
    ("Two-Spirit",                      "", "two_spirit"),
]

GENDER_SELF_KEY    = "not_listed"
GENDER_DECLINE_KEY = "prefer_not_to_answer"

FAMILIARIZATION_INSTRUCTION = (
    "Next you will hear the metronome that plays during every task in this study.",
    f"It runs at *{METRONOME_BPM} beats per minute* — one click every "
    f"{BEAT_MS / 1000:.1f} seconds.",
    "*Every time a beat plays, you should be starting a movement.*",
    "The whole movement — out and back again — happens within that one beat.",
    f"So a minute of the task would be {METRONOME_BPM} movements, and each "
    f"{TRIAL_SECONDS} second trial is {TRIAL_BEATS}.",
    "Listen to the beat on the next screen for as long as you like, then continue.",
)

CALIBRATION_REPS = 5


def _calibration_instruction(direction):
    return (
        "First we need to measure your maximum comfortable finger abduction distance.",
        "Rest your hand flat on the screen, index finger hovering over the X.",
        f"Tap the X, then move your index finger as far {direction} as is comfortable. "
        "Keep the rest of your hand still.",
        "At your furthest point, touch the screen, lift off, and return to the X.",
        "The metronome you just heard plays throughout. *Tap with the metronome*, "
        "out and back within 1 beat.",
        f"Repeat for a total of {CALIBRATION_REPS} reps.",
        "Ask the experimenter any questions now, then tell them you are ready.",
    )


CALIBRATION_INSTRUCTION_RH = _calibration_instruction("LEFT")
CALIBRATION_INSTRUCTION_LH = _calibration_instruction("RIGHT")

ME_INSTRUCTION = (
    "You will perform the same finger abduction movement you just calibrated.",
    "Rest your hand flat on the screen, index finger hovering over the X. "
    "The circle shows your target distance for that trial.",
    "When you are ready, *rest* your index finger on the X. "
    "Nothing starts until you do.",
    "Two seconds later a bell sounds, Begin appears, and a metronome starts. "
    "That is your cue to start.",
    "Move from the X out to the circle, touch down, lift off, and return to the X — "
    "*one complete movement on every beat*.",
    "Match the metronome rather than rushing: *ten movements*, each as accurate as you can "
    "make it. A second bell ends the trial after 15 seconds.",
    "Eight trials. The circle shows the target distance for each one.",
    "Ask the experimenter any questions now, then tell them you are ready.",
)

def _imagery_instruction(focus):
    return (
        "You will imagine yourself performing the finger abduction movement you calibrated.",
        "Rest your hand flat on the screen, index finger hovering over the X. "
        "The circle shows your target distance for that trial.",
        "When you are ready, *rest* your index finger on the X and leave it there for the whole "
        "trial. Nothing starts until you do.",
        "Two seconds later a bell sounds, Begin appears, and a metronome starts. "
        "That is your cue to start.",
        "*Without moving your finger*, imagine the movement out to the circle and back, "
        f"focusing intensely on what it {focus}.",
        "Imagine *one repetition on every beat* until the second bell, 15 seconds later.",
        "You may close your eyes — the bells mark the start and the end, so you do not need "
        "to watch the screen.",
        "Eight trials. The circle shows the target distance for each one.",
        "Ask the experimenter any questions now, then tell them you are ready.",
    )


KMI_INSTRUCTION = _imagery_instruction("*FEELS* like")
VMI_INSTRUCTION = _imagery_instruction("*LOOKS* like from *your own perspective*")

AO_INSTRUCTION = (
    "You will watch somebody else perform the finger abduction movement you calibrated.",
    "First, tap whichever of the two hands shown looks most like your own.",
    "Rest your hand flat on the screen beside the X at the edge, so the rest of your hand "
    "is off the video.",
    "When you are ready, *rest* your index finger on the X and leave it there. "
    "Nothing starts until you do.",
    "Two seconds later a bell sounds, a metronome starts, and the video plays.",
    "Keep your finger on the X and your hand flat and still for the whole video, and "
    "*watch the movement closely*.",
    "The movement follows the metronome. A second bell ends the trial after 15 seconds.",
    "Eight trials, four at each target distance.",
    "Ask the experimenter any questions now, then tell them you are ready.",
)

IMAGERY_INSTRUCTION = (
    "One last question about the block you just completed.",
    "Imagery does not always come out the way it was asked for. We want to know what you "
    "actually did.",
    "Drag the marker to show whether you were mostly *feeling* the movement, mostly "
    "*seeing* it, or an even mix of the two.",
)

IMAGERY_MIN, IMAGERY_MAX = -50, 50
IMAGERY_LEFT_LABEL   = "Entirely feeling"
IMAGERY_RIGHT_LABEL  = "Entirely seeing"
IMAGERY_ANCHORS = {
    IMAGERY_MIN: "only felt the movement",
    0:           "an even mix",
    IMAGERY_MAX: "only saw the movement",
}

AO_IMAGERY_QUESTION = "Did you find yourself performing imagery during this block?"

MAAS_INSTRUCTION = (
    "This scale asks about your present-moment awareness during the block you just "
    "completed.",
    "You will rate 5 statements from 0 (not at all) to 6 (very much).",
    "Answer according to what really reflected your experience, rather than what you think "
    "it should have been.",
)

NASA_TLX_INSTRUCTION = (
    "This measures your subjective workload during the tasks you just completed.",
    "You will rate six dimensions by dragging a marker along each scale.",
    "Each scale runs from 0 to 100.",
)

os.environ.setdefault("SDL_TOUCH_MOUSE_EVENTS", "0")
os.environ.setdefault("SDL_MOUSE_TOUCH_EVENTS", "0")

try:
    pygame.mixer.pre_init(44100, -16, 2, 512)
except Exception:
    pass

pygame.init()
screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
pygame.display.set_caption("Motor Overflow Study - NIMBL @ UBCO")
clock = pygame.time.Clock()

TRIG = bbtk_trigger.Trigger()
atexit.register(TRIG.close)


USE_WINDOWS_PALM_REJECTION = True

REJECT_PALM_FLAGGED = True

PALM_AREA_PX2 = 0

PALM_AREA_RATIO = 3.0

BIRTH_SLACK_PX = 25

TOUCH_DWELL_MS    = 25
TOUCH_MIN_SAMPLES = 2

MAX_CLAIM_AGE_MS = 5000

CONTACT_GEOMETRY.install()
if USE_WINDOWS_PALM_REJECTION:
    CONTACT_GEOMETRY.set_windows_palm_rejection(True)

pygame.event.set_blocked(pygame.FINGERMOTION)

BELL_PATH = os.path.join(_HERE, "bell.wav")
try:
    _bell = pygame.mixer.Sound(BELL_PATH)
except Exception:
    _bell = None


def play_bell():
    if _bell is not None:
        try:
            _bell.play()
        except Exception:
            pass


METRONOME_HZ     = 1000
METRONOME_MS     = 40
METRONOME_LEVEL  = 0.62


def _click_samples(rate):
    n    = max(1, int(rate * METRONOME_MS / 1000))
    edge = max(1, int(n * 0.15))
    peak = 32767 * METRONOME_LEVEL
    out  = []
    for i in range(n):
        env = min(1.0, i / edge, (n - i) / edge)
        out.append(int(peak * env * math.sin(2 * math.pi * METRONOME_HZ * i / rate)))
    return out


def _build_metronome(beats=TRIAL_BEATS, full_last_beat=False):
    init = pygame.mixer.get_init()
    if init is None:
        return None
    rate, size, channels = init
    if abs(size) != 16:
        return None
    click  = _click_samples(rate)
    if full_last_beat:
        frames = int(rate * beats * BEAT_MS / 1000)
    else:
        frames = int(rate * ((beats - 1) * BEAT_MS + METRONOME_MS) / 1000) + 1
    buf    = array.array("h", bytes(frames * channels * 2))
    for k in range(beats):
        start = int(rate * k * BEAT_MS / 1000)
        for i, value in enumerate(click):
            base = (start + i) * channels
            for c in range(channels):
                buf[base + c] = value
    return pygame.mixer.Sound(buffer=buf.tobytes())


try:
    _metronome = _build_metronome()
except Exception:
    _metronome = None

try:
    _metronome_loop = _build_metronome(beats=1, full_last_beat=True)
except Exception:
    _metronome_loop = None

_metronome_channel = None
try:
    if _metronome is not None and pygame.mixer.get_init():
        pygame.mixer.set_reserved(1)
        _metronome_channel = pygame.mixer.Channel(0)
except Exception:
    _metronome_channel = None


def start_metronome():
    if _metronome is None or _metronome_channel is None:
        return
    try:
        _metronome_channel.stop()
        _metronome_channel.play(_metronome)
    except Exception:
        pass


def start_metronome_loop():
    if _metronome_loop is None or _metronome_channel is None:
        return
    try:
        _metronome_channel.stop()
        _metronome_channel.play(_metronome_loop, loops=-1)
    except Exception:
        pass


def stop_metronome():
    if _metronome_channel is None:
        return
    try:
        _metronome_channel.stop()
    except Exception:
        pass


def beat_of(t_ms):
    if t_ms is None:
        return "", ""
    k = int(round(t_ms / BEAT_MS))
    return k + 1, int(round(t_ms - k * BEAT_MS))

W, H = screen.get_size()

TARGET_EDGE_MARGIN = CIRCLE_HIT_RADIUS + 20

LAYOUT_DROP_FRAC = 0.08

CROSS_POS   = None
ACTIVE_ZONE = None


def configure_active_side(handedness):
    global CROSS_POS, ACTIVE_ZONE, REACH_CONE_AXIS_DEG
    if handedness == "rh":
        REACH_CONE_AXIS_DEG = 180 - REACH_CONE_AXIS_BELOW_HORIZ_DEG
    else:
        REACH_CONE_AXIS_DEG = 0 + REACH_CONE_AXIS_BELOW_HORIZ_DEG
    drop          = int(H * LAYOUT_DROP_FRAC)
    zone_height   = int(H * 0.46) + drop
    center_offset = int(W * 0.09)
    if handedness == "rh":
        ACTIVE_ZONE = pygame.Rect(0, 0, W // 2, zone_height)
        cross_x     = W // 2 - center_offset
    else:
        ACTIVE_ZONE = pygame.Rect(W // 2, 0, W - W // 2, zone_height)
        cross_x     = W // 2 + center_offset
    CROSS_POS = (cross_x, int(H * 0.16) + drop)


def button_side(handedness):
    return "left" if handedness == "rh" else "right"


def notice_x():
    return W // 4 if ACTIVE_ZONE.left < W // 2 else W - W // 4


def target_position(distance, angle):
    x = CROSS_POS[0] + distance * math.cos(angle)
    y = CROSS_POS[1] + distance * math.sin(angle)
    x = min(max(x, ACTIVE_ZONE.left + TARGET_EDGE_MARGIN), ACTIVE_ZONE.right - TARGET_EDGE_MARGIN)
    y = min(max(y, TARGET_EDGE_MARGIN), ACTIVE_ZONE.height - TARGET_EDGE_MARGIN)
    return (x, y)


def target_geometry(distance, angle):
    pos    = target_position(distance, angle)
    actual = math.hypot(pos[0] - CROSS_POS[0], pos[1] - CROSS_POS[1])
    return pos, actual, abs(actual - distance) > 1.0


HIT_REGION_GAP = 8

MIN_HIT_RADIUS  = 20
HARD_REGION_GAP = 4


def block_hit_radius(distance):
    limit   = min(CROSS_HIT_RADIUS, CIRCLE_HIT_RADIUS)
    ceiling = distance / 2 - HARD_REGION_GAP
    return int(max(1, min(limit, ceiling,
                          max(MIN_HIT_RADIUS, distance / 2 - HIT_REGION_GAP))))


def in_reach_cone(fx, fy, slack_px=0.0):
    dx, dy = fx - CROSS_POS[0], fy - CROSS_POS[1]
    angle  = math.degrees(math.atan2(dy, dx))
    diff   = (angle - REACH_CONE_AXIS_DEG + 180) % 360 - 180
    half   = REACH_CONE_HALF_WIDTH_DEG
    if slack_px:
        dist = math.hypot(dx, dy)
        half += math.degrees(math.atan2(slack_px, max(dist, 1.0)))
    return abs(diff) <= half


def draw_touch_zone_x(radius=None, center=None):
    if not SHOW_TOUCH_ZONES:
        return
    r  = int(CROSS_HIT_RADIUS if radius is None else radius)
    cx, cy = CROSS_POS if center is None else center
    overlay = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
    pygame.draw.circle(overlay, ZONE_FILL, (r, r), r)
    pygame.draw.circle(overlay, ZONE_EDGE, (r, r), r, 1)
    screen.blit(overlay, (cx - r, cy - r))


def draw_touch_zone_cone():
    if not SHOW_TOUCH_ZONES:
        return
    radius = math.hypot(W, H)
    lo = math.radians(REACH_CONE_AXIS_DEG - REACH_CONE_HALF_WIDTH_DEG)
    hi = math.radians(REACH_CONE_AXIS_DEG + REACH_CONE_HALF_WIDTH_DEG)
    points = [CROSS_POS]
    steps = 32
    for i in range(steps + 1):
        a = lo + (hi - lo) * i / steps
        points.append((CROSS_POS[0] + radius * math.cos(a), CROSS_POS[1] + radius * math.sin(a)))
    overlay = pygame.Surface((W, H), pygame.SRCALPHA)
    overlay.set_clip(ACTIVE_ZONE)
    pygame.draw.polygon(overlay, ZONE_FILL, points)
    pygame.draw.polygon(overlay, ZONE_EDGE, points, max(2, s(3)))
    pygame.draw.circle(overlay, (0, 0, 0, 0), CROSS_POS, CALIBRATION_EXCLUSION_RADIUS)
    pygame.draw.circle(overlay, ZONE_EDGE, CROSS_POS, CALIBRATION_EXCLUSION_RADIUS, 1)
    overlay.set_clip(None)
    screen.blit(overlay, (0, 0))

REF_W, REF_H = 1280, 800
S = min(W / REF_W, H / REF_H)


def s(v):
    return int(round(v * S))


def mfont(size, bold=False):
    return pygame.font.SysFont("cambria", s(size), bold=bold)

font_title   = mfont(42, bold=True)
font_large   = mfont(52, bold=True)
font_body    = mfont(34)
font_bold    = mfont(34, bold=True)
font_small   = mfont(24)
font_xs      = mfont(18)
font_xs_bold = mfont(18, bold=True)


def lh(font, mult=1.10):
    return int(round(font.get_linesize() * mult))

try:
    raw  = pygame.image.load(os.path.join(_HERE, "nimbl_logo.png"))
    _lh  = s(100)
    logo = pygame.transform.smoothscale(raw, (int(raw.get_width() * _lh / raw.get_height()), _lh))
except Exception:
    logo = None


def text_c(text, font, color, cx, cy):
    img = font.render(text, True, color)
    screen.blit(img, img.get_rect(center=(cx, cy)))


def multiline_c(text, font, color, cx, top, line_height=None):
    line_height = line_height if line_height is not None else lh(font)
    for line in text.split("\n"):
        text_c(line, font, color, cx, top)
        top += line_height
    return top


def wrap_text(text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        if font.size(test)[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


EMPHASIS_MARK = "*"


def emphasis_tokens(text):
    tokens   = []
    emph     = False
    open_end = False
    for part in text.split(EMPHASIS_MARK):
        words = part.split()
        if words and tokens and open_end and not part[:1].isspace():
            word, was_emph = tokens[-1]
            tokens[-1] = (word + words.pop(0), was_emph)
        for word in words:
            tokens.append((word, emph))
        if part:
            open_end = not part[-1:].isspace()
        emph = not emph
    return tokens


def wrap_runs(text, font, font_emph, max_width):
    space = font.size(" ")[0]
    lines, line, width = [], [], 0
    for word, emph in emphasis_tokens(text):
        w   = (font_emph if emph else font).size(word)[0]
        add = w if not line else w + space
        if line and width + add > max_width:
            lines.append(line)
            line, width = [(word, emph)], w
        else:
            line.append((word, emph))
            width += add
    if line:
        lines.append(line)
    return lines


def draw_runs(line, font, font_emph, color, color_emph, x, y):
    space = font.size(" ")[0]
    for i, (word, emph) in enumerate(line):
        img = (font_emph if emph else font).render(word, True,
                                                   color_emph if emph else color)
        if i:
            x += space
        screen.blit(img, img.get_rect(midleft=(x, y)))
        x += img.get_width()


INSTRUCTION_FONT_SIZES = (34, 31, 28, 26, 24, 22, 20)


def fit_wrapped(text, max_width, max_height, sizes=INSTRUCTION_FONT_SIZES):
    for size in sizes:
        font  = mfont(size)
        emph  = mfont(size, bold=True)
        lines = wrap_runs(text, font, emph, max_width)
        if len(lines) * lh(font) <= max_height:
            return font, emph, lines
    return font, emph, lines


INSTRUCTION_POINT_GAP = 0.6

INSTRUCTION_BULLET = "•  "


def fit_points(points, max_width, max_height, sizes=INSTRUCTION_FONT_SIZES):
    for size in sizes:
        font    = mfont(size)
        emph    = mfont(size, bold=True)
        step    = lh(font)
        indent  = font.size(INSTRUCTION_BULLET)[0]
        wrapped = [wrap_runs(p, font, emph, max_width - indent) for p in points]
        height  = (sum(len(w) for w in wrapped) * step
                   + int(step * INSTRUCTION_POINT_GAP) * (len(wrapped) - 1))
        if height <= max_height:
            return font, emph, indent, wrapped
    return font, emph, indent, wrapped


def draw_wrapped(text, font, color, x, y, max_width, line_height=None):
    line_height = line_height if line_height is not None else lh(font)
    for line in wrap_text(text, font, max_width):
        screen.blit(font.render(line, True, color), (x, y))
        y += line_height
    return y


def draw_notice(text, color=None):
    if not text:
        return
    color   = color or ERROR
    cx      = notice_x()
    max_w   = W // 2 - s(120)
    lines   = wrap_text(text, font_bold, max_w)
    step    = lh(font_bold)
    band_top = ACTIVE_ZONE.height if ACTIVE_ZONE is not None else int(H * 0.54)
    y        = (band_top + H - s(120)) // 2 - (len(lines) - 1) * step // 2
    for line in lines:
        text_c(line, font_bold, color, cx, y)
        y += step


def block_status_pos():
    return notice_x(), ACTIVE_ZONE.height + s(46)


def draw_block_footer(phase, remaining, pos):
    cx, cy = pos
    if phase == "wait":
        text_c("Touch the X to begin", font_title, ACCENT, cx, cy)
    elif phase == "rest":
        text_c("Rest your finger on the X", font_title, ACCENT, cx, cy)
    elif phase == "begin":
        text_c("Begin", font_title, OK, cx, cy)
    elif remaining is not None:
        text_c(f"{remaining:0.1f}s remaining", font_small, TEXT_SECONDARY, cx, cy)


def draw_rating_row(values, selection, y, anchors=None):
    values  = list(values)
    spacing = min(s(120), (W - s(160)) // max(len(values), 1))
    start_x = W // 2 - (len(values) - 1) * spacing // 2
    hit_w   = spacing - s(20)
    rects   = {}
    for i, val in enumerate(values):
        cx = start_x + i * spacing
        if selection == val:
            text_c(str(val), font_large, ACCENT,         cx, y + s(20))
        else:
            text_c(str(val), font_body,  TEXT_SECONDARY, cx, y + s(20))
        rects[val] = pygame.Rect(cx - hit_w // 2, y - s(4), hit_w, s(56))
        if anchors and val in anchors:
            text_c(anchors[val], font_xs, TEXT_SECONDARY, cx, y + s(68))
    return rects


def draw_x(pos, size=15, color=None, width=3):
    color = color or TEXT
    x, y = int(pos[0]), int(pos[1])
    pygame.draw.line(screen, color, (x - size, y - size), (x + size, y + size), width)
    pygame.draw.line(screen, color, (x + size, y - size), (x - size, y + size), width)


def draw_cross(active, hit_radius=None, center=None):
    center = CROSS_POS if center is None else center
    if active:
        draw_touch_zone_x(hit_radius, center)
    draw_x(center, color=ACCENT if active else TEXT)
    if active:
        pygame.draw.circle(screen, ACCENT, center, CROSS_RADIUS, 2)


_FRAME = 0


def flip():
    global _FRAME
    _FRAME += 1
    pygame.display.flip()
    TRIG.service()


def check_quit(event):
    if event.type == pygame.QUIT:
        TRIG.close()
        pygame.quit(); sys.exit()
    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
        TRIG.close()
        pygame.quit(); sys.exit()


BUTTON_W      = s(300)
BUTTON_H      = s(60)
BUTTON_RADIUS = s(14)


def draw_continue_button(label="Continue", y=None, side=None):
    if y is None:
        y = H - s(90)
    if side == "right":
        bx = W - BUTTON_W - s(40)
    elif side == "left":
        bx = s(40)
    else:
        bx = W // 2 - BUTTON_W // 2
    rect = pygame.Rect(bx, y, BUTTON_W, BUTTON_H)
    pygame.draw.rect(screen, ACCENT, rect, border_radius=BUTTON_RADIUS)
    text_c(label, font_bold, BACKGROUND, rect.centerx, rect.centery)
    return rect


def is_palm_contact(finger_id):
    record = CONTACT_GEOMETRY.get(finger_id)
    if not record:
        return False
    if REJECT_PALM_FLAGGED and record["palm"]:
        return True
    return bool(PALM_AREA_PX2 and record["max_area"]
                and record["max_area"] > PALM_AREA_PX2)


def is_button_touched(event, rect):
    if rect is None:
        return False
    if event.type == pygame.FINGERDOWN:
        return (not is_palm_contact(event.finger_id)
                and rect.collidepoint(event.x * W, event.y * H))
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        return rect.collidepoint(event.pos)
    return False


def is_button_activated(event, rect):
    return ((rect is not None and event.type == pygame.KEYDOWN and event.key in ENTER)
            or is_button_touched(event, rect))


def get_touch_down(event):
    if event.type == pygame.FINGERDOWN:
        if is_palm_contact(event.finger_id):
            return None
        return event.x * W, event.y * H, event.finger_id, event.touch_id
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        return float(event.pos[0]), float(event.pos[1]), "mouse", None
    return None


def get_touch_up(event):
    if event.type == pygame.FINGERUP:
        return event.finger_id
    if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
        return "mouse"
    return None


_ARMED        = None
_BLOCKED_FIDS = set()


def touch_circle(center, radius):
    cx, cy = center
    def shape(x, y):
        return math.hypot(x - cx, y - cy) <= radius
    shape.center = center
    shape.grow   = lambda px: touch_circle(center, radius + px)
    return shape


def touch_rect(rect):
    if rect is None:
        def empty(x, y):
            return False
        empty.grow = lambda px: empty
        return empty
    def shape(x, y):
        return rect.collidepoint(x, y)
    shape.grow = lambda px: touch_rect(rect.inflate(px * 2, px * 2))
    return shape


def touch_reach_wedge(slack_px=0.0):
    def shape(x, y):
        zone = ACTIVE_ZONE if not slack_px else ACTIVE_ZONE.inflate(slack_px * 2,
                                                                   slack_px * 2)
        return (
            in_reach_cone(x, y, slack_px)
            and zone.collidepoint(x, y)
            and math.hypot(x - CROSS_POS[0], y - CROSS_POS[1])
                > CALIBRATION_EXCLUSION_RADIUS - slack_px
        )
    shape.grow = lambda px: touch_reach_wedge(slack_px + px)
    return shape


def arm_touch(*shapes):
    global _ARMED
    _ARMED = [sh for sh in shapes if sh is not None]


def disarm_touch():
    global _ARMED
    _ARMED = None
    _BLOCKED_FIDS.clear()


def _touch_allowed(x, y):
    if _ARMED is None:
        return True
    return any(shape(x, y) for shape in _ARMED)


class _Contact:
    """One touch, from landing to lifting.

    `witnessed` is False for a touch that was already on the glass when a screen
    opened. Those can keep a circle occupied, but they are never allowed to
    count as somebody arriving on it, because we never saw them arrive."""

    def __init__(self, fid, x, y, now, witnessed=True):
        self.fid       = fid
        self.witnessed = witnessed
        self.born_ms   = now
        self.born_pos  = (x, y)
        self.x, self.y = x, y
        self.last_ms   = now
        self.samples   = 1
        self.speed     = 0.0
        self.w = self.h = self.area = None
        self.palm      = False
        self.counted   = set()
        self.refresh_geometry()

    def refresh_geometry(self):
        geo = CONTACT_GEOMETRY.get(self.fid)
        if not geo:
            return
        self.w, self.h = geo["w"], geo["h"]
        if geo["max_area"] is not None:
            self.area = geo["max_area"] if self.area is None else max(self.area,
                                                                     geo["max_area"])
        self.palm = self.palm or geo["palm"]

    def update(self, x, y, now):
        dt = now - self.last_ms
        if dt > 0:
            inst = math.hypot(x - self.x, y - self.y) * 1000.0 / dt
            self.speed   = inst if self.speed == 0.0 else 0.6 * self.speed + 0.4 * inst
            self.last_ms = now
        self.x, self.y = x, y
        self.samples  += 1
        self.refresh_geometry()

    def age(self, now):
        return now - self.born_ms


_contacts     = {}
_polled_frame = -1
_REJECTS      = {}


def _sdl_finger_table():
    if _sdl2_touch is None:
        return None
    try:
        out = []
        for d in range(_sdl2_touch.get_num_devices()):
            dev = _sdl2_touch.get_device(d)
            for i in range(_sdl2_touch.get_num_fingers(dev)):
                finger = _sdl2_touch.get_finger(dev, i)
                if finger is not None:
                    out.append((finger["id"], finger["x"] * W, finger["y"] * H))
        return out
    except Exception:
        return None


def _poll_contacts():
    global _polled_frame
    if _polled_frame == _FRAME:
        return
    _polled_frame = _FRAME
    now   = pygame.time.get_ticks()
    table = _sdl_finger_table()
    if table is not None:
        seen = set()
        for fid, x, y in table:
            seen.add(fid)
            contact = _contacts.get(fid)
            if contact is None:
                _contacts[fid] = _Contact(fid, x, y, now, witnessed=False)
            else:
                contact.update(x, y, now)
        for fid in [f for f in _contacts if f != "mouse" and f not in seen]:
            _contacts.pop(fid, None)
    if pygame.mouse.get_pressed()[0]:
        mx, my  = pygame.mouse.get_pos()
        contact = _contacts.get("mouse")
        if contact is None:
            _contacts["mouse"] = _Contact("mouse", float(mx), float(my), now)
        else:
            contact.update(float(mx), float(my), now)
    else:
        _contacts.pop("mouse", None)


def _note_reject(contact, reason):
    if reason and reason not in contact.counted:
        contact.counted.add(reason)
        _REJECTS[reason] = _REJECTS.get(reason, 0) + 1
    return reason


def _smallest_known_area():
    areas = [c.area for c in _contacts.values() if c.area]
    return min(areas) if areas else None


def contact_reject_reason(contact, smallest_area=None):
    if REJECT_PALM_FLAGGED and contact.palm:
        return "palm"
    if contact.area is not None:
        if PALM_AREA_PX2 and contact.area > PALM_AREA_PX2:
            return "size"
        if smallest_area is None:
            smallest_area = _smallest_known_area()
        if (PALM_AREA_RATIO and smallest_area
                and contact.area > smallest_area * PALM_AREA_RATIO):
            return "size"
    return ""


def touch_is_eligible(fid):
    contact = _contacts.get(fid)
    if contact is None:
        return True, ""
    reason = contact_reject_reason(contact)
    _note_reject(contact, reason)
    return (not reason), reason


def active_fingers():
    _poll_contacts()
    return [(c.fid, c.x, c.y) for c in _contacts.values()]


def fingers_in(shape):
    return [c for c in active_fingers() if shape(c[1], c[2])]


def candidates_in(shape, arrivals=False):
    _poll_contacts()
    now      = pygame.time.get_ticks()
    smallest = _smallest_known_area()
    born_ok  = shape.grow(BIRTH_SLACK_PX) if hasattr(shape, "grow") else shape
    out      = []
    for contact in _contacts.values():
        if not shape(contact.x, contact.y):
            continue
        reason = contact_reject_reason(contact, smallest)
        if not reason:
            if not contact.witnessed:
                reason = "unwitnessed"
            elif not born_ok(*contact.born_pos):
                reason = "birth"
            elif arrivals and (contact.age(now) < TOUCH_DWELL_MS
                               or contact.samples < TOUCH_MIN_SAMPLES):
                reason = "dwell"
            elif arrivals and contact.age(now) > MAX_CLAIM_AGE_MS:
                reason = "age"
        _note_reject(contact, reason)
        if not reason:
            out.append(contact)
    return out


LIFT_DEBOUNCE_FRAMES = 3

STALE_NOTICE_MS = 1500


class RegionWatch:
    """Watches one shape and notices somebody arriving on it.

    Something merely lying there does not count. Anything already inside when
    the watch begins is ignored for good: it was there before we started
    looking, so we cannot say it arrived."""

    def __init__(self, shape):
        self.shape = shape
        self.center = getattr(shape, "center", None)
        self.stale = {c[0] for c in fingers_in(shape)}
        self.empty_frames = 0
        self.arrival = None

    def _pool(self, arrivals):
        self.stale &= {c[0] for c in fingers_in(self.shape)}
        return [(c.fid, c.x, c.y) for c in candidates_in(self.shape, arrivals)
                if c.fid not in self.stale]

    def fresh(self):
        return self._pool(False)

    def arrivals(self):
        return self._pool(True)

    def entered(self):
        arriving = self._pool(True)
        if not arriving:
            self.empty_frames = 0
            return None
        if len(arriving) == 1:
            return arriving[0]
        sized = [c for c in arriving if _contacts.get(c[0]) and _contacts[c[0]].area]
        if sized:
            return min(sized, key=lambda c: _contacts[c[0]].area)
        if self.center is None:
            return arriving[0]
        cx, cy = self.center
        return min(arriving, key=lambda c: math.hypot(c[1] - cx, c[2] - cy))

    def lifted(self):
        if self.fresh():
            self.empty_frames = 0
            return False
        self.empty_frames += 1
        return self.empty_frames >= LIFT_DEBOUNCE_FRAMES

    def arrived(self):
        if self.arrival is None:
            self.arrival = self.entered()
        return self.arrival

    def blocked_by_stale(self):
        return bool(fingers_in(self.shape)) and not self.fresh()


def pump(tlog=None, phase="", on_blocked=None):
    for event in pygame.event.get():
        if event.type == pygame.FINGERDOWN:
            fx, fy = event.x * W, event.y * H
            _contacts[event.finger_id] = _Contact(event.finger_id, fx, fy,
                                                  pygame.time.get_ticks())
            allowed, reason = touch_is_eligible(event.finger_id)
            if not allowed:
                _BLOCKED_FIDS.add(event.finger_id)
                if tlog is not None:
                    tlog.add("down", phase, fx, fy, event.finger_id,
                             f"blocked - {reason}")
                continue
            if not _touch_allowed(fx, fy):
                _BLOCKED_FIDS.add(event.finger_id)
                note = on_blocked(fx, fy) if on_blocked is not None else None
                if tlog is not None:
                    tlog.add("down", phase, fx, fy, event.finger_id,
                             note or "blocked - outside live zone")
                continue
        elif event.type == pygame.FINGERUP:
            _contacts.pop(event.finger_id, None)
            if event.finger_id in _BLOCKED_FIDS:
                _BLOCKED_FIDS.discard(event.finger_id)
                if tlog is not None:
                    tlog.add("up", phase, None, None, event.finger_id,
                             "blocked - outside live zone")
                continue
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            _contacts["mouse"] = _Contact("mouse", float(event.pos[0]),
                                          float(event.pos[1]),
                                          pygame.time.get_ticks())
            if not _touch_allowed(*event.pos):
                _BLOCKED_FIDS.add("mouse")
                continue
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            _contacts.pop("mouse", None)
            if "mouse" in _BLOCKED_FIDS:
                _BLOCKED_FIDS.discard("mouse")
                continue
        yield event


BEGIN_MS = 200

REST_MS = 2000

assert bbtk_trigger.AB_NOMINAL_MS == REST_MS + BEGIN_MS
assert bbtk_trigger.BC_NOMINAL_MS == TRIAL_SECONDS * 1000


class LiftCounter:
    """Counts how often the finger leaves the X and how often it comes back.

    Used in the three conditions where the finger is supposed to stay where it
    is: imagining the movement, either way, and watching somebody else."""

    FIELDS = ("lifted", "n_lifts", "returned_after_lift", "n_returns")

    def __init__(self, watch):
        self.watch     = watch
        self.n_lifts   = 0
        self.n_returns = 0
        self._empty    = 0
        self._off      = False
        self._started  = False

    def update(self):
        if self.watch.fresh():
            self._started = True
            self._empty   = 0
            if self._off:
                self._off = False
                self.n_returns += 1
        elif self._started:
            self._empty += 1
            if self._empty == LIFT_DEBOUNCE_FRAMES and not self._off:
                self._off     = True
                self.n_lifts += 1

    def columns(self):
        return {
            "lifted":              int(self.n_lifts > 0),
            "n_lifts":             self.n_lifts,
            "returned_after_lift": "N/A" if self.n_lifts == 0 else int(self.n_returns > 0),
            "n_returns":           self.n_returns,
        }


def run_rest_period(tlog, draw, watch, lift=None):
    phase       = "wait"
    t_end       = None
    stale_since = None
    entry_fid   = None

    while True:
        draw(phase, None if t_end is None else max(0, (t_end - pygame.time.get_ticks()) / 1000))
        if stale_since is not None and pygame.time.get_ticks() - stale_since > STALE_NOTICE_MS:
            draw_notice("Lift your hand and place your index finger on the X.")
        flip()
        clock.tick(FPS)

        arm_touch(watch.shape)
        for event in pump(tlog, phase=phase):
            check_quit(event)

        if phase == "wait":
            entry = watch.entered()
            if entry is not None:
                TRIAL_TRIG["a"] = TRIG.pulse("a")
                tlog.add("down", "wait", entry[1], entry[2], entry[0],
                         "trial start touch")
                entry_fid   = entry[0]
                phase       = "rest"
                stale_since = None
                t_end       = pygame.time.get_ticks() + REST_MS
            elif watch.blocked_by_stale():
                if stale_since is None:
                    stale_since = pygame.time.get_ticks()
            else:
                stale_since = None
            continue

        if lift is not None and phase == "begin":
            lift.update()

        if pygame.time.get_ticks() >= t_end:
            if phase == "rest":
                play_bell()
                tlog.add("down", "rest", None, None, "", "begin cue")
                phase = "begin"
                t_end = pygame.time.get_ticks() + BEGIN_MS
            else:
                TRIAL_TRIG["b"] = TRIG.pulse("b")
                start_metronome()
                tlog.add("down", "begin", None, None, "", "trial onset")
                disarm_touch()
                return entry_fid


NUMPAD_BTN_W = s(110)
NUMPAD_BTN_H = s(82)
NUMPAD_GAP   = s(12)

_NUMPAD_LAYOUT = [
    ["7", "8", "9"],
    ["4", "5", "6"],
    ["1", "2", "3"],
    [None, "0", "DEL"],
]


def draw_numpad(cx, top):
    rects = {}
    for ri, row in enumerate(_NUMPAD_LAYOUT):
        for ci, key in enumerate(row):
            if key is None:
                continue
            x    = cx - (3 * NUMPAD_BTN_W + 2 * NUMPAD_GAP) // 2 + ci * (NUMPAD_BTN_W + NUMPAD_GAP)
            y    = top + ri * (NUMPAD_BTN_H + NUMPAD_GAP)
            rect = pygame.Rect(x, y, NUMPAD_BTN_W, NUMPAD_BTN_H)
            pygame.draw.rect(screen, TEXT_SECONDARY, rect, border_radius=s(10))
            text_c(key, font_bold, BACKGROUND, rect.centerx, rect.centery)
            rects[key] = rect
    return rects


KEY_BTN_W = s(104)
KEY_BTN_H = s(70)
KEY_GAP   = s(10)

_KEYBOARD_LAYOUT = [
    ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
    ["A", "S", "D", "F", "G", "H", "J", "K", "L", "-"],
    ["SHIFT", "Z", "X", "C", "V", "B", "N", "M", "’", "DEL"],
    ["SPACE"],
]

_KEY_SPAN = {"SHIFT": 1, "DEL": 1, "SPACE": 6}

_KEY_INDENT = {3: 2}

_KEYBOARD_COLS = 10


def draw_keyboard(cx, top, shift=False):
    rects = {}
    total = _KEYBOARD_COLS * KEY_BTN_W + (_KEYBOARD_COLS - 1) * KEY_GAP
    left  = cx - total // 2
    for ri, row in enumerate(_KEYBOARD_LAYOUT):
        col = _KEY_INDENT.get(ri, 0)
        y   = top + ri * (KEY_BTN_H + KEY_GAP)
        for key in row:
            span = _KEY_SPAN.get(key, 1)
            x    = left + col * (KEY_BTN_W + KEY_GAP)
            w    = span * KEY_BTN_W + (span - 1) * KEY_GAP
            rect = pygame.Rect(x, y, w, KEY_BTN_H)
            live = ACCENT if (key == "SHIFT" and shift) else TEXT_SECONDARY
            pygame.draw.rect(screen, live, rect, border_radius=s(10))
            if key == "SPACE":
                label = "space"
            elif len(key) == 1 and key.isalpha():
                label = key if shift else key.lower()
            else:
                label = key
            text_c(label, font_small if len(label) > 1 else font_bold,
                   BACKGROUND, rect.centerx, rect.centery)
            rects[key] = rect
            col += span
    return rects


def key_char(key, shift):
    if key in ("SHIFT", "DEL"):
        return None
    if key == "SPACE":
        return " "
    if len(key) == 1 and key.isalpha():
        return key if shift else key.lower()
    return key


def study_folder(name):
    folder = os.path.join(STUDY_ROOT, name)
    os.makedirs(folder, exist_ok=True)
    return folder


def _stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _out_path(folder, pid, handedness, name):
    return os.path.join(study_folder(folder), f"{pid}_{handedness}_mo_{name}.csv")


def _write_csv(path, rows, append=False):
    header = not (append and os.path.exists(path))
    with open(path, "a" if append else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if header:
            writer.writeheader()
        writer.writerows(rows)


def existing_pids():
    pids = set()
    for path in glob.glob(os.path.join(STUDY_ROOT, "*", "*_*")):
        head = os.path.basename(path).split("_", 1)[0]
        if head.isdigit():
            pids.add(head)
    return pids


def save_initialization(pid, age, sex, ratings, handedness,
                        culture=(), culture_text="", gender="", gender_text=""):
    def sub(idxs):
        return round(sum(ratings[i] for i in idxs) / 4, 3)
    iv_avg  = sub([1, 4, 7, 10])
    ev_avg  = sub([2, 5, 8, 11])
    kin_avg = sub([0, 3, 6,  9])
    avg     = round(sum(ratings) / 12, 3)

    picked = set(culture)

    row = {
        "participant_id": pid,
        "handedness":     handedness,
        "age":            age,
        "sex":            sex,
        "gender":         gender,
        "gender_text":    gender_text,
        "timestamp":      _stamp(),
    }
    for _, _, key in CULTURE_OPTIONS:
        row[f"culture_{key}"] = int(key in picked)
    row[f"culture_{CULTURE_SELF_KEY}"]    = int(CULTURE_SELF_KEY in picked)
    row["culture_self_identify_text"]     = culture_text
    row[f"culture_{CULTURE_DECLINE_KEY}"] = int(CULTURE_DECLINE_KEY in picked)
    row["culture_n_selected"]             = len(picked)

    for i, r in enumerate(ratings, 1):
        row[f"miq3_q{i:02d}"] = r
    row["miq3_iv_avg"]  = iv_avg
    row["miq3_ev_avg"]  = ev_avg
    row["miq3_kin_avg"] = kin_avg
    row["miq3_avg"]     = avg

    _write_csv(_out_path("mo_initialization", pid, handedness, "initialization"), [row])


def save_calibration(pid, handedness, vectors, peak, large, small, angle_deg):
    row = {
        "participant_id": pid,
        "handedness":     handedness,
        "timestamp":      _stamp(),
    }
    for i, (dx, dy) in enumerate(vectors, 1):
        row[f"calib_dx_{i}"]   = round(dx, 1)
        row[f"calib_dy_{i}"]   = round(dy, 1)
        row[f"calib_dist_{i}"] = round(math.hypot(dx, dy))
    row["peak_dist_avg_px"] = peak
    row["angle_deg"]        = round(angle_deg, 2)
    row["large_amp_px"]     = large
    row["small_amp_px"]     = small
    _write_csv(_out_path("mo_calibration", pid, handedness, "calibration"), [row])


def save_maas(pid, handedness, condition, maas_scores):
    row  = {
        "participant_id": pid,
        "handedness":     handedness,
        "condition":      condition,
        "timestamp":      _stamp(),
    }
    for i, score in enumerate(maas_scores, 1):
        row[f"maas_{i:02d}_raw"] = score
    reverse_scored = [MAAS_MAX - v for v in maas_scores]
    for i, score in enumerate(reverse_scored, 1):
        row[f"maas_{i:02d}_rev"] = score
    row["maas_state_mean"] = round(sum(reverse_scored) / len(reverse_scored), 3)
    _write_csv(_out_path("mo_questionnaire", pid, handedness, "maas"), [row], append=True)


def save_tlx(pid, handedness, tlx_scores):
    row  = {
        "participant_id": pid,
        "handedness":     handedness,
        "timestamp":      _stamp(),
    }
    tlx_keys = ["tlx_mental", "tlx_physical", "tlx_temporal",
                "tlx_performance", "tlx_effort", "tlx_frustration"]
    for key, score in zip(tlx_keys, tlx_scores):
        row[key] = score
    row["tlx_mean"] = round(sum(tlx_scores) / 6, 1)
    _write_csv(_out_path("mo_questionnaire", pid, handedness, "tlx"), [row])


def save_session(pid, handedness, task_order, trial_seqs):
    row  = {
        "participant_id":   pid,
        "handedness":       handedness,
        "timestamp":        _stamp(),
        "first_condition":  ">".join(FIXED_CONDITIONS),
        "condition_order":  ">".join(task_order),
        "trials_per_condition": TRIALS_PER_CONDITION,
        "trial_seconds":        TRIAL_SECONDS,
    }
    for i, cond in enumerate(task_order, 1):
        row[f"cond_{i}"] = cond
    for cond in CONDITIONS:
        row[f"{cond.lower()}_trial_seq"]  = ">".join(trial_seqs[cond])
        row[f"{cond.lower()}_size_first"] = trial_seqs[cond][0]
    row["triggers_enabled"]   = TRIGGERS_ENABLED_AT_CONTINUE
    row["trigger_mock"]       = int(TRIG.mock)
    row["trigger_port"]       = TRIG.port or ""
    row["trigger_epoch"]      = TRIG.epoch_wallclock
    row["trigger_answered"]   = int(TRIG.answered)
    row["n_test_pulses"]      = TRIG.n_test
    row["trig_ab_nominal_ms"] = bbtk_trigger.AB_NOMINAL_MS
    row["trig_bc_nominal_ms"] = bbtk_trigger.BC_NOMINAL_MS
    _write_csv(_out_path("mo_session", pid, handedness, "session"), [row])


class TouchLog:
    """Gathers up every touch seen during a trial and writes them all out at the
    end.

    A touch that landed somewhere that was not listening, or that failed one of
    the filters, is written down like any other; its note says so, beginning
    "blocked - " and naming the reason."""

    def __init__(self):
        self.rows = []
        self.t0   = pygame.time.get_ticks()
        _REJECTS.clear()

    def add(self, kind, phase, x, y, fid, note=""):
        now    = pygame.time.get_ticks()
        onset  = TRIAL_CTX["onset"]
        target = TRIAL_CTX["target"]
        t_trial = None if onset is None else now - onset
        beat_i, beat_off = beat_of(t_trial)
        self.rows.append({
            "t_ms":      now - self.t0,
            "t_since_onset_ms": "" if t_trial is None else t_trial,
            "trial_index": BLOCK_CTX["trial_index"],
            "beat_index":      beat_i,
            "beat_offset_ms":  beat_off,
            "event":     kind,
            "phase":     phase,
            "x":         round(x, 1) if x is not None else "",
            "y":         round(y, 1) if y is not None else "",
            "dist_from_cross_px": (round(math.hypot(x - CROSS_POS[0], y - CROSS_POS[1]), 1)
                                   if x is not None else ""),
            "dist_from_target_px": (round(math.hypot(x - target[0], y - target[1]), 1)
                                    if x is not None and target is not None else ""),
            "finger_id": fid,
            "note":      note,
        })


BLOCK_CTX = {"cond_order_index": "", "condition_position": "",
             "trial_index": "", "size_position": ""}

TRIAL_CTX = {"onset": None, "target": None}

TRIAL_TRIG = {"a": None, "b": None, "c": None}

TRIGGERS_ENABLED_AT_CONTINUE = 0


def set_trial_ctx(onset=None, target=None):
    TRIAL_CTX["onset"]  = onset
    TRIAL_CTX["target"] = target


def save_task_block(pid, handedness, condition, block_label, size_label,
                    reps, seconds, touch_log, lift=None, misses=None):
    row = {
        "participant_id": pid,
        "handedness":     handedness,
        "condition":      condition,
        "block_label":    block_label,
        "size_label":     size_label,
        "timestamp":      _stamp(),
        "cond_order_index":   BLOCK_CTX["cond_order_index"],
        "condition_position": BLOCK_CTX["condition_position"],
        "trial_index":        BLOCK_CTX["trial_index"],
        "size_position":      BLOCK_CTX["size_position"],
        "duration_s":     seconds if seconds is not None else "",
        "reps":           reps if reps is not None else "",
        "metronome_bpm":  METRONOME_BPM,
        "n_beats":        TRIAL_BEATS,
        "cross_x":        CROSS_POS[0],
        "cross_y":        CROSS_POS[1],
    }
    row.update(lift.columns() if lift is not None
               else {k: "" for k in LiftCounter.FIELDS})
    row["n_missed_target_touches"] = "" if misses is None else misses
    row["imagery_bias"] = ""
    row["ao_imagery"]   = ""
    row["trig_a_ms"] = "" if TRIAL_TRIG["a"] is None else TRIAL_TRIG["a"]
    row["trig_b_ms"] = "" if TRIAL_TRIG["b"] is None else TRIAL_TRIG["b"]
    row["trig_c_ms"] = "" if TRIAL_TRIG["c"] is None else TRIAL_TRIG["c"]
    row["trig_ok"]   = int(all(TRIAL_TRIG[k] is not None for k in ("a", "b", "c")))

    _write_csv(_out_path("mo_task", pid, handedness, "task"), [row], append=True)

    trace = [{"participant_id": pid, "handedness": handedness, "condition": condition,
              "block_label": block_label, "size_label": size_label, **r}
             for r in touch_log.rows]
    if trace:
        _write_csv(_out_path("mo_task", pid, handedness, "touches"), trace, append=True)


def save_reps(pid, handedness, condition, size_label, rep_rows):
    if not rep_rows:
        return
    rows = [{"participant_id": pid, "handedness": handedness,
             "condition": condition, "size_label": size_label,
             "trial_index": BLOCK_CTX["trial_index"],
             "condition_position": BLOCK_CTX["condition_position"], **r}
            for r in rep_rows]
    _write_csv(_out_path("mo_task", pid, handedness, "reps"), rows, append=True)


def update_task_column(pid, handedness, condition, column, value):
    path = _out_path("mo_task", pid, handedness, "task")
    if not os.path.exists(path):
        return
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            rows   = list(reader)
        if column not in fields:
            return
        for row in rows:
            if row.get("condition") == condition and not row.get(column):
                row[column] = value
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".csv")
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        shutil.move(tmp, path)
    except Exception:
        pass


def save_imagery_bias(pid, handedness, condition, value):
    row = {
        "participant_id": pid,
        "handedness":     handedness,
        "condition":      condition,
        "timestamp":      _stamp(),
        "imagery_bias":   value,
        "scale_min":      IMAGERY_MIN,
        "scale_max":      IMAGERY_MAX,
    }
    _write_csv(_out_path("mo_questionnaire", pid, handedness, "imagery_bias"), [row], append=True)
    update_task_column(pid, handedness, condition, "imagery_bias", value)


def save_ao_imagery(pid, handedness, value):
    row = {
        "participant_id": pid,
        "handedness":     handedness,
        "condition":      "AO",
        "timestamp":      _stamp(),
        "ao_imagery":     value,
    }
    _write_csv(_out_path("mo_questionnaire", pid, handedness, "ao_imagery"), [row], append=True)
    update_task_column(pid, handedness, "AO", "ao_imagery", value)


def validate_pid(text):
    if not text.isdigit():
        return "Participant ID must be a 3-digit number (001–999)."
    if len(text) != 3:
        return "Participant ID must be exactly 3 digits (e.g. 042)."
    n = int(text)
    if n < 1 or n > 999:
        return "Participant ID must be between 001 and 999."
    if f"{n:03d}" in existing_pids():
        return f"ID {n:03d} already has data on this machine. Use a different ID."
    return ""


def validate_age(text):
    if not text.isdigit():
        return "Age must be a whole number."
    if int(text) < 18:
        return "Participant must be 18 or older."
    return ""


def wait_for_continue(draw, label="Continue"):
    while True:
        screen.fill(BACKGROUND)
        draw()
        btn = draw_continue_button(label)
        flip()
        clock.tick(FPS)
        for event in pygame.event.get():
            check_quit(event)
            if is_button_activated(event, btn):
                return


def screen_two_buttons(title, left, right, title_y=None, btn_y=None, cx=None,
                       question="", hint="", gated=False):
    BTN_W, BTN_H = s(220), s(110)
    GAP          = s(40)
    cx           = W // 2 if cx is None else cx
    title_y      = H // 2 - s(120) if title_y is None else title_y
    btn_y        = H // 2 - BTN_H // 2 + s(30) if btn_y is None else btn_y
    rects = (pygame.Rect(cx - GAP // 2 - BTN_W, btn_y, BTN_W, BTN_H),
             pygame.Rect(cx + GAP // 2,          btn_y, BTN_W, BTN_H))
    while True:
        screen.fill(BACKGROUND)
        text_c(title, font_title, TEXT, W // 2, title_y)
        if question:
            max_w = min(W - s(160), s(900))
            draw_wrapped(question, font_body, TEXT, W // 2 - max_w // 2, H // 2 - s(80), max_w)
        for rect, (letter, caption, _, _) in zip(rects, (left, right)):
            pygame.draw.rect(screen, ACCENT, rect, border_radius=s(14))
            text_c(letter,  font_large, BACKGROUND,     rect.centerx, rect.centery - s(8))
            text_c(caption, font_small, TEXT_SECONDARY, rect.centerx, rect.bottom + s(18))
        if hint:
            text_c(hint, font_small, TEXT_SECONDARY, W // 2, btn_y + BTN_H + s(70))
        flip()
        clock.tick(FPS)
        if gated:
            arm_touch(*(touch_rect(r) for r in rects))
        for event in (pump() if gated else pygame.event.get()):
            check_quit(event)
            for rect, (_, _, key, value) in zip(rects, (left, right)):
                if ((event.type == pygame.KEYDOWN and event.key == key)
                        or is_button_touched(event, rect)):
                    if gated:
                        disarm_touch()
                    return value


def screen_welcome():
    def draw():
        if logo is not None:
            screen.blit(logo, (W // 2 - logo.get_width() // 2, s(220)))
        text_c("Motor Overflow Study", font_title, TEXT,           W // 2, s(370))
        text_c("NIMBL @ UBCO",         font_small, TEXT_SECONDARY, W // 2, s(415))
    wait_for_continue(draw, "Begin")


def screen_handedness():
    return screen_two_buttons("Participant Hand",
                              ("L", "Left",  pygame.K_l, "lh"),
                              ("R", "Right", pygame.K_r, "rh"))


def screen_text_input(title, hint, validator, max_len=None, keys="number",
                      start_text="", allow_cancel=False):
    text  = start_text
    error = ""
    shift = not start_text
    letters = keys == "letters"
    while True:
        screen.fill(BACKGROUND)
        text_c(title, font_title, TEXT,           W // 2, s(130))
        text_c(hint,  font_small, TEXT_SECONDARY, W // 2, s(178))
        blink = "|" if pygame.time.get_ticks() % 1000 < 500 else ""
        entry_font = font_large if not letters else font_body
        text_c(text + blink, entry_font, TEXT, W // 2, s(245))
        if error:
            text_c(error, font_small, ERROR, W // 2, s(300))
        if letters:
            np_rects = draw_keyboard(W // 2, s(335), shift)
        else:
            np_rects = draw_numpad(W // 2, s(335))
        btn    = draw_continue_button("Confirm")
        cancel = (draw_continue_button("Cancel", side="left")
                  if allow_cancel else None)
        flip()
        clock.tick(FPS)
        for event in pygame.event.get():
            check_quit(event)
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_BACKSPACE:
                    text = text[:-1]; error = ""
                elif event.key in ENTER:
                    err = validator(text)
                    if err:
                        error = err
                    else:
                        return text
                elif event.unicode.isprintable():
                    if max_len is None or len(text) < max_len:
                        text += event.unicode; error = ""
            else:
                if is_button_touched(event, cancel):
                    return None
                for key, rect in np_rects.items():
                    if is_button_touched(event, rect):
                        if key == "DEL":
                            text = text[:-1]
                        elif key == "SHIFT":
                            shift = not shift
                        else:
                            char = key_char(key, shift)
                            if max_len is None or len(text) < max_len:
                                text += char
                            shift = False
                        error = ""
                        break
                if is_button_touched(event, btn):
                    err = validator(text)
                    if err:
                        error = err
                    else:
                        return text


def screen_sex():
    return screen_two_buttons("Participant Sex",
                              ("M", "Male",   pygame.K_m, "M"),
                              ("F", "Female", pygame.K_f, "F"),
                              hint="or press  M / F  on keyboard")

CHECKLIST_FONT_SIZES = (26, 24, 22, 20, 18, 16, 14)

CHECKLIST_ROW_GAP = 0.45


def fit_checklist(options, max_width, max_height):
    for size in CHECKLIST_FONT_SIZES:
        name_font = mfont(size)
        ex_font   = mfont(max(size - 5, 12))
        step_n    = lh(name_font)
        step_e    = lh(ex_font)
        gap       = int(step_n * CHECKLIST_ROW_GAP)
        wrapped   = [wrap_text(ex, ex_font, max_width) if ex else []
                     for _, ex, _ in options]
        height    = sum(step_n + len(w) * step_e + gap for w in wrapped)
        if height <= max_height:
            return name_font, ex_font, wrapped
    return name_font, ex_font, wrapped


def draw_check(rect, on, multi):
    if multi:
        pygame.draw.rect(screen, ACCENT if on else TEXT_SECONDARY, rect, 2,
                         border_radius=s(4))
        if on:
            x, y, w, h = rect
            pygame.draw.lines(screen, ACCENT, False, [
                (x + w * 0.20, y + h * 0.52),
                (x + w * 0.42, y + h * 0.75),
                (x + w * 0.80, y + h * 0.25),
            ], s(3))
    else:
        centre = rect.center
        pygame.draw.circle(screen, ACCENT if on else TEXT_SECONDARY, centre,
                           rect.width // 2, 2)
        if on:
            pygame.draw.circle(screen, ACCENT, centre, rect.width // 4)


def screen_checklist(title, question, options, multi, free_key, free_label,
                     free_title, decline_key, decline_label):
    rows = list(options)
    rows.append((free_label, "", free_key))
    rows.append((decline_label, "", decline_key))

    box    = s(28)
    left   = s(90)
    text_x = left + box + s(22)
    top    = s(190)
    bottom = H - s(150)
    name_font, ex_font, wrapped = fit_checklist(rows, W - text_x - s(90),
                                                bottom - top)
    step_n = lh(name_font)
    step_e = lh(ex_font)
    gap    = int(step_n * CHECKLIST_ROW_GAP)

    picked    = set()
    free_text = ""

    while True:
        screen.fill(BACKGROUND)
        text_c(title,    font_title, TEXT, W // 2, s(70))
        text_c(question, font_small, TEXT_SECONDARY, W // 2, s(130))

        hits = {}
        y    = top
        for (name, _, key), ex_lines in zip(rows, wrapped):
            on   = key in picked
            rect = pygame.Rect(left, y + (step_n - box) // 2, box, box)
            draw_check(rect, on, multi)

            label = name
            if key == free_key and free_text:
                label = f"{name}  {free_text}"
            screen.blit(name_font.render(label, True, ACCENT if on else TEXT),
                        (text_x, y))
            ey = y + step_n
            for line in ex_lines:
                screen.blit(ex_font.render(line, True, TEXT_SECONDARY), (text_x, ey))
                ey += step_e

            row_h     = step_n + len(ex_lines) * step_e
            hits[key] = pygame.Rect(left, y, W - left - s(60), row_h)
            y         = ey + gap

        btn = draw_continue_button("Confirm") if picked else None
        if not picked:
            text_c("Select an answer to continue.", font_small, TEXT_SECONDARY,
                   W // 2, H - s(90))
        flip()
        clock.tick(FPS)

        for event in pygame.event.get():
            check_quit(event)
            if is_button_activated(event, btn):
                return [key for _, _, key in rows if key in picked], free_text

            for key, rect in hits.items():
                if not is_button_touched(event, rect):
                    continue
                if key == free_key:
                    written = screen_text_input(
                        free_title, "Type your answer, then Confirm.",
                        lambda t: "" if t.strip() else "Type an answer, or Cancel.",
                        max_len=60, keys="letters", start_text=free_text,
                        allow_cancel=True)
                    if written is None:
                        picked.discard(key)
                        free_text = ""
                        break
                    free_text = written.strip()
                elif key in picked:
                    picked.discard(key)
                    break
                if not multi or key == decline_key:
                    picked.clear()
                elif decline_key in picked:
                    picked.discard(decline_key)
                picked.add(key)
                break


def screen_cultural_background():
    return screen_checklist(
        "Cultural Background", CULTURE_QUESTION, CULTURE_OPTIONS, True,
        CULTURE_SELF_KEY,    "Prefer to self-identify:",
        "Prefer to self-identify",
        CULTURE_DECLINE_KEY, "Prefer not to answer")


def screen_gender():
    picked, written = screen_checklist(
        "Gender", GENDER_QUESTION, GENDER_OPTIONS, False,
        GENDER_SELF_KEY,    "Not listed:", "Gender — not listed",
        GENDER_DECLINE_KEY, "Prefer not to answer")
    return picked[0], written


def screen_miq3_intro():
    v_lines = [f"{i+1} — {lbl}" for i, lbl in enumerate(VISUAL_LABELS)]
    k_lines = [f"{i+1} — {lbl}" for i, lbl in enumerate(KINESTHETIC_LABELS)]

    def draw():
        text_c("MIQ-3 Rating Scales", font_title, TEXT, W // 2, s(60))

        step = lh(font_small)
        text_c("Visual Imagery Scale", font_body, TEXT, W // 2, s(120))
        y = s(158)
        for line in v_lines:
            text_c(line, font_small, TEXT_SECONDARY, W // 2, y)
            y += step

        text_c("Kinesthetic Imagery Scale", font_body, TEXT, W // 2, y + s(16))
        y += lh(font_body) + s(20)
        for line in k_lines:
            text_c(line, font_small, TEXT_SECONDARY, W // 2, y)
            y += step

    wait_for_continue(draw, "Begin")


def run_rating_items(n_items, values, draw_item, anchors=None, row_clear=60):
    values    = list(values)
    ratings   = [None] * n_items
    q_idx     = 0
    selection = None

    while q_idx < n_items:
        screen.fill(BACKGROUND)
        y         = draw_item(q_idx)
        num_rects = draw_rating_row(values, selection, y, anchors=anchors)
        y        += s(row_clear)
        btn = None
        if selection is None:
            text_c(f"Tap or press {values[0]} – {values[-1]} to select",
                   font_small, TEXT_SECONDARY, W // 2, y + s(10))
        else:
            text_c(f"Selected: {selection}", font_small, ACCENT, W // 2, y + s(10))
            btn = draw_continue_button("Confirm")

        flip()
        clock.tick(FPS)

        for event in pygame.event.get():
            check_quit(event)
            if event.type == pygame.KEYDOWN:
                if event.key in NUM_KEYS and NUM_KEYS[event.key] in values:
                    selection = NUM_KEYS[event.key]
                elif event.key in ENTER and selection is not None:
                    ratings[q_idx] = selection
                    q_idx    += 1
                    selection = None
                    break
            else:
                for val, rect in num_rects.items():
                    if is_button_touched(event, rect):
                        selection = val
                        break
                if is_button_touched(event, btn):
                    ratings[q_idx] = selection
                    q_idx    += 1
                    selection = None
                    break

    return ratings


def screen_miq3():
    def draw_item(q_idx):
        item_num, label, img_type, move_key = ITEMS[q_idx]
        type_name, _        = IMG_TYPE[img_type]
        start_pos, action_text = MOVEMENT_INSTRUCTIONS[move_key]
        mental_text         = MENTAL_TASK[img_type]

        text_c(f"Item {item_num} of 12  —  {label}  ({type_name})", font_small, TEXT_SECONDARY, W // 2, s(36))

        text_w = int(W * 0.89)
        text_x = (W - text_w) // 2
        y      = s(72)

        for section, content in [
            ("STARTING POSITION", start_pos),
            ("ACTION",            action_text),
            ("MENTAL TASK",       mental_text),
        ]:
            lbl_surf = font_xs_bold.render(section, True, ACCENT)
            screen.blit(lbl_surf, (text_x, y))
            y += lbl_surf.get_height() + s(4)
            y  = draw_wrapped(content, font_small, TEXT_SECONDARY, text_x, y, text_w)
            y += s(16)

        text_c("Rate the ease/difficulty of the mental task:", font_small, TEXT, W // 2, y + s(10))
        return y + s(40)

    return run_rating_items(12, range(1, 8), draw_item)


def screen_done(pid):
    def draw():
        text_c("Initialization Complete", font_title, OK,   W // 2, s(340))
        text_c(f"Participant {pid}",       font_body,  TEXT, W // 2, s(420))
    wait_for_continue(draw)


def screen_instructions(text, button_label="Continue", title="", side=None):
    max_w  = min(W - s(160), s(1000))
    left   = (W - max_w) // 2
    top    = s(160) if title else s(60)
    bottom = H - s(90) - s(30)

    points = None if isinstance(text, str) else list(text)
    if points is None:
        font, femph, lines = fit_wrapped(text, max_w, bottom - top)
        step   = lh(font)
        gap    = 0
        indent = 0
        block  = len(lines) * step
    else:
        font, femph, indent, wrapped = fit_points(points, max_w, bottom - top)
        step   = lh(font)
        gap    = int(step * INSTRUCTION_POINT_GAP)
        block  = sum(len(w) for w in wrapped) * step + gap * (len(wrapped) - 1)

    text_y = H // 2 - block // 2 + step // 2
    text_y = min(max(text_y, top + step // 2), bottom - block + step // 2)
    while True:
        screen.fill(BACKGROUND)
        if title:
            text_c(title, font_title, TEXT, W // 2, s(80))
        y = text_y
        if points is None:
            for line in lines:
                width = sum((femph if e else font).size(w)[0] for w, e in line) \
                        + font.size(" ")[0] * max(0, len(line) - 1)
                draw_runs(line, font, femph, TEXT, ACCENT, W // 2 - width // 2, y)
                y += step
        else:
            for point in wrapped:
                for i, line in enumerate(point):
                    if i == 0:
                        bullet = font.render(INSTRUCTION_BULLET.strip(), True, ACCENT)
                        screen.blit(bullet, bullet.get_rect(midleft=(left, y)))
                    draw_runs(line, font, femph, TEXT, ACCENT, left + indent, y)
                    y += step
                y += gap
        btn = draw_continue_button(button_label, side=side)
        flip()
        clock.tick(FPS)
        if side is not None:
            arm_touch(touch_rect(btn))
        else:
            disarm_touch()
        for event in pump():
            check_quit(event)
            if is_button_activated(event, btn):
                disarm_touch()
                return


def screen_metronome_familiarization():
    start_metronome_loop()
    t0     = pygame.time.get_ticks()
    cy     = H // 2 + s(20)
    r_rest = s(26)
    r_peak = s(74)
    while True:
        since = pygame.time.get_ticks() - t0
        into  = since % BEAT_MS
        beat  = since // BEAT_MS + 1
        flare = max(0.0, 1.0 - into / (BEAT_MS * 0.55)) ** 2
        radius = int(r_rest + (r_peak - r_rest) * flare)

        screen.fill(BACKGROUND)
        text_c("The Metronome", font_title, TEXT, W // 2, s(70))
        text_c(f"{METRONOME_BPM} beats per minute — one movement on every beat",
               font_body, TEXT_SECONDARY, W // 2, s(140))

        pygame.draw.circle(screen, ACCENT, (W // 2, cy), radius)
        pygame.draw.circle(screen, TEXT_SECONDARY, (W // 2, cy), r_peak, 1)
        text_c(f"Beat {beat}", font_small, TEXT_SECONDARY, W // 2, cy + r_peak + s(50))

        if _metronome_loop is None:
            draw_notice("No sound output — tell the experimenter before continuing.")
        text_c("Listen for as long as you like, then continue.",
               font_small, TEXT_SECONDARY, W // 2, H - s(150))
        btn = draw_continue_button()
        flip()
        clock.tick(FPS)
        disarm_touch()
        for event in pump():
            check_quit(event)
            if is_button_activated(event, btn):
                stop_metronome()
                return


def run_calibration(pid, handedness):
    sub          = "wait_x"
    vectors      = []
    reps         = 0
    peak         = None
    reach_taken  = False
    err          = ""
    err_until    = 0
    x_shape      = touch_circle(CROSS_POS, CROSS_HIT_RADIUS)
    wedge_shape  = touch_reach_wedge()
    x_watch      = RegionWatch(x_shape)
    wedge_watch  = None

    start_metronome_loop()

    def notify(msg, seconds=3.5):
        nonlocal err, err_until
        err       = msg
        err_until = pygame.time.get_ticks() + int(seconds * 1000)

    def take_reach():
        nonlocal peak, reach_taken
        if reach_taken:
            return True
        reaching = wedge_watch.arrivals()
        if not reaching:
            return False
        far = max(reaching, key=lambda c: math.hypot(c[1] - CROSS_POS[0],
                                                     c[2] - CROSS_POS[1]))
        peak, reach_taken = (far[1], far[2]), True
        return True

    def explain_blocked(fx, fy):
        if sub != "in_flight" or (err and pygame.time.get_ticks() < err_until):
            return
        dist = math.hypot(fx - CROSS_POS[0], fy - CROSS_POS[1])
        if not ACTIVE_ZONE.collidepoint(fx, fy):
            return
        if dist <= CALIBRATION_EXCLUSION_RADIUS:
            notify("Too close to the X — reach further out before touching down.")
        elif in_reach_cone(fx, fy):
            return
        else:
            notify("That touch was outside the shaded area — reach out to "
                   "the side, within the shaded zone.")

    while True:
        screen.fill(BACKGROUND)
        draw_touch_zone_cone()
        text_c(f"Calibration  —  Rep {min(reps + 1, CALIBRATION_REPS)} of {CALIBRATION_REPS}",
               font_title, TEXT, W // 2, s(60))

        draw_cross(sub in ("wait_x", "on_x", "returning"))

        list_top = H - s(110) - (CALIBRATION_REPS - 2) * lh(font_small)
        for i, (dx, dy) in enumerate(vectors):
            text_c(f"Rep {i + 1}:  {round(math.hypot(dx, dy))} px", font_small, OK,
                   W // 2, list_top + i * lh(font_small))
        if err and pygame.time.get_ticks() < err_until:
            draw_notice(err)
        elif err:
            err = ""

        flip()
        clock.tick(FPS)

        if sub in ("wait_x", "returning"):
            arm_touch(x_shape)
        elif sub in ("on_x", "in_flight"):
            arm_touch(wedge_shape)
        else:
            arm_touch(x_shape)

        for event in pump(phase=sub, on_blocked=explain_blocked):
            check_quit(event)

        if sub == "wait_x":
            if x_watch.entered():
                err = ""; sub = "on_x"; reach_taken = False
                wedge_watch = RegionWatch(wedge_shape)
            elif x_watch.blocked_by_stale() and not (err and pygame.time.get_ticks() < err_until):
                notify("Lift your hand and place your index finger on the X.")
        elif sub == "on_x":
            take_reach()
            if x_watch.lifted():
                sub = "in_flight"
        elif sub == "in_flight":
            if take_reach():
                sub = "at_max"; err = ""
                x_watch = RegionWatch(x_shape)
        elif sub == "at_max":
            x_watch.arrived()
            if wedge_watch.lifted():
                sub = "returning"
        elif sub == "returning":
            if x_watch.arrived():
                vectors.append((peak[0] - CROSS_POS[0], peak[1] - CROSS_POS[1]))
                reps += 1; err = ""
                if reps >= CALIBRATION_REPS:
                    avg_dx    = sum(v[0] for v in vectors) / len(vectors)
                    avg_dy    = sum(v[1] for v in vectors) / len(vectors)
                    avg_peak  = math.hypot(avg_dx, avg_dy)
                    angle     = math.atan2(avg_dy, avg_dx)
                    peak_dist = round(avg_peak)
                    large_amp = round(avg_peak * 0.80)
                    small_amp = round(avg_peak * 0.30)
                    save_calibration(pid, handedness, vectors, peak_dist, large_amp, small_amp,
                                      math.degrees(angle))
                    stop_metronome()
                    disarm_touch()
                    return peak_dist, large_amp, small_amp, angle
                else:
                    sub = "on_x"; reach_taken = False
                    wedge_watch = RegionWatch(wedge_shape)
            elif x_watch.blocked_by_stale() and not (err and pygame.time.get_ticks() < err_until):
                notify("Lift your hand and place your index finger back on the X.")


def screen_calib_results(peak_dist, large_amp, small_amp, angle, handedness):
    large_pos, large_actual, large_clamped = target_geometry(large_amp, angle)
    small_pos, small_actual, small_clamped = target_geometry(small_amp, angle)

    while True:
        screen.fill(BACKGROUND)
        text_c(
            f"Peak (avg of {CALIBRATION_REPS}): {peak_dist} px   |   Large (80%): {large_amp} px   |   "
            f"Small (30%): {small_amp} px",
            font_small, TEXT_SECONDARY, W // 2, s(60),
        )
        text_c(
            f"Touch tolerance — large: {block_hit_radius(large_actual)} px   |   "
            f"small: {block_hit_radius(small_actual)} px   (nominal {CIRCLE_HIT_RADIUS} px)",
            font_xs, TEXT_SECONDARY, W // 2, s(92),
        )
        draw_x(CROSS_POS)
        pygame.draw.circle(screen, TEXT, large_pos, CIRCLE_RADIUS, 2)
        pygame.draw.circle(screen, TEXT, small_pos, CIRCLE_RADIUS, 2)
        text_c("Calibration Complete", font_title, OK, W // 2, H - s(300))
        if large_clamped or small_clamped:
            which = " and ".join(n for n, c in (("Large", large_clamped), ("Small", small_clamped)) if c)
            text_c(f"NOTE: {which} target clamped to fit the active zone — "
                   f"actual {round(large_actual)} / {round(small_actual)} px",
                   font_small, ERROR, W // 2, H - s(250))
        text_c("Would you like to redo the calibration?", font_body, TEXT, W // 2, H - s(200))

        side     = button_side(handedness)
        redo_btn = draw_continue_button("Redo Calibration", y=H - s(170), side=side)
        cont_btn = draw_continue_button("Begin Task",       y=H - s(90),  side=side)
        flip()
        clock.tick(FPS)
        arm_touch(touch_rect(redo_btn), touch_rect(cont_btn))
        for event in pump():
            check_quit(event)
            if is_button_touched(event, redo_btn):
                disarm_touch()
                return "redo"
            if is_button_activated(event, cont_btn):
                disarm_touch()
                return "continue"

def run_covert_timed_block(pid, handedness, condition, amp, angle, task_type,
                           size_label, seconds=TRIAL_SECONDS):
    target  = target_position(amp, angle)
    tlog    = TouchLog()
    hit_r   = block_hit_radius(math.hypot(target[0] - CROSS_POS[0],
                                          target[1] - CROSS_POS[1]))
    x_shape = touch_circle(CROSS_POS, hit_r)
    x_watch = RegionWatch(x_shape)
    lift    = LiftCounter(x_watch)

    def draw_frame(phase, remaining):
        screen.fill(BACKGROUND)
        text_c(task_type, font_title, TEXT, W // 2, s(60))
        draw_cross(phase in ("wait", "rest", "begin"), hit_r)
        pygame.draw.circle(screen, TEXT, target, CIRCLE_RADIUS, 2)
        draw_block_footer(phase, remaining, block_status_pos())

    def note_touch(fx, fy):
        away = math.hypot(fx - CROSS_POS[0], fy - CROSS_POS[1])
        return f"touch during imagery, {round(away)} px from the X"

    run_rest_period(tlog, draw_frame, x_watch, lift=lift)
    t_onset = pygame.time.get_ticks()
    t_end   = t_onset + seconds * 1000
    set_trial_ctx(onset=t_onset, target=target)

    while True:
        draw_frame("running", max(0, (t_end - pygame.time.get_ticks()) / 1000))
        flip()
        clock.tick(FPS)

        if pygame.time.get_ticks() >= t_end:
            TRIAL_TRIG["c"] = TRIG.pulse("c")
            play_bell()
            stop_metronome()
            save_task_block(pid, handedness, condition, task_type, size_label,
                            None, seconds, tlog, lift=lift)
            set_trial_ctx()
            disarm_touch()
            return

        arm_touch()
        for event in pump(tlog, phase="running", on_blocked=note_touch):
            check_quit(event)

        lift.update()


AO_ROTATE = cv2.ROTATE_90_COUNTERCLOCKWISE
AO_LETTERBOX_LEVEL = 12

AO_FIXATION_STRIP_FRAC = 0.38
AO_FIXATION_STRIP      = int(W * AO_FIXATION_STRIP_FRAC)

AO_FIXATION_Y_FRAC = 0.30


def ao_cross_pos(handedness):
    return (W - AO_FIXATION_STRIP // 2 if handedness == "rh" else AO_FIXATION_STRIP // 2,
            int(H * AO_FIXATION_Y_FRAC))


def ao_video_area(handedness):
    width = W - AO_FIXATION_STRIP
    return pygame.Rect(0 if handedness == "rh" else AO_FIXATION_STRIP, 0, width, H)


def ao_content_box(cap, samples=5):
    full = (0, 0,
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))  or 0,
            int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if n < samples:
        return full
    brightest = None
    for i in range(1, samples + 1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * i / (samples + 1)))
        ok, frame = cap.read()
        if not ok:
            continue
        grey      = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightest = grey if brightest is None else cv2.max(brightest, grey)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    if brightest is None:
        return full
    lit  = brightest > AO_LETTERBOX_LEVEL
    cols = lit.any(axis=0).nonzero()[0]
    rows = lit.any(axis=1).nonzero()[0]
    if not len(cols) or not len(rows):
        return full
    x, w = int(cols[0]), int(cols[-1] - cols[0] + 1)
    y, h = int(rows[0]), int(rows[-1] - rows[0] + 1)
    if w < full[2] * 0.2 or h < full[3] * 0.2:
        return full
    return (x, y, w, h)


def ao_frame_surface(frame, box, area):
    x, y, w, h = box
    frame = frame[y:y + h, x:x + w]
    frame = cv2.rotate(frame, AO_ROTATE)
    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    fh, fw = frame.shape[:2]
    scale  = min(area.width / fw, area.height / fh)
    frame  = cv2.resize(frame, (max(1, int(fw * scale)), max(1, int(fh * scale))),
                        interpolation=cv2.INTER_AREA)
    surf   = pygame.surfarray.make_surface(frame.swapaxes(0, 1))
    return surf, (area.x + (area.width  - surf.get_width())  // 2,
                  area.y + (area.height - surf.get_height()) // 2)


def ao_still(model, handedness, size, max_w, max_h):
    path = AO_VIDEO_PATHS[(model, handedness, size)]
    if not os.path.exists(path):
        return None
    cap = cv2.VideoCapture(path)
    try:
        box = ao_content_box(cap)
        n   = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if n:
            cap.set(cv2.CAP_PROP_POS_FRAMES, n // 3)
        ok, frame = cap.read()
        if not ok:
            return None
        surf, _at = ao_frame_surface(frame, box, pygame.Rect(0, 0, max_w, max_h))
        return surf
    except Exception:
        return None
    finally:
        cap.release()


def screen_ao_hand_choice(handedness):
    thumb_w, thumb_h = s(430), s(300)
    gap    = s(60)
    top    = s(210)
    stills = {m: ao_still(m, handedness, "Large", thumb_w, thumb_h) for m in AO_HAND_MODELS}
    rects  = {}
    total  = len(AO_HAND_MODELS) * thumb_w + (len(AO_HAND_MODELS) - 1) * gap
    for i, model in enumerate(AO_HAND_MODELS):
        x = W // 2 - total // 2 + i * (thumb_w + gap)
        rects[model] = pygame.Rect(x, top, thumb_w, thumb_h)

    while True:
        screen.fill(BACKGROUND)
        text_c("Action Observation", font_title, TEXT, W // 2, s(70))
        text_c("Which hand looks most like your own?", font_body, TEXT, W // 2, s(140))
        for model, rect in rects.items():
            pygame.draw.rect(screen, ACCENT, rect.inflate(s(10), s(10)), 2, border_radius=s(12))
            still = stills[model]
            if still is not None:
                screen.blit(still, still.get_rect(center=rect.center))
            else:
                text_c("video not found", font_small, ERROR, rect.centerx, rect.centery)
            text_c(f"Hand {model.upper()}", font_bold, TEXT,
                   rect.centerx, rect.bottom + s(40))
        text_c("Tap the hand that looks most like yours.",
               font_small, TEXT_SECONDARY, W // 2, H - s(70))
        flip()
        clock.tick(FPS)
        arm_touch(*(touch_rect(r) for r in rects.values()))
        for event in pump():
            check_quit(event)
            for model, rect in rects.items():
                if is_button_touched(event, rect):
                    disarm_touch()
                    return model


def run_ao_block(pid, handedness, size_label, amp, model, seconds=TRIAL_SECONDS):
    video_path = AO_VIDEO_PATHS[(model, handedness, size_label)]
    missing    = not os.path.exists(video_path)
    cap   = None if missing else cv2.VideoCapture(video_path)
    fps   = FPS if missing else (cap.get(cv2.CAP_PROP_FPS) or 30)
    box   = None if missing else ao_content_box(cap)
    side  = button_side(handedness)
    area  = ao_video_area(handedness)
    cross = ao_cross_pos(handedness)
    tlog  = TouchLog()

    x_shape = touch_circle(cross, CROSS_HIT_RADIUS)
    x_watch = RegionWatch(x_shape)
    lift    = LiftCounter(x_watch)

    def note_touch(fx, fy):
        away = math.hypot(fx - cross[0], fy - cross[1])
        return f"touch while watching, {round(away)} px from the cross"

    def draw_gate(phase, remaining):
        screen.fill(BACKGROUND)
        text_c("Action Observation", font_title, TEXT, area.centerx, s(60))
        draw_cross(True, CROSS_HIT_RADIUS, cross)
        draw_block_footer(phase, remaining, (cross[0], s(120)))

    if missing:
        while True:
            screen.fill(BACKGROUND)
            text_c("Video Not Found", font_title, ERROR, W // 2, H // 2 - s(110))
            draw_wrapped(
                f"Expected file: {os.path.basename(video_path)}   "
                f"in {os.path.dirname(video_path)}. "
                "This block cannot present its stimulus. Notify the experimenter "
                "before continuing — the block will be recorded with no duration.",
                font_small, TEXT_SECONDARY, s(80), H // 2 - s(50), W - s(160),
            )
            btn = draw_continue_button("Skip Trial", side=side)
            flip()
            clock.tick(FPS)
            arm_touch(touch_rect(btn))
            for event in pump(tlog, phase="error"):
                check_quit(event)
                if is_button_activated(event, btn):
                    save_task_block(pid, handedness, "AO", "Action Observation", size_label,
                                    None, None, tlog)
                    disarm_touch()
                    return

    run_rest_period(tlog, draw_gate, x_watch, lift=lift)
    t_onset = pygame.time.get_ticks()
    t_end   = t_onset + seconds * 1000
    set_trial_ctx(onset=t_onset, target=None)

    shown = 0
    last  = None
    spent = False

    while pygame.time.get_ticks() < t_end:
        elapsed = pygame.time.get_ticks() - t_onset
        want    = int(elapsed * fps / 1000) + 1
        while not spent and shown < want:
            ok, frame = cap.read()
            if not ok:
                spent = True
                break
            shown += 1
            if shown < want:
                continue
            last = ao_frame_surface(frame, box, area)

        screen.fill(BACKGROUND)
        if last is not None:
            screen.blit(*last)
        draw_cross(False, CROSS_HIT_RADIUS, cross)
        flip()
        clock.tick(FPS)
        arm_touch()
        for event in pump(tlog, phase="playing", on_blocked=note_touch):
            check_quit(event)
        lift.update()

    if cap is not None:
        cap.release()
    TRIAL_TRIG["c"] = TRIG.pulse("c")
    play_bell()
    stop_metronome()
    save_task_block(pid, handedness, "AO", "Action Observation", size_label,
                    None, seconds, tlog, lift=lift)
    set_trial_ctx()
    disarm_touch()


def screen_maas_intro():
    def draw():
        text_c("Present-Moment Experiences", font_title, TEXT, W // 2, s(60))
        y = draw_wrapped(
            "Below is a collection of statements about your experience during the block "
            "you just completed. Using the 0-6 scale below, please indicate to what degree "
            "you were having each experience described. Please answer according to what "
            "really reflected your experience rather than what you think your experience "
            "should have been.",
            font_body, TEXT_SECONDARY, s(80), s(120), W - s(160),
        )
        draw_rating_row(range(MAAS_MIN, MAAS_MAX + 1), None, y + s(40), anchors=MAAS_ANCHORS)
    wait_for_continue(draw, "Begin")


def screen_maas():
    n_items = len(MAAS_ITEMS)

    def draw_item(q_idx):
        text_c(f"Present-Moment Experiences  —  Item {q_idx + 1} of {n_items}",
               font_small, TEXT_SECONDARY, W // 2, s(30))
        text_c("To what degree were you having this experience during the block you just completed?",
               font_small, TEXT_SECONDARY, W // 2, s(70))
        return draw_wrapped(MAAS_ITEMS[q_idx], font_body, TEXT, s(80), s(240), W - s(160)) + s(60)

    return run_rating_items(n_items, range(MAAS_MIN, MAAS_MAX + 1), draw_item,
                            anchors=MAAS_ANCHORS, row_clear=96)


SLIDER_X1 = W // 6
SLIDER_X2 = W - W // 6
SLIDER_W  = SLIDER_X2 - SLIDER_X1


class SliderTrack:
    def __init__(self, lo, hi, step=1):
        self.lo, self.hi, self.step = lo, hi, step

    def x_of(self, val):
        return SLIDER_X1 + int((val - self.lo) / (self.hi - self.lo) * SLIDER_W)

    def val_at(self, px):
        frac = (px - SLIDER_X1) / SLIDER_W
        val  = self.lo + round(frac * (self.hi - self.lo) / self.step) * self.step
        return max(self.lo, min(self.hi, val))


class SliderDrag:
    """Follows one finger, or the mouse, from touching down on a track to
    lifting off it. `idx` is which of the tracks is being dragged, or None."""

    def __init__(self):
        self.idx = None
        self.fid = None

    def handle(self, event, hits):
        down = get_touch_down(event)
        if down is not None:
            fx, fy, fid, _device_id = down
            for i, (rect, track) in enumerate(hits):
                if rect.collidepoint(fx, fy):
                    self.idx, self.fid = i, fid
                    return i, track.val_at(fx)
        elif self.idx is not None:
            track = hits[self.idx][1]
            if event.type == pygame.FINGERMOTION and event.finger_id == self.fid:
                return self.idx, track.val_at(event.x * W)
            if (event.type == pygame.MOUSEMOTION and self.fid == "mouse"
                    and pygame.mouse.get_pressed()[0]):
                return self.idx, track.val_at(event.pos[0])
            up_id = get_touch_up(event)
            if up_id is not None and up_id == self.fid:
                self.idx = self.fid = None
        return None


def screen_nasa_tlx():
    pygame.event.set_allowed(pygame.FINGERMOTION)
    values   = [50] * 6
    drag     = SliderDrag()
    track    = SliderTrack(0, 100, step=5)

    area_top = s(110)
    area_bot = H - s(90)
    spacing  = (area_bot - area_top) / 6

    while True:
        screen.fill(BACKGROUND)
        text_c("NASA Task Load Index", font_title, TEXT, W // 2, s(55))

        hits = []
        for i, (name, lbl_l, lbl_r) in enumerate(NASA_TLX_SCALES):
            cy = int(area_top + (i + 0.5) * spacing)

            text_c(name, font_bold, ACCENT if drag.idx == i else TEXT, W // 2, cy - s(46))

            pygame.draw.line(screen, TEXT_SECONDARY, (SLIDER_X1, cy), (SLIDER_X2, cy), 2)
            for t in range(21):
                tx = SLIDER_X1 + int(t / 20 * SLIDER_W)
                h  = s(8) if t % 4 == 0 else s(4)
                pygame.draw.line(screen, TEXT_SECONDARY, (tx, cy - h), (tx, cy + h), 1)

            text_c(lbl_l, font_small, TEXT_SECONDARY, SLIDER_X1 - s(55), cy)
            text_c(lbl_r, font_small, TEXT_SECONDARY, SLIDER_X2 + s(55), cy)

            tx = track.x_of(values[i])
            pygame.draw.circle(screen, ACCENT, (tx, cy), s(18))
            text_c(str(values[i]), font_xs, BACKGROUND, tx, cy)

            hits.append((pygame.Rect(SLIDER_X1 - s(18), cy - s(26), SLIDER_W + s(36), s(52)),
                         track))

        btn = draw_continue_button("Confirm")
        flip()
        clock.tick(FPS)

        for event in pygame.event.get():
            check_quit(event)
            moved = drag.handle(event, hits)
            if moved is not None:
                values[moved[0]] = moved[1]
            if is_button_activated(event, btn):
                pygame.event.set_blocked(pygame.FINGERMOTION)
                return values


def screen_imagery_slider(condition_label):
    pygame.event.set_allowed(pygame.FINGERMOTION)
    value = 0
    drag  = SliderDrag()
    track = SliderTrack(IMAGERY_MIN, IMAGERY_MAX)
    cy    = H // 2 + s(30)
    hits  = [(pygame.Rect(SLIDER_X1 - s(20), cy - s(30), SLIDER_W + s(40), s(60)), track)]

    while True:
        screen.fill(BACKGROUND)
        text_c("Imagery Check", font_title, TEXT, W // 2, s(60))
        text_c(f"During the {condition_label} block you just completed:",
               font_body, TEXT_SECONDARY, W // 2, s(130))
        text_c("Were you mostly FEELING the movement, or mostly SEEING it?",
               font_body, TEXT, W // 2, s(185))

        pygame.draw.line(screen, TEXT_SECONDARY, (SLIDER_X1, cy), (SLIDER_X2, cy), 2)
        for val in range(IMAGERY_MIN, IMAGERY_MAX + 1, 10):
            tx  = track.x_of(val)
            tall = val % 25 == 0
            h    = s(12) if tall else s(5)
            pygame.draw.line(screen, TEXT_SECONDARY, (tx, cy - h), (tx, cy + h), 1)
            if val in IMAGERY_ANCHORS:
                text_c(str(val), font_xs, TEXT_SECONDARY, tx, cy + s(34))
                text_c(IMAGERY_ANCHORS[val], font_xs, TEXT_SECONDARY, tx, cy + s(60))

        text_c(IMAGERY_LEFT_LABEL,  font_small, TEXT, SLIDER_X1, cy - s(60))
        text_c(IMAGERY_RIGHT_LABEL, font_small, TEXT, SLIDER_X2, cy - s(60))

        tx = track.x_of(value)
        pygame.draw.circle(screen, ACCENT, (tx, cy), s(20))
        text_c(f"{value:+d}" if value else "0", font_xs, BACKGROUND, tx, cy)

        text_c("Drag the marker, then confirm.", font_small, TEXT_SECONDARY,
               W // 2, H - s(150))
        btn = draw_continue_button("Confirm")
        flip()
        clock.tick(FPS)

        for event in pygame.event.get():
            check_quit(event)
            moved = drag.handle(event, hits)
            if moved is not None:
                value = moved[1]
            if is_button_activated(event, btn):
                pygame.event.set_blocked(pygame.FINGERMOTION)
                return value


def screen_ao_imagery_check(handedness):
    cx = W // 4 if button_side(handedness) == "left" else W - W // 4
    return screen_two_buttons("Action Observation",
                              ("Y", "Yes", pygame.K_y, 1),
                              ("N", "No",  pygame.K_n, 0),
                              title_y=s(60), btn_y=H // 2 + s(40), cx=cx,
                              question=AO_IMAGERY_QUESTION, gated=True)


def run_ao_imagery_check(pid, handedness):
    value = screen_ao_imagery_check(handedness)
    save_ao_imagery(pid, handedness, value)
    return value


def run_imagery_slider(pid, handedness, condition):
    label = CONDITION_META[condition][1]
    screen_instructions(IMAGERY_INSTRUCTION, title="Imagery Check",
                        side=button_side(handedness))
    value = screen_imagery_slider(label)
    save_imagery_bias(pid, handedness, condition, value)
    return value


def run_maas(pid, handedness, condition):
    screen_instructions(MAAS_INSTRUCTION, title="Mindful Attention Awareness Scale (MAAS)",
                         side=button_side(handedness))
    screen_maas_intro()
    maas_scores = screen_maas()
    save_maas(pid, handedness, condition, maas_scores)


def run_nasa_tlx(pid, handedness):
    screen_instructions(NASA_TLX_INSTRUCTION, title="NASA Task Load Index (NASA-TLX)")
    tlx_scores = screen_nasa_tlx()
    save_tlx(pid, handedness, tlx_scores)


def run_task_block(pid, handedness, condition, amp, angle, block_label, size_label,
                   seconds=TRIAL_SECONDS):
    target       = target_position(amp, angle)
    reps         = 0
    tlog         = TouchLog()
    target_dist  = math.hypot(target[0] - CROSS_POS[0], target[1] - CROSS_POS[1])
    hit_r        = block_hit_radius(target_dist)
    x_shape      = touch_circle(CROSS_POS, hit_r)
    target_shape = touch_circle(target, hit_r)
    x_watch      = RegionWatch(x_shape)

    def draw_frame(phase, remaining):
        screen.fill(BACKGROUND)
        text_c(block_label, font_title, TEXT, W // 2, s(60))
        draw_cross(phase in ("wait", "rest", "begin", "on_x", "returning"), hit_r)
        circle_active = phase in ("in_flight1", "at_circle")
        pygame.draw.circle(screen, ACCENT if circle_active else TEXT, target, CIRCLE_RADIUS, 2)
        draw_block_footer(phase, remaining, block_status_pos())

    run_rest_period(tlog, draw_frame, x_watch)
    t_onset      = pygame.time.get_ticks()
    t_end        = t_onset + seconds * 1000
    set_trial_ctx(onset=t_onset, target=target)
    sub          = "on_x"
    target_watch = RegionWatch(target_shape)

    misses     = 0
    rep_misses = 0
    rep_rows   = []
    t_out      = None
    t_target   = None
    hit_pos    = None

    def now_ms():
        return pygame.time.get_ticks() - t_onset

    def note_miss(fx, fy):
        nonlocal misses, rep_misses
        if sub not in ("on_x", "in_flight1"):
            return None
        if not ACTIVE_ZONE.collidepoint(fx, fy):
            return None
        if math.hypot(fx - CROSS_POS[0], fy - CROSS_POS[1]) <= hit_r:
            return None
        misses     += 1
        rep_misses += 1
        off = math.hypot(fx - target[0], fy - target[1])
        return f"miss - target by {round(off)} px"

    while True:
        draw_frame(sub, max(0, (t_end - pygame.time.get_ticks()) / 1000))
        flip()
        clock.tick(FPS)

        if pygame.time.get_ticks() >= t_end:
            TRIAL_TRIG["c"] = TRIG.pulse("c")
            play_bell()
            stop_metronome()
            save_task_block(pid, handedness, condition, block_label, size_label,
                            reps, seconds, tlog, misses=misses)
            save_reps(pid, handedness, condition, size_label, rep_rows)
            set_trial_ctx()
            disarm_touch()
            return reps

        if sub in ("on_x", "in_flight1"):
            arm_touch(target_shape)
        else:
            arm_touch(x_shape)

        for event in pump(tlog, phase=sub, on_blocked=note_miss):
            check_quit(event)

        if sub == "on_x":
            target_watch.arrived()
            if x_watch.lifted():
                sub   = "in_flight1"
                t_out = now_ms()
        elif sub == "in_flight1":
            entry = target_watch.arrived()
            if entry is not None:
                sub      = "at_circle"
                x_watch  = RegionWatch(x_shape)
                t_target = now_ms()
                tlog.add("down", "in_flight1", entry[1], entry[2], entry[0], "on target")
                hit_pos  = (entry[1], entry[2])
        elif sub == "at_circle":
            x_watch.arrived()
            if target_watch.lifted():
                sub = "returning"
        elif sub == "returning":
            entry = x_watch.arrived()
            if entry is not None and hit_pos is not None:
                reps      += 1
                t_return   = now_ms()
                achieved   = math.hypot(hit_pos[0] - CROSS_POS[0],
                                        hit_pos[1] - CROSS_POS[1])
                rep_rows.append({
                    "rep_index":          reps,
                    "t_out_ms":           t_out,
                    "t_target_ms":        t_target,
                    "t_return_ms":        t_return,
                    "out_duration_ms":    "" if t_out is None else t_target - t_out,
                    "return_duration_ms": t_return - t_target,
                    "cycle_ms":           "" if t_out is None else t_return - t_out,
                    "target_touch_x":     round(hit_pos[0], 1),
                    "target_touch_y":     round(hit_pos[1], 1),
                    "achieved_dist_px":   round(achieved, 1),
                    "error_px":           round(achieved - target_dist, 1),
                    "n_missed_touches":   rep_misses,
                })
                sub          = "on_x"
                target_watch = RegionWatch(target_shape)
                rep_misses   = 0
                t_out = t_target = None
                tlog.add("down", "returning", entry[1], entry[2], entry[0],
                         f"rep {reps} complete")

CONDITION_META = {
    "ME":  (ME_INSTRUCTION,  "Motor Execution"),
    "KMI": (KMI_INSTRUCTION, "Kinesthetic Motor Imagery"),
    "VMI": (VMI_INSTRUCTION, "Visual Motor Imagery"),
    "AO":  (AO_INSTRUCTION,  "Action Observation"),
}


def screen_trial_complete(handedness, label, trial_index, n_trials):
    last = trial_index >= n_trials
    while True:
        screen.fill(BACKGROUND)
        text_c("Block Complete" if last else "Trial Complete",
               font_title, OK, W // 2, H // 2 - s(100))
        text_c(label, font_body, TEXT, W // 2, H // 2 - s(30))
        text_c(f"Trial {trial_index} of {n_trials}",
               font_body, TEXT_SECONDARY, W // 2, H // 2 + s(30))
        text_c("Lift your hand off the screen. You may rest."
               if last else
               "Lift your hand off the screen, then continue when you are ready.",
               font_small, TEXT_SECONDARY, W // 2, H // 2 + s(90))
        if TRIG.failed:
            text_c("Trigger link lost — tell the experimenter.",
                   font_small, ERROR, W // 2, H // 2 + s(140))
        btn = draw_continue_button(side=button_side(handedness))
        flip()
        clock.tick(FPS)
        arm_touch(touch_rect(btn))
        for event in pump():
            check_quit(event)
            if is_button_activated(event, btn):
                disarm_touch()
                return


def run_condition(condition, pid, handedness, large_amp, small_amp, angle,
                  trial_seq, cond_position, order_index):
    side               = button_side(handedness)
    instruction, label = CONDITION_META[condition]
    amps               = {"Small": small_amp, "Large": large_amp}
    n_trials           = len(trial_seq)

    screen_instructions(instruction, title=label, side=side)
    hand_model = screen_ao_hand_choice(handedness) if condition == "AO" else None

    seen = {size: 0 for size in SIZES}
    for i, size in enumerate(trial_seq, 1):
        seen[size] += 1
        TRIAL_TRIG["a"] = TRIAL_TRIG["b"] = TRIAL_TRIG["c"] = None
        BLOCK_CTX["cond_order_index"]   = order_index
        BLOCK_CTX["condition_position"] = cond_position
        BLOCK_CTX["trial_index"]        = i
        BLOCK_CTX["size_position"]      = seen[size]

        amp = amps[size]
        if condition == "ME":
            run_task_block(pid, handedness, condition, amp, angle, label, size)
        elif condition in ("KMI", "VMI"):
            run_covert_timed_block(pid, handedness, condition, amp, angle, label, size)
        elif condition == "AO":
            run_ao_block(pid, handedness, size, amp, hand_model)

        screen_trial_complete(handedness, label, i, n_trials)

    BLOCK_CTX["trial_index"] = ""
    BLOCK_CTX["size_position"] = ""
    if condition in ("KMI", "VMI"):
        run_imagery_slider(pid, handedness, condition)
    elif condition == "AO":
        run_ao_imagery_check(pid, handedness)
    run_maas(pid, handedness, condition)


def screen_trigger_setup():
    global TRIGGERS_ENABLED_AT_CONTINUE
    ok, reason = TRIG.open(bbtk_trigger.DEFAULT_PORT)
    ports = TRIG.list_ports() if not ok else []
    # Ports are laid out two to a row in small buttons so that even eight of
    # them fit under the status and instruction text on the tablet screen.
    MAX_PORT_BUTTONS = 8
    PORT_COLS = 2
    PORT_BTN_W, PORT_BTN_H, PORT_GAP = s(400), s(44), s(10)
    instruction = ("Start Spike2 sampling now with Motor_Overflow_Config_v3. "
                   "Tap Test Pulse and confirm one marker appears on the Trig channel "
                   "for each tap. Keep Spike2 sampling until the Task Complete screen.")
    while True:
        screen.fill(BACKGROUND)
        text_c("Trigger Setup", font_title, TEXT, W // 2, s(90))

        detail = ""
        if TRIG.mock:
            status, colour = "MOCK MODE — no hardware, no pulses reach Spike2", ERROR
        elif TRIG.enabled and TRIG.answered:
            status, colour = f"BBTK answered on {TRIG.port} — now check Spike2", OK
        elif TRIG.enabled:
            status, colour = f"{TRIG.port} open but the BBTK did not answer", ERROR
            detail = reason
        else:
            status, colour = "BBTK not connected", ERROR
            detail = reason
        text_c(status, font_body, colour, W // 2, s(170))
        top = s(212)
        for line in wrap_text(detail, font_small, W - s(200)):
            text_c(line, font_small, ERROR, W // 2, top)
            top += lh(font_small)
        top += s(24)

        for line in wrap_text(instruction, font_small, W - s(200)):
            text_c(line, font_small, TEXT_SECONDARY, W // 2, top)
            top += lh(font_small)
        top += s(30)

        test_btn  = None
        port_btns = []
        rescan_btn = None
        if TRIG.enabled:
            test_btn = draw_continue_button("Test Pulse", y=top)
            text_c(f"Test pulses sent: {TRIG.n_test}", font_small, TEXT_SECONDARY,
                   W // 2, top + BUTTON_H + s(30))
        else:
            text_c("Tap the port the BBTK is on:", font_small, TEXT_SECONDARY, W // 2, top)
            top += s(36)
            grid_w = PORT_COLS * PORT_BTN_W + (PORT_COLS - 1) * PORT_GAP
            left = W // 2 - grid_w // 2
            for i, (device, desc) in enumerate(ports[:MAX_PORT_BUTTONS]):
                col, row = i % PORT_COLS, i // PORT_COLS
                rect = pygame.Rect(left + col * (PORT_BTN_W + PORT_GAP),
                                   top + row * (PORT_BTN_H + PORT_GAP),
                                   PORT_BTN_W, PORT_BTN_H)
                pygame.draw.rect(screen, ACCENT, rect, border_radius=BUTTON_RADIUS)
                desc  = desc.replace(f"({device})", "").strip()
                label = f"{device} — {desc}"
                while font_xs.size(label)[0] > PORT_BTN_W - s(20) and len(desc) > 3:
                    desc  = desc[:-4] + "…"
                    label = f"{device} — {desc}"
                text_c(label, font_xs, BACKGROUND, rect.centerx, rect.centery)
                port_btns.append((rect, device))
            if not ports:
                text_c("No COM ports found.", font_small, TEXT_SECONDARY, W // 2, top)
            # Rescan sits on the bottom row between Abort and Continue, so the
            # port list never has to make room for it.
            rescan_btn = draw_continue_button("Rescan")

        cont_btn  = draw_continue_button("Continue", side="right")
        abort_btn = draw_continue_button("Abort",    side="left")
        flip()
        clock.tick(FPS)
        for event in pygame.event.get():
            check_quit(event)
            if test_btn is not None and is_button_touched(event, test_btn):
                TRIG.test_pulse()
            for rect, device in port_btns:
                if is_button_touched(event, rect):
                    ok, reason = TRIG.open(device)
                    if not ok:
                        ports = TRIG.list_ports()
            if rescan_btn is not None and is_button_touched(event, rescan_btn):
                ok, reason = TRIG.open(bbtk_trigger.DEFAULT_PORT)
                ports = TRIG.list_ports() if not ok else []
            if is_button_touched(event, abort_btn):
                TRIG.close()
                pygame.quit(); sys.exit()
            if is_button_activated(event, cont_btn):
                TRIGGERS_ENABLED_AT_CONTINUE = int(TRIG.enabled)
                return


def screen_task_complete():
    def draw():
        text_c("Task Complete",               font_title, OK,   W // 2, s(360))
        text_c("Thank you for participating.", font_body,  TEXT, W // 2, s(440))
    wait_for_continue(draw)


screen_trigger_setup()

handedness = screen_handedness()
configure_active_side(handedness)

pid_raw    = screen_text_input("Enter Participant ID", "(001 – 999)", validate_pid, max_len=3)
pid        = f"{int(pid_raw):03d}"
task_order, order_index = get_task_order(pid)
trial_seqs = get_trial_sequences(pid)
save_session(pid, handedness, task_order, trial_seqs)

screen_welcome()

screen_instructions(MIQ3_INSTRUCTIONS, title="MIQ-3 Instructions")

screen_miq3_intro()
ratings = screen_miq3()

screen_done(pid)

screen_instructions(FAMILIARIZATION_INSTRUCTION, title="The Metronome")
screen_metronome_familiarization()

calib_instruction = CALIBRATION_INSTRUCTION_RH if handedness == "rh" else CALIBRATION_INSTRUCTION_LH

while True:
    screen_instructions(calib_instruction, "Begin", title="Calibration Instructions",
                        side=button_side(handedness))
    peak_dist, large_amp, small_amp, angle = run_calibration(pid, handedness)
    if screen_calib_results(peak_dist, large_amp, small_amp, angle, handedness) != "redo":
        break

for cond_position, condition in enumerate(task_order, 1):
    run_condition(condition, pid, handedness, large_amp, small_amp, angle,
                  trial_seqs[condition], cond_position, order_index)

run_nasa_tlx(pid, handedness)

age = int(screen_text_input("Enter Participant Age", "(must be 18 or older)", validate_age))
sex = screen_sex()
culture, culture_text = screen_cultural_background()
gender,  gender_text  = screen_gender()
save_initialization(pid, age, sex, ratings, handedness,
                    culture, culture_text, gender, gender_text)

screen_task_complete()
