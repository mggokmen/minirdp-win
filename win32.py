"""MiniRDP'nin Windows'a özel kısımları (ctypes ile Win32 API): fare/klavye, pano, ekran, uyku engeli."""
import ctypes
import logging
import time
from ctypes import wintypes as W

log = logging.getLogger("minirdp")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Koordinatların ölçeklenmiş değil gerçek piksel olması için (mss'ten önce çağrılmalı).
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
except (AttributeError, OSError):
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


# ---------------------------------------------------------------- ekran

SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79


def primary_size():
    """Birincil ekranın boyutu; birincil ekran her zaman (0, 0) noktasından başlar."""
    return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", W.WCHAR * 32), ("dmSpecVersion", W.WORD), ("dmDriverVersion", W.WORD),
        ("dmSize", W.WORD), ("dmDriverExtra", W.WORD), ("dmFields", W.DWORD),
        ("dmPositionX", W.LONG), ("dmPositionY", W.LONG),
        ("dmDisplayOrientation", W.DWORD), ("dmDisplayFixedOutput", W.DWORD),
        ("dmColor", W.SHORT), ("dmDuplex", W.SHORT), ("dmYResolution", W.SHORT),
        ("dmTTOption", W.SHORT), ("dmCollate", W.SHORT), ("dmFormName", W.WCHAR * 32),
        ("dmLogPixels", W.WORD), ("dmBitsPerPel", W.DWORD), ("dmPelsWidth", W.DWORD),
        ("dmPelsHeight", W.DWORD), ("dmDisplayFlags", W.DWORD), ("dmDisplayFrequency", W.DWORD),
        ("dmICMMethod", W.DWORD), ("dmICMIntent", W.DWORD), ("dmMediaType", W.DWORD),
        ("dmDitherType", W.DWORD), ("dmReserved1", W.DWORD), ("dmReserved2", W.DWORD),
        ("dmPanningWidth", W.DWORD), ("dmPanningHeight", W.DWORD),
    ]


assert ctypes.sizeof(DEVMODEW) == 220
user32.EnumDisplaySettingsW.argtypes = [W.LPCWSTR, W.DWORD, ctypes.POINTER(DEVMODEW)]
user32.ChangeDisplaySettingsW.argtypes = [ctypes.POINTER(DEVMODEW), W.DWORD]
ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
DM_PELSWIDTH, DM_PELSHEIGHT = 0x80000, 0x100000


def apply_resolution(want):
    """Birincil ekranı [genişlik, yükseklik] moduna alır (en yüksek yenileme hızıyla)."""
    w, h = want
    cur = DEVMODEW(dmSize=ctypes.sizeof(DEVMODEW))
    user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(cur))
    if (cur.dmPelsWidth, cur.dmPelsHeight) == (w, h):
        return
    best, i = None, 0
    while True:
        m = DEVMODEW(dmSize=ctypes.sizeof(DEVMODEW))
        if not user32.EnumDisplaySettingsW(None, i, ctypes.byref(m)):
            break
        i += 1
        if (m.dmPelsWidth, m.dmPelsHeight) == (w, h) and m.dmBitsPerPel == cur.dmBitsPerPel:
            if best is None or m.dmDisplayFrequency > best.dmDisplayFrequency:
                best = m
    if best is None:
        log.warning("Çözünürlük %sx%s bu ekranda yok", w, h)
        return
    best.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | 0x400000  # | DM_DISPLAYFREQUENCY
    err = user32.ChangeDisplaySettingsW(ctypes.byref(best), 0)
    log.info("Çözünürlük %sx%s yapıldı (hata kodu %s)", w, h, err)


# ---------------------------------------------------------------- SendInput

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.LONG), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", W.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [W.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1

MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x4, 0x8

MOUSE = {  # js button -> (down, up)
    0: (0x0002, 0x0004),  # sol
    1: (0x0020, 0x0040),  # orta
    2: (0x0008, 0x0010),  # sağ
}

# Tarayıcının KeyboardEvent.code değeri (fiziksel tuş) -> PS/2 set 1 tarama kodu; 0xE0xx = genişletilmiş tuş.
# Tarama kodu gönderildiği için tuşun anlamını bu bilgisayarın klavye düzeni belirler.
SCANCODES = {
    "Escape": 0x01, "Digit1": 0x02, "Digit2": 0x03, "Digit3": 0x04, "Digit4": 0x05, "Digit5": 0x06,
    "Digit6": 0x07, "Digit7": 0x08, "Digit8": 0x09, "Digit9": 0x0A, "Digit0": 0x0B, "Minus": 0x0C,
    "Equal": 0x0D, "Backspace": 0x0E, "Tab": 0x0F, "KeyQ": 0x10, "KeyW": 0x11, "KeyE": 0x12,
    "KeyR": 0x13, "KeyT": 0x14, "KeyY": 0x15, "KeyU": 0x16, "KeyI": 0x17, "KeyO": 0x18, "KeyP": 0x19,
    "BracketLeft": 0x1A, "BracketRight": 0x1B, "Enter": 0x1C, "ControlLeft": 0x1D, "KeyA": 0x1E,
    "KeyS": 0x1F, "KeyD": 0x20, "KeyF": 0x21, "KeyG": 0x22, "KeyH": 0x23, "KeyJ": 0x24, "KeyK": 0x25,
    "KeyL": 0x26, "Semicolon": 0x27, "Quote": 0x28, "Backquote": 0x29, "ShiftLeft": 0x2A,
    "Backslash": 0x2B, "KeyZ": 0x2C, "KeyX": 0x2D, "KeyC": 0x2E, "KeyV": 0x2F, "KeyB": 0x30,
    "KeyN": 0x31, "KeyM": 0x32, "Comma": 0x33, "Period": 0x34, "Slash": 0x35, "ShiftRight": 0x36,
    "NumpadMultiply": 0x37, "AltLeft": 0x38, "Space": 0x39, "CapsLock": 0x3A,
    "F1": 0x3B, "F2": 0x3C, "F3": 0x3D, "F4": 0x3E, "F5": 0x3F, "F6": 0x40, "F7": 0x41, "F8": 0x42,
    "F9": 0x43, "F10": 0x44, "ScrollLock": 0x46, "Numpad7": 0x47, "Numpad8": 0x48,
    "Numpad9": 0x49, "NumpadSubtract": 0x4A, "Numpad4": 0x4B, "Numpad5": 0x4C, "Numpad6": 0x4D,
    "NumpadAdd": 0x4E, "Numpad1": 0x4F, "Numpad2": 0x50, "Numpad3": 0x51, "Numpad0": 0x52,
    "NumpadDecimal": 0x53, "IntlBackslash": 0x56, "F11": 0x57, "F12": 0x58, "IntlRo": 0x73,
    "IntlYen": 0x7D,
    "NumpadEnter": 0xE01C, "ControlRight": 0xE01D, "NumpadDivide": 0xE035, "AltRight": 0xE038,
    "Home": 0xE047, "ArrowUp": 0xE048, "PageUp": 0xE049, "ArrowLeft": 0xE04B, "ArrowRight": 0xE04D,
    "End": 0xE04F, "ArrowDown": 0xE050, "PageDown": 0xE051, "Insert": 0xE052, "Delete": 0xE053,
    "MetaLeft": 0xE05B, "MetaRight": 0xE05C, "ContextMenu": 0xE05D,
}
# Tarama koduyla güvenilir gönderilemeyen tuşlar sanal tuş koduyla gönderilir.
VKEYS = {"NumLock": (0x90, True), "Pause": (0x13, False), "PrintScreen": (0x2C, True)}
MODIFIERS = {"ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight", "AltLeft", "AltRight", "MetaLeft", "MetaRight"}


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    if user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) != len(inputs):
        # Genellikle UIPI: hedef pencere yönetici yetkisiyle çalışıyor ve biz çalışmıyoruz.
        raise OSError(ctypes.get_last_error(), "SendInput engellendi")


def _mouse(flags, dx=0, dy=0, data=0):
    return INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx=dx, dy=dy, mouseData=data, dwFlags=flags))


def _key(down, vk=0, scan=0, flags=0):
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags | (0 if down else KEYEVENTF_KEYUP)))


class InputController:
    def __init__(self):
        self.mods = set()
        self.buttons = set()
        self.wheel_rest = [0.0, 0.0]

    def _abs(self, x, y):
        """0..1 aralığındaki konumu birincil ekranda, SendInput'un 0..65535 sanal masaüstü ölçeğine çevirir."""
        w, h = primary_size()
        px = min(max(float(x), 0.0), 1.0) * (w - 1)
        py = min(max(float(y), 0.0), 1.0) * (h - 1)
        vx, vy = user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw, vh = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        return round((px - vx) * 65535 / max(vw - 1, 1)), round((py - vy) * 65535 / max(vh - 1, 1))

    def _move(self, x, y):
        dx, dy = self._abs(x, y)
        return _mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, dx, dy)

    def mouse_move(self, x, y):
        _send(self._move(x, y))

    def mouse_button(self, b, down, x, y):
        if b not in MOUSE:
            return
        (self.buttons.add if down else self.buttons.discard)(b)
        _send(self._move(x, y), _mouse(MOUSE[b][0 if down else 1]))

    def wheel(self, dx, dy):
        # Tarayıcı bir tekerlek adımı için ~100 piksel, Windows 120 birim (WHEEL_DELTA) kullanır.
        # Dokunmatik yüzeyden gelen küçük değerler birikerek gönderilir.
        ev = []
        for i, (val, flag, sign) in enumerate(((dy, MOUSEEVENTF_WHEEL, -1), (dx, MOUSEEVENTF_HWHEEL, 1))):
            self.wheel_rest[i] += sign * max(min(float(val), 2000), -2000) * 1.2
            amount = int(self.wheel_rest[i])
            if amount:
                self.wheel_rest[i] -= amount
                ev.append(_mouse(flag, data=amount))
        if ev:
            _send(*ev)

    def key(self, code, down):
        if code in VKEYS:
            vk, ext = VKEYS[code]
            _send(_key(down, vk=vk, flags=KEYEVENTF_EXTENDEDKEY if ext else 0))
            return
        sc = SCANCODES.get(code)
        if sc is None:
            return
        if code in MODIFIERS:
            (self.mods.add if down else self.mods.discard)(code)
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if sc > 0xFF else 0)
        _send(_key(down, scan=sc & 0xFF, flags=flags))

    def type_text(self, s):
        ev = []
        for ch in str(s)[:2000]:
            if ch in "\r\n":
                ev += [_key(True, scan=0x1C, flags=KEYEVENTF_SCANCODE), _key(False, scan=0x1C, flags=KEYEVENTF_SCANCODE)]
                continue
            if ch == "\t":
                ev += [_key(True, scan=0x0F, flags=KEYEVENTF_SCANCODE), _key(False, scan=0x0F, flags=KEYEVENTF_SCANCODE)]
                continue
            # Unicode olarak gönderilir: klavye düzeninden, CapsLock'tan ve Türkçe karakterlerden bağımsız.
            b = ch.encode("utf-16-le")
            for i in range(0, len(b), 2):
                unit = int.from_bytes(b[i:i + 2], "little")
                ev += [_key(True, scan=unit, flags=KEYEVENTF_UNICODE), _key(False, scan=unit, flags=KEYEVENTF_UNICODE)]
        for i in range(0, len(ev), 200):
            _send(*ev[i:i + 200])

    def release_all(self):
        for code in list(self.mods):
            try:
                self.key(code, False)
            except OSError:
                pass
        self.mods.clear()
        for b in list(self.buttons):
            try:
                _send(_mouse(MOUSE[b][1]))
            except OSError:
                pass
        self.buttons.clear()


# ---------------------------------------------------------------- pano

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
user32.OpenClipboard.argtypes = [W.HWND]
user32.GetClipboardData.argtypes = [W.UINT]
user32.GetClipboardData.restype = W.HANDLE
user32.SetClipboardData.argtypes = [W.UINT, W.HANDLE]
user32.SetClipboardData.restype = W.HANDLE
user32.GetClipboardSequenceNumber.restype = W.DWORD
user32.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
user32.CreateWindowExW.restype = W.HWND
kernel32.GlobalAlloc.argtypes = [W.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = W.HGLOBAL
kernel32.GlobalLock.argtypes = [W.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [W.HGLOBAL]
kernel32.GlobalFree.argtypes = [W.HGLOBAL]
kernel32.SetThreadExecutionState.argtypes = [W.DWORD]
kernel32.SetThreadExecutionState.restype = W.DWORD

_clip_hwnd = None


class _Clipboard:
    """Panoyu açar; başka bir uygulama o an kullanıyorsa kısa süre tekrar dener."""

    def __enter__(self):
        global _clip_hwnd
        if _clip_hwnd is None:
            # Pano sahibi olarak görünmez bir pencere (NULL sahiple SetClipboardData başarısız olabilir).
            _clip_hwnd = user32.CreateWindowExW(0, "STATIC", "MiniRDP", 0, 0, 0, 0, 0, W.HWND(-3), None, None, None)
        for _ in range(20):
            if user32.OpenClipboard(_clip_hwnd):
                return self
            time.sleep(0.01)
        raise OSError("Pano açılamadı")

    def __exit__(self, *exc):
        user32.CloseClipboard()


def clip_count():
    return user32.GetClipboardSequenceNumber()


def clip_read():
    try:
        with _Clipboard():
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = kernel32.GlobalLock(h)
            try:
                return ctypes.wstring_at(p) if p else ""
            finally:
                kernel32.GlobalUnlock(h)
    except OSError:
        log.warning("Pano okunamadı")
        return ""


def clip_write(text):
    data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-16-le") + b"\0\0"
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    try:
        with _Clipboard():
            user32.EmptyClipboard()
            if not user32.SetClipboardData(CF_UNICODETEXT, h):
                raise OSError("SetClipboardData başarısız")
    except OSError:
        kernel32.GlobalFree(h)  # başarılı olursa belleğin sahibi sistemdir
        log.warning("Panoya yazılamadı")


# ---------------------------------------------------------------- diğer

def keep_awake():
    """Çağıran iş parçacığı yaşadığı sürece bilgisayarın uyumasını ve ekranın kapanmasını engeller."""
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2
    kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False
