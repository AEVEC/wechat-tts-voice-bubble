from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import queue
import re
import sys
import tempfile
import threading
import time
import tkinter as tk
from ctypes import wintypes
from io import BytesIO
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
import psutil
import pyautogui
import sounddevice as sd
from affine import Affine
from PIL import Image
from resvg import render, usvg

import wechat_tts_voice as engine


APP_NAME = "微信 TTS 挂件"
CONFIG_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / "WeChatTTSWidget"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "widget.log"
ASSET_DIR = Path(__file__).resolve().parent / "assets"
ICON_DIR = ASSET_DIR / "material_symbols"
APP_ICON_PATH = ASSET_DIR / "app-icon.ico"
DEFAULT_GEOMETRY = "500x550+40+120"
WECHAT_NAMES = {"weixin.exe", "wechat.exe"}

SURFACE = "#F6FAF7"
CARD = "#FFFFFF"
TEXT = "#17221C"
MUTED = "#6F7C74"
PRIMARY = "#0A7A52"
PRIMARY_HOVER = "#086844"
ACTION = "#078A59"
ACTION_HOVER = "#06764C"
SUCCESS = "#12B76A"
BORDER = "#DDE7E1"
TONAL = "#EAF6EF"
ERROR = "#B84235"
ERROR_SURFACE = "#FFF0ED"
FONT_FAMILY = "Microsoft YaHei UI"

WM_APP = 0x8000
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_HOTKEY = 0x0312
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_NULL = 0x0000
TRAY_CALLBACK_MESSAGE = WM_APP + 1
TRAY_ICON_ID = 1
HOTKEY_ID = 0x5754
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_Z = 0x5A
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
IDI_APPLICATION = 32512
TRAY_MENU_SHOW = 1001
TRAY_MENU_QUICK = 1002
TRAY_MENU_EXIT = 1003

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WndClass(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NotifyIconData(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeout", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)

ctk.set_appearance_mode("light")


def normalized_geometry(value: str) -> str:
    """Keep the saved position while migrating old compact window sizes."""
    match = re.fullmatch(r"\s*(\d+)x(\d+)([+-]\d+)([+-]\d+)\s*", value)
    if not match:
        return DEFAULT_GEOMETRY
    width, height, left, top = match.groups()
    return f"{max(500, int(width))}x{max(550, int(height))}{left}{top}"


def svg_icon(name: str, color: str, size: int = 24) -> ctk.CTkImage:
    """Render an official Material Symbol SVG into a DPI-friendly Tk image."""
    svg_path = ICON_DIR / f"{name}.svg"
    svg_text = svg_path.read_text(encoding="utf-8")
    render_size = size * 4
    svg_text = svg_text.replace('height="24"', f'height="{render_size}"', 1)
    svg_text = svg_text.replace('width="24"', f'width="{render_size}"', 1)
    svg_text = svg_text.replace("<path ", f'<path fill="{color}" ', 1)

    tree = usvg.Tree.from_str(svg_text, usvg.Options.default())
    png_data = bytes(render(tree, Affine.identity()[0:6]))
    with Image.open(BytesIO(png_data)) as rendered:
        image = rendered.convert("RGBA").resize(
            (size, size), Image.Resampling.LANCZOS
        )
    return ctk.CTkImage(light_image=image, dark_image=image, size=(size, size))


class DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def protect_secret(value: str) -> str:
    if not value:
        return ""
    raw = value.encode("utf-8")
    raw_buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(
        len(raw), ctypes.cast(raw_buffer, ctypes.POINTER(ctypes.c_byte))
    )
    encrypted = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source),
        APP_NAME,
        None,
        None,
        None,
        0,
        ctypes.byref(encrypted),
    ):
        raise ctypes.WinError()
    try:
        result = ctypes.string_at(encrypted.pbData, encrypted.cbData)
        return base64.b64encode(result).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(encrypted.pbData)


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    raw = base64.b64decode(value)
    raw_buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(
        len(raw), ctypes.cast(raw_buffer, ctypes.POINTER(ctypes.c_byte))
    )
    decrypted = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(decrypted),
    ):
        raise ctypes.WinError()
    try:
        result = ctypes.string_at(decrypted.pbData, decrypted.cbData)
        return result.decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(decrypted.pbData)


class AppConfig:
    def __init__(self) -> None:
        self.tts_url = engine.DEFAULT_TTS_URL
        self.token = ""
        self.geometry = DEFAULT_GEOMETRY
        self.load()

    def load(self) -> None:
        if not CONFIG_FILE.is_file():
            return
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            self.tts_url = str(data.get("tts_url") or engine.DEFAULT_TTS_URL)
            self.geometry = normalized_geometry(
                str(data.get("geometry") or DEFAULT_GEOMETRY)
            )
            self.token = unprotect_secret(str(data.get("token_dpapi") or ""))
        except Exception:
            logging.exception("读取配置失败")

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "tts_url": self.tts_url,
            "token_dpapi": protect_secret(self.token),
            "geometry": self.geometry,
        }
        CONFIG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.RegisterClassW.argtypes = [ctypes.POINTER(WndClass)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.UnregisterClassW.restype = wintypes.BOOL
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.DefWindowProcW.restype = LRESULT
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.RegisterHotKey.argtypes = [
    wintypes.HWND,
    ctypes.c_int,
    wintypes.UINT,
    wintypes.UINT,
]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE,
    wintypes.LPCWSTR,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = wintypes.HICON
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [
    wintypes.HMENU,
    wintypes.UINT,
    ctypes.c_size_t,
    wintypes.LPCWSTR,
]
user32.AppendMenuW.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    ctypes.c_void_p,
]
user32.TrackPopupMenu.restype = wintypes.UINT
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.DestroyMenu.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NotifyIconData)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
INSTANCE_MUTEX_NAME = "Local\\WeChatTTSWidget"


class WinTrayController:
    """Own a native Windows tray icon and Ctrl+Alt+Z global hotkey."""

    def __init__(self, actions: "queue.SimpleQueue[str]") -> None:
        self.actions = actions
        self.hwnd: int | None = None
        self.hotkey_registered = False
        self.error: str | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._message_loop,
            name="wechat-tts-tray",
            daemon=True,
        )
        self._wnd_proc: WNDPROC | None = None
        self._notify_data: NotifyIconData | None = None
        self._class_name = f"WeChatTTSVoiceBubbleTray_{os.getpid()}"

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(timeout=3.0)
        if self.error:
            raise RuntimeError(self.error)
        if not self.hwnd:
            raise RuntimeError("系统托盘初始化超时。")

    def stop(self) -> None:
        hwnd = self.hwnd
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)

    def _load_icon(self) -> int:
        icon = 0
        for candidate in (APP_ICON_PATH, Path(sys.executable).resolve()):
            if not candidate.is_file():
                continue
            icon = user32.LoadImageW(
                None,
                str(candidate),
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
            if icon:
                break
        if not icon:
            icon = user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
        return int(icon or 0)

    def _show_menu(self, hwnd: int) -> None:
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            user32.AppendMenuW(menu, MF_STRING, TRAY_MENU_SHOW, "打开主窗口")
            user32.AppendMenuW(
                menu,
                MF_STRING,
                TRAY_MENU_QUICK,
                "快速发送    Ctrl+Alt+Z",
            )
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, TRAY_MENU_EXIT, "退出")
            point = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(point)):
                return
            user32.SetForegroundWindow(hwnd)
            command = user32.TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
                point.x,
                point.y,
                0,
                hwnd,
                None,
            )
            if command == TRAY_MENU_SHOW:
                self.actions.put("show")
            elif command == TRAY_MENU_QUICK:
                self.actions.put("quick")
            elif command == TRAY_MENU_EXIT:
                self.actions.put("exit")
            user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(menu)

    def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == TRAY_CALLBACK_MESSAGE:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.actions.put("show")
            elif lparam == WM_RBUTTONUP:
                self._show_menu(hwnd)
            return 0
        if message == WM_HOTKEY and wparam == HOTKEY_ID:
            self.actions.put("quick")
            return 0
        if message == WM_DESTROY:
            if self.hotkey_registered:
                user32.UnregisterHotKey(hwnd, HOTKEY_ID)
                self.hotkey_registered = False
            if self._notify_data is not None:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._notify_data))
            self.hwnd = None
            user32.PostQuitMessage(0)
            return 0
        return int(user32.DefWindowProcW(hwnd, message, wparam, lparam))

    def _message_loop(self) -> None:
        instance = kernel32.GetModuleHandleW(None)
        registered = False
        try:
            self._wnd_proc = WNDPROC(self._window_proc)
            window_class = WndClass()
            window_class.lpfnWndProc = self._wnd_proc
            window_class.hInstance = instance
            window_class.lpszClassName = self._class_name
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError()
            registered = True

            hwnd = user32.CreateWindowExW(
                0,
                self._class_name,
                APP_NAME,
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                instance,
                None,
            )
            if not hwnd:
                raise ctypes.WinError()
            self.hwnd = int(hwnd)

            notify_data = NotifyIconData()
            notify_data.cbSize = ctypes.sizeof(NotifyIconData)
            notify_data.hWnd = hwnd
            notify_data.uID = TRAY_ICON_ID
            notify_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            notify_data.uCallbackMessage = TRAY_CALLBACK_MESSAGE
            notify_data.hIcon = self._load_icon()
            notify_data.szTip = APP_NAME
            self._notify_data = notify_data
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(notify_data)):
                raise ctypes.WinError()

            self.hotkey_registered = bool(
                user32.RegisterHotKey(
                    hwnd,
                    HOTKEY_ID,
                    MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
                    VK_Z,
                )
            )
            if not self.hotkey_registered:
                logging.warning("注册全局快捷键 Ctrl+Alt+Z 失败，错误码=%d", kernel32.GetLastError())
            self._ready.set()

            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as error:
            self.error = str(error)
            logging.exception("系统托盘初始化失败")
            self._ready.set()
        finally:
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)
                self.hwnd = None
            if registered:
                user32.UnregisterClassW(self._class_name, instance)


def acquire_single_instance() -> int | None:
    kernel32.SetLastError(0)
    handle = kernel32.CreateMutexW(None, False, INSTANCE_MUTEX_NAME)
    if not handle:
        raise ctypes.WinError()
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        hwnd = user32.FindWindowW(None, APP_NAME)
        if hwnd:
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
        kernel32.CloseHandle(handle)
        return None
    return handle


def find_wechat_windows() -> list[int]:
    windows: list[int] = []
    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_proc_type
    def enum_proc(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title_length = user32.GetWindowTextLengthW(hwnd)
        if title_length <= 0:
            return True
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, title_length + 1)
        if title_buffer.value != "微信":
            return True

        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        try:
            process_name = psutil.Process(process_id.value).name().casefold()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return True
        if process_name in WECHAT_NAMES:
            windows.append(hwnd)
        return True

    user32.EnumWindows(enum_proc, 0)
    return windows


def activate_wechat_window() -> int:
    windows = find_wechat_windows()
    if len(windows) != 1:
        raise RuntimeError(
            f"需要恰好一个微信主窗口，当前找到 {len(windows)} 个。"
            "请打开微信并保留一个主窗口。"
        )

    hwnd = windows[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE

    foreground = user32.GetForegroundWindow()
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)

    attached_target = False
    attached_foreground = False
    try:
        if target_thread and target_thread != current_thread:
            attached_target = bool(
                user32.AttachThreadInput(current_thread, target_thread, True)
            )
        if foreground_thread and foreground_thread != current_thread:
            attached_foreground = bool(
                user32.AttachThreadInput(current_thread, foreground_thread, True)
            )
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
    finally:
        if attached_foreground:
            user32.AttachThreadInput(current_thread, foreground_thread, False)
        if attached_target:
            user32.AttachThreadInput(current_thread, target_thread, False)

    time.sleep(0.35)
    if engine.foreground_process_name() not in WECHAT_NAMES:
        raise RuntimeError("无法将微信切换到前台，请手动点开当前聊天后重试。")
    return hwnd


def wechat_voice_control_points(hwnd: int) -> dict[str, tuple[int, int]]:
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError()
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise ctypes.WinError()
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width < 650 or height < 500:
        raise RuntimeError("微信窗口过小，请将主窗口放大后重试。")
    right = origin.x + width
    bottom = origin.y + height
    return {
        "open": (right - 122, bottom - 27),
        "cancel": (right - 246, bottom - 31),
        "send": (right - 43, bottom - 31),
    }


def voice_mode_visible(points: dict[str, tuple[int, int]]) -> bool:
    send_x, send_y = points["send"]
    cancel_x, cancel_y = points["cancel"]
    send_image = pyautogui.screenshot(region=(send_x - 18, send_y - 18, 36, 36))
    cancel_image = pyautogui.screenshot(
        region=(cancel_x - 18, cancel_y - 18, 36, 36)
    )
    green_pixels = sum(
        1
        for red, green, blue in send_image.getdata()
        if green >= 145 and green > red * 1.45 and green > blue * 1.25
    )
    cancel_gray_pixels = sum(
        1
        for red, green, blue in cancel_image.getdata()
        if 205 <= red <= 248 and abs(red - green) <= 8 and abs(green - blue) <= 8
    )
    cancel_dark_pixels = sum(
        1
        for red, green, blue in cancel_image.getdata()
        if max(red, green, blue) < 155
    )
    return green_pixels >= 80 and cancel_gray_pixels >= 120 and cancel_dark_pixels >= 4


def enter_voice_mode(hwnd: int) -> dict[str, tuple[int, int]]:
    points = wechat_voice_control_points(hwnd)
    if voice_mode_visible(points):
        raise RuntimeError("微信已经处于语音录制模式，请先取消当前录音。")
    pyautogui.click(*points["open"])
    time.sleep(0.40)
    if not voice_mode_visible(points):
        raise RuntimeError(
            "点击后微信没有进入语音录制模式。请确认当前聊天支持语音消息，"
            "并保持微信窗口尺寸不小于 650×500。"
        )
    return points


def cancel_voice_mode(points: dict[str, tuple[int, int]]) -> None:
    if voice_mode_visible(points):
        pyautogui.click(*points["cancel"])
        time.sleep(0.25)


def finish_voice_mode(points: dict[str, tuple[int, int]]) -> None:
    if not voice_mode_visible(points):
        raise RuntimeError("微信录音控件意外消失，未执行发送。")
    pyautogui.click(*points["send"])
    time.sleep(0.55)
    if voice_mode_visible(points):
        raise RuntimeError("点击发送后微信仍处于录音模式，发送可能未完成。")


def default_input_name() -> str:
    default_input = int(sd.default.device[0])
    if default_input < 0:
        return ""
    return str(sd.query_devices(default_input)["name"])


def run_preflight(config: AppConfig) -> tuple[bool, str]:
    try:
        engine.require_local_audio_session()
    except Exception as error:
        return False, str(error)
    if not config.token:
        return False, "请先在设置中保存 TTS Token"
    if not config.tts_url.strip():
        return False, "请先设置 TTS 地址"
    if len(find_wechat_windows()) != 1:
        return False, "请打开一个微信主窗口"
    try:
        input_name = default_input_name()
    except Exception as error:
        return False, f"无法读取默认麦克风：{error}"
    if "cable output" not in input_name.casefold():
        return False, f"默认麦克风不是 CABLE Output（当前：{input_name or '无'}）"
    try:
        engine.find_output_device(engine.DEFAULT_DEVICE_NAME)
    except Exception as error:
        return False, f"VB-CABLE 播放端不可用：{error}"
    return True, "设备已连接"


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, owner: "WidgetApp") -> None:
        super().__init__(owner.root)
        self.owner = owner
        self.title("TTS 设置")
        self.geometry("520x520")
        self.resizable(False, False)
        self.transient(owner.root)
        self.grab_set()
        self.configure(fg_color=SURFACE)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(22, 14))
        ctk.CTkLabel(
            header,
            text="连接设置",
            font=ctk.CTkFont(FONT_FAMILY, 20, "bold"),
            text_color=TEXT,
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="配置你的 TTS 服务，凭据只保存在当前电脑。",
            font=ctk.CTkFont(FONT_FAMILY, 12),
            text_color=MUTED,
        ).pack(anchor="w", pady=(4, 0))

        card = ctk.CTkFrame(
            self,
            fg_color=CARD,
            corner_radius=18,
            border_width=1,
            border_color=BORDER,
        )
        card.pack(fill="both", expand=True, padx=24, pady=(0, 16))

        ctk.CTkLabel(
            card,
            text="TTS 接口地址",
            font=ctk.CTkFont(FONT_FAMILY, 12, "bold"),
            text_color=TEXT,
        ).pack(anchor="w", padx=18, pady=(18, 7))
        self.url_var = tk.StringVar(value=owner.config.tts_url)
        url_entry = ctk.CTkEntry(
            card,
            textvariable=self.url_var,
            height=42,
            corner_radius=11,
            border_width=1,
            border_color=BORDER,
            fg_color="#FBFDFC",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=TEXT,
        )
        url_entry.pack(fill="x", padx=18)

        ctk.CTkLabel(
            card,
            text="Bearer Token",
            font=ctk.CTkFont(FONT_FAMILY, 12, "bold"),
            text_color=TEXT,
        ).pack(anchor="w", padx=18, pady=(17, 7))
        self.token_var = tk.StringVar()
        token_entry = ctk.CTkEntry(
            card,
            textvariable=self.token_var,
            show="●",
            height=42,
            corner_radius=11,
            border_width=1,
            border_color=BORDER,
            fg_color="#FBFDFC",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=TEXT,
        )
        token_entry.pack(fill="x", padx=18)
        token_hint = "已加密保存；留空表示不修改" if owner.config.token else "尚未配置"
        ctk.CTkLabel(
            card,
            text=token_hint,
            font=ctk.CTkFont(FONT_FAMILY, 11),
            text_color=MUTED,
        ).pack(anchor="w", padx=18, pady=(6, 0))

        secure = ctk.CTkFrame(card, fg_color=TONAL, corner_radius=10)
        secure.pack(fill="x", padx=18, pady=(13, 18))
        ctk.CTkLabel(
            secure,
            text="●  Token 使用 Windows DPAPI 加密，仅当前用户可解密",
            font=ctk.CTkFont(FONT_FAMILY, 11),
            text_color=PRIMARY,
        ).pack(anchor="w", padx=12, pady=9)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=24, pady=(0, 22))
        ctk.CTkButton(
            actions,
            text="取消",
            width=92,
            height=42,
            corner_radius=12,
            fg_color="transparent",
            hover_color="#E9EFEB",
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(FONT_FAMILY, 12),
            command=self.destroy,
        ).pack(side="right", padx=(10, 0))
        ctk.CTkButton(
            actions,
            text="保存设置",
            width=118,
            height=42,
            corner_radius=12,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            text_color="white",
            font=ctk.CTkFont(FONT_FAMILY, 12, "bold"),
            command=self.save,
        ).pack(side="right")
        self.after(50, token_entry.focus_set)

    def save(self) -> None:
        url = self.url_var.get().strip()
        token = self.token_var.get().strip()
        if not url:
            messagebox.showerror("设置错误", "TTS 接口地址不能为空。", parent=self)
            return
        self.owner.config.tts_url = url
        if token:
            self.owner.config.token = token
        try:
            self.owner.config.save()
        except Exception as error:
            logging.exception("保存配置失败")
            messagebox.showerror("保存失败", str(error), parent=self)
            return
        self.owner.refresh_preflight()
        self.destroy()


class QuickSendWindow(ctk.CTkToplevel):
    """A borderless text-only surface opened by the global hotkey."""

    def __init__(self, owner: "WidgetApp") -> None:
        super().__init__(owner.root)
        self.owner = owner
        self._focus_confirmed = False
        self.title("快速发送语音")
        self.geometry("520x132")
        self.resizable(False, False)
        self.overrideredirect(True)
        self.configure(fg_color=BORDER)
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self.bind("<Escape>", lambda _event: self.hide())

        self.text = ctk.CTkTextbox(
            self,
            height=130,
            wrap="word",
            corner_radius=16,
            border_width=1,
            border_color=PRIMARY,
            fg_color=CARD,
            text_color=TEXT,
            font=ctk.CTkFont(FONT_FAMILY, 14),
            scrollbar_button_color="#C8D8CF",
            scrollbar_button_hover_color="#AFC5B8",
            activate_scrollbars=True,
            undo=True,
        )
        self.text.pack(fill="both", expand=True, padx=1, pady=1)
        self.text.bind("<KeyRelease>", self.on_text_modified)
        self.text.bind("<Return>", self.on_return)
        self.text.bind(
            "<<Paste>>", lambda _event: self.after(10, self.on_text_modified)
        )
        self.withdraw()

    def show(self) -> None:
        if not self.winfo_exists() or self.owner.busy:
            return
        self.update_idletasks()
        width = self.winfo_width() or 520
        height = self.winfo_height() or 132
        left = max(0, (self.winfo_screenwidth() - width) // 2)
        top = max(0, (self.winfo_screenheight() - height) // 3)
        self.geometry(f"{width}x{height}+{left}+{top}")
        self._focus_confirmed = False
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self._focus_text()
        self.after_idle(self._focus_text)
        self.after(60, self._focus_text)
        self.after(220, lambda: self.attributes("-topmost", False))

    def _focus_text(self, retries: int = 3) -> None:
        if not self.winfo_exists() or self.state() == "withdrawn":
            return
        self.lift()
        self.focus_force()
        self.text.focus_force()
        self.text.mark_set("insert", "end-1c")
        self.text.see("insert")
        if self.focus_get() == self.text._textbox:
            if not self._focus_confirmed:
                logging.info("快速发送浮层已获得文本输入焦点")
                self._focus_confirmed = True
            return
        if retries > 0:
            self.after(50, lambda: self._focus_text(retries - 1))
        else:
            logging.warning("快速发送浮层未能获得文本输入焦点")

    def hide(self) -> None:
        if self.winfo_exists():
            self.attributes("-topmost", False)
            self.withdraw()

    def on_text_modified(self, _event: tk.Event | None = None) -> None:
        content = self.text.get("1.0", "end-1c")
        if len(content) > 500:
            content = content[:500]
            self.text.delete("1.0", "end")
            self.text.insert("1.0", content)

    def set_busy(self, busy: bool) -> None:
        self.text.configure(state="disabled" if busy else "normal")

    def on_return(self, event: tk.Event) -> str | None:
        if event.state & 0x0001:  # Shift+Enter inserts a newline.
            return None
        self.send()
        return "break"

    def send(self) -> None:
        text = self.text.get("1.0", "end-1c").strip()
        if text and self.owner.start_send_text(text, source="quick"):
            self.hide()

    def send_finished(self) -> None:
        self.text.delete("1.0", "end")
        self.on_text_modified()


class WidgetApp:
    def __init__(self) -> None:
        self.config = AppConfig()
        env_token = os.getenv("TTS_TOKEN", "").strip()
        if env_token and not self.config.token:
            self.config.token = env_token

        self.root = ctk.CTk()
        self.root.title(APP_NAME)
        self.root.iconbitmap(default=str(APP_ICON_PATH))
        self.root.geometry(self.config.geometry)
        self.root.minsize(480, 550)
        self.root.configure(fg_color=SURFACE)
        self.busy = False
        self.preflight_ok = False
        self.active_send_source: str | None = None
        self._quitting = False
        self._system_actions: queue.SimpleQueue[str] = queue.SimpleQueue()
        self.tray: WinTrayController | None = None
        self._waveform_icon = svg_icon("graphic_eq", "#FFFFFF", 24)
        self._settings_icon = svg_icon("settings", "#34413A", 22)
        self._chevron_icon = svg_icon("chevron_right", MUTED, 18)
        self._mic_icon = svg_icon("mic", "#FFFFFF", 22)
        self._trash_icon = svg_icon("delete", PRIMARY, 21)
        self._build_ui()
        self.quick_window = QuickSendWindow(self)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Control-Return>", self.on_shortcut)
        try:
            self.tray = WinTrayController(self._system_actions)
            self.tray.start()
        except Exception as error:
            logging.exception("启动系统托盘失败")
            self.tray = None
            self.root.after(
                100,
                lambda detail=str(error): messagebox.showwarning(
                    "系统托盘不可用",
                    f"无法启动系统托盘，关闭窗口将直接退出。\n\n{detail}",
                    parent=self.root,
                ),
            )
        else:
            if not self.tray.hotkey_registered:
                self.root.after(
                    100,
                    lambda: messagebox.showwarning(
                        "快捷键不可用",
                        "Ctrl+Alt+Z 已被其他程序占用。仍可从托盘菜单打开快速发送窗口。",
                        parent=self.root,
                    ),
                )
        self.root.after(75, self.process_system_actions)
        self.root.after(150, self.refresh_preflight)
        self.root.after(2500, self.periodic_refresh)

    def _build_ui(self) -> None:
        outer = ctk.CTkFrame(self.root, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=20, pady=(18, 16))

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x")
        logo = ctk.CTkFrame(
            header,
            width=44,
            height=44,
            corner_radius=22,
            fg_color=PRIMARY,
        )
        logo.pack(side="left", padx=(0, 12))
        logo.pack_propagate(False)
        ctk.CTkLabel(
            logo,
            text="",
            image=self._waveform_icon,
        ).place(relx=0.5, rely=0.5, anchor="center")

        heading = ctk.CTkFrame(header, fg_color="transparent")
        heading.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            heading,
            text="微信 TTS",
            text_color=TEXT,
            font=ctk.CTkFont(FONT_FAMILY, 21, "bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            heading,
            text="文字转语音 · 发送到当前聊天",
            text_color=MUTED,
            font=ctk.CTkFont(FONT_FAMILY, 12),
        ).pack(anchor="w", pady=(1, 0))

        settings_surface = ctk.CTkFrame(
            header,
            width=44,
            height=44,
            corner_radius=22,
            fg_color=CARD,
            border_width=1,
            border_color="#D4DDD7",
            cursor="hand2",
        )
        settings_surface.pack(side="right")
        settings_surface.pack_propagate(False)
        settings_label = ctk.CTkLabel(
            settings_surface,
            text="",
            image=self._settings_icon,
            cursor="hand2",
        )
        settings_label.place(relx=0.5, rely=0.5, anchor="center")
        for control in (settings_surface, settings_label):
            control.bind("<Button-1>", lambda _event: self.open_settings())

        target = ctk.CTkFrame(
            outer,
            fg_color=CARD,
            corner_radius=16,
            border_width=1,
            border_color=BORDER,
            height=78,
        )
        target.pack(fill="x", pady=(17, 10))
        target.pack_propagate(False)
        target_text = ctk.CTkFrame(target, fg_color="transparent")
        target_text.pack(side="left", padx=16, pady=12)
        ctk.CTkLabel(
            target_text,
            text="发送给",
            text_color=MUTED,
            font=ctk.CTkFont(FONT_FAMILY, 10),
        ).pack(anchor="w")
        ctk.CTkLabel(
            target_text,
            text="微信当前聊天",
            text_color=TEXT,
            font=ctk.CTkFont(FONT_FAMILY, 15, "bold"),
        ).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(
            target,
            text="",
            image=self._chevron_icon,
        ).pack(side="right", padx=17)

        self.status_var = tk.StringVar(value="正在检查环境…")
        self.status_frame = ctk.CTkFrame(outer, fg_color=TONAL, corner_radius=12)
        self.status_frame.pack(fill="x", pady=(0, 10))
        self.status_dot = ctk.CTkLabel(
            self.status_frame,
            text="●",
            width=18,
            text_color=SUCCESS,
            font=ctk.CTkFont("Segoe UI", 11),
        )
        self.status_dot.pack(side="left", padx=(12, 2), pady=9)
        self.status_label = ctk.CTkLabel(
            self.status_frame,
            textvariable=self.status_var,
            text_color=PRIMARY,
            font=ctk.CTkFont(FONT_FAMILY, 11),
            anchor="w",
            justify="left",
            wraplength=352,
        )
        self.status_label.pack(side="left", fill="x", expand=True, padx=(0, 12), pady=9)

        editor = ctk.CTkFrame(
            outer,
            fg_color=CARD,
            corner_radius=18,
            border_width=1,
            border_color=PRIMARY,
        )
        editor.pack(fill="both", expand=True)
        self.text = ctk.CTkTextbox(
            editor,
            height=150,
            wrap="word",
            corner_radius=0,
            border_width=0,
            fg_color="transparent",
            text_color=TEXT,
            font=ctk.CTkFont(FONT_FAMILY, 13),
            scrollbar_button_color="#C8D8CF",
            scrollbar_button_hover_color="#AFC5B8",
            activate_scrollbars=True,
            undo=True,
        )
        self.text.pack(fill="both", expand=True, padx=8, pady=(7, 0))
        self.text.bind("<KeyRelease>", self.on_text_modified)
        self.text.bind("<<Paste>>", lambda _event: self.root.after(10, self.on_text_modified))
        self.text.bind("<FocusIn>", self.on_text_modified)

        self.placeholder = ctk.CTkLabel(
            self.text,
            text="输入要转换成语音的文字…",
            text_color="#99A59E",
            font=ctk.CTkFont(FONT_FAMILY, 13),
            cursor="xterm",
        )
        self.placeholder.place(x=8, y=5)
        self.placeholder.bind("<Button-1>", lambda _event: self.text.focus_set())

        editor_meta = ctk.CTkFrame(editor, fg_color="transparent")
        editor_meta.pack(fill="x", padx=15, pady=(4, 12))
        ctk.CTkLabel(
            editor_meta,
            text="最长 58 秒",
            text_color=MUTED,
            font=ctk.CTkFont(FONT_FAMILY, 10),
        ).pack(side="left")
        self.count_var = tk.StringVar(value="0 / 500")
        shortcut = ctk.CTkLabel(
            editor_meta,
            text="Ctrl + Enter",
            text_color=MUTED,
            fg_color="#EEF3F0",
            corner_radius=7,
            font=ctk.CTkFont("Segoe UI", 10),
            padx=8,
            pady=3,
        )
        shortcut.pack(side="right")
        ctk.CTkLabel(
            editor_meta,
            textvariable=self.count_var,
            text_color=MUTED,
            font=ctk.CTkFont("Segoe UI", 10),
        ).pack(side="right", padx=(0, 12))

        actions = ctk.CTkFrame(outer, fg_color="transparent")
        actions.pack(fill="x", pady=(14, 0))
        self.clear_button = ctk.CTkButton(
            actions,
            text="清空",
            image=self._trash_icon,
            compound="left",
            width=126,
            height=50,
            corner_radius=14,
            fg_color=CARD,
            hover_color="#E9EFEB",
            border_width=1,
            border_color=BORDER,
            text_color=PRIMARY,
            text_color_disabled="#B7C0BA",
            font=ctk.CTkFont(FONT_FAMILY, 13, "bold"),
            command=self.clear_text,
        )
        self.clear_button.pack(side="left")
        self.send_button = ctk.CTkButton(
            actions,
            text="发送语音",
            image=self._mic_icon,
            compound="left",
            height=50,
            corner_radius=14,
            fg_color=ACTION,
            hover_color=ACTION_HOVER,
            border_width=0,
            text_color="white",
            text_color_disabled="#EEF6F1",
            font=ctk.CTkFont(FONT_FAMILY, 13, "bold"),
            command=self.start_send,
        )
        self.send_button.pack(side="right", fill="x", expand=True, padx=(12, 0))

        ctk.CTkLabel(
            outer,
            text="将自动切换到微信并发送语音气泡",
            text_color=MUTED,
            font=ctk.CTkFont(FONT_FAMILY, 10),
        ).pack(anchor="w", pady=(11, 0), padx=2)
        self.update_send_button()

    def open_settings(self) -> None:
        if not self.busy:
            SettingsDialog(self)

    def on_text_modified(self, _event: tk.Event | None = None) -> None:
        content = self.text.get("1.0", "end-1c")
        if len(content) > 500:
            content = content[:500]
            self.text.delete("1.0", "end")
            self.text.insert("1.0", content)
        self.count_var.set(f"{len(content)} / 500")
        if content:
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=8, y=5)
        self.update_send_button()

    def clear_text(self) -> None:
        if not self.busy:
            self.text.delete("1.0", "end")
            self.on_text_modified()
            self.text.focus_set()

    def update_send_button(self) -> None:
        if not hasattr(self, "send_button"):
            return
        has_text = bool(self.text.get("1.0", "end-1c").strip())
        state = "normal" if has_text and self.preflight_ok and not self.busy else "disabled"
        self.send_button.configure(
            state=state,
            fg_color=ACTION if state == "normal" else "#A9CEBA",
            hover_color=ACTION_HOVER if state == "normal" else "#A9CEBA",
        )

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status_var.set(text)
        self.status_frame.configure(fg_color=ERROR_SURFACE if error else TONAL)
        self.status_dot.configure(text_color=ERROR if error else SUCCESS)
        self.status_label.configure(text_color=ERROR if error else PRIMARY)

    def refresh_preflight(self) -> None:
        if self.busy:
            return
        ok, detail = run_preflight(self.config)
        self.preflight_ok = ok
        self.set_status(detail, error=not ok)
        self.update_send_button()

    def periodic_refresh(self) -> None:
        if not self._quitting and self.root.winfo_exists():
            self.refresh_preflight()
            self.root.after(2500, self.periodic_refresh)

    def process_system_actions(self) -> None:
        if self._quitting or not self.root.winfo_exists():
            return
        while True:
            try:
                action = self._system_actions.get_nowait()
            except queue.Empty:
                break
            if action == "show":
                self.show_main_window()
            elif action == "quick":
                self.show_quick_window()
            elif action == "exit":
                self.request_exit()
        if not self._quitting:
            self.root.after(75, self.process_system_actions)

    def show_main_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(160, lambda: self.root.attributes("-topmost", False))
        self.root.after(20, self.text.focus_set)

    def show_quick_window(self) -> None:
        self.quick_window.show()

    def hide_to_tray(self) -> None:
        if self.tray is None:
            self.shutdown()
            return
        if self.root.state() == "normal":
            self.config.geometry = self.root.geometry()
            try:
                self.config.save()
            except Exception:
                logging.exception("隐藏到托盘时保存配置失败")
        self.root.withdraw()

    def request_exit(self) -> None:
        if self.busy:
            self.show_main_window()
            messagebox.showinfo(
                "正在发送", "请等待当前语音发送完成后再退出。", parent=self.root
            )
            return
        self.shutdown()

    def shutdown(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        if self.root.state() == "normal":
            self.config.geometry = self.root.geometry()
        try:
            self.config.save()
        except Exception:
            logging.exception("退出时保存配置失败")
        try:
            if self.quick_window.winfo_exists():
                self.quick_window.destroy()
        except tk.TclError:
            pass
        if self.tray is not None:
            self.tray.stop()
            self.tray = None
        self.root.destroy()

    def on_shortcut(self, _event: tk.Event) -> str:
        if str(self.send_button.cget("state")) != "disabled":
            self.start_send()
        return "break"

    def start_send(self) -> None:
        text = self.text.get("1.0", "end-1c").strip()
        if text:
            self.start_send_text(text, source="main")

    def start_send_text(self, text: str, *, source: str) -> bool:
        if self.busy or not text:
            return False
        ok, detail = run_preflight(self.config)
        if not ok:
            self.preflight_ok = False
            self.set_status(detail, error=True)
            self.update_send_button()
            if source == "quick":
                messagebox.showerror("无法发送", detail, parent=self.quick_window)
            return False

        self.busy = True
        self.active_send_source = source
        self.text.configure(state="disabled")
        self.clear_button.configure(state="disabled")
        self.send_button.configure(
            state="disabled", text="生成中…", image=None, fg_color="#A9CEBA"
        )
        self.quick_window.set_busy(True)
        self.set_status("正在生成 TTS 音频…")
        threading.Thread(target=self._send_worker, args=(text,), daemon=True).start()
        return True

    def _post_progress(self, text: str) -> None:
        self.root.after(0, lambda: self._apply_progress(text))

    def _apply_progress(self, text: str) -> None:
        self.set_status(text)
        self.quick_window.set_busy(True)

    def _send_worker(self, text: str) -> None:
        temp_path: Path | None = None
        voice_points: dict[str, tuple[int, int]] | None = None
        try:
            logging.info("开始发送，字符数=%d", len(text))
            handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            handle.close()
            temp_path = Path(handle.name)
            engine.request_tts(text, temp_path, self.config.tts_url, self.config.token)

            device_index, device = engine.find_output_device(
                engine.DEFAULT_DEVICE_NAME
            )
            _audio, _sample_rate, duration = engine.prepare_audio(
                temp_path, device, 58.0
            )
            logging.info(
                "TTS 已生成，时长=%.2f，首选播放设备=%s，索引=%d",
                duration,
                device["name"],
                device_index,
            )
            self._post_progress(f"音频 {duration:.1f} 秒 · 正在切换到微信…")

            hwnd = activate_wechat_window()
            if "cable output" not in default_input_name().casefold():
                raise RuntimeError("发送前检查失败：默认麦克风已不再是 CABLE Output。")

            pyautogui.FAILSAFE = True
            voice_points = enter_voice_mode(hwnd)
            logging.info("微信已进入语音录制模式")
            time.sleep(0.20)
            self._post_progress(f"正在发送 {duration:.1f} 秒语音…")
            (
                duration,
                device_index,
                device,
                sample_rate,
            ) = engine.play_audio_with_fallback(
                temp_path, engine.DEFAULT_DEVICE_NAME, 58.0
            )
            host_name = str(sd.query_hostapis(device["hostapi"])["name"])
            logging.info(
                "音频播放完成，设备=%s，后端=%s，索引=%d，采样率=%d",
                device["name"],
                host_name,
                device_index,
                sample_rate,
            )
            time.sleep(0.20)
            finish_voice_mode(voice_points)
            voice_points = None
            logging.info("微信发送控件已完成")
            self.root.after(0, lambda: self._send_finished(duration))
        except Exception as error:
            logging.exception("发送失败")
            self.root.after(0, lambda value=str(error): self._send_failed(value))
        finally:
            sd.stop()
            if voice_points is not None:
                try:
                    cancel_voice_mode(voice_points)
                except Exception:
                    logging.exception("取消微信录音模式失败")
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _restore_controls(self) -> None:
        self.busy = False
        self.text.configure(state="normal")
        self.clear_button.configure(state="normal")
        self.send_button.configure(text="发送语音", image=self._mic_icon)
        self.update_send_button()
        self.quick_window.set_busy(False)

    def _send_finished(self, duration: float) -> None:
        source = self.active_send_source
        self.active_send_source = None
        self._restore_controls()
        if source == "quick":
            self.quick_window.send_finished()
        else:
            self.text.delete("1.0", "end")
            self.on_text_modified()
        self.set_status(f"已触发发送 · 语音约 {duration:.1f} 秒")
        self.root.bell()

    def _send_failed(self, detail: str) -> None:
        source = self.active_send_source
        self.active_send_source = None
        self._restore_controls()
        self.set_status(detail, error=True)
        if source == "quick":
            self.quick_window.show()
            parent: tk.Misc = self.quick_window
        else:
            self.show_main_window()
            parent = self.root
        messagebox.showerror("发送失败", detail, parent=parent)

    def on_close(self) -> None:
        self.hide_to_tray()

    def run(self) -> None:
        self.text.focus_set()
        try:
            self.root.mainloop()
        finally:
            if self.tray is not None:
                self.tray.stop()
                self.tray = None


if __name__ == "__main__":
    instance_mutex = acquire_single_instance()
    if instance_mutex is not None:
        try:
            WidgetApp().run()
        finally:
            kernel32.CloseHandle(instance_mutex)
