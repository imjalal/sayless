"""Global hotkey for Sayless (default Ctrl+T, from any window).

Opens Sayless, or brings it to the front if it is already open. Runs hidden; install.ps1 -Hotkey
starts it with Windows. The hotkey and the apps that keep their own shortcut are set in
%USERPROFILE%\\.sayless\\config.json ("hotkey", "hotkey_pass_through"). Log: %USERPROFILE%\\.sayless\\hotkey.log
"""
import ctypes, json, os, shutil, subprocess, sys, threading, time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get("SAYLESS_HOME") or Path.home() / ".sayless")

def load_config():
    cfg = {"title": "Sayless", "hotkey": "ctrl+t", "hotkey_pass_through": []}
    try:
        cfg.update(json.loads((HOME / "config.json").read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg

CFG = load_config()
TITLE = CFG["title"]
PASS_THROUGH = {p.lower() for p in CFG.get("hotkey_pass_through", [])}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL, WM_KEYDOWN, WM_SYSKEYDOWN = 13, 0x0100, 0x0104
VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN = 0x11, 0x10, 0x12, 0x5B, 0x5C
SPECIAL = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "`": 0xC0, "backtick": 0xC0,
           **{f"f{i}": 0x6F + i for i in range(1, 13)}}

def parse_hotkey(s):
    parts = [p.strip().lower() for p in s.split("+")]
    key = parts[-1]
    vk = SPECIAL.get(key) or (ord(key.upper()) if len(key) == 1 and key.isalnum() else None)
    if vk is None:
        raise ValueError(f"unknown key in hotkey: {s}")
    mods = {m for m in parts[:-1]}
    return vk, "ctrl" in mods, "shift" in mods, "alt" in mods, "win" in mods

VK, NEED_CTRL, NEED_SHIFT, NEED_ALT, NEED_WIN = parse_hotkey(CFG.get("hotkey", "ctrl+t"))
LRESULT = ctypes.c_ssize_t

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

def log(*a):
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        with open(HOME / "hotkey.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(map(str, a)) + "\n")
    except Exception:
        pass

def down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)

def foreground_exe():
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf, n = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
    ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
    kernel32.CloseHandle(h)
    return os.path.basename(buf.value).lower() if ok else ""

def find_window():
    found = []
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value.startswith(TITLE) and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0] if found else None

def focus(hwnd):
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.keybd_event(VK_MENU, 0, 0, 0)  # an Alt tap lets Windows hand over the focus
    user32.keybd_event(VK_MENU, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)

def clean_env():
    return {k: v for k, v in os.environ.items()
            if not (k.startswith("CLAUDE") or k in ("ANTHROPIC_BASE_URL", "AI_AGENT"))}

def open_sayless():
    hwnd = find_window()
    if hwnd:
        focus(hwnd); return
    py = str(Path(sys.executable).with_name("python.exe"))
    script = str(ROOT / "sayless.py")
    wt = shutil.which("wt")
    if wt:
        cmd = [wt, "-w", "new", "--title", TITLE, py, script]
        flags = 0
    else:
        cmd, flags = [py, script], 0x00000010  # CREATE_NEW_CONSOLE
    subprocess.Popen(cmd, cwd=str(Path.home()), env=clean_env(), creationflags=flags)
    for _ in range(50):  # wait for the window, then pull it to the front
        time.sleep(0.1)
        hwnd = find_window()
        if hwnd:
            focus(hwnd); return

def on_hotkey():
    try:
        open_sayless()
    except Exception as e:
        log("open failed:", repr(e))

@HOOKPROC
def hook(n, wparam, lparam):
    if n == 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
        k = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if (k.vkCode == VK and down(VK_CONTROL) == NEED_CTRL and down(VK_SHIFT) == NEED_SHIFT
                and down(VK_MENU) == NEED_ALT and (down(VK_LWIN) or down(VK_RWIN)) == NEED_WIN):
            if foreground_exe() not in PASS_THROUGH:
                threading.Thread(target=on_hotkey, daemon=True).start()
                return 1  # swallow the key so the window underneath doesn't also get it
    return user32.CallNextHookEx(None, n, wparam, lparam)

def main():
    kernel32.CreateMutexW(None, False, "SaylessHotkey")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS: one copy is enough
        return
    if not user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook, None, 0):
        log("hook failed", ctypes.get_last_error()); return
    log(f"listening for {CFG.get('hotkey', 'ctrl+t')}")
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg)); user32.DispatchMessageW(ctypes.byref(msg))

if __name__ == "__main__":
    main()
