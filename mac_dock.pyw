# -*- coding: utf-8 -*-
"""Mekong Dock - thanh ung dung kieu macOS dat o mep TREN man hinh.

- Lan dau chay: tu lay cac shortcut (.lnk) tren Desktop (ca Desktop chung) dua vao Dock.
- Re chuot: icon phong to nhu macOS, hien ten ben duoi. Bam: mo ung dung (icon nay len).
- Cham sang duoi icon = ung dung dang chay (theo ten exe, hoac theo cong voi 9Router/OpenClaw/Zalo...).
- Chuot phai vao icon: mo vi tri tep, doi cho, go khoi Dock.
  Chuot phai vao nen Dock: them ung dung, quet lai Desktop, tu an, giu cho tren man hinh, chay cung Windows, thoat.
- "Giu cho tren man hinh" dang ky Dock la AppBar o mep tren: cua so phong to se nam ben duoi, khong che Dock.

Chay: pythonw mac_dock.pyw
Danh sach ung dung luu trong dock_apps.json, cai dat trong dock_settings.json (cung thu muc, sua tay duoc).
Chi dung thu vien chuan cua Python; icon duoc trich bang PowerShell (System.Drawing) va luu trong icons\\.
"""
import base64
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import hashlib
import json
import math
import os
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
import unicodedata
import winreg
from pathlib import Path
from tkinter import filedialog, messagebox

import gcal

HERE = Path(__file__).resolve().parent
APPS_FILE = HERE / "dock_apps.json"
SETTINGS_FILE = HERE / "dock_settings.json"
ICON_DIR = HERE / "icons"
LOG_FILE = HERE / "dock.log"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "MekongDock"

KEY = "#010203"          # mau "trong suot" (click xuyen qua)
PANEL = "#1e2230"
PANEL_LINE = "#3b4256"
DOT = "#e5e7eb"
TIP_BG = "#111827"

# Cong de biet ung dung "dang chay" voi cac shortcut chay qua pythonw/wscript/.bat
PORTS = {"9router": 20128, "9router tts": 8973, "openclaw": 18789, "zalo gateway launcher": 8088}
# Exe "trung gian" - khong dung ten exe de doan trang thai
GENERIC = {"", "pythonw.exe", "python.exe", "wscript.exe", "cscript.exe", "cmd.exe", "explorer.exe",
           "powershell.exe", "start.bat"}

DEFAULTS = {"reserve": False, "autohide": False, "size": 48, "hide_taskbar": False,  # luon hien, noi tren cua so
            "desktop_calendar": True, "calendars": [gcal.HOLIDAYS_VN], "search_hotkey": "alt+space",
            "search_counts": {}}
GOOGLE_CAL = "https://calendar.google.com/calendar/r"
FINDER = {"name": "Finder", "path": "explorer.exe", "target": r"%SystemRoot%\explorer.exe", "exe": "explorer.exe",
          "icon": r"%SystemRoot%\explorer.exe,0"}
REVEAL_DELAY = 0.25  # giay chuot phai nam o mep tren truoc khi Dock hien (tranh bat nham khi bam tab Chrome)

EXTRAS = [
    {"kind": "sep"},
    {"kind": "network", "name": "Kết nối"},
    {"kind": "volume", "name": "Âm lượng"},
    {"kind": "calendar", "name": "Lịch"},
    {"kind": "clock", "name": "Đồng hồ"},
    {"name": "Tải về", "path": "explorer.exe", "args": "shell:Downloads",
     "icon": r"%SystemRoot%\System32\imageres.dll,-184"},
    {"name": "Thùng rác", "path": "explorer.exe", "args": "shell:RecycleBinFolder",
     "icon": r"%SystemRoot%\System32\imageres.dll,-55"},
]


def log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except OSError:
        pass


def load_json(path: Path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


_k32 = ctypes.windll.kernel32
_k32.OpenProcess.restype = wt.HANDLE
_k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
_k32.CloseHandle.argtypes = [wt.HANDLE]


def process_names() -> set[str]:
    """Ten exe (chu thuong) cua cac tien trinh dang chay - goi API, khong tao tien trinh tasklist."""
    pids = (wt.DWORD * 4096)()
    got = wt.DWORD()
    if not ctypes.windll.psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(got)):
        return set()
    names, buf = set(), ctypes.create_unicode_buffer(1024)
    for pid in pids[:got.value // ctypes.sizeof(wt.DWORD)]:
        h = _k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        size = wt.DWORD(len(buf))
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            names.add(os.path.basename(buf.value).casefold())
        _k32.CloseHandle(h)
    return names


_u32 = ctypes.windll.user32
_u32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
_u32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, wt.LONG]
_u32.GetWindow.restype = wt.HWND
_u32.GetForegroundWindow.restype = wt.HWND
_u32.InternalGetWindowText.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
_ENUM_PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow"}


def exe_of(pid: int) -> str:
    h = _k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf, size = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
    ok = _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
    _k32.CloseHandle(h)
    return buf.value if ok else ""


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wt.USHORT), ("MaximumLength", wt.USHORT), ("Buffer", ctypes.c_void_p)]


def process_cmdline(pid: int) -> str:
    """Dong lenh cua tien trinh (NtQueryInformationProcess, ProcessCommandLineInformation) - khong can WMI."""
    h = _k32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        buf, ret = ctypes.create_string_buffer(65536), wt.ULONG()
        if ctypes.windll.ntdll.NtQueryInformationProcess(h, 60, buf, len(buf), ctypes.byref(ret)) != 0:
            return ""
        us = _UNICODE_STRING.from_buffer(buf)
        return ctypes.wstring_at(us.Buffer, us.Length // 2) if us.Buffer else ""
    finally:
        _k32.CloseHandle(h)


class _GUID(ctypes.Structure):
    _fields_ = [("a", wt.DWORD), ("b", wt.WORD), ("c", wt.WORD), ("d", ctypes.c_ubyte * 8)]


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wt.DWORD)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", wt.USHORT), ("r1", wt.USHORT), ("r2", wt.USHORT), ("r3", wt.USHORT),
                ("p", ctypes.c_void_p), ("p2", ctypes.c_void_p)]


def _guid(s: str) -> _GUID:
    import uuid
    u, g = uuid.UUID(s), _GUID()
    g.a, g.b, g.c = u.fields[0], u.fields[1], u.fields[2]
    g.d[:] = list(u.bytes[8:])
    return g


_IID_PROPSTORE = _guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
_PKEY_AUMID = _PROPERTYKEY(_guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5)  # System.AppUserModel.ID
_GET_VALUE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_PROPERTYKEY),
                                ctypes.POINTER(_PROPVARIANT))
_RELEASE = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def window_aumid(h: int) -> str:
    """AppUserModelID cua cua so. Chrome --app dat theo trang: 'Chrome.notebooklmogle.com_/.UserData.Profile2'."""
    ps = ctypes.c_void_p()
    if ctypes.windll.shell32.SHGetPropertyStoreForWindow(wt.HWND(h), ctypes.byref(_IID_PROPSTORE),
                                                        ctypes.byref(ps)) != 0 or not ps:
        return ""
    vt = ctypes.cast(ctypes.cast(ps, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    pv = _PROPVARIANT()
    try:
        _GET_VALUE(vt[5])(ps, ctypes.byref(_PKEY_AUMID), ctypes.byref(pv))
        return ctypes.wstring_at(pv.p) if pv.vt == 31 and pv.p else ""  # VT_LPWSTR
    finally:
        ctypes.windll.ole32.PropVariantClear(ctypes.byref(pv))
        _RELEASE(vt[2])(ps)


# ---------------------------------------------------------------- Am luong (Core Audio) + mang

_ole32 = ctypes.windll.ole32
_CLSID_ENUM = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")      # MMDeviceEnumerator
_IID_ENUM = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")        # IMMDeviceEnumerator
_IID_VOLUME = _guid("5CDF2C82-841E-4546-9722-0CF74078229A")      # IAudioEndpointVolume
_PKEY_FRIENDLY = _PROPERTYKEY(_guid("a45c254e-df1c-4efd-8020-67d146a850e0"), 14)  # ten thiet bi


def _vtbl(p: ctypes.c_void_p):
    return ctypes.cast(ctypes.cast(p, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))


def _com(p, index, *argtypes, restype=ctypes.c_long):
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(_vtbl(p)[index])


class Volume:
    """Am luong loa mac dinh qua IAudioEndpointVolume (khong can thu vien ngoai). Goi tren luong chinh."""

    def __init__(self):
        _ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED (luong Tk)

    def _endpoint(self):
        """-> (IAudioEndpointVolume, ten thiet bi). Lay lai moi lan: thiet bi mac dinh co the doi (tai nghe...)."""
        enum, dev, vol = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        if _ole32.CoCreateInstance(ctypes.byref(_CLSID_ENUM), None, 23, ctypes.byref(_IID_ENUM),
                                   ctypes.byref(enum)) != 0:
            return None, ""
        try:
            # GetDefaultAudioEndpoint(eRender=0, eMultimedia=1)
            if _com(enum, 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                    enum, 0, 1, ctypes.byref(dev)) != 0:
                return None, ""
        finally:
            _com(enum, 2, restype=ctypes.c_ulong)(enum)
        try:
            _com(dev, 3, ctypes.POINTER(_GUID), wt.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                dev, ctypes.byref(_IID_VOLUME), 23, None, ctypes.byref(vol))  # Activate
            name, store = "", ctypes.c_void_p()
            if _com(dev, 4, wt.DWORD, ctypes.POINTER(ctypes.c_void_p))(dev, 0, ctypes.byref(store)) == 0:
                pv = _PROPVARIANT()  # OpenPropertyStore -> GetValue(PKEY_Device_FriendlyName)
                _GET_VALUE(_vtbl(store)[5])(store, ctypes.byref(_PKEY_FRIENDLY), ctypes.byref(pv))
                name = ctypes.wstring_at(pv.p) if pv.vt == 31 and pv.p else ""
                _ole32.PropVariantClear(ctypes.byref(pv))
                _RELEASE(_vtbl(store)[2])(store)
        finally:
            _com(dev, 2, restype=ctypes.c_ulong)(dev)
        return (vol if vol else None), name

    def get(self) -> tuple[int, bool, str]:
        """-> (phan tram 0..100, tat tieng, ten thiet bi); (-1, False, '') neu khong co loa."""
        vol, name = self._endpoint()
        if not vol:
            return -1, False, ""
        try:
            lv, mute = ctypes.c_float(), wt.BOOL()
            _com(vol, 9, ctypes.POINTER(ctypes.c_float))(vol, ctypes.byref(lv))  # GetMasterVolumeLevelScalar
            _com(vol, 15, ctypes.POINTER(wt.BOOL))(vol, ctypes.byref(mute))     # GetMute
            return round(lv.value * 100), bool(mute.value), name
        finally:
            _com(vol, 2, restype=ctypes.c_ulong)(vol)

    def set(self, percent: int | None = None, mute: bool | None = None) -> None:
        vol, _ = self._endpoint()
        if not vol:
            return
        try:
            if percent is not None:
                _com(vol, 7, ctypes.c_float, ctypes.c_void_p)(vol, max(0, min(100, percent)) / 100, None)
            if mute is not None:
                _com(vol, 14, wt.BOOL, ctypes.c_void_p)(vol, bool(mute), None)
        finally:
            _com(vol, 2, restype=ctypes.c_ulong)(vol)


def network_status() -> dict:
    """Trang thai mang (goi o luong nen): online, wifi (SSID), signal %, wired."""
    flags = wt.DWORD()
    online = bool(ctypes.windll.wininet.InternetGetConnectedState(ctypes.byref(flags), 0))
    ssid, signal = "", 0
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, timeout=8,
                             creationflags=NO_WINDOW).stdout.decode("utf-8", "replace")
        if re.search(r"^\s*State\s*:\s*connected", out, re.M | re.I):
            m = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.M)
            ssid = m.group(1).strip() if m else ""
            m = re.search(r"^\s*Signal\s*:\s*(\d+)%", out, re.M)
            signal = int(m.group(1)) if m else 0
    except (OSError, subprocess.SubprocessError):
        pass
    return {"online": online, "ssid": ssid, "signal": signal, "wired": online and not ssid}


def window_pid(h: int) -> int:
    pid = wt.DWORD()
    _u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return pid.value


def top_windows() -> list[tuple[int, str, str, str]]:
    """Cua so ung dung dang mo (giong taskbar), theo thu tu tren -> duoi: (hwnd, exe, tieu de, lop cua so).
    Dung InternalGetWindowText: khong gui WM_GETTEXT nen cua so bi treo khong lam Dock treo theo."""
    out, exes, me = [], {}, os.getpid()
    title, cls, cloaked, pid = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(64), wt.DWORD(), wt.DWORD()

    def cb(h, _):
        if not _u32.IsWindowVisible(h) or _u32.GetWindow(h, 4):  # GW_OWNER: hop thoai con
            return True
        ex = _u32.GetWindowLongW(h, -20)
        if ex & 0x80 and not ex & 0x40000:  # WS_EX_TOOLWINDOW, tru khi co WS_EX_APPWINDOW
            return True
        if _u32.InternalGetWindowText(h, title, 256) == 0:
            return True
        _u32.GetClassNameW(h, cls, 64)
        if cls.value in SKIP_CLASSES:
            return True
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(h, 14, ctypes.byref(cloaked), 4) == 0 and cloaked.value:
            return True
        _u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == me:
            return True
        if pid.value not in exes:
            exes[pid.value] = exe_of(pid.value)  # "" neu tien trinh chay quyen admin (vd UltraViewer)
        out.append((h, exes[pid.value], title.value, cls.value))
        return True

    _u32.EnumWindows(_ENUM_PROC(cb), 0)
    return out


_desc_cache: dict[str, str] = {}


def file_description(path: str) -> str:
    """Ten ung dung tu thong tin phien ban cua exe (vd 'Google Chrome'), khong co thi lay ten tep."""
    if path in _desc_cache:
        return _desc_cache[path]
    name = Path(path).stem
    try:
        ver = ctypes.windll.version
        n = ver.GetFileVersionInfoSizeW(path, None)
        if n:
            data = ctypes.create_string_buffer(n)
            ver.GetFileVersionInfoW(path, 0, n, data)
            ptr, ln = ctypes.c_void_p(), wt.UINT()
            if ver.VerQueryValueW(data, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(ln)) and ln.value:
                lang, cp = ctypes.cast(ptr, ctypes.POINTER(wt.WORD * 2)).contents
                key = rf"\StringFileInfo\{lang:04x}{cp:04x}\FileDescription"
                if ver.VerQueryValueW(data, key, ctypes.byref(ptr), ctypes.byref(ln)) and ln.value > 1:
                    desc = ctypes.wstring_at(ptr, ln.value - 1).strip()
                    if desc:
                        name = desc
    except OSError:
        pass
    if Path(path).name.casefold() == "explorer.exe":
        name = "File Explorer"
    _desc_cache[path] = name
    return name


def focus_window(h: int) -> None:
    if _u32.IsIconic(h):
        _u32.ShowWindow(h, 9)  # SW_RESTORE
    if not _u32.SetForegroundWindow(h):
        # Windows chan doi cua so phia truoc: nhan-nha Alt mot cai la duoc phep
        _u32.keybd_event(0x12, 0, 0, 0)
        _u32.keybd_event(0x12, 0, 2, 0)
        _u32.SetForegroundWindow(h)
    _u32.BringWindowToTop(h)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


_MON_PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
_u32.MonitorFromWindow.restype = wt.HMONITOR
_u32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]


def monitor_rect(hmon) -> tuple[int, int, int, int]:
    mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    _u32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    r = mi.rcMonitor
    return r.left, r.top, r.right, r.bottom


def monitors() -> list[tuple[int, int, int, int]]:
    """(trai, tren, phai, duoi) cua tung man hinh, man hinh chinh dung dau."""
    out = []
    _u32.EnumDisplayMonitors(None, None, _MON_PROC(lambda h, dc, rc, lp: out.append(monitor_rect(h)) or True), 0)
    out.sort(key=lambda r: (r[0] != 0 or r[1] != 0, r[0], r[1]))  # man hinh chinh co goc (0,0)
    return out or [(0, 0, _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1))]


# ---------------------------------------------------------------- Lich am (thuat toan Ho Ngoc Duc, mui gio +7)

def _jd(dd: int, mm: int, yy: int) -> int:
    a = (14 - mm) // 12
    y, m = yy + 4800 - a, mm + 12 * a - 3
    jd = dd + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    return jd if jd >= 2299161 else dd + (153 * m + 2) // 5 + 365 * y + y // 4 - 32083


def _new_moon_day(k: int, tz: int = 7) -> int:
    T = k / 1236.85
    T2, T3, dr = T * T, T * T * T, math.pi / 180
    jd1 = 2415020.75933 + 29.53058868 * k + 0.0001178 * T2 - 0.000000155 * T3
    jd1 += 0.00033 * math.sin((166.56 + 132.87 * T - 0.009173 * T2) * dr)
    M = 359.2242 + 29.10535608 * k - 0.0000333 * T2 - 0.00000347 * T3
    Mpr = 306.0253 + 385.81691806 * k + 0.0107306 * T2 + 0.00001236 * T3
    F = 21.2964 + 390.67050646 * k - 0.0016528 * T2 - 0.00000239 * T3
    c1 = (0.1734 - 0.000393 * T) * math.sin(M * dr) + 0.0021 * math.sin(2 * dr * M)
    c1 = c1 - 0.4068 * math.sin(Mpr * dr) + 0.0161 * math.sin(dr * 2 * Mpr)
    c1 = c1 - 0.0004 * math.sin(dr * 3 * Mpr)
    c1 = c1 + 0.0104 * math.sin(dr * 2 * F) - 0.0051 * math.sin(dr * (M + Mpr))
    c1 = c1 - 0.0074 * math.sin(dr * (M - Mpr)) + 0.0004 * math.sin(dr * (2 * F + M))
    c1 = c1 - 0.0004 * math.sin(dr * (2 * F - M)) - 0.0006 * math.sin(dr * (2 * F + Mpr))
    c1 = c1 + 0.0010 * math.sin(dr * (2 * F - Mpr)) + 0.0005 * math.sin(dr * (2 * Mpr + M))
    if T < -11:
        dt = 0.001 + 0.000839 * T + 0.0002261 * T2 - 0.00000845 * T3 - 0.000000081 * T * T3
    else:
        dt = -0.000278 + 0.000265 * T + 0.000262 * T2
    return math.floor(jd1 + c1 - dt + 0.5 + tz / 24)


def _sun_longitude(day: int, tz: int = 7) -> int:
    T = (day - 0.5 - tz / 24 - 2451545.0) / 36525
    T2, dr = T * T, math.pi / 180
    M = 357.52910 + 35999.05030 * T - 0.0001559 * T2 - 0.00000048 * T * T2
    L0 = 280.46645 + 36000.76983 * T + 0.0003032 * T2
    DL = (1.914600 - 0.004817 * T - 0.000014 * T2) * math.sin(dr * M)
    DL += (0.019993 - 0.000101 * T) * math.sin(dr * 2 * M) + 0.000290 * math.sin(dr * 3 * M)
    L = (L0 + DL) * dr
    L -= math.pi * 2 * math.floor(L / (math.pi * 2))
    return math.floor(L / math.pi * 6)


def _month11(yy: int) -> int:
    k = math.floor((_jd(31, 12, yy) - 2415021) / 29.530588853)
    nm = _new_moon_day(k)
    return _new_moon_day(k - 1) if _sun_longitude(nm) >= 9 else nm


def _leap_offset(a11: int) -> int:
    k = math.floor((a11 - 2415021.076998695) / 29.530588853 + 0.5)
    i, arc = 1, _sun_longitude(_new_moon_day(k + 1))
    while True:
        last = arc
        i += 1
        arc = _sun_longitude(_new_moon_day(k + i))
        if not (arc != last and i < 14):
            return i - 1


def lunar(d: dt.date) -> tuple[int, int, int, bool]:
    """Ngay duong -> (ngay, thang, nam, thang nhuan) am lich Viet Nam."""
    day = _jd(d.day, d.month, d.year)
    k = math.floor((day - 2415021.076998695) / 29.530588853)
    start = _new_moon_day(k + 1)
    if start > day:
        start = _new_moon_day(k)
    a11 = b11 = _month11(d.year)
    if a11 >= start:
        year, a11 = d.year, _month11(d.year - 1)
    else:
        year, b11 = d.year + 1, _month11(d.year + 1)
    diff = math.floor((start - a11) / 29)
    month, leap = diff + 11, False
    if b11 - a11 > 365:
        lo = _leap_offset(a11)
        if diff >= lo:
            month = diff + 10
            leap = diff == lo
    if month > 12:
        month -= 12
    if month >= 11 and diff < 4:
        year -= 1
    return day - start + 1, month, year, leap


CAN = ["Giáp", "Ất", "Bính", "Đinh", "Mậu", "Kỷ", "Canh", "Tân", "Nhâm", "Quý"]
CHI = ["Tý", "Sửu", "Dần", "Mão", "Thìn", "Tỵ", "Ngọ", "Mùi", "Thân", "Dậu", "Tuất", "Hợi"]
THU = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def can_chi(year: int) -> str:
    return f"{CAN[(year + 6) % 10]} {CHI[(year + 8) % 12]}"


# ---------------------------------------------------------------- Taskbar Windows

ABM_GETSTATE, ABM_SETSTATE, ABS_AUTOHIDE = 4, 10, 1


_u32.FindWindowExW.restype = wt.HWND
_u32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]


def taskbar_windows() -> list[int]:
    """Taskbar man hinh chinh + taskbar cac man hinh phu."""
    out = [_u32.FindWindowW("Shell_TrayWnd", None)]
    h = 0
    while True:
        h = _u32.FindWindowExW(None, h, "Shell_SecondaryTrayWnd", None)
        if not h:
            break
        out.append(h)
    return [h for h in out if h]


def taskbar_show(show: bool) -> None:
    """Giau / hien han taskbar (tu an thoi chua du: ung dung nhap nhay hay di chuot xuong deu lam no hien)."""
    for h in taskbar_windows():
        if bool(_u32.IsWindowVisible(h)) != show:
            _u32.ShowWindow(h, 5 if show else 0)  # SW_SHOW / SW_HIDE


def taskbar_autohide(on: bool | None = None) -> bool:
    """Doc (on=None) hoac dat che do tu an cua taskbar Windows - giong tick o Settings > Taskbar."""
    abd = APPBARDATA(cbSize=ctypes.sizeof(APPBARDATA), hWnd=_u32.FindWindowW("Shell_TrayWnd", None))
    if on is not None:
        abd.lParam = ABS_AUTOHIDE if on else 0
        _shell32.SHAppBarMessage(ABM_SETSTATE, ctypes.byref(abd))
    return bool(_shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(abd)) & ABS_AUTOHIDE)


# ---------------------------------------------------------------- PowerShell helpers

def run_ps(script: str, payload) -> str:
    """Chay PowerShell an; payload (JSON) truyen qua file tam, duong dan nam trong $env:DOCK_IN."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="dock-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    try:
        enc = base64.b64encode(("[Console]::OutputEncoding=[Text.Encoding]::UTF8\n" + script)
                               .encode("utf-16-le")).decode()
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                           capture_output=True, env={**os.environ, "DOCK_IN": path},
                           creationflags=NO_WINDOW, timeout=120)
        if r.returncode:
            log("PowerShell lỗi: " + r.stderr.decode("utf-8", "replace")[-500:])
        return r.stdout.decode("utf-8", "replace")
    finally:
        os.remove(path)


PS_RESOLVE = r"""
$sh = New-Object -ComObject WScript.Shell
$paths = Get-Content -LiteralPath $env:DOCK_IN -Raw -Encoding UTF8 | ConvertFrom-Json
$out = @()
foreach ($p in $paths) {
  try { $l = $sh.CreateShortcut($p); $out += [pscustomobject]@{path=$p; target=$l.TargetPath; icon=$l.IconLocation} }
  catch { $out += [pscustomobject]@{path=$p; target=''; icon=''} }
}
ConvertTo-Json -InputObject @($out) -Compress
"""

PS_ICONS = r"""
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class DockIx {
  [DllImport("user32.dll", CharSet=CharSet.Unicode)]
  public static extern uint PrivateExtractIcons(string f, int i, int cx, int cy, IntPtr[] h, int[] id, uint n, uint fl);
  [DllImport("user32.dll")] public static extern bool DestroyIcon(IntPtr h);
}
'@
$jobs = Get-Content -LiteralPath $env:DOCK_IN -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($j in $jobs) {
  try {
    $s = [int]$j.size; $h = New-Object IntPtr[] 1; $id = New-Object int[] 1
    $n = [DockIx]::PrivateExtractIcons($j.file, [int]$j.index, $s, $s, $h, $id, 1, 0)
    if ($n -ge 1 -and $n -ne 0xFFFFFFFF -and $h[0] -ne [IntPtr]::Zero) {
      $src = ([Drawing.Icon]::FromHandle($h[0])).ToBitmap()
      [void][DockIx]::DestroyIcon($h[0])
    } else {
      $src = ([Drawing.Icon]::ExtractAssociatedIcon($j.file)).ToBitmap()
    }
    $bmp = New-Object Drawing.Bitmap $s, $s
    $g = [Drawing.Graphics]::FromImage($bmp)
    $g.InterpolationMode = 'HighQualityBicubic'
    $g.DrawImage($src, 0, 0, $s, $s); $g.Dispose()
    $bmp.Save($j.out, [Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose(); $src.Dispose()
  } catch { }
}
"""


def resolve_links(paths: list[str]) -> dict:
    if not paths:
        return {}
    try:
        rows = json.loads(run_ps(PS_RESOLVE, paths) or "[]")
    except json.JSONDecodeError:
        rows = []
    return {r["path"]: r for r in rows if isinstance(r, dict)}


def desktop_dirs() -> list[Path]:
    out = []
    for csidl in (0x10, 0x19):  # CSIDL_DESKTOPDIRECTORY, CSIDL_COMMON_DESKTOPDIRECTORY
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
            out.append(Path(buf.value))
    return out


def entry_for(path: str, info: dict | None = None) -> dict:
    p = Path(path)
    e = {"name": p.stem, "path": str(p)}
    if p.suffix.lower() == ".lnk" and info:
        e["target"] = info.get("target") or ""
        e["icon"] = info.get("icon") or ""
    else:
        e["target"] = str(p)
        e["icon"] = ""
    port = PORTS.get(e["name"].casefold())
    if port:
        e["port"] = port
    return e


def scan_desktop(known: set[str]) -> list[dict]:
    seen, links = set(), []
    for d in desktop_dirs():
        for f in sorted(d.glob("*.lnk")):
            k = f.stem.casefold()
            if k in seen or str(f).casefold() in known:
                continue
            seen.add(k)
            links.append(str(f))
    info = resolve_links(links)
    apps = [entry_for(p, info.get(p)) for p in links]
    return sorted(apps, key=lambda a: a["name"].casefold())


def icon_source(app: dict) -> tuple[str, int]:
    spec = os.path.expandvars(app.get("icon") or "")
    if spec:
        file, _, idx = spec.rpartition(",")
        if not file:
            file, idx = spec, "0"
        try:
            index = int(idx)
        except ValueError:
            file, index = spec, 0
        if file and os.path.isfile(file):
            return file, index
    target = os.path.expandvars(app.get("target") or "")
    if target and os.path.isfile(target):
        return target, 0
    return os.path.expandvars(app.get("path") or ""), 0


def icon_png(app: dict, size: int) -> Path:
    file, index = icon_source(app)
    h = hashlib.md5(f"{file.casefold()}|{index}|{size}".encode("utf-8")).hexdigest()[:16]
    return ICON_DIR / f"{h}.png"


# ---------------------------------------------------------------- AppBar (giu cho o mep tren)

class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT),
                ("uEdge", wt.UINT), ("rc", wt.RECT), ("lParam", wt.LPARAM)]


ABM_NEW, ABM_REMOVE, ABM_QUERYPOS, ABM_SETPOS = 0, 1, 2, 3
ABE_TOP = 1
_shell32 = ctypes.windll.shell32
_shell32.SHAppBarMessage.argtypes = [wt.DWORD, ctypes.POINTER(APPBARDATA)]
_shell32.SHAppBarMessage.restype = ctypes.c_size_t


class AppBar:
    def __init__(self, hwnd: int):
        self.abd = APPBARDATA(cbSize=ctypes.sizeof(APPBARDATA), hWnd=hwnd, uCallbackMessage=0x0400 + 0x77)
        self.active = False

    def reserve(self, mon: tuple[int, int, int, int], height: int) -> int:
        """Giu dai cao `height` o mep tren man hinh `mon`; tra ve toa do y bat dau."""
        if not self.active:
            _shell32.SHAppBarMessage(ABM_NEW, ctypes.byref(self.abd))
            self.active = True
        a = self.abd
        a.uEdge = ABE_TOP
        a.rc.left, a.rc.top, a.rc.right, a.rc.bottom = mon[0], mon[1], mon[2], mon[1] + height
        _shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(a))
        a.rc.bottom = a.rc.top + height
        _shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(a))
        return a.rc.top

    def remove(self) -> None:
        if self.active:
            _shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(self.abd))
            self.active = False


# ---------------------------------------------------------------- Dock

def round_rect(cv: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def tile_color(name: str) -> str:
    palette = ["#2563eb", "#7c3aed", "#db2777", "#ea580c", "#16a34a", "#0891b2", "#4f46e5", "#ca8a04"]
    return palette[int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16) % len(palette)]


class Dock:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.s = {**DEFAULTS, **load_json(SETTINGS_FILE, {})}
        self.apps: list[dict] = load_json(APPS_FILE, None) or []
        self.images: dict[tuple[str, int], tk.PhotoImage] = {}
        self.running: set[int] = set()
        self.hover: int | None = None
        self.bounce: dict[int, int] = {}
        self.slots: list[tuple[int, float, float]] = []
        self.hidden = bool(self.s["autohide"])
        self.leave_at = 0.0
        self.edge_at = 0.0
        self.panel_x = (0.0, 0.0)
        self.menu_open = False
        self.ui: queue.Queue = queue.Queue()
        self.extract_lock = threading.Lock()
        self.fs_was = False
        self.dbg_at = 0.0
        self.hold_until = 0.0
        self.sys_pop: SystemPopup | None = None
        self.volume = Volume()
        self.vol: tuple[int, bool, str] = self.volume.get()  # (%, tat tieng, ten loa)
        self.net: dict = {}
        self.cmd_cache: dict[int, str] = {}
        self.aumid_cache: dict[int, str] = {}
        self.fit_at = 0.0
        self.fit_last: tuple = ()
        self.launched_at: dict[int, float] = {}  # id(app) -> luc bam mo (app mot cua so)
        self.wins: dict[int, list[int]] = {}   # id(muc da pin) -> hwnd cua so dang mo
        self.dyn: list[dict] = []               # ung dung dang chay nhung chua pin
        self.view: list[dict] = []              # nhung gi dang hien tren Dock

        try:
            self.scale = ctypes.windll.user32.GetDpiForSystem() / 96
        except (AttributeError, OSError):
            self.scale = root.winfo_fpixels("1i") / 96
        self.mons = monitors()
        self.mon = self.mons[0]   # man hinh dang chua Dock (mac dinh man hinh chinh)
        self.sw = self.mon[2] - self.mon[0]
        self.mons_at = time.time()
        self.minute = ""
        self.cal: tk.Toplevel | None = None

        root.overrideredirect(True)
        root.configure(bg=KEY)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", KEY)
        self.cv = tk.Canvas(root, bg=KEY, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Motion>", self.on_motion)
        self.cv.bind("<Leave>", self.on_leave)
        self.cv.bind("<ButtonRelease-1>", self.on_click)
        self.cv.bind("<Button-3>", self.on_right)

        self.tip = tk.Toplevel(root)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        self.tip_lb = tk.Label(self.tip, bg=TIP_BG, fg="#f9fafb", font=("Segoe UI", 10), padx=10, pady=3)
        self.tip_lb.pack()
        self.tip.withdraw()

        self.v_reserve = tk.BooleanVar(value=self.s["reserve"])
        self.v_autohide = tk.BooleanVar(value=self.s["autohide"])
        self.v_dockmode = tk.StringVar()
        self.v_autostart = tk.BooleanVar(value=self.autostart_enabled())
        self.v_taskbar = tk.BooleanVar(value=self.s["hide_taskbar"])
        if self.s["hide_taskbar"]:
            if not taskbar_autohide():
                taskbar_autohide(True)  # vd Windows/Explorer vua khoi dong lai
            taskbar_show(False)  # tu an de cua so dung het chieu cao, giau han de no khong hien lai
        self.taskbar_at = 0.0

        if not self.apps:
            self.apps = [dict(FINDER)] + scan_desktop(set()) + [dict(e) for e in EXTRAS]
            self.save_apps()

        self.build_view()
        self.compute_metrics()
        root.update_idletasks()
        self.appbar = AppBar(int(root.wm_frame(), 16))
        # WS_EX_NOACTIVATE: bam vao Dock khong lay focus, nen biet duoc cua so nao dang o phia truoc
        hwnd = self.appbar.abd.hWnd
        _u32.SetWindowLongW(hwnd, -20, _u32.GetWindowLongW(hwnd, -20) | 0x08000000)
        self.place()
        self.draw()
        self.v_desk = tk.BooleanVar(value=self.s["desktop_calendar"])
        self.v_desk_mode = tk.StringVar(value=self.s.get("desk_mode", "card"))
        self.desk: DesktopCalendar | None = None
        self.desk_fetch_at = time.time()
        if self.v_desk.get():
            self.desk = self.make_desk()
        self.spot: Spotlight | None = None
        self.stack: StackPopup | None = None
        threading.Thread(target=self.watch_system, daemon=True).start()
        self.cv.bind("<MouseWheel>", self.on_wheel)
        self.index = AppIndex(self)
        start_hotkey(self)
        threading.Thread(target=self.extract_icons, daemon=True).start()
        threading.Thread(target=self.watch_running, daemon=True).start()
        self.poll_mouse()

    # --- danh sach hien thi ---
    def build_view(self):
        """Muc da pin | ung dung dang chay chua pin | (vach) Tai ve, Thung rac."""
        at = self.first_sep()
        dyn = ([{"kind": "sep", "dynamic": True}] + self.dyn) if self.dyn else []
        self.view = self.apps[:at] + dyn + self.apps[at:]

    def windows_of(self, a: dict) -> list[int]:
        return a.get("hwnds", []) if a.get("dynamic") else self.wins.get(id(a), [])

    # --- kich thuoc / vi tri ---
    def compute_metrics(self):
        k = self.scale
        n = sum(1 for a in self.view if a.get("kind") != "sep")
        nsep = len(self.view) - n
        self.gap = round(10 * k)
        self.sepw = round(16 * k)
        self.padx = round(10 * k)
        self.m = round(5 * k)
        self.pad = round(7 * k)
        base = round(self.s.get("size", 48) * k)
        room = self.sw - round(40 * k) - nsep * self.sepw - 2 * self.padx
        while base > 24 and n * (base + self.gap) + round(base * 0.8) > room:
            base -= 2
        base -= base % 4  # theo bac 4px: them/go vai icon van dung lai duoc anh icon da trich
        self.base, self.mid, self.big = base, round(base * 1.18), round(base * 1.4)
        self.panel_h = self.pad * 2 + base + round(6 * k)
        self.bar_h = self.m * 2 + self.panel_h                      # phan giu cho tren man hinh
        self.win_h = self.m + self.pad + self.big + round(14 * k)  # them cho icon phong to tran xuong

    def place(self):
        if self.v_reserve.get() and not self.v_autohide.get():
            top = self.appbar.reserve(self.mon, self.bar_h)
        else:
            self.appbar.remove()
            top = self.mon[1]
        self.win_y = top if not self.hidden else self.mon[1] - self.win_h
        self.root.geometry(f"{self.sw}x{self.win_h}+{self.mon[0]}+{self.win_y}")

    def set_monitor(self, mon: tuple[int, int, int, int]):
        """Dua Dock sang man hinh khac (chuot cham mep tren man hinh do) - tinh lai kich thuoc theo be ngang."""
        if mon != self.mon:
            self.mon, self.sw = mon, mon[2] - mon[0]
            log(f"Chuyển Dock sang màn hình {mon}")
            self.relayout(save=False)

    # --- ve ---
    def sizes(self) -> list[int | None]:
        out = []
        for i, a in enumerate(self.view):
            if a.get("kind") == "sep":
                out.append(None)
                continue
            d = abs(i - self.hover) if self.hover is not None else 99
            out.append(self.big if d == 0 else self.mid if d == 1 else self.base)
        return out

    def image(self, app: dict, size: int) -> tk.PhotoImage | None:
        png = icon_png(app, size)
        key = (str(png), size)
        if key not in self.images:
            if not png.exists():
                return None
            try:
                self.images[key] = tk.PhotoImage(file=str(png))
            except tk.TclError:
                return None
        return self.images[key]

    def draw(self):
        cv = self.cv
        cv.delete("all")
        sizes = self.sizes()
        widths = [self.sepw if s is None else s + self.gap for s in sizes]
        total = sum(widths) + 2 * self.padx
        x = (self.sw - total) / 2
        self.panel_x = (x, x + total)
        m = self.m
        round_rect(cv, x, m, x + total, m + self.panel_h, round(16 * self.scale), fill=PANEL, outline=PANEL_LINE)
        x += self.padx
        self.slots = []
        r = max(2, round(2.2 * self.scale))
        for i, (a, s, w) in enumerate(zip(self.view, sizes, widths)):
            self.slots.append((i, x, x + w))
            if s is None:
                cx = x + w / 2
                cv.create_line(cx, m + self.pad, cx, m + self.panel_h - self.pad, fill=PANEL_LINE, width=1)
                x += w
                continue
            ix = x + self.gap / 2
            iy = m + self.pad + self.bounce.get(i, 0)
            kind = a.get("kind")
            img = None if kind else self.image(a, s)
            if kind == "calendar":
                self.draw_calendar_tile(ix, iy, s)
            elif kind == "clock":
                round_rect(cv, ix, iy, ix + s, iy + s, s // 4, fill="#2b3142", outline="#4b5468")
                hh, mm = time.strftime("%H"), time.strftime("%M")
                cv.create_text(ix + s / 2, iy + s * 0.38, text=hh, fill="#f9fafb",
                               font=("Segoe UI", -round(s * 0.34), "bold"))
                cv.create_text(ix + s / 2, iy + s * 0.70, text=mm, fill="#93c5fd",
                               font=("Segoe UI", -round(s * 0.30), "bold"))
            elif kind == "group":
                self.draw_group_tile(a, ix, iy, s)
            elif kind == "volume":
                self.draw_volume_tile(ix, iy, s)
            elif kind == "network":
                self.draw_network_tile(ix, iy, s)
            elif kind == "search":
                round_rect(cv, ix, iy, ix + s, iy + s, s // 4, fill="#2b3142", outline="#4b5468")
                c, rr, lw = s * 0.44, s * 0.2, max(2, round(s * 0.07))
                cv.create_oval(ix + c - rr, iy + c - rr, ix + c + rr, iy + c + rr, outline="#f9fafb", width=lw)
                cv.create_line(ix + c + rr * 0.7, iy + c + rr * 0.7, ix + s * 0.76, iy + s * 0.76,
                               fill="#f9fafb", width=lw, capstyle="round")
            elif img:
                cv.create_image(ix, iy, image=img, anchor="nw")
            else:
                round_rect(cv, ix, iy, ix + s, iy + s, s // 4, fill=tile_color(a.get("name", "?")), outline="")
                cv.create_text(ix + s / 2, iy + s / 2, text=(a.get("name") or "?")[:1].upper(),
                               fill="white", font=("Segoe UI", -round(s * 0.45), "bold"))
            if a.get("dynamic") or id(a) in self.running or self.windows_of(a):
                cy = m + self.pad + s + round(4 * self.scale)
                cv.create_oval(ix + s / 2 - r, cy - r, ix + s / 2 + r, cy + r, fill=DOT, outline="")
            x += w

    def draw_volume_tile(self, ix: float, iy: float, s: int):
        """Loa + so vach song theo muc am luong; tat tieng -> dau X do."""
        cv, (lv, mute, _) = self.cv, self.vol
        round_rect(cv, ix, iy, ix + s, iy + s, s // 4, fill="#2b3142", outline="#4b5468")
        cx, cy, u = ix + s * 0.36, iy + s / 2, s / 10
        cv.create_polygon(cx - 2.2 * u, cy - u, cx - 0.8 * u, cy - u, cx + 1.2 * u, cy - 2.6 * u,
                          cx + 1.2 * u, cy + 2.6 * u, cx - 0.8 * u, cy + u, cx - 2.2 * u, cy + u, fill="#f9fafb")
        lw = max(2, round(s * 0.05))
        if mute or lv == 0:
            x0 = cx + 2.3 * u
            cv.create_line(x0, cy - 1.4 * u, x0 + 2.8 * u, cy + 1.4 * u, fill="#f87171", width=lw, capstyle="round")
            cv.create_line(x0, cy + 1.4 * u, x0 + 2.8 * u, cy - 1.4 * u, fill="#f87171", width=lw, capstyle="round")
        else:
            waves = 1 if lv < 34 else 2 if lv < 67 else 3
            for k in range(3):
                r = (1.9 + 1.3 * k) * u
                cv.create_arc(cx + 1.2 * u - r, cy - r, cx + 1.2 * u + r, cy + r, start=-45, extent=90, style="arc",
                              outline="#f9fafb" if k < waves else "#4b5468", width=lw)
        if lv >= 0:  # vach muc am luong o day o
            cv.create_line(ix + s * 0.18, iy + s * 0.86, ix + s * 0.82, iy + s * 0.86, fill="#4b5468", width=lw)
            cv.create_line(ix + s * 0.18, iy + s * 0.86, ix + s * (0.18 + 0.64 * lv / 100), iy + s * 0.86,
                           fill="#93c5fd" if not mute else "#6b7280", width=lw)

    def draw_network_tile(self, ix: float, iy: float, s: int):
        """Song Wi-Fi theo cuong do; co day -> o man hinh noi day; mat mang -> do."""
        cv, net = self.cv, self.net
        round_rect(cv, ix, iy, ix + s, iy + s, s // 4, fill="#2b3142", outline="#4b5468")
        lw = max(2, round(s * 0.07))
        cx, base = ix + s / 2, iy + s * 0.72
        if net.get("wired"):  # ba o vuong noi day kieu bieu tuong Ethernet
            q = s * 0.16
            for x, y in ((cx - q / 2, iy + s * 0.22), (cx - q * 1.9, base - q / 2), (cx + q * 0.9, base - q / 2)):
                cv.create_rectangle(x, y, x + q, y + q, outline="#f9fafb", width=max(1, lw - 1))
            cv.create_line(cx, iy + s * 0.22 + q, cx, base - q, fill="#f9fafb", width=max(1, lw - 1))
            cv.create_line(cx - q * 1.4, base - q, cx + q * 1.4, base - q, fill="#f9fafb", width=max(1, lw - 1))
            return
        online, sig = net.get("online", True), net.get("signal", 0)
        bars = 0 if not net.get("ssid") else 1 if sig < 40 else 2 if sig < 70 else 3
        for k in range(3):
            r = s * (0.16 + 0.15 * k)
            on = online and k < bars
            cv.create_arc(cx - r, base - r, cx + r, base + r, start=50, extent=80, style="arc",
                          outline="#f9fafb" if on else "#4b5468", width=lw)
        dot = s * 0.05
        cv.create_oval(cx - dot, base - dot, cx + dot, base + dot, fill="#f9fafb" if online else "#f87171",
                       outline="")
        if net and not online:
            cv.create_line(ix + s * 0.22, iy + s * 0.22, ix + s * 0.78, iy + s * 0.78, fill="#f87171", width=lw)

    def mini(self, s: int) -> int:
        return round(s * 0.38)

    def draw_group_tile(self, g: dict, ix: float, iy: float, s: int):
        """O nhom kieu thu muc iOS: luoi 2x2 icon nho cua thanh vien."""
        cv = self.cv
        round_rect(cv, ix, iy, ix + s, iy + s, s // 4, fill="#3a4258", outline="#56607a")
        ms, pad = self.mini(s), s * 0.08
        gap = (s - 2 * pad - 2 * ms)
        for k, m in enumerate(g.get("members", [])[:4]):
            mx = ix + pad + (k % 2) * (ms + gap)
            my = iy + pad + (k // 2) * (ms + gap)
            img = self.image(m, ms)
            if img:
                cv.create_image(mx, my, image=img, anchor="nw")
            else:
                round_rect(cv, mx, my, mx + ms, my + ms, max(2, ms // 4), fill=tile_color(m.get("name", "?")),
                           outline="")
                cv.create_text(mx + ms / 2, my + ms / 2, text=(m.get("name") or "?")[:1].upper(), fill="white",
                               font=("Segoe UI", -round(ms * 0.5), "bold"))

    def draw_calendar_tile(self, ix: float, iy: float, s: int):
        """O lich kieu macOS: dai do ghi thu, so ngay lon, ngay am o duoi."""
        cv, today = self.cv, dt.date.today()
        band = round(s * 0.28)
        round_rect(cv, ix, iy, ix + s, iy + s, s // 4, fill="#ffffff", outline="")
        round_rect(cv, ix, iy, ix + s, iy + band + s // 4, s // 4, fill="#ef4444", outline="")
        cv.create_rectangle(ix, iy + band, ix + s, iy + band + s // 4, fill="#ffffff", outline="")
        thu = "CN" if today.weekday() == 6 else f"THỨ {today.weekday() + 2}"
        cv.create_text(ix + s / 2, iy + band / 2 + 1, text=thu, fill="#ffffff",
                       font=("Segoe UI", -max(8, round(s * 0.19)), "bold"))
        cv.create_text(ix + s / 2, iy + band + (s - band) * 0.42, text=str(today.day), fill="#111827",
                       font=("Segoe UI", -round(s * 0.44), "bold"))
        ld, lm, _, _ = lunar(today)
        cv.create_text(ix + s / 2, iy + s - max(6, s * 0.12), text=f"{ld}/{lm}", fill="#9ca3af",
                       font=("Segoe UI", -max(7, round(s * 0.15))))

    def label_of(self, a: dict) -> str:
        if a.get("kind") == "calendar":
            d = dt.date.today()
            ld, lm, ly, leap = lunar(d)
            return (f"{THU[d.weekday()]}, {d.day}/{d.month}/{d.year}  ·  Âm lịch {ld}/{lm}"
                    f"{' (nhuận)' if leap else ''} năm {can_chi(ly)}")
        if a.get("kind") == "clock":
            return time.strftime("%H:%M  ·  ") + THU[dt.date.today().weekday()]
        if a.get("kind") == "search":
            return "Tìm ứng dụng  ·  Alt+Space"
        if a.get("kind") == "volume":
            lv, mute, name = self.vol
            return "Không có loa" if lv < 0 else \
                f"Âm lượng {lv}%{' · tắt tiếng' if mute else ''}  ·  lăn chuột để chỉnh"
        if a.get("kind") == "network":
            n = self.net
            if not n:
                return "Kết nối"
            if not n.get("online"):
                return "Không có kết nối Internet"
            return f"Wi-Fi: {n['ssid']} · {n['signal']}%" if n.get("ssid") else "Ethernet (có dây) · đã kết nối"
        n = len(self.windows_of(a))
        return a.get("name", "") + (f"  ·  {n} cửa sổ" if n > 1 else "")

    def index_at(self, x: float, y: float) -> int | None:
        if y > self.m + self.pad + self.big + self.pad:
            return None
        for i, x1, x2 in self.slots:
            if x1 <= x < x2:
                return None if self.view[i].get("kind") == "sep" else i
        return None

    # --- chuot ---
    def on_motion(self, ev):
        i = self.index_at(ev.x, ev.y)
        if i != self.hover:
            self.hover = i
            self.draw()
            self.show_tip()

    def on_leave(self, _ev=None):
        if self.hover is not None:
            self.hover = None
            self.draw()
        self.tip.withdraw()

    def show_tip(self):
        if self.hover is None:
            self.tip.withdraw()
            return
        x1 = x2 = 0
        for i, a, b in self.slots:
            if i == self.hover:
                x1, x2 = a, b
        self.tip_lb.configure(text=self.label_of(self.view[self.hover]))
        self.tip.update_idletasks()
        w = self.tip.winfo_reqwidth()
        y = self.win_y + self.m + self.pad + self.big + round(12 * self.scale)
        self.tip.geometry(f"+{int(self.mon[0] + (x1 + x2) / 2 - w / 2)}+{y}")
        self.tip.deiconify()
        self.tip.lift()

    def on_click(self, ev):
        i = self.index_at(ev.x, ev.y)
        if i is not None:
            self.activate(i)

    def activate(self, i: int):
        """Nhu macOS: dang co cua so thi chuyen sang (bam tiep de xoay vong / thu nho), chua co thi mo."""
        a = self.view[i]
        kind = a.get("kind")
        if kind in ("calendar", "clock"):
            self.toggle_calendar(i)
            return
        if kind == "search":
            self.toggle_search()
            return
        if kind in ("volume", "network"):
            self.toggle_sys(i, kind)
            return
        if kind == "group":
            self.toggle_stack(i)
            return
        self.focus_app(a, i)

    def focus_app(self, a: dict, i: int | None = None):
        hwnds = [h for h in self.windows_of(a) if _u32.IsWindow(h)]
        if not hwnds:
            # app mot cua so (Zalo Web): vua bam mo ma cua so chua kip hien -> khong mo them ban nua
            if a.get("single") and time.time() - self.launched_at.get(id(a), 0) < 15:
                log(f"{a.get('name')} đang mở, chờ cửa sổ")
                return
            self.launched_at[id(a)] = time.time()
            self.launch_app(a, i)
            return
        if len(hwnds) > 1 and not a.get("single"):  # nhieu cua so -> hien danh sach de chon
            self.show_windows(a, hwnds, i)
            return
        fg = _u32.GetForegroundWindow()
        if fg in hwnds:
            _u32.ShowWindow(fg, 6)  # SW_MINIMIZE - bam lai vao ung dung dang dung thi thu nho
            return
        focus_window(hwnds[0])
        log(f"Chuyển sang {a.get('name')}")

    def show_windows(self, a: dict, hwnds: list[int], i: int | None = None):
        self.close_stack()
        self.close_calendar()
        if i is not None:
            x1, x2 = next(((p, q) for k, p, q in self.slots if k == i), (0, self.sw))
            anchor = self.mon[0] + (x1 + x2) / 2
        else:  # mo tu bang nhom: canh theo con tro
            pt = wt.POINT()
            _u32.GetCursorPos(ctypes.byref(pt))
            anchor = pt.x
        self.stack = StackPopup(self, {"name": f"{a.get('name', '')} · {len(hwnds)} cửa sổ", "members": []},
                                anchor_x=anchor, top=self.win_y + self.m + self.panel_h + round(10 * self.scale),
                                windows=hwnds, app=a)
        self.tip.withdraw()

    def launch(self, i: int):
        self.launch_app(self.view[i], i)

    def launch_app(self, app: dict, i: int | None = None):
        if i is not None:
            self.animate_bounce(i)

        def work():
            try:
                path = os.path.expandvars(app.get("path", ""))
                args = app.get("args") or ""
                cwd = app.get("cwd") or None
                if app.get("url") and app.get("port") and port_open(int(app["port"])):
                    os.startfile(app["url"])  # server da chay (9Router...) -> mo trang, khong bat them ban nua
                elif app.get("hidden"):  # script (vd node open-zalo.mjs): chay an, khong hien console
                    subprocess.Popen([path] + (args.split() if args else []), cwd=cwd, creationflags=NO_WINDOW,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif args or cwd:
                    os.startfile(path, arguments=args, cwd=cwd)
                else:
                    os.startfile(path)
                log(f"Mở {app.get('name')} ({time.time() - t0:.1f}s)")
            except OSError as e:
                msg = f"Không mở được {app.get('name')}:\n{e}"
                log(msg.replace("\n", " "))
                self.ui_call(messagebox.showerror, "Mekong Dock", msg)
        t0 = time.time()
        log(f"Bấm {app.get('name')}")
        threading.Thread(target=work, daemon=True).start()

    def animate_bounce(self, i: int, step: int = 0):
        seq = [0.25, 0.45, 0.25, 0, 0.18, 0.3, 0.18, 0]
        if step >= len(seq):
            self.bounce.pop(i, None)
            self.draw()
            return
        self.bounce[i] = round(seq[step] * self.base)
        self.draw()
        self.root.after(45, self.animate_bounce, i, step + 1)

    # --- menu chuot phai ---
    def on_right(self, ev):
        i = self.index_at(ev.x, ev.y)
        menu = tk.Menu(self.root, tearoff=0)
        if i is not None and self.view[i].get("kind"):
            app = self.view[i]
            kind = app.get("kind")
            if kind in ("calendar", "clock"):
                menu.add_command(label="Mở lịch tháng", command=lambda: self.toggle_calendar(i))
                menu.add_command(label="Mở Google Calendar", command=lambda: os.startfile(GOOGLE_CAL))
            elif kind == "search":
                menu.add_command(label="Tìm ứng dụng (Alt+Space)", command=self.toggle_search)
            elif kind == "volume":
                menu.add_command(label="Bật tiếng" if self.vol[1] else "Tắt tiếng",
                                 command=lambda: self.set_volume(mute=not self.vol[1]))
                menu.add_command(label="Bộ trộn âm lượng", command=lambda: os.startfile("sndvol.exe"))
                menu.add_command(label="Cài đặt âm thanh", command=lambda: os.startfile("ms-settings:sound"))
            elif kind == "network":
                for label, uri in NET_LINKS:
                    menu.add_command(label=label, command=lambda u=uri: os.startfile(u))
            elif kind == "group":
                menu.add_command(label=f"Mở nhóm {app.get('name')}", command=lambda: self.toggle_stack(i))
                hwnds = [h for h in self.windows_of(app) if _u32.IsWindow(h)]
                if hwnds:
                    menu.add_command(label=f"Đóng {len(hwnds)} cửa sổ",
                                     command=lambda: [_u32.PostMessageW(h, 0x0010, 0, 0) for h in hwnds])
            menu.add_separator()
            menu.add_command(label="◀ Chuyển sang trái", command=lambda: self.move(app, -1))
            menu.add_command(label="Chuyển sang phải ▶", command=lambda: self.move(app, 1))
            menu.add_command(label="Gỡ khỏi Dock", command=lambda: self.remove(app))
            menu.add_separator()
        elif i is not None:
            app = self.view[i]
            hwnds = [h for h in self.windows_of(app) if _u32.IsWindow(h)]
            title = ctypes.create_unicode_buffer(256)
            for h in hwnds[:12]:  # danh sach cua so de chon nhanh
                _u32.InternalGetWindowText(h, title, 256)
                t = title.value if len(title.value) <= 60 else title.value[:57] + "…"
                menu.add_command(label="▢ " + t, command=lambda h=h: focus_window(h))
            if hwnds:
                menu.add_separator()
            if not (hwnds and app.get("single")):  # app mot cua so: dang mo thi khong cho mo them
                menu.add_command(label="Mở cửa sổ mới" if hwnds else f"Mở {app.get('name')}",
                                 command=lambda: self.launch(i))
            if hwnds:
                menu.add_command(label=f"Đóng {len(hwnds)} cửa sổ",
                                 command=lambda: [_u32.PostMessageW(h, 0x0010, 0, 0) for h in hwnds])  # WM_CLOSE
            menu.add_command(label="Mở vị trí tệp", command=lambda: self.reveal(app))
            menu.add_separator()
            if app.get("dynamic"):
                menu.add_command(label="Giữ trong Dock", command=lambda: self.pin(app))
            else:
                menu.add_command(label="Đổi tên…", command=lambda: self.rename(app))
                menu.add_command(label="◀ Chuyển sang trái", command=lambda: self.move(app, -1))
                menu.add_command(label="Chuyển sang phải ▶", command=lambda: self.move(app, 1))
                menu.add_command(label="Gỡ khỏi Dock", command=lambda: self.remove(app))
            menu.add_separator()
        menu.add_command(label="Thêm ứng dụng…", command=self.add_app)
        menu.add_command(label="Quét lại Desktop", command=self.rescan)
        menu.add_command(label="Làm mới icon", command=self.refresh_icons)
        menu.add_separator()
        self.v_dockmode.set("auto" if self.v_autohide.get() else "reserve" if self.v_reserve.get() else "float")
        menu.add_command(label="Chế độ Dock:", state="disabled")
        menu.add_radiobutton(label="   Tự ẩn (chạm mép trên để hiện)", variable=self.v_dockmode, value="auto",
                             command=self.on_dockmode)
        menu.add_radiobutton(label="   Luôn hiện, chừa chỗ (cửa sổ nằm dưới Dock)", variable=self.v_dockmode,
                             value="reserve", command=self.on_dockmode)
        menu.add_radiobutton(label="   Luôn hiện, nổi trên cửa sổ", variable=self.v_dockmode, value="float",
                             command=self.on_dockmode)
        menu.add_separator()
        menu.add_checkbutton(label="Ẩn taskbar Windows", variable=self.v_taskbar, command=self.on_taskbar)
        menu.add_checkbutton(label="Lịch trên hình nền", variable=self.v_desk, command=self.on_desk)
        if self.s.get("embed_url"):
            menu.add_radiobutton(label="   Kiểu: trang Google Calendar", variable=self.v_desk_mode, value="embed",
                                 command=self.on_desk_mode)
            menu.add_radiobutton(label="   Kiểu: thẻ lịch gọn", variable=self.v_desk_mode, value="card",
                                 command=self.on_desk_mode)
        menu.add_command(label="   Đặt trang lịch nhúng…", command=self.set_embed)
        menu.add_command(label="Kết nối Google Calendar…", command=self.connect_calendar)
        if len(self.s.get("calendars") or []) > 1:
            menu.add_command(label="Bỏ kết nối lịch riêng", command=self.disconnect_calendars)
        menu.add_command(label="Tìm ứng dụng (Alt+Space)", command=self.toggle_search)
        menu.add_checkbutton(label="Chạy cùng Windows", variable=self.v_autostart, command=self.on_autostart)
        menu.add_command(label="Sửa danh sách (dock_apps.json)", command=lambda: os.startfile(str(APPS_FILE)))
        menu.add_separator()
        menu.add_command(label="Thoát Dock", command=lambda: self.quit())
        self.tip.withdraw()
        self.menu_open = True  # dang mo menu thi khong tu an
        try:
            menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            menu.grab_release()
            self.menu_open = False
            self.leave_at = 0.0

    def reveal(self, app: dict):
        p = os.path.expandvars(app.get("path", ""))
        if os.path.exists(p):
            subprocess.Popen(["explorer", "/select,", p])

    def pos(self, app: dict) -> int:
        return next(k for k, a in enumerate(self.apps) if a is app)

    def rename(self, app: dict):
        from tkinter import simpledialog
        name = simpledialog.askstring("Mekong Dock", "Tên hiển thị:", initialvalue=app.get("name", ""),
                                      parent=self.root)
        if name:
            app["name"] = name.strip()
            self.save_apps()

    def move(self, app: dict, d: int):
        i = self.pos(app)
        j = i + d
        if 0 <= j < len(self.apps):
            self.apps[i], self.apps[j] = self.apps[j], self.apps[i]
            self.relayout()

    def remove(self, app: dict):
        log(f"Gỡ {app.get('name')}")
        del self.apps[self.pos(app)]
        self.relayout()

    def pin(self, app: dict):
        exe = app["target"]
        self.apps.insert(self.first_sep(), {"name": app["name"], "path": exe, "target": exe, "icon": ""})
        self.dyn = [a for a in self.dyn if a is not app]
        log(f"Giữ trong Dock: {app['name']}")
        self.relayout()

    def first_sep(self) -> int:
        return next((k for k, a in enumerate(self.apps) if a.get("kind") == "sep"), len(self.apps))

    def add_app(self):
        path = filedialog.askopenfilename(title="Chọn ứng dụng / shortcut", parent=self.root, filetypes=[
            ("Ứng dụng", "*.exe *.lnk *.bat *.cmd *.pyw *.vbs *.url"), ("Tất cả", "*.*")])
        if not path:
            return
        path = str(Path(path))
        info = resolve_links([path]).get(path) if path.lower().endswith(".lnk") else None
        self.apps.insert(self.first_sep(), entry_for(path, info))
        self.relayout()
        threading.Thread(target=self.extract_icons, daemon=True).start()

    def rescan(self):
        def work():
            known = {os.path.expandvars(a.get("path", "")).casefold() for a in self.apps}
            names = {a.get("name", "").casefold() for a in self.apps}
            new = [a for a in scan_desktop(known) if a["name"].casefold() not in names]
            def apply():
                at = self.first_sep()
                self.apps[at:at] = new
                self.relayout()
                messagebox.showinfo("Mekong Dock", f"Đã thêm {len(new)} ứng dụng mới từ Desktop.")
            self.ui_call(apply)
            self.extract_icons()
        threading.Thread(target=work, daemon=True).start()

    def refresh_icons(self):
        for f in ICON_DIR.glob("*.png"):
            f.unlink(missing_ok=True)
        self.images.clear()
        self.draw()
        threading.Thread(target=self.extract_icons, daemon=True).start()

    def relayout(self, save: bool = True):
        if save:
            self.save_apps()
        old = (self.base, self.mid, self.big)
        self.build_view()
        self.compute_metrics()
        # chi so cu khong con dung sau khi them/go/doi cho
        self.hover, self.bounce = None, {}
        self.tip.withdraw()
        self.place()
        self.draw()
        if (self.base, self.mid, self.big) != old:  # kich thuoc moi -> can anh icon moi
            threading.Thread(target=self.extract_icons, daemon=True).start()

    def save_apps(self):
        save_json(APPS_FILE, self.apps)

    def on_dockmode(self):
        mode = self.v_dockmode.get()
        self.v_autohide.set(mode == "auto")
        self.v_reserve.set(mode == "reserve")
        log(f"Chế độ Dock: {mode}")
        self.on_option()

    def on_option(self):
        self.s["reserve"] = self.v_reserve.get()
        self.s["autohide"] = self.v_autohide.get()
        save_json(SETTINGS_FILE, self.s)
        self.hidden = False
        self.place()

    # --- am luong + mang ---
    def watch_system(self):
        """Luong nen: doc am luong moi 2 giay (doi bang phim / app khac), mang moi 10 giay."""
        reader, n = Volume(), 0  # COM khoi tao rieng cho luong nay
        while True:
            try:
                vol = reader.get()
                net = network_status() if n % 5 == 0 else None
                n += 1
                self.ui_call(self.apply_system, vol, net)
            except Exception as e:
                log(f"watch_system: {type(e).__name__}: {e}")
            time.sleep(2)

    def apply_system(self, vol, net):
        changed = vol != self.vol or (net is not None and net != self.net)
        self.vol = vol
        if net is not None:
            self.net = net
        if changed:
            if not self.hidden:
                self.draw()
            if self.sys_pop is not None:
                self.sys_pop.sync()

    def set_volume(self, percent: int | None = None, mute: bool | None = None):
        self.volume.set(percent, mute)
        self.vol = self.volume.get()
        self.draw()

    def on_wheel(self, ev):
        i = self.index_at(ev.x, ev.y)
        if i is not None and self.view[i].get("kind") == "volume" and self.vol[0] >= 0:
            step = 2 if ev.delta > 0 else -2
            self.set_volume(percent=self.vol[0] + step * max(1, abs(ev.delta) // 120), mute=False)
            self.show_tip()
            if self.sys_pop is not None:
                self.sys_pop.sync()

    def toggle_sys(self, i: int, kind: str):
        if self.sys_pop is not None:
            same = self.sys_pop.kind == kind
            self.close_sys()
            if same:
                return
        self.close_stack()
        self.close_calendar()
        x1, x2 = next(((p, q) for k, p, q in self.slots if k == i), (0, self.sw))
        self.sys_pop = SystemPopup(self, kind, anchor_x=self.mon[0] + (x1 + x2) / 2,
                                   top=self.win_y + self.m + self.panel_h + round(10 * self.scale))
        self.tip.withdraw()

    def close_sys(self):
        if self.sys_pop is not None:
            self.sys_pop.destroy()
            self.sys_pop = None
            self.leave_at = 0.0

    # --- tim kiem + nhom ---
    def toggle_search(self):
        if self.spot is not None:
            self.close_search()
            return
        self.close_stack()
        self.close_calendar()
        self.tip.withdraw()
        self.spot = Spotlight(self)

    def close_search(self):
        if self.spot is not None:
            self.spot.destroy()
            self.spot = None
            self.leave_at = 0.0

    def toggle_stack(self, i: int):
        if self.stack is not None:
            self.close_stack()
            return
        self.close_calendar()
        x1 = x2 = 0
        for k, a, b in self.slots:
            if k == i:
                x1, x2 = a, b
        self.stack = StackPopup(self, self.view[i], anchor_x=self.mon[0] + (x1 + x2) / 2,
                                top=self.win_y + self.m + self.panel_h + round(10 * self.scale))
        self.tip.withdraw()

    def close_stack(self):
        if self.stack is not None:
            self.stack.destroy()
            self.stack = None
            self.leave_at = 0.0

    # --- lich tren hinh nen ---
    def desk_tick(self):
        if self.desk is None:
            return
        if not self.desk.alive():  # Explorer khoi dong lai -> lop hinh nen moi, dung lai widget
            try:
                self.desk.destroy()
            except tk.TclError:
                pass
            log("Dựng lại lịch trên hình nền")
            self.desk = self.make_desk()
        else:
            self.desk.tick()

    def make_desk(self):
        if self.s.get("desk_mode") == "embed" and self.s.get("embed_url"):
            return DesktopWeb(self, self.s["embed_url"])
        return DesktopCalendar(self)

    def on_desk_mode(self):
        self.s["desk_mode"] = self.v_desk_mode.get()
        save_json(SETTINGS_FILE, self.s)
        if self.desk is not None:
            self.desk.destroy()
            self.desk = self.make_desk() if self.v_desk.get() else None

    def set_embed(self):
        """Dan ma <iframe> (hoac dia chi) tu Google Calendar > Cai dat > Tich hop lich > Ma nhung."""
        from tkinter import simpledialog
        text = simpledialog.askstring("Trang lịch nhúng", "Dán mã <iframe …> hoặc địa chỉ calendar/embed:",
                                      initialvalue=self.s.get("embed_url", ""), parent=self.root)
        if not text:
            return
        m = re.search(r'src="([^"]+)"', text)
        url = (m.group(1) if m else text).strip().replace("&amp;", "&")
        if "calendar.google.com/calendar/embed" not in url:
            messagebox.showerror("Trang lịch nhúng", "Không phải địa chỉ calendar.google.com/calendar/embed",
                                 parent=self.root)
            return
        self.s["embed_url"], self.s["desk_mode"] = url, "embed"
        self.v_desk_mode.set("embed")
        self.on_desk_mode()

    def on_desk(self):
        self.s["desktop_calendar"] = self.v_desk.get()
        save_json(SETTINGS_FILE, self.s)
        if self.v_desk.get() and self.desk is None:
            self.desk = self.make_desk()
        elif not self.v_desk.get() and self.desk is not None:
            self.desk.destroy()
            self.desk = None

    def connect_calendar(self):
        from tkinter import simpledialog
        url = simpledialog.askstring(
            "Kết nối Google Calendar",
            "Dán 'Địa chỉ bí mật ở định dạng iCal' của lịch:\n"
            "Google Calendar → ⚙ Cài đặt → chọn lịch của anh → Tích hợp lịch\n"
            "(địa chỉ dạng https://calendar.google.com/calendar/ical/…/private-…/basic.ics)",
            parent=self.root)
        if not url or not url.strip():
            return
        url = url.strip()

        def work():
            try:
                text = gcal.fetch(url)
                if "BEGIN:VCALENDAR" not in text:
                    raise ValueError("địa chỉ không trả về lịch iCal")
                n = text.count("BEGIN:VEVENT")
            except Exception as e:
                msg = f"Không tải được lịch:\n{e}"
                self.ui_call(messagebox.showerror, "Google Calendar", msg)
                return

            def ok():
                cals = list(self.s.get("calendars") or [gcal.HOLIDAYS_VN])
                if url not in cals:
                    cals.append(url)
                self.s["calendars"] = cals
                save_json(SETTINGS_FILE, self.s)
                log(f"Kết nối Google Calendar ({n} sự kiện)")
                if self.desk is not None:
                    self.desk.refresh()
                messagebox.showinfo("Google Calendar", f"Đã kết nối lịch ({n} sự kiện).")
            self.ui_call(ok)
        threading.Thread(target=work, daemon=True).start()

    def disconnect_calendars(self):
        if messagebox.askyesno("Google Calendar", "Bỏ mọi lịch riêng đã kết nối (giữ lịch Ngày lễ)?",
                               parent=self.root):
            self.s["calendars"] = [gcal.HOLIDAYS_VN]
            save_json(SETTINGS_FILE, self.s)
            if self.desk is not None:
                self.desk.refresh()

    def on_taskbar(self):
        on = self.v_taskbar.get()
        taskbar_autohide(on)
        taskbar_show(not on)
        self.s["hide_taskbar"] = on
        save_json(SETTINGS_FILE, self.s)
        log(f"Taskbar Windows: {'ẩn' if on else 'hiện'}")

    # --- lich thang ---
    def toggle_calendar(self, i: int):
        if self.cal is not None:
            self.close_calendar()
            return
        x1 = x2 = 0
        for k, a, b in self.slots:
            if k == i:
                x1, x2 = a, b
        self.cal = CalendarPopup(self, anchor_x=self.mon[0] + (x1 + x2) / 2,
                                 top=self.win_y + self.m + self.panel_h + round(10 * self.scale))
        self.tip.withdraw()

    def close_calendar(self):
        if self.cal is not None:
            self.cal.destroy()
            self.cal = None
            self.leave_at = 0.0

    def fullscreen_app(self) -> bool:
        """Cua so phia truoc phu kin man hinh (video, game, F11, trinh chieu)?"""
        u = ctypes.windll.user32
        fg = u.GetForegroundWindow()
        if not fg or not u.IsWindowVisible(fg) or u.IsIconic(fg):
            return False
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        if pid.value == os.getpid():
            return False
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(fg, cls, 64)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False
        cloaked = wt.DWORD()
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(fg, 14, ctypes.byref(cloaked), 4) == 0 and cloaked.value:
            return False  # cua so an (virtual desktop, app UWP dang treo)
        # taskbar tu an -> cua so phong to cung phu kin man hinh; chi tinh cua so khong khung (F11, video, game)
        if u.IsZoomed(fg) or (_u32.GetWindowLongW(fg, -16) & 0x00C00000) == 0x00C00000:  # WS_CAPTION
            if self.fs_was:
                self.fs_was = False
                log("Toàn màn hình: tắt (cửa sổ phóng to)")
            return False
        rc = wt.RECT()
        u.GetWindowRect(fg, ctypes.byref(rc))
        # chi tinh cua so phu kin dung man hinh dang chua Dock
        m = monitor_rect(u.MonitorFromWindow(fg, 2))
        full = m == self.mon and rc.left <= m[0] and rc.top <= m[1] and rc.right >= m[2] and rc.bottom >= m[3]
        if full != self.fs_was:
            self.fs_was = full
            t = ctypes.create_unicode_buffer(128)
            u.GetWindowTextW(fg, t, 128)
            log(f"Toàn màn hình: {'bật' if full else 'tắt'} ({cls.value} '{t.value}')")
        return full

    def keep_below_dock(self):
        """Cua so dang dung lan len vung Dock (keo len, mo o vi tri cu) -> day xuong ngay duoi Dock, nhu macOS."""
        u = _u32
        if self.hidden or self.v_autohide.get() or u.GetAsyncKeyState(0x01) & 0x8000:  # dang keo cua so
            return
        fg = u.GetForegroundWindow()
        if not fg or u.IsZoomed(fg) or u.IsIconic(fg) or not u.IsWindowVisible(fg) or u.GetWindow(fg, 4):
            return  # phong to (Windows tu xep duoi Dock), thu nho, hop thoai con
        if (u.GetWindowLongW(fg, -16) & 0x00C00000) != 0x00C00000 or window_pid(fg) == os.getpid():
            return  # khong co thanh tieu de (popup, menu, toan man hinh) hoac cua so cua Dock
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(fg, cls, 64)
        if cls.value in SKIP_CLASSES:
            return
        if monitor_rect(u.MonitorFromWindow(fg, 2)) != self.mon:
            return
        rc = wt.RECT()
        u.GetWindowRect(fg, ctypes.byref(rc))
        mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        u.GetMonitorInfoW(u.MonitorFromWindow(fg, 2), ctypes.byref(mi))
        limit = mi.rcWork.top if self.v_reserve.get() else self.mon[1] + self.m + self.panel_h + 2
        x1, x2 = self.mon[0] + self.panel_x[0], self.mon[0] + self.panel_x[1]
        if rc.top >= limit or rc.right <= x1 or rc.left >= x2:
            return  # khong cham thanh Dock
        attempt = (fg, rc.left, rc.top, rc.right, rc.bottom)
        if attempt == self.fit_last:  # da thu ma cua so khong chiu di (vd app chay quyen admin) -> thoi
            return
        self.fit_last = attempt
        h = min(rc.bottom - rc.top, mi.rcWork.bottom - limit)
        u.SetWindowPos(fg, 0, rc.left, limit, rc.right - rc.left, h, 0x0004 | 0x0010)  # NOZORDER|NOACTIVATE
        log(f"Đẩy cửa sổ xuống dưới Dock ({cls.value})")

    def set_hidden(self, hidden: bool):
        if hidden != self.hidden:
            self.hidden = hidden
            self.leave_at = self.edge_at = 0.0
            if hidden:
                self.on_leave()
                self.close_calendar()
                self.close_stack()
                self.close_sys()
            self.place()

    def autostart_enabled(self) -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, RUN_VALUE)
            return True
        except OSError:
            return False

    def on_autostart(self):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if self.v_autostart.get():
                exe = Path(sys.executable)
                pyw = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, f'"{pyw}" "{Path(__file__).resolve()}"')
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE)
                except FileNotFoundError:
                    pass

    # --- nen ---
    def extract_icons(self):
        with self.extract_lock:  # khong chay hai PowerShell trich icon cung luc
            self._extract_icons()

    def _extract_icons(self):
        ICON_DIR.mkdir(exist_ok=True)
        jobs = []
        full = {self.base, self.mid, self.big}
        todo = [(a, full) for a in list(self.apps) + list(self.dyn) if not a.get("kind")]
        # thanh vien nhom: icon nho trong o nhom + icon thuong trong bang nhom
        minis = {self.mini(s) for s in full} | {round(32 * self.scale)}
        for g in list(self.apps):
            if g.get("kind") == "group":
                todo += [(m, minis) for m in g.get("members", [])]
        for a, sizes in todo:  # vach ngan, o lich, dong ho, tim kiem: tu ve, khong co icon
            file, index = icon_source(a)
            if not file or not os.path.exists(file):
                continue
            for s in sizes:
                png = icon_png(a, s)
                if not png.exists():
                    jobs.append({"file": file, "index": index, "size": s, "out": str(png)})
        if jobs:
            run_ps(PS_ICONS, jobs)
            self.ui_call(self.draw)

    def ui_call(self, fn, *args):
        """Luong nen KHONG duoc goi Tk truc tiep - day vao hang doi, poll_mouse chay tren luong chinh."""
        self.ui.put((fn, args))

    def match(self, a: dict, exe: str, title: str, h: int = 0) -> int:
        """Muc do cua so (exe, tieu de) thuoc ung dung `a`: 1 = theo tieu de / dong lenh (uu tien nhat),
        2 = theo exe, 0 = khong. Nho vay cua so Mail (Chrome --app) thuoc Mail chu khong thuoc Chrome."""
        name = Path(exe).name.casefold() if exe else ""
        if a.get("aumid"):
            # app web Chrome --app: moi trang mot AppUserModelID rieng, tieu de thi doi theo noi dung
            t = Path(os.path.expandvars(a.get("target") or "")).name.casefold()
            if not h or name != t:
                return 0
            if h not in self.aumid_cache:
                self.aumid_cache[h] = window_aumid(h).casefold()
            return 1 if a["aumid"].casefold() in self.aumid_cache[h] else 0
        if a.get("window_cmd"):
            # vd Zalo Web: Edge chay voi --user-data-dir=...zalo-login\profile; tieu de doi theo cuoc tro chuyen
            if not h or name != (a.get("window_exe") or "").casefold():
                return 0
            pid = window_pid(h)
            if pid not in self.cmd_cache:
                self.cmd_cache[pid] = process_cmdline(pid).casefold()
            return 1 if a["window_cmd"].casefold() in self.cmd_cache[pid] else 0
        if a.get("window"):
            # app web (Chrome --app): tieu de khop VA dung trinh duyet - "Gemini" khong gianh cua so Gemini Web2API
            # window_exe: cua so do chuong trinh khac mo (vd Zalo Web: node mo Edge)
            t = (a.get("window_exe") or Path(os.path.expandvars(a.get("target") or "")).name).casefold()
            if t and t not in GENERIC and name and name != t:
                return 0
            return 1 if a["window"].casefold() in title.casefold() else 0
        if a.get("exe"):  # vd Finder = moi cua so cua explorer.exe
            return 2 if name and name == a["exe"].casefold() else 0
        t = Path(os.path.expandvars(a.get("target") or "")).name.casefold()
        if t and t not in GENERIC and name:
            return 2 if name == t else 0
        # chay qua pythonw/wscript/.bat: nhan theo tieu de cua so (vd "Zalo Gateway Launcher")
        return 1 if title.casefold() == (a.get("name") or "").casefold() else 0

    def units(self) -> list[dict]:
        """Moi ung dung co the so khop cua so: muc da pin + thanh vien trong cac nhom."""
        out = []
        for a in list(self.apps):
            if not a.get("kind"):
                out.append(a)
            elif a.get("kind") == "group":
                out += list(a.get("members", []))
        return out

    def watch_running(self):
        n = 0
        while True:
            try:
                pinned = self.units()
                stacks = [g for g in list(self.apps) if g.get("kind") == "group"]
                member_of = {id(m): g for g in stacks for m in g.get("members", [])}
                wins: dict[int, list[int]] = {}
                groups: dict[str, list[int]] = {}
                self.cmd_cache = {}  # dong lenh theo pid, lam moi moi luot quet
                self.aumid_cache = {}
                for h, exe, title, cls in top_windows():
                    levels = [(self.match(a, exe, title, h), a) for a in pinned]
                    best = min((lv for lv, _ in levels if lv), default=0)
                    owners = [a for lv, a in levels if lv and lv == best]
                    if not owners:  # nhom theo loai cua so (vd moi cua so dong lenh -> Terminal)
                        owners = [g for g in stacks if cls in g.get("classes", [])]
                    for a in owners:
                        wins.setdefault(id(a), []).append(h)
                        g = member_of.get(id(a))
                        if g is not None:
                            wins.setdefault(id(g), []).append(h)
                    if not owners and exe:  # khong biet exe thi khong dung icon rieng duoc
                        groups.setdefault(exe, []).append(h)
                names = {exe: file_description(exe) for exe in groups}
                if n % 3 == 0:  # tien trinh khong co cua so (tray, server) - kiem tra thua hon
                    procs = process_names()
                    run = set()
                    for a in pinned:
                        exe = Path(os.path.expandvars(a.get("target") or "")).name.casefold()
                        if (a.get("port") and port_open(int(a["port"]))) or (exe not in GENERIC and exe in procs):
                            run.add(id(a))
                    for g in stacks:
                        if any(id(m) in run for m in g.get("members", [])):
                            run.add(id(g))
                else:
                    run = self.running
                n += 1
                self.ui_call(self.apply_scan, wins, groups, names, run)
            except Exception as e:  # khong de luong nen chet
                log(f"watch_running: {type(e).__name__}: {e}")
            time.sleep(1.5)

    def apply_scan(self, wins, groups, names, run):
        old = {a["target"]: a for a in self.dyn}
        order = [e for e in old if e in groups] + [e for e in groups if e not in old]
        dyn = []
        for exe in order:
            a = old.get(exe) or {"dynamic": True, "name": names[exe], "path": exe, "target": exe, "icon": ""}
            a["hwnds"] = groups[exe]
            dyn.append(a)
        changed = list(old) != order
        drawn = (wins, run, [a["hwnds"] for a in dyn]) != (self.wins, self.running, [a.get("hwnds") for a in self.dyn])
        self.dyn, self.wins, self.running = dyn, wins, run
        if changed:
            self.relayout(save=False)
            if any(not icon_png(a, self.base).exists() for a in dyn):
                threading.Thread(target=self.extract_icons, daemon=True).start()
        elif drawn:
            self.draw()

    def poll_mouse(self):
        # Loi trong mot nhip khong duoc lam chet vong lap (pythonw khong co console de bao loi)
        try:
            self.poll_once()
        except Exception as e:
            log(f"poll_mouse: {type(e).__name__}: {e}")
        self.root.after(120, self.poll_mouse)

    def poll_once(self):
        """Tu an: chuot cham mep tren thi hien, roi xa Dock thi an lai."""
        flag = HERE / "quit.flag"  # thoat sach (tra cho tren man hinh) khi can khoi dong lai Dock tu ben ngoai
        if flag.exists():
            flag.unlink(missing_ok=True)
            log("Thoát theo quit.flag")
            self.quit(restore_taskbar=False)
            return
        show = HERE / "show.flag"  # shortcut Desktop bam lan nua khi Dock dang chay
        if show.exists():
            show.unlink(missing_ok=True)
            self.hold_until = time.time() + 4  # giu Dock hien vai giay du dang tu an
            self.set_hidden(False)
        while not self.ui.empty():
            fn, args = self.ui.get_nowait()
            try:
                fn(*args)
            except Exception as e:
                log(f"ui_call {getattr(fn, '__name__', fn)}: {e}")
        # Win+D / "Show desktop" thu nho ca Dock -> bung lai ngay, khong lay focus
        if ctypes.windll.user32.IsIconic(self.appbar.abd.hWnd):
            ctypes.windll.user32.ShowWindow(self.appbar.abd.hWnd, 4)  # SW_SHOWNOACTIVATE
        now = time.time()
        if now - self.fit_at > 0.4:
            self.fit_at = now
            self.keep_below_dock()
        if self.v_taskbar.get() and now - self.taskbar_at > 1:  # Explorer hay tu hien lai taskbar
            self.taskbar_at = now
            taskbar_show(False)
        if now - self.mons_at > 3:  # cam/rut man hinh, doi do phan giai
            self.mons_at = now
            self.mons = monitors()
            if self.mon not in self.mons:
                self.set_monitor(self.mons[0])
        minute = time.strftime("%H:%M")
        if minute != self.minute:  # o lich + dong ho tren Dock, lich tren hinh nen
            self.minute = minute
            if not self.hidden:
                self.draw()
            self.desk_tick()
        if self.desk is not None and now - self.desk_fetch_at > 600:  # tai lai Google Calendar moi 10 phut
            self.desk_fetch_at = now
            self.desk.refresh()
        busy = (self.menu_open or self.cal is not None or self.stack is not None  # dang mo menu/lich/nhom
                or self.sys_pop is not None or now < self.hold_until)
        if self.fullscreen_app():
            self.set_hidden(True)          # khong de Dock noi tren ung dung toan man hinh
        elif not self.v_autohide.get():
            self.set_hidden(False)
        else:
            pt = wt.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            if now - self.dbg_at > 1 and (HERE / "debug.flag").exists():  # chan doan: tao file debug.flag
                self.dbg_at = now
                log(f"dbg hidden={self.hidden} pt=({pt.x},{pt.y}) panel_x={self.panel_x} "
                    f"edge_at={self.edge_at:.1f} win_y={self.win_y} sw={self.sw} menu={self.menu_open} "
                    f"geom={self.root.winfo_geometry()} state={self.root.state()} n={len(self.apps)}")
            # man hinh dang co con tro
            mon = next((m for m in self.mons if m[0] <= pt.x < m[2] and m[1] <= pt.y < m[3]), self.mon)
            if self.hidden:
                if pt.y <= mon[1] + 2:
                    if mon != self.mon:
                        self.set_monitor(mon)  # Dock di theo man hinh, nhu macOS
                    x1, x2 = self.panel_x
                    if x1 <= pt.x - mon[0] <= x2:
                        if not self.edge_at:
                            self.edge_at = now
                        elif now - self.edge_at >= REVEAL_DELAY:
                            self.set_hidden(False)
                    else:
                        self.edge_at = 0.0
                else:
                    self.edge_at = 0.0
            elif (mon != self.mon or pt.y > self.mon[1] + self.win_h + 10) and not busy:
                if not self.leave_at:
                    self.leave_at = now
                elif now - self.leave_at > 0.6:
                    self.set_hidden(True)
            else:
                self.leave_at = 0.0

    def quit(self, restore_taskbar: bool = True):
        if restore_taskbar and self.v_taskbar.get():
            taskbar_autohide(False)  # thoat Dock thi tra lai taskbar, khong de mat ca hai
            taskbar_show(True)
        elif self.v_taskbar.get():
            taskbar_show(True)  # khoi dong lai Dock: hien tam, ban moi se giau lai (lo Dock moi khong chay)
        if isinstance(self.desk, DesktopWeb):
            self.desk.destroy()  # dong cua so Chrome dang gan tren hinh nen
        self.appbar.remove()
        self.root.destroy()


# ---------------------------------------------------------------- Tim kiem kieu Spotlight

PS_START_APPS = r"""
Get-StartApps | ForEach-Object { [pscustomobject]@{name=$_.Name; id=$_.AppID} } | ConvertTo-Json -Compress
"""
SETTINGS_PAGES = [("Cài đặt Windows", "ms-settings:"), ("Wi-Fi", "ms-settings:network-wifi"),
                  ("Bluetooth", "ms-settings:bluetooth"), ("Màn hình (Display)", "ms-settings:display"),
                  ("Âm thanh (Sound)", "ms-settings:sound"), ("Windows Update", "ms-settings:windowsupdate"),
                  ("Ứng dụng đã cài (Gỡ cài đặt)", "ms-settings:appsfeatures"),
                  ("Máy in", "ms-settings:printers"), ("Hình nền", "ms-settings:personalization-background")]


def norm(s: str) -> str:
    """Bo dau tieng Viet + chu thuong: 'Tài liệu' -> 'tai lieu'."""
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").casefold()


def calc(q: str) -> str | None:
    """Tinh bieu thuc so don gian (+ - * / ^ % ngoac) mot cach an toan, khong dung eval."""
    import ast
    import operator as op
    expr = q.replace(",", ".").replace("^", "**").replace("x", "*").replace("×", "*").replace("÷", "/").strip()
    if not re.fullmatch(r"[\d\s.+\-*/()%]+", expr) or not re.search(r"\d\s*[-+*/%]", expr):
        return None
    ops = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.Pow: op.pow,
           ast.Mod: op.mod, ast.USub: op.neg, ast.UAdd: op.pos}

    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            if isinstance(n.op, ast.Pow) and abs(ev(n.right)) > 100:
                raise ValueError
            return ops[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in ops:
            return ops[type(n.op)](ev(n.operand))
        raise ValueError
    try:
        v = ev(ast.parse(expr, mode="eval").body)
    except Exception:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v:,.10g}".replace(",", " ") if isinstance(v, float) else f"{v:,}".replace(",", ".")


class AppIndex:
    """Danh muc ung dung/tep de tim: Start Menu, app UWP, Desktop, Dock, trang Cai dat."""

    def __init__(self, dock: "Dock"):
        self.dock = dock
        self.items: list[dict] = []
        self.icon_size = round(28 * dock.scale)
        self.rebuild()

    def rebuild(self):
        threading.Thread(target=self._build, daemon=True).start()

    def _build(self):
        items, seen = [], set()

        def add(name, path, sub, kind="app", args="", icon=""):
            key = norm(name)
            if not name or key in seen or "uninstall" in key or "go cai dat" in key or key.startswith("go "):
                return
            seen.add(key)
            items.append({"name": name, "n": key, "path": path, "args": args, "sub": sub, "kind": kind,
                          "target": "", "icon": icon})

        for a in self.dock.units():  # muc trong Dock, ke ca thanh vien cac nhom
            add(a.get("name"), a.get("path", ""), "Trong Dock", args=a.get("args", ""), icon=a.get("icon", ""))
        roots = [Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
                 Path(os.environ.get("PROGRAMDATA", "")) / r"Microsoft\Windows\Start Menu\Programs"]
        for root in roots:
            for f in sorted(root.rglob("*")):
                if f.suffix.lower() in (".lnk", ".url"):
                    add(f.stem, str(f), "Ứng dụng")
        try:
            for r in json.loads(run_ps(PS_START_APPS, []) or "[]"):
                add(r.get("name"), "explorer.exe", "Ứng dụng Windows", kind="uwp",
                    args=f"shell:AppsFolder\\{r.get('id')}")
        except (json.JSONDecodeError, AttributeError):
            pass
        for name, uri in SETTINGS_PAGES:
            add(name, uri, "Cài đặt", kind="uwp")
        for d in desktop_dirs():
            for f in sorted(d.iterdir()):
                if f.name.lower() == "desktop.ini" or f.name.startswith("~$"):
                    continue
                if f.is_dir():
                    add(f.name, str(f), "Thư mục trên Desktop", kind="folder",
                        icon=r"%SystemRoot%\System32\shell32.dll,3")
                    if f.name in ("Ứng dụng", "Tài liệu"):
                        for g in sorted(f.iterdir()):
                            add(g.stem if g.suffix.lower() in (".lnk", ".url") else g.name, str(g),
                                f"Desktop › {f.name}", kind="file" if g.is_file() else "folder")
                else:
                    add(f.stem if f.suffix.lower() in (".lnk", ".url") else f.name, str(f), "Desktop",
                        kind="file")
        self.items = items
        log(f"Chỉ mục tìm kiếm: {len(items)} mục")
        # icon cho ket qua tim kiem (lan dau hoi lau, sau do dung lai tu icons\)
        jobs = []
        for it in items:
            if it["kind"] == "uwp":
                continue
            file, index = icon_source(it)
            png = icon_png(it, self.icon_size)
            if file and os.path.exists(file) and not png.exists():
                jobs.append({"file": file, "index": index, "size": self.icon_size, "out": str(png)})
        if jobs:
            with self.dock.extract_lock:
                run_ps(PS_ICONS, jobs)

    def search(self, q: str, limit: int = 8) -> list[dict]:
        qn = norm(q).strip()
        if not qn:
            return []
        counts = self.dock.s.get("search_counts") or {}
        scored = []
        for it in self.items:
            n = it["n"]
            words = [w for w in re.split(r"[\s\-_.()\[\]›,]+", n) if w]
            if n == qn:
                s = 100
            elif n.startswith(qn):
                s = 85
            elif any(w.startswith(qn) for w in words):
                s = 70
            elif "".join(w[0] for w in words).startswith(qn.replace(" ", "")):
                s = 60  # chu cai dau: "gc" -> Google Chrome
            elif qn in n:
                s = 45
            else:
                continue
            s += min(25, 5 * counts.get(it["n"], 0)) + (5 if it["kind"] in ("app", "uwp") else 0) \
                + (8 if it["sub"] == "Trong Dock" else 0)
            scored.append((-s, len(n), it))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [t[2] for t in scored[:limit]]


class Spotlight(tk.Toplevel):
    """O tim kiem giua man hinh (Alt+Space). Len/Xuong chon, Enter mo, Esc dong."""
    BG, FG, DIM, SEL, LINE = "#1e2230", "#f9fafb", "#9ca3af", "#2563eb", "#3b4256"

    def __init__(self, dock: "Dock"):
        super().__init__(dock.root)
        self.dock = dock
        k = self.k = dock.scale
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=self.LINE)
        self.width = round(640 * k)
        self.results: list[dict] = []
        self.sel = 0
        self.imgs: dict[str, tk.PhotoImage] = {}
        box = tk.Frame(self, bg=self.BG)
        box.pack(padx=1, pady=1, fill="both")
        top = tk.Frame(box, bg=self.BG, padx=round(16 * k), pady=round(12 * k))
        top.pack(fill="x")
        tk.Label(top, text="⌕", bg=self.BG, fg=self.DIM, font=("Segoe UI Symbol", -round(26 * k))).pack(side="left")
        self.q = tk.StringVar()
        self.entry = tk.Entry(top, textvariable=self.q, bg=self.BG, fg=self.FG, insertbackground=self.FG,
                              relief="flat", font=("Segoe UI", -round(22 * k)), width=40)
        self.entry.pack(side="left", fill="x", expand=True, padx=(round(10 * k), 0))
        self.list = tk.Frame(box, bg=self.BG)
        self.list.pack(fill="x")
        self.q.trace_add("write", lambda *_: self.update_results())
        for key, fn in (("<Escape>", lambda e: dock.close_search()), ("<Return>", lambda e: self.open_sel()),
                        ("<Down>", lambda e: self.move(1)), ("<Up>", lambda e: self.move(-1)),
                        ("<Tab>", lambda e: self.move(1) or "break")):
            self.entry.bind(key, fn)
        self.bind("<FocusOut>", self.on_focus_out)
        pt = wt.POINT()
        _u32.GetCursorPos(ctypes.byref(pt))
        mon = next((m for m in monitors() if m[0] <= pt.x < m[2] and m[1] <= pt.y < m[3]), dock.mon)
        self.x = mon[0] + (mon[2] - mon[0] - self.width) // 2
        self.y = mon[1] + (mon[3] - mon[1]) // 5
        self.render()
        self.after(30, self.grab_focus)

    def grab_focus(self):
        hwnd = int(self.wm_frame(), 16)
        focus_window(hwnd)
        self.entry.focus_force()

    def on_focus_out(self, _e):
        def check():
            try:
                inside = self.focus_get() is not None
            except (KeyError, tk.TclError):
                inside = False
            if not inside:
                self.dock.close_search()
        self.after(200, check)

    def update_results(self):
        q = self.q.get()
        self.results = self.dock.index.search(q)
        r = calc(q)
        if r is not None:
            self.results.insert(0, {"name": f"= {r}", "sub": "Máy tính · Enter để chép", "kind": "calc", "value": r})
        if q.strip():
            self.results.append({"name": f"Tìm “{q.strip()}” trên Google", "sub": "Web", "kind": "web", "q": q.strip()})
        self.sel = 0
        self.render()

    def move(self, d: int):
        if self.results:
            self.sel = (self.sel + d) % len(self.results)
            self.render()
        return "break"

    def icon(self, it: dict) -> tk.PhotoImage | None:
        if it["kind"] in ("calc", "web", "uwp"):
            return None
        png = icon_png(it, self.dock.index.icon_size)
        if str(png) not in self.imgs and png.exists():
            try:
                self.imgs[str(png)] = tk.PhotoImage(file=str(png))
            except tk.TclError:
                return None
        return self.imgs.get(str(png))

    def render(self):
        for w in self.list.winfo_children():
            w.destroy()
        k = self.k
        if self.results:
            tk.Frame(self.list, bg=self.LINE, height=1).pack(fill="x")
        for i, it in enumerate(self.results):
            bg = self.SEL if i == self.sel else self.BG
            row = tk.Frame(self.list, bg=bg, padx=round(14 * k), pady=round(6 * k), cursor="hand2")
            row.pack(fill="x")
            img = self.icon(it)
            if img:
                ic = tk.Label(row, image=img, bg=bg)
            else:
                glyph = {"calc": "=", "web": "G"}.get(it["kind"], (it["name"] or "?")[:1].upper())
                ic = tk.Label(row, text=glyph, bg=tile_color(it["name"]) if it["kind"] == "uwp" else "#374151",
                              fg="white", font=("Segoe UI", -round(15 * k), "bold"))
                ic.configure(width=2)
            ic.pack(side="left")
            name = tk.Label(row, text=it["name"], bg=bg, fg=self.FG, font=("Segoe UI", -round(16 * k)), anchor="w")
            name.pack(side="left", padx=(round(10 * k), 0))
            sub = tk.Label(row, text=it.get("sub", ""), bg=bg, fg="#dbeafe" if i == self.sel else self.DIM,
                           font=("Segoe UI", -round(12 * k)))
            sub.pack(side="right")
            for w in (row, ic, name, sub):
                w.bind("<Button-1>", lambda e, i=i: (setattr(self, "sel", i), self.open_sel()))
                w.bind("<Enter>", lambda e, i=i: self.hover(i))
        if not self.results and self.q.get().strip():
            tk.Label(self.list, text="Không tìm thấy", bg=self.BG, fg=self.DIM, pady=round(10 * k),
                     font=("Segoe UI", -round(13 * k))).pack()
        self.update_idletasks()
        self.geometry(f"{self.width}x{self.winfo_reqheight()}+{self.x}+{self.y}")

    def hover(self, i: int):
        if i != self.sel:
            self.sel = i
            self.render()

    def open_sel(self):
        if not self.results:
            return "break"
        it = self.results[self.sel]
        dock = self.dock
        dock.close_search()
        if it["kind"] == "calc":
            dock.root.clipboard_clear()
            dock.root.clipboard_append(it["value"])
            return "break"
        if it["kind"] == "web":
            import urllib.parse
            os.startfile("https://www.google.com/search?q=" + urllib.parse.quote(it["q"]))
            return "break"
        counts = dict(dock.s.get("search_counts") or {})
        counts[it["n"]] = counts.get(it["n"], 0) + 1
        dock.s["search_counts"] = counts
        save_json(SETTINGS_FILE, dock.s)

        def work():
            try:
                if it.get("args"):
                    os.startfile(it["path"], arguments=it["args"])
                else:
                    os.startfile(it["path"])
                log(f"Tìm và mở: {it['name']}")
            except OSError as e:
                dock.ui_call(messagebox.showerror, "Mekong Dock", f"Không mở được {it['name']}:\n{e}")
        threading.Thread(target=work, daemon=True).start()
        return "break"


def start_hotkey(dock: "Dock") -> None:
    """Alt+Space toan he thong -> mo/dong o tim kiem (RegisterHotKey can vong thong diep rieng)."""
    def run():
        if not _u32.RegisterHotKey(None, 1, 0x0001 | 0x4000, 0x20):  # MOD_ALT | MOD_NOREPEAT, VK_SPACE
            log("Không đăng ký được Alt+Space (ứng dụng khác đang dùng, vd PowerToys Run)")
            return
        msg = wt.MSG()
        while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                dock.ui_call(dock.toggle_search)
    threading.Thread(target=run, daemon=True, name="hotkey").start()


def desktop_layer() -> int:
    """Cua so WorkerW nam giua hinh nen va icon Desktop (cach Wallpaper Engine lam). 0 neu khong tim duoc."""
    progman = _u32.FindWindowW("Progman", None)
    res = ctypes.c_size_t()
    _u32.SendMessageTimeoutW(progman, 0x052C, 0xD, 0x1, 0, 1000, ctypes.byref(res))  # yeu cau tao WorkerW
    _u32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 1000, ctypes.byref(res))
    found = []
    _u32.FindWindowExW.restype = wt.HWND

    def cb(h, _):
        if _u32.FindWindowExW(h, None, "SHELLDLL_DefView", None):
            w = _u32.FindWindowExW(None, h, "WorkerW", None)  # WorkerW ngay sau cua so chua icon
            if w:
                found.append(w)
        return True
    _u32.EnumWindows(_ENUM_PROC(cb), 0)
    return found[0] if found else progman


CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


class DesktopWeb:
    """Trang Google Calendar nhung (iframe embed) dan len hinh nen: mo bang Chrome --app (dung phien dang nhap
    Google san co nen thay ca lich rieng), roi gan cua so vao lop WorkerW sau icon Desktop. Chi de xem."""
    RELOAD = 1800  # tai lai trang moi 30 phut

    TITLE = 28  # thanh tieu de Chrome tu ve trong cua so app (px o 100%) - bi khung chua che mat

    def __init__(self, dock: "Dock", url: str):
        self.dock, self.url = dock, url
        self.hwnd = 0
        self.started = time.time()
        # khung chua gan tren hinh nen; Chrome nam trong khung, day len tren de giau thanh tieu de
        self.box = tk.Toplevel(dock.root)
        self.box.overrideredirect(True)
        self.box.configure(bg="#ffffff")
        x, y, w, h = self.rect()
        vx, vy = _u32.GetSystemMetrics(76), _u32.GetSystemMetrics(77)
        self.box.geometry(f"{w}x{h}+{x - vx}+{y - vy}")
        self.box.update_idletasks()
        self.box_hwnd = int(self.box.wm_frame(), 16)
        self.parent = desktop_layer()
        self.close_orphans()
        _u32.SetParent(self.box_hwnd, self.parent)
        self.box.geometry(f"{w}x{h}+{x - vx}+{y - vy}")
        self.launch()

    def rect(self) -> tuple[int, int, int, int]:
        mon, k = monitors()[0], self.dock.scale
        w = min(round(820 * k), int((mon[2] - mon[0]) * 0.56))
        h = min(round(620 * k), mon[3] - mon[1] - round(130 * k))
        return mon[2] - w - round(24 * k), mon[1] + round(92 * k), w, h

    def chrome_windows(self) -> set[int]:
        return {h for h, exe, _, cls in top_windows()
                if cls == "Chrome_WidgetWin_1" and Path(exe).name.casefold() == "chrome.exe"}

    def launch(self):
        before = self.chrome_windows()
        x, y, w, h = self.rect()
        subprocess.Popen([CHROME, f"--app={self.url}", f"--window-size={w},{h}", f"--window-position={x},{y}",
                          "--no-first-run"], creationflags=NO_WINDOW)

        def wait():  # cho cua so app moi cua Chrome xuat hien roi gan vao hinh nen
            for _ in range(60):
                time.sleep(0.5)
                new = self.chrome_windows() - before
                if new:
                    self.dock.ui_call(self.attach, new.pop())
                    return
            log("Lịch nhúng: Chrome không mở được cửa sổ app")
        threading.Thread(target=wait, daemon=True).start()

    def attach(self, hwnd: int):
        self.hwnd = hwnd
        _, _, w, h = self.rect()
        crop = round(self.TITLE * self.dock.scale)
        _u32.SetWindowLongW(hwnd, -16, 0x40000000 | 0x10000000)  # WS_CHILD | WS_VISIBLE
        ex = _u32.GetWindowLongW(hwnd, -20)
        _u32.SetWindowLongW(hwnd, -20, (ex | 0x80) & ~0x40000)  # WS_EX_TOOLWINDOW, bo WS_EX_APPWINDOW
        _u32.SetParent(hwnd, self.box.winfo_id())
        # lon hon khung vai px moi phia: vien cua so Chrome nam ngoai, khong de lo khoang toi
        b = round(4 * self.dock.scale)
        _u32.SetWindowPos(hwnd, 0, -b, -crop, w + 2 * b + 16, h + crop + b + 16,
                          0x0040 | 0x0020)  # SWP_SHOWWINDOW | SWP_FRAMECHANGED
        self.started = time.time()
        log("Lịch nhúng: đã gắn lên hình nền")

    def alive(self) -> bool:
        if not (_u32.IsWindow(self.parent) and _u32.IsWindow(self.box_hwnd)):
            return False
        if not self.hwnd:  # dang cho Chrome mo cua so
            return time.time() - self.started < 40
        return bool(_u32.IsWindow(self.hwnd))

    def tick(self):
        if self.hwnd and time.time() - self.started > self.RELOAD:  # trang nhung khong tu cap nhat
            self.close_chrome()
            self.started = time.time()
            self.launch()

    def refresh(self):
        pass  # trang tu tai qua tick()

    def close_orphans(self):
        """Dong cua so Chrome con sot lai tren hinh nen tu lan Dock truoc (bi tat dot ngot)."""
        cls = ctypes.create_unicode_buffer(64)
        _u32.GetParent.restype = wt.HWND
        found = []

        def cb(h, _):
            _u32.GetClassNameW(h, cls, 64)
            if cls.value == "Chrome_WidgetWin_1" and _u32.GetParent(h) is not None:
                found.append(h)
            return True
        _u32.EnumChildWindows(self.parent, _ENUM_PROC(cb), 0)
        for h in found:  # chi cap con truc tiep cua WorkerW hoac cua khung Tk cu
            p = _u32.GetParent(h)
            if p == self.parent or (p and _u32.GetParent(p) == self.parent):
                _u32.PostMessageW(h, 0x0010, 0, 0)
        if found:
            log(f"Lịch nhúng: đóng {len(found)} cửa sổ cũ trên hình nền")

    def close_chrome(self):
        if self.hwnd and _u32.IsWindow(self.hwnd):
            _u32.PostMessageW(self.hwnd, 0x0010, 0, 0)  # WM_CLOSE
        self.hwnd = 0

    def destroy(self):
        self.close_chrome()
        try:
            self.box.destroy()
        except tk.TclError:
            pass


class DesktopCalendar(tk.Toplevel):
    """The lich + su kien Google Calendar dan len hinh nen (sau icon Desktop, Win+D khong an)."""
    BG, FG, DIM, LINE, NOW = "#141824", "#f3f4f6", "#9ca3af", "#2a3040", "#1d4ed8"
    COLORS = ["#60a5fa", "#f87171", "#34d399", "#fbbf24", "#c084fc", "#f472b6"]

    def __init__(self, dock: "Dock"):
        super().__init__(dock.root)
        self.dock = dock
        self.overrideredirect(True)
        self.configure(bg=self.BG)
        k = dock.scale
        self.k = k
        self.f = lambda size, *st: ("Segoe UI", -round(size * k), *st)
        self.width = round(330 * k)
        self.events: list = []
        self.errors: list[str] = []
        self.fetched = ""
        self.body = tk.Frame(self, bg=self.BG, padx=round(18 * k), pady=round(16 * k))
        self.body.pack(fill="both", expand=True)
        self.render()
        self.update_idletasks()
        self.parent = desktop_layer()
        self.hwnd = int(self.wm_frame(), 16)
        _u32.SetParent(self.hwnd, self.parent)
        self.place_on_desktop()
        self.minute = ""
        self.refresh()

    def place_on_desktop(self):
        mon = monitors()[0]  # man hinh chinh
        self.update_idletasks()
        w, h = self.width, self.winfo_reqheight()
        x = mon[2] - w - round(28 * self.k)
        y = mon[1] + round(96 * self.k)
        # toa do cua WorkerW tinh tu goc man hinh ao (co the am khi co man hinh ben trai)
        vx, vy = _u32.GetSystemMetrics(76), _u32.GetSystemMetrics(77)
        self.geometry(f"{w}x{h}+{x - vx}+{y - vy}")

    def alive(self) -> bool:
        return bool(_u32.IsWindow(self.hwnd)) and bool(_u32.IsWindow(self.parent))

    def refresh(self):
        """Tai lai lich (luong nen) - goi luc dau va moi 10 phut."""
        urls = self.dock.s.get("calendars") or [gcal.HOLIDAYS_VN]

        def work():
            events, errors = gcal.upcoming(urls, days=14)
            self.dock.ui_call(self.apply, events, errors)
        threading.Thread(target=work, daemon=True).start()

    def apply(self, events, errors):
        if events or not errors:   # loi mang: giu su kien cu
            self.events = events
        self.errors = errors
        self.fetched = time.strftime("%H:%M")
        for e in errors:
            log(f"Google Calendar: {e}")
        self.render()

    def tick(self):
        m = time.strftime("%H:%M")
        if m != self.minute:
            self.minute = m
            self.render()

    def render(self):
        for w in self.body.winfo_children():
            w.destroy()
        b, k, today = self.body, self.k, dt.date.today()
        ld, lm, ly, leap = lunar(today)
        top = tk.Frame(b, bg=self.BG)
        top.pack(fill="x")
        tk.Label(top, text=str(today.day), bg=self.BG, fg=self.FG, font=self.f(54, "bold")).pack(side="left")
        info = tk.Frame(top, bg=self.BG)
        info.pack(side="left", padx=(round(12 * k), 0), anchor="center")
        tk.Label(info, text=THU[today.weekday()], bg=self.BG, fg="#f87171", font=self.f(17, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(info, text=f"Tháng {today.month}, {today.year}", bg=self.BG, fg=self.FG, font=self.f(14),
                 anchor="w").pack(fill="x")
        tk.Label(info, text=f"Âm lịch {ld}/{lm}{' nhuận' if leap else ''} · {can_chi(ly)}", bg=self.BG,
                 fg=self.DIM, font=self.f(12), anchor="w").pack(fill="x")
        tk.Frame(b, bg=self.LINE, height=1).pack(fill="x", pady=(round(12 * k), round(8 * k)))
        tk.Label(b, text="LỊCH SẮP TỚI", bg=self.BG, fg=self.DIM, font=self.f(11, "bold"), anchor="w").pack(fill="x")

        now = dt.datetime.now()
        cal_color: dict[str, str] = {}
        shown, last_day, rows = 0, None, 0
        for e in self.events:
            if e.end <= now and not (e.all_day and e.start.date() == today):
                continue  # da xong
            day = max(e.start.date(), today)
            if rows >= 12:
                break
            if day != last_day:
                last_day = day
                label = "Hôm nay" if day == today else "Ngày mai" if day == today + dt.timedelta(days=1) \
                    else f"{THU[day.weekday()]}, {day.day}/{day.month}"
                tk.Label(b, text=label, bg=self.BG, fg=self.FG, font=self.f(13, "bold"),
                         anchor="w").pack(fill="x", pady=(round(8 * k), round(2 * k)))
            color = cal_color.setdefault(e.calendar, self.COLORS[len(cal_color) % len(self.COLORS)])
            ongoing = not e.all_day and e.start <= now < e.end
            row = tk.Frame(b, bg=self.NOW if ongoing else self.BG)
            row.pack(fill="x", pady=1)
            tk.Frame(row, bg=color, width=round(3 * k)).pack(side="left", fill="y", padx=(0, round(8 * k)))
            when = "Cả ngày" if e.all_day else f"{e.start:%H:%M}–{e.end:%H:%M}"
            if ongoing:
                when = "Đang diễn ra · " + when
            col = tk.Frame(row, bg=row["bg"])
            col.pack(side="left", fill="x", expand=True)
            title = e.title if len(e.title) <= 42 else e.title[:40] + "…"
            tk.Label(col, text=title, bg=row["bg"], fg=self.FG, font=self.f(13), anchor="w").pack(fill="x")
            sub = when + (f" · {e.location[:30]}" if e.location else "")
            tk.Label(col, text=sub, bg=row["bg"], fg="#dbeafe" if ongoing else self.DIM, font=self.f(11),
                     anchor="w").pack(fill="x")
            shown += 1
            rows += 1
        if not shown:
            tk.Label(b, text="Không có sự kiện trong 14 ngày tới", bg=self.BG, fg=self.DIM, font=self.f(13),
                     anchor="w").pack(fill="x", pady=(round(6 * k), 0))
        tk.Frame(b, bg=self.LINE, height=1).pack(fill="x", pady=(round(12 * k), round(6 * k)))
        private = len(self.dock.s.get("calendars") or []) > 1
        foot = ("Cập nhật " + self.fetched if self.fetched else "Đang tải lịch…")
        if self.errors:
            foot += " · lỗi tải một lịch (xem dock.log)"
        if not private:
            foot += "\nChỉ có ngày lễ. Thêm lịch riêng: chuột phải Dock → Kết nối Google Calendar"
        tk.Label(b, text=foot, bg=self.BG, fg="#6b7280", font=self.f(10), anchor="w", justify="left",
                 wraplength=self.width - round(36 * k)).pack(fill="x")
        if hasattr(self, "hwnd"):
            self.place_on_desktop()  # chieu cao doi theo so su kien


NET_LINKS = [("Chọn mạng Wi-Fi…", "ms-availablenetworks:"), ("Cài đặt Wi-Fi", "ms-settings:network-wifi"),
             ("Mạng & Internet", "ms-settings:network-status"), ("Bluetooth", "ms-settings:bluetooth"),
             ("VPN", "ms-settings:network-vpn"), ("Chế độ máy bay", "ms-settings:network-airplanemode")]


class SystemPopup(tk.Toplevel):
    """Bang am luong (thanh keo, tat tieng) hoac ket noi (mang hien tai + loi tat cai dat Windows)."""
    BG, FG, DIM, HOV, LINE, ACC = "#1e2230", "#f3f4f6", "#9ca3af", "#2b3348", "#3b4256", "#3b82f6"

    def __init__(self, dock: "Dock", kind: str, anchor_x: float, top: int):
        super().__init__(dock.root)
        self.dock, self.kind = dock, kind
        k = self.k = dock.scale
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=self.LINE)
        self.body = tk.Frame(self, bg=self.BG, padx=round(14 * k), pady=round(10 * k))
        self.body.pack(padx=1, pady=1)
        self.f = lambda size, *st: ("Segoe UI", -round(size * k), *st)
        self.dragging = False
        (self.build_volume if kind == "volume" else self.build_network)()
        self.update_idletasks()
        w, mon = self.winfo_reqwidth(), dock.mon
        self.geometry(f"+{int(min(max(anchor_x - w / 2, mon[0] + 8), mon[2] - w - 8))}+{top}")
        self.bind("<Escape>", lambda e: dock.close_sys())
        self.bind("<FocusOut>", self.on_focus_out)
        self.after(50, self.focus_force)

    def header(self, text):
        tk.Label(self.body, text=text, bg=self.BG, fg=self.DIM, font=self.f(11, "bold"),
                 anchor="w").pack(fill="x", pady=(0, round(6 * self.k)))

    def link(self, text, command):
        lb = tk.Label(self.body, text=text, bg=self.BG, fg=self.FG, font=self.f(13), anchor="w", cursor="hand2",
                      padx=round(6 * self.k), pady=round(4 * self.k))
        lb.pack(fill="x")
        lb.bind("<Button-1>", lambda e: (self.dock.close_sys(), command()))
        lb.bind("<Enter>", lambda e: lb.configure(bg=self.HOV))
        lb.bind("<Leave>", lambda e: lb.configure(bg=self.BG))

    # --- am luong ---
    def build_volume(self):
        k, (lv, _, name) = self.k, self.dock.vol
        self.header("ÂM LƯỢNG")
        if lv < 0:
            tk.Label(self.body, text="Không tìm thấy loa / tai nghe", bg=self.BG, fg=self.DIM,
                     font=self.f(13)).pack(anchor="w")
        else:
            tk.Label(self.body, text=name or "Loa mặc định", bg=self.BG, fg=self.FG, font=self.f(13),
                     anchor="w").pack(fill="x")
            row = tk.Frame(self.body, bg=self.BG)
            row.pack(fill="x", pady=round(8 * k))
            self.mute_btn = tk.Label(row, text="", bg=self.BG, fg=self.FG, font=("Segoe UI Symbol", -round(20 * k)),
                                     cursor="hand2", width=2)
            self.mute_btn.pack(side="left")
            self.mute_btn.bind("<Button-1>", lambda e: (self.dock.set_volume(mute=not self.dock.vol[1]), self.sync()))
            self.scale = tk.Scale(row, from_=0, to=100, orient="horizontal", length=round(240 * k), showvalue=False,
                                  bg=self.BG, fg=self.FG, troughcolor="#374151", highlightthickness=0, bd=0,
                                  sliderrelief="flat", activebackground=self.ACC, command=self.on_scale)
            self.scale.pack(side="left", padx=(round(6 * k), 0))
            self.scale.bind("<ButtonPress-1>", lambda e: setattr(self, "dragging", True))
            # tha chuot: ap dung gia tri cuoi roi moi tat co (bam vao ranh truot cung tinh)
            self.scale.bind("<ButtonRelease-1>", lambda e: (self.on_scale(self.scale.get()),
                                                            setattr(self, "dragging", False)))
            self.pct = tk.Label(row, text="", bg=self.BG, fg=self.FG, font=self.f(13), width=4)
            self.pct.pack(side="left")
            self.sync()
        tk.Frame(self.body, bg=self.LINE, height=1).pack(fill="x", pady=round(6 * k))
        self.link("Bộ trộn âm lượng (từng ứng dụng)", lambda: os.startfile("sndvol.exe"))
        self.link("Chọn loa / tai nghe khác…", lambda: os.startfile("ms-settings:sound"))

    def on_scale(self, value):
        if not self.dragging:  # chi doi am luong khi nguoi dung keo/bam thanh truot, khong theo scale.set()
            return
        v = int(float(value))
        if v != self.dock.vol[0]:
            self.dock.set_volume(percent=v, mute=False if v > 0 else None)
            self.pct.configure(text=f"{v}%")
            self.mute_btn.configure(text="🔊")

    def sync(self):
        """Cap nhat bang theo am luong hien tai (doi bang phim, lan chuot tren Dock...)."""
        if self.kind != "volume":
            return self.build_network_status()
        lv, mute, _ = self.dock.vol
        if lv < 0 or not hasattr(self, "scale"):
            return
        if not self.dragging:
            self.scale.set(lv)
        self.pct.configure(text=f"{lv}%")
        self.mute_btn.configure(text="🔇" if mute else "🔊", fg="#f87171" if mute else self.FG)

    # --- ket noi ---
    def build_network(self):
        self.header("KẾT NỐI")
        self.status = tk.Frame(self.body, bg=self.BG)
        self.status.pack(fill="x")
        self.build_network_status()
        tk.Frame(self.body, bg=self.LINE, height=1).pack(fill="x", pady=round(6 * self.k))
        for label, uri in NET_LINKS:
            self.link(label, lambda u=uri: os.startfile(u))

    def build_network_status(self):
        for w in self.status.winfo_children():
            w.destroy()
        n = self.dock.net
        if not n:
            line, sub, color = "Đang kiểm tra…", "", self.DIM
        elif not n.get("online"):
            line, sub, color = "Không có Internet", "Kiểm tra Wi-Fi hoặc dây mạng", "#f87171"
        elif n.get("ssid"):
            line, sub, color = f"Wi-Fi: {n['ssid']}", f"Sóng {n['signal']}% · đã kết nối Internet", "#34d399"
        else:
            line, sub, color = "Ethernet (có dây)", "Đã kết nối Internet", "#34d399"
        tk.Label(self.status, text="● " + line, bg=self.BG, fg=color, font=self.f(14, "bold"),
                 anchor="w").pack(fill="x")
        if sub:
            tk.Label(self.status, text=sub, bg=self.BG, fg=self.DIM, font=self.f(11), anchor="w").pack(fill="x")

    def on_focus_out(self, _e):
        def check():
            try:
                inside = self.focus_get() is not None
            except (KeyError, tk.TclError):
                inside = False
            if not inside:
                self.dock.close_sys()
        self.after(150, check)


class StackPopup(tk.Toplevel):
    """Bang nhom (kieu Stack macOS): moi thanh vien + cua so dang mo. Bam ten de chuyen/mo, bam cua so de chuyen."""
    BG, FG, DIM, HOV, LINE = "#1e2230", "#f3f4f6", "#9ca3af", "#2b3348", "#3b4256"

    def __init__(self, dock: "Dock", group: dict, anchor_x: float, top: int,
                 windows: list[int] | None = None, app: dict | None = None):
        """windows + app: bang cua so cua MOT ung dung (bam icon co tu 2 cua so tro len)."""
        super().__init__(dock.root)
        self.dock, self.group = dock, group
        k = self.k = dock.scale
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=self.LINE)
        self.imgs: list[tk.PhotoImage] = []
        body = tk.Frame(self, bg=self.BG, padx=round(10 * k), pady=round(8 * k))
        body.pack(padx=1, pady=1)
        tk.Label(body, text=group.get("name", "Nhóm").upper(), bg=self.BG, fg=self.DIM,
                 font=("Segoe UI", -round(11 * k), "bold"), anchor="w").pack(fill="x", pady=(0, round(4 * k)))
        claimed: set[int] = set()
        title = ctypes.create_unicode_buffer(256)
        if windows is not None:
            for h in windows[:15]:
                _u32.InternalGetWindowText(h, title, 256)
                t = title.value if len(title.value) <= 60 else title.value[:58] + "…"
                self.row(body, t, "", lambda h=h: self.pick_window(h), app=app)
            tk.Frame(body, bg=self.LINE, height=1).pack(fill="x", pady=round(4 * k))
            self.row(body, "＋  Mở cửa sổ mới", "", lambda: (dock.close_stack(), dock.launch_app(app)))
        for m in group.get("members", []):
            hwnds = [h for h in dock.windows_of(m) if _u32.IsWindow(h)]
            claimed.update(hwnds)
            running = bool(hwnds) or id(m) in dock.running
            status = f"{len(hwnds)} cửa sổ" if hwnds else ("đang chạy" if running else "chưa mở")
            self.row(body, m.get("name", ""), status, lambda m=m: self.pick(m), app=m, bold=True, dot=running)
            for h in hwnds[:8]:
                _u32.InternalGetWindowText(h, title, 256)
                self.window_row(body, h, title.value)
        others = [h for h in dock.windows_of(group) if h not in claimed and _u32.IsWindow(h)]
        if others:  # cua so nhan theo loai (vd console mysqld) khong thuoc thanh vien nao
            tk.Frame(body, bg=self.LINE, height=1).pack(fill="x", pady=round(4 * k))
            tk.Label(body, text="Cửa sổ khác", bg=self.BG, fg=self.DIM, font=("Segoe UI", -round(11 * k)),
                     anchor="w").pack(fill="x")
            for h in others[:10]:
                _u32.InternalGetWindowText(h, title, 256)
                self.window_row(body, h, title.value)
        self.update_idletasks()
        w = self.winfo_reqwidth()
        mon = dock.mon
        x = int(min(max(anchor_x - w / 2, mon[0] + 8), mon[2] - w - 8))
        self.geometry(f"+{x}+{top}")
        self.bind("<Escape>", lambda e: dock.close_stack())
        self.bind("<FocusOut>", self.on_focus_out)
        self.after(50, self.focus_force)

    def row(self, parent, text, status, command, app=None, bold=False, dot=False):
        k = self.k
        r = tk.Frame(parent, bg=self.BG, padx=round(6 * k), pady=round(4 * k), cursor="hand2")
        r.pack(fill="x")
        widgets = [r]
        img = None
        if app is not None:
            png = icon_png(app, round(32 * k))
            if png.exists():
                try:
                    img = tk.PhotoImage(file=str(png))
                    self.imgs.append(img)
                except tk.TclError:
                    img = None
            ic = tk.Label(r, image=img, bg=self.BG) if img else tk.Label(
                r, text=(text or "?")[:1].upper(), width=2, bg=tile_color(text), fg="white",
                font=("Segoe UI", -round(14 * k), "bold"))
            ic.pack(side="left", padx=(0, round(8 * k)))
            widgets.append(ic)
        lb = tk.Label(r, text=("● " if dot else "") + text, bg=self.BG, fg=self.FG, anchor="w",
                      font=("Segoe UI", -round(14 * k), "bold" if bold else "normal"))
        lb.pack(side="left")
        st = tk.Label(r, text=status, bg=self.BG, fg=self.DIM, font=("Segoe UI", -round(11 * k)))
        st.pack(side="right", padx=(round(16 * k), 0))
        widgets += [lb, st]
        tint = [r, lb, st] + ([widgets[1]] if img else [])  # o chu cai giu mau rieng
        for wdg in widgets:
            wdg.bind("<Button-1>", lambda e: command())
            wdg.bind("<Enter>", lambda e: [x.configure(bg=self.HOV) for x in tint])
            wdg.bind("<Leave>", lambda e: [x.configure(bg=self.BG) for x in tint])

    def window_row(self, parent, h, title):
        t = title if len(title) <= 56 else title[:54] + "…"
        self.row(parent, "    ▢ " + t, "", lambda h=h: self.pick_window(h))

    def pick(self, m: dict):
        self.dock.close_stack()
        self.dock.focus_app(m)

    def pick_window(self, h: int):
        self.dock.close_stack()
        focus_window(h)

    def on_focus_out(self, _e):
        def check():
            try:
                inside = self.focus_get() is not None
            except (KeyError, tk.TclError):
                inside = False
            if not inside:
                self.dock.close_stack()
        self.after(150, check)


class CalendarPopup(tk.Toplevel):
    """Lich thang (duong + am) hien ngay duoi Dock. Esc / bam ra ngoai / bam lai o lich de dong."""
    BG, FG, DIM, SUN, TODAY, CELL = "#1e2230", "#f3f4f6", "#6b7280", "#f87171", "#2563eb", "#262b3b"

    def __init__(self, dock: "Dock", anchor_x: float, top: int):
        super().__init__(dock.root)
        self.dock = dock
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=PANEL_LINE)
        k = dock.scale
        self.f_big = ("Segoe UI", -round(15 * k))
        self.f_small = ("Segoe UI", -round(10 * k))
        self.f_head = ("Segoe UI", -round(15 * k), "bold")
        self.cell = round(46 * k)
        self.month = dt.date.today().replace(day=1)
        self.body = tk.Frame(self, bg=self.BG, padx=round(12 * k), pady=round(10 * k))
        self.body.pack(padx=1, pady=1)
        self.render()
        self.update_idletasks()
        w = self.winfo_reqwidth()
        mon = dock.mon
        x = int(min(max(anchor_x - w / 2, mon[0] + 8), mon[2] - w - 8))
        self.geometry(f"+{x}+{top}")
        self.bind("<Escape>", lambda e: dock.close_calendar())
        self.bind("<FocusOut>", self.on_focus_out)
        self.after(50, self.focus_force)

    def on_focus_out(self, _e):
        # FocusOut ca khi focus chuyen giua cac nut con -> doi mot chut roi xem focus con trong lich khong
        def check():
            try:
                inside = self.focus_get() is not None
            except (KeyError, tk.TclError):
                inside = False
            if not inside:
                self.dock.close_calendar()
        self.after(150, check)

    def shift(self, months: int):
        y, m = divmod(self.month.month - 1 + months, 12)
        self.month = dt.date(self.month.year + y, m + 1, 1)
        self.render()

    def render(self):
        for w in self.body.winfo_children():
            w.destroy()
        today, first = dt.date.today(), self.month
        head = tk.Frame(self.body, bg=self.BG)
        head.grid(row=0, column=0, columnspan=7, sticky="we", pady=(0, 6))
        btn = dict(bg=self.BG, fg=self.FG, activebackground=self.CELL, activeforeground=self.FG, bd=0,
                   font=self.f_head, cursor="hand2", padx=8)
        tk.Button(head, text="‹", command=lambda: self.shift(-1), **btn).pack(side="left")
        tk.Label(head, text=f"Tháng {first.month}, {first.year}", bg=self.BG, fg=self.FG,
                 font=self.f_head).pack(side="left", expand=True)
        tk.Button(head, text="›", command=lambda: self.shift(1), **btn).pack(side="right")
        for c, name in enumerate(["T2", "T3", "T4", "T5", "T6", "T7", "CN"]):
            tk.Label(self.body, text=name, bg=self.BG, fg=self.SUN if c == 6 else self.DIM,
                     font=self.f_small, width=4).grid(row=1, column=c)
        start = first - dt.timedelta(days=first.weekday())
        for n in range(42):
            d = start + dt.timedelta(days=n)
            if n == 35 and d.month != first.month:
                break  # thang chi can 5 hang
            ld, lm, _, _ = lunar(d)
            is_today = d == today
            bg = self.TODAY if is_today else self.BG
            fg = self.FG if d.month == first.month else self.DIM
            if d.weekday() == 6 and not is_today and d.month == first.month:
                fg = self.SUN
            cell = tk.Frame(self.body, bg=bg, width=self.cell, height=self.cell)
            cell.grid(row=2 + n // 7, column=n % 7, padx=1, pady=1)
            cell.pack_propagate(False)
            tk.Label(cell, text=str(d.day), bg=bg, fg=fg, font=self.f_big).pack(pady=(2, 0))
            # mung 1 am lich ghi ca thang
            tk.Label(cell, text=f"{ld}/{lm}" if ld == 1 else str(ld), bg=bg,
                     fg="#fca5a5" if ld == 1 else ("#dbeafe" if is_today else self.DIM),
                     font=self.f_small).pack()
        ld, lm, ly, leap = lunar(today)
        foot = tk.Frame(self.body, bg=self.BG)
        foot.grid(row=9, column=0, columnspan=7, sticky="we", pady=(8, 0))
        tk.Label(foot, text=f"{THU[today.weekday()]}, {today.day}/{today.month}/{today.year}\n"
                            f"Âm lịch {ld}/{lm}{' nhuận' if leap else ''} năm {can_chi(ly)}",
                 bg=self.BG, fg=self.FG, font=self.f_small, justify="left").pack(side="left")
        small = dict(btn, font=self.f_small, padx=6)
        tk.Button(foot, text="Google Calendar ↗", command=lambda: os.startfile(GOOGLE_CAL), **small).pack(side="right")
        if (first.year, first.month) != (today.year, today.month):
            tk.Button(foot, text="Hôm nay", command=lambda: self.shift(
                (today.year - first.year) * 12 + today.month - first.month), **small).pack(side="right")


def main():
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "MekongDock_single_instance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        (HERE / "show.flag").touch()  # Dock dang chay: bao no hien ra (bam shortcut lan nua)
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    root.report_callback_exception = lambda typ, val, tb: log(
        "Tk: " + "".join(traceback.format_exception(typ, val, tb)).strip().replace("\n", " | "))
    root.title("Mekong Dock")
    dock = Dock(root)
    try:
        root.mainloop()
    finally:
        dock.appbar.remove()
        del mutex


if __name__ == "__main__":
    main()
