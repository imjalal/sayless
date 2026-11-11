"""Zap - a Windows terminal that does what you say.

It looks and works like a normal PowerShell prompt, with Claude Code inside:

  paint, open chrome, spotify      -> opens instantly (local app index, no AI)
  youtube, google cats             -> opens the site / search instantly
  close spotify                    -> closes it instantly
  dir, git status, ping x          -> runs as a normal shell command
  anything else                    -> Claude Code does it on your PC (one warm process)

Prefixes:  !cmd  force shell     ?text  force Claude
Commands:  /help /new /model /alias /aliases /apps /config /login /exit
Ctrl+C while Claude works stops it.  `python zap.py paint` runs one line and exits.

https://github.com/imjalal/zap  -  MIT licence  -  not affiliated with Anthropic.
"""
from __future__ import annotations

import ctypes, difflib, json, os, re, shutil, socket, subprocess, sys, threading, time, uuid, webbrowser
from pathlib import Path
from urllib.parse import quote_plus

__version__ = "2.0.0"

if os.name != "nt":
    sys.exit("Zap runs on Windows 10/11 only.")

HOME = Path(os.environ.get("ZAP_HOME") or Path.home() / ".zap")
CONFIG_FILE, APPS_CACHE, HISTORY = HOME / "config.json", HOME / "apps.json", HOME / "history.txt"
NO_WINDOW, DETACHED, NEW_GROUP = 0x08000000, 0x00000008, 0x00000200

os.system("")  # enable ANSI colours in the Windows console
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
DIM, RED, ACCENT, RESET, CLEAR_LINE = "\x1b[90m", "\x1b[31m", "\x1b[36m", "\x1b[0m", "\r\x1b[2K"
PROMPT_STYLE = "ps"

# ------------------------------------------------------------------ looks
# name, background, text, accent (prompt + logo), dim (hints), error, prompt style.
# Switch with /look and a number. Look 0 leaves your terminal's own colours alone.
LOOKS = [
    ("Classic",    "#0C0C0C", "#CCCCCC", "#3B8EEA", "#767676", "#E74856", "ps"),
    ("PowerShell", "#012456", "#EEEDF0", "#F9F1A5", "#8FA3C4", "#FF7B7B", "ps"),
    ("Midnight",   "#0B1020", "#C8D3F5", "#82AAFF", "#5B6B95", "#FF757F", "arrow"),
    ("Matrix",     "#000000", "#00E65C", "#7CFFB2", "#0A8F3C", "#FF5555", "arrow"),
    ("Vampire",    "#22212C", "#F8F8F2", "#BD93F9", "#7970A9", "#FF6E6E", "arrow"),
    ("Arctic",     "#2B303B", "#D8DEE9", "#88C0D0", "#6C7A96", "#D0707A", "arrow"),
    ("Amber",      "#140D00", "#FFB000", "#FFD466", "#9A6A00", "#FF5A36", "ps"),
    ("Synthwave",  "#1A1030", "#F4EEFF", "#FF4FD8", "#8B78B8", "#FF5370", "bolt"),
    ("Ocean",      "#001B26", "#B7D6DF", "#2EC4B6", "#4F7C8A", "#FF6B6B", "arrow"),
    ("Paper",      "#FAFAF7", "#2B2B2B", "#0B63CE", "#8A8A85", "#C62828", "arrow"),
]

def _rgb(hex_colour: str) -> str:
    h = hex_colour.lstrip("#")
    return f"{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}"

def fg(hex_colour: str) -> str:
    return f"\x1b[38;2;{_rgb(hex_colour)}m"

def bg(hex_colour: str) -> str:
    return f"\x1b[48;2;{_rgb(hex_colour)}m"

def apply_look(n: int) -> str:
    """Recolour the whole window (OSC 10/11/12, supported by Windows Terminal) and our own accents."""
    global DIM, RED, ACCENT, PROMPT_STYLE
    out = sys.stdout
    if not 1 <= n <= len(LOOKS):
        DIM, RED, ACCENT, PROMPT_STYLE = "\x1b[90m", "\x1b[31m", "\x1b[36m", "ps"
        if out.isatty():
            out.write("\x1b]110\x07\x1b]111\x07\x1b]112\x07"); out.flush()  # back to the terminal's own colours
        return "your terminal's own colours"
    name, back, text, accent, dim, err, style = LOOKS[n - 1]
    DIM, RED, ACCENT, PROMPT_STYLE = fg(dim), fg(err), fg(accent), style
    if out.isatty():
        out.write(f"\x1b]11;{back}\x07\x1b]10;{text}\x07\x1b]12;{accent}\x07"); out.flush()
    return name

def show_looks():
    current = CFG.get("look", 0)
    print()
    for i, (name, back, text, accent, dim, err, style) in enumerate(LOOKS, 1):
        mark = f"{ACCENT}>{RESET}" if i == current else " "
        sample = {"ps": f"{fg(text)}PS C:\\> ", "arrow": f"{fg(accent)}~ ❯ ", "bolt": f"{fg(accent)}⚡ ~ › "}[style]
        swatch = f"{bg(back)} {sample}{fg(text)}open paint {fg(dim)}opened paint {RESET}"
        print(f" {mark}{ACCENT}{i:>2}{RESET}  {name:<11} {swatch}")
    print(f"  {ACCENT} 0{RESET}  {'Yours':<11} {DIM}keep my terminal's own colours{RESET}")
    print(f"\n{DIM}type a number{RESET}")

def set_look(choice: str) -> bool:
    choice = choice.strip().lower()
    names = [l[0].lower() for l in LOOKS]
    if choice.isdigit() and int(choice) <= len(LOOKS):
        n = int(choice)
    elif choice in names:
        n = names.index(choice) + 1
    elif choice in ("yours", "none", "off", "default"):
        n = 0
    else:
        return False
    CFG["look"] = n
    save_config()
    print(f"{DIM}look: {apply_look(n)}{RESET}")
    return True

def short_cwd() -> str:
    cwd, home = os.getcwd(), str(Path.home())
    path = "~" + cwd[len(home):] if cwd.lower().startswith(home.lower()) else cwd
    parts = path.split("\\")
    return path if len(parts) <= 3 else f"{parts[0]}\\…\\{parts[-1]}"

def prompt_text() -> str:
    if PROMPT_STYLE == "arrow":
        return f"{ACCENT}{short_cwd()} ❯{RESET} "
    if PROMPT_STYLE == "bolt":
        return f"{ACCENT}⚡{RESET} {short_cwd()} {ACCENT}›{RESET} "
    return f"PS {os.getcwd()}> "

LOGO = (" ╺━┓┏━┓┏━┓", " ┏━┛┣━┫┣━┛", " ┗━╸╹ ╹╹  ")

def banner(brain_ready: bool):
    if not CFG.get("banner", True):
        return
    note = "" if brain_ready else "  ·  Claude Code not found: apps and commands only"
    print(f"{ACCENT}{LOGO[0]}{RESET}")
    print(f"{ACCENT}{LOGO[1]}{RESET}   just say it.")
    print(f"{ACCENT}{LOGO[2]}{RESET}   {DIM}v{__version__} · {CFG['model']} · /help · /look{note}{RESET}\n")

# ------------------------------------------------------------------ config
DEFAULTS = {
    "title": "Zap",
    "model": "haiku",                 # haiku = fastest; sonnet / opus = smarter
    "effort": "low",
    "permission_mode": "auto",        # Claude Code's own safety classifier decides what may run
    "show_timing": True,
    "look": 0,                        # 0 = your terminal's colours; 1-10 = a Zap look (/look)
    "banner": True,
    "confirm_changes": True,          # Claude asks "Go ahead?" before deleting / moving / changing things
    "recipes": {},                    # saved with /save: Claude tasks that now run instantly
    "aliases": {},                    # "work": "C:\\Projects", "music": "spotify" ...
    "sites": {},                      # "jira": "https://..."
    "search_roots": ["~/Desktop", "~/Downloads", "~/Documents", "~/Videos", "~/Pictures"],
    "about_me": "",                   # anything Claude should know about your PC (folders, habits)
    "hotkey": "ctrl+t",               # used by zap_hotkey.pyw
    "hotkey_pass_through": [],        # apps that keep their own hotkey, e.g. ["chrome.exe"]
}

def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except FileNotFoundError:
        HOME.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"{RED}config.json has an error, using defaults: {e}{RESET}")
    return cfg

def save_config():
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(CFG, indent=2, ensure_ascii=False), encoding="utf-8")

CFG = load_config()

def clean_env() -> dict:
    """Environment for child processes without markers from a parent Claude Code session."""
    inside = any(k in os.environ for k in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID"))
    return {k: v for k, v in os.environ.items()
            if not (k.startswith("CLAUDE") or k == "AI_AGENT" or (inside and k == "ANTHROPIC_BASE_URL"))}

# ------------------------------------------------------------------ fast path: open things
ALIASES = {
    "paint": "mspaint", "notepad": "notepad", "calc": "calc", "calculator": "calc", "cmd": "cmd",
    "terminal": "wt", "powershell": "powershell", "explorer": "explorer", "files": "explorer",
    "file explorer": "explorer", "task manager": "taskmgr", "control panel": "control",
    "settings": "ms-settings:", "wifi": "ms-settings:network-wifi", "bluetooth": "ms-settings:bluetooth",
    "sound": "ms-settings:sound", "display": "ms-settings:display", "apps": "ms-settings:appsfeatures",
    "updates": "ms-settings:windowsupdate", "snipping tool": "snippingtool", "vs code": "code", "vscode": "code",
    "downloads": "~/Downloads", "desktop": "~/Desktop", "documents": "~/Documents", "pictures": "~/Pictures",
    "videos": "~/Videos", "music": "~/Music", "home": "~", "recycle bin": "shell:RecycleBinFolder",
    "startup": "shell:startup",
}
SITES = {
    "youtube": "https://youtube.com", "gmail": "https://mail.google.com", "google": "https://google.com",
    "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai", "github": "https://github.com",
    "whatsapp web": "https://web.whatsapp.com", "facebook": "https://facebook.com", "instagram": "https://instagram.com",
    "tiktok": "https://tiktok.com", "x": "https://x.com", "twitter": "https://x.com", "reddit": "https://reddit.com",
    "netflix": "https://netflix.com", "drive": "https://drive.google.com", "maps": "https://maps.google.com",
    "calendar": "https://calendar.google.com", "linkedin": "https://linkedin.com", "amazon": "https://amazon.com",
}
SEARCH = {"google": "https://www.google.com/search?q=", "search": "https://www.google.com/search?q=",
          "youtube": "https://www.youtube.com/results?search_query=", "yt": "https://www.youtube.com/results?search_query=",
          "maps": "https://www.google.com/maps/search/", "github": "https://github.com/search?q=",
          "amazon": "https://www.amazon.com/s?k=", "translate": "https://translate.google.com/?sl=auto&tl=en&text="}
# process image names for apps whose window name differs from the exe
PROCESS_NAMES = {"paint": "mspaint.exe", "calculator": "CalculatorApp.exe", "calc": "CalculatorApp.exe",
                 "vs code": "Code.exe", "vscode": "Code.exe", "word": "WINWORD.EXE", "excel": "EXCEL.EXE",
                 "powerpoint": "POWERPNT.EXE", "outlook": "OUTLOOK.EXE", "edge": "msedge.exe",
                 "file explorer": "explorer.exe", "notepad": "Notepad.exe", "teams": "ms-teams.exe"}
NOISE = re.compile(r"uninstall|readme|read me|help|documentation|website|release notes|license|manual", re.I)
APPS: dict[str, str] = {}

def _scan_shortcuts() -> dict:
    found = {}
    dirs = [Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
            Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
            Path.home() / "Desktop", Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"]
    for d in dirs:
        if d.is_dir():
            for f in list(d.rglob("*.lnk")) + list(d.glob("*.url")):
                found.setdefault(f.stem.lower(), str(f))
    return found

def _scan_store_apps() -> dict:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
                         capture_output=True, text=True, encoding="utf-8", creationflags=NO_WINDOW, timeout=30)
    data = json.loads(out.stdout or "[]")
    data = [data] if isinstance(data, dict) else data
    return {a["Name"].lower(): "shell:AppsFolder\\" + a["AppID"] for a in data if a.get("Name")}

def refresh_apps(sync: bool = False):
    def work():
        apps = _scan_shortcuts()
        try:
            apps.update(_scan_store_apps())
        except Exception:
            pass
        APPS.clear(); APPS.update({k: v for k, v in apps.items() if not NOISE.search(k)})
        try:
            HOME.mkdir(parents=True, exist_ok=True)
            APPS_CACHE.write_text(json.dumps(APPS), encoding="utf-8")
        except OSError:
            pass
    work() if sync else threading.Thread(target=work, daemon=True).start()

def load_apps():
    try:
        APPS.update(json.loads(APPS_CACHE.read_text(encoding="utf-8")))
    except Exception:
        APPS.update({k: v for k, v in _scan_shortcuts().items() if not NOISE.search(k)})
    refresh_apps()  # keep the cache fresh in the background

def expand(target: str) -> str:
    return os.path.expandvars(os.path.expanduser(target)) if target.startswith(("~", "%")) else target

def find_app(name: str, allow_path: bool = False) -> str | None:
    name = re.sub(r"\s+(app|application|program|website|site)$", "", name.strip().lower())
    if not name:
        return None
    user_aliases = {k.lower(): v for k, v in CFG.get("aliases", {}).items()}
    user_sites = {k.lower(): v for k, v in CFG.get("sites", {}).items()}
    for table in (user_aliases, user_sites, ALIASES, SITES, APPS):
        if name in table:
            return expand(table[name])
    starts = [k for k in APPS if k.startswith(name)]
    words = [k for k in APPS if re.search(r"\b" + re.escape(name) + r"\b", k)]
    hits = starts or words
    if hits:
        return APPS[min(hits, key=len)]
    if allow_path and " " not in name and len(name) > 2 and shutil.which(name):  # only after "open X"
        return shutil.which(name)
    names = list(user_aliases) + list(ALIASES) + list(SITES) + list(APPS)
    close = difflib.get_close_matches(name, names, n=1, cutoff=0.82)  # typo tolerance: "spotfy"
    if close:
        return find_app(close[0])
    if " " not in name and len(name) >= 4:  # "chorme" -> the "chrome" in "google chrome"
        words = {}
        for k in sorted(APPS, key=len):
            for w in k.split():
                if len(w) >= 4:
                    words.setdefault(w, k)
        close = difflib.get_close_matches(name, list(words), n=1, cutoff=0.8)
        if close:
            return APPS[words[close[0]]]
    return None

def launch(target: str):
    """Start fully detached, so the app never prints its own logs into this window."""
    subprocess.Popen(["cmd", "/c", "start", "", target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=DETACHED | NEW_GROUP | NO_WINDOW, close_fds=True)

# ------------------------------------------------------------------ fast path: close things
def running_processes() -> list[str]:
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                         creationflags=NO_WINDOW, errors="replace").stdout
    return sorted({line.split('","')[0].strip('"') for line in out.splitlines() if line.startswith('"')})

def close_app(name: str) -> bool:
    name = name.strip().lower()
    procs = running_processes()
    low = {p.lower(): p for p in procs}
    want = PROCESS_NAMES.get(name)
    target = low.get(want.lower()) if want else None
    if not target:
        key = re.sub(r"[^a-z0-9]", "", name)
        cands = [p for p in procs if key and key in re.sub(r"[^a-z0-9]", "", p.lower())]
        cands = [p for p in cands if p.lower() not in ("svchost.exe", "explorer.exe", "python.exe", "py.exe")]
        target = min(cands, key=len) if cands else None
    if not target:
        return False
    # Ask each visible window to close, exactly like clicking X (the app may still ask to save).
    windows = app_windows(target)
    for hwnd in windows:
        ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), 0x0010, 0, 0)  # WM_CLOSE
    for _ in range(15):
        time.sleep(0.1)
        if not any(p.lower() == target.lower() for p in running_processes()):
            say(f"closed {name}"); return True
    if windows and app_windows(target):
        say(f"{name} is still open (it may be asking to save). Say 'force close {name}' to end it.")
        return True
    # nothing on screen any more (tray / background): ending it loses nothing visible
    subprocess.run(["taskkill", "/IM", target, "/F"], capture_output=True, creationflags=NO_WINDOW)
    say(f"closed {name}")
    return True

def force_close(name: str) -> bool:
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    want = (PROCESS_NAMES.get(name.lower()) or "").lower()
    hits = [p for p in running_processes() if p.lower() == want or (key and key in re.sub(r"[^a-z0-9]", "", p.lower()))]
    hits = [p for p in hits if p.lower() not in ("svchost.exe", "explorer.exe", "python.exe", "py.exe")]
    if not hits:
        return False
    subprocess.run(["taskkill", "/IM", min(hits, key=len), "/F", "/T"], capture_output=True, creationflags=NO_WINDOW)
    say(f"force closed {name}")
    return True

def app_windows(image: str) -> list:
    """Visible, titled top-level windows that belong to processes named `image`."""
    from ctypes import wintypes
    user32, kernel32 = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            h = kernel32.OpenProcess(0x1000, False, pid.value)
            if h:
                buf, n = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
                if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) and \
                        os.path.basename(buf.value).lower() == image.lower():
                    found.append(hwnd)
                kernel32.CloseHandle(h)
        return True
    user32.EnumWindows(cb, 0)
    return found

# ------------------------------------------------------------------ fast path router
VERB = re.compile(r"^(please\s+)?(open|launch|start|run|show)\s+(up\s+)?(the\s+|my\s+)?", re.I)
CLOSE = re.compile(r"^(please\s+)?(close|quit|kill|exit|stop)\s+(the\s+|my\s+)?(.+?)(\s+app)?$", re.I)
SHELL_WORDS = {"cd", "ls", "dir", "cls", "clear", "pwd", "echo", "cat", "type", "mkdir", "md", "rm", "del", "rmdir",
               "copy", "cp", "move", "mv", "ren", "git", "npm", "npx", "node", "py", "python", "pip", "ping",
               "ipconfig", "tasklist", "taskkill", "winget", "choco", "scoop", "curl", "wget", "ssh", "scp", "tree",
               "whoami", "hostname", "systeminfo", "shutdown", "netstat", "nslookup", "tracert", "where", "set",
               "ffmpeg", "yt-dlp", "gh", "docker", "deno", "bun", "pnpm", "yarn", "rg", "cargo", "go", "java",
               "dotnet", "code", "start", "get-process", "get-childitem", "get-content", "set-location",
               "start-process", "stop-process", "get-service", "select-string", "test-path", "invoke-webrequest"}
SENTENCE = re.compile(r"\b(the|my|a|an|me|please|what|how|why|can|could|all|for|to|and|is|it|this|that|which|"
                      r"find|show|make|turn|i|you|with|from|in|on|of)\b", re.I)

def say(msg: str):
    print(f"{DIM}{msg}{RESET}")

def try_fast(line: str) -> bool:
    """Handle the line locally in milliseconds. False = not ours."""
    low = line.strip().lower()
    words = low.split()
    if not words:
        return True
    m = re.match(r"^(google|search|youtube|yt|maps|github|amazon|translate)\s+(?:for\s+)?(.+)$", low)
    if m and not VERB.match(low):
        webbrowser.open(SEARCH[m.group(1)] + quote_plus(m.group(2)))
        say(f"{m.group(1)}: {m.group(2)}")
        return True
    m = re.match(r"^force\s+(?:close|quit|kill)\s+(.+)$", low)
    if m:
        return force_close(m.group(1))
    m = CLOSE.match(low)
    if m and len(m.group(4).split()) <= 3:
        return close_app(m.group(4))
    had_verb = bool(VERB.match(low))
    if not had_verb and words[0] in SHELL_WORDS:
        return False
    name = VERB.sub("", low)
    if not had_verb and (len(name.split()) > 3 or SENTENCE.search(name) and len(name.split()) > 1):
        return False
    target = find_app(name, allow_path=had_verb)
    if not target:
        return False
    launch(target)
    say(f"opened {name}")
    return True

# ------------------------------------------------------------------ shell path
def looks_like_shell(line: str) -> bool:
    words = line.split()
    first = words[0].lower()
    if first in SHELL_WORDS:
        return not (len(words) > 3 and SENTENCE.search(line) and not re.search(r"[-/\\.\"'|>$]", line))
    if re.search(r"[|>$]|^\.\\|^[a-z]:\\", line, re.I):
        return True
    exe = shutil.which(first)
    if not exe or len(words) > 3 or SENTENCE.search(line):
        return False
    # Git for Windows puts Unix tools named like English words on PATH (yes, test, time, sort, more...).
    # A bare word like that is a person talking, not a command.
    unixy = "/usr/bin/" in exe.lower().replace(os.sep, "/") or "/mingw64/" in exe.lower().replace(os.sep, "/")
    waits_for_input = first in ("more", "sort", "find", "print", "clip", "choice", "pause")
    return not ((unixy or waits_for_input) and len(words) == 1)

def run_shell(line: str):
    s = line.strip()
    m = re.match(r"^(cd|chdir|set-location)(?:\s+(.*))?$", s, re.I)
    if m:
        target = (m.group(2) or "~").strip().strip('"')
        try:
            os.chdir(expand(target) if target != "-" else os.environ.get("OLDPWD", os.getcwd()))
        except OSError:
            # forgiving cd: "cd desk" -> Desktop
            here = [d for d in os.listdir(".") if os.path.isdir(d) and d.lower().startswith(target.lower())]
            if here:
                os.chdir(here[0])
            else:
                print(f"{RED}no such folder: {target}{RESET}")
        return
    if s.lower() in ("cls", "clear"):
        os.system("cls"); return
    global LAST_FAIL
    LAST_FAIL = None
    first = s.split()[0].lower()
    known = bool(shutil.which(first))
    try:
        code = subprocess.run(["powershell", "-NoProfile", "-NoLogo", "-Command", s]).returncode
        if INSTALLER.match(s) or (code != 0 and not known):
            refresh_path()  # a new install is usable right away, no restart
            if code != 0 and not known and shutil.which(first):
                code = subprocess.run(["powershell", "-NoProfile", "-NoLogo", "-Command", s]).returncode
        if code != 0:
            LAST_FAIL = (s, code, os.getcwd())
            say("that failed. type fix and Claude will find out why and sort it out")
    except KeyboardInterrupt:
        pass

LAST_FAIL = None
INSTALLER = re.compile(r"^(winget|choco|scoop)\s+(install|upgrade)|^(pip|pip3|npm|pnpm|yarn|bun|cargo|go|dotnet)\s+"
                       r"(install|i|add|tool)\b", re.I)

def refresh_path():
    """Re-read PATH from the registry so freshly installed programs work in this window ("not recognized" fix)."""
    import winreg
    parts = []
    for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(root, key) as k:
                parts += [os.path.expandvars(x) for x in winreg.QueryValueEx(k, "Path")[0].split(";") if x]
        except OSError:
            pass
    seen, merged = set(), []
    for x in parts + os.environ.get("PATH", "").split(";"):
        if x and x.lower().rstrip("\\") not in seen:
            seen.add(x.lower().rstrip("\\")); merged.append(x)
    os.environ["PATH"] = ";".join(merged)

def install(name: str):
    """install ffmpeg -> winget does it, PATH is refreshed, and we prove it works in this window."""
    if not shutil.which("winget"):
        print(f"{RED}winget is missing (it ships with Windows 10/11 'App Installer').{RESET}"); return
    try:
        subprocess.run(["winget", "install", name])
    except KeyboardInterrupt:
        return
    refresh_path()
    exe = shutil.which(name) or shutil.which(name.split(".")[-1])
    say(f"{name} is ready in this window: {exe}" if exe else "done. if it added a command, it works in this window now")

# Commands that deserve a plain-words warning before they run (typed, pasted or forced with !).
DANGER = [
    (r"\brm\s+-[a-z]*[rf]|Remove-Item\b.*-Recurse|\b(rd|rmdir)\s+/s|\bdel\s+/[sqf]", "deletes files and folders for good (no Recycle Bin)"),
    (r"\bformat\s+[a-z]:|\bdiskpart\b|Clear-Disk|Format-Volume", "can wipe a whole drive"),
    (r"\b(iex|Invoke-Expression)\b|\|\s*(iex|sh|bash|cmd)\b|DownloadString|-urlcache", "downloads code from the internet and runs it straight away"),
    (r"\s-e(nc|ncodedcommand|c)?\s+[A-Za-z0-9+/=]{20,}|FromBase64String", "runs hidden, scrambled code - a common scam trick"),
    (r"\bmshta\b|rundll32.*(javascript|http)|regsvr32.*(http|scrobj)", "uses a trick that malware often uses"),
    (r"\breg\s+delete|Remove-ItemProperty\b.*HK(LM|CU)", "deletes Windows settings from the registry"),
    (r"Set-MpPreference|DisableRealtimeMonitoring|advfirewall.*\boff\b|Add-MpPreference.*Exclusion", "weakens your antivirus or firewall"),
    (r"\bbcdedit\b|\bcipher\s+/w|\btakeown\b|Disable-ComputerRestore|vssadmin.*delete", "changes deep system settings that are hard to undo"),
    (r"git\s+push\b.*(--force|-f\b)|git\s+reset\s+--hard|git\s+clean\s+-[a-z]*f", "can destroy work in git that cannot be brought back"),
    (r"^(shutdown|Stop-Computer|Restart-Computer)\b", "shuts down or restarts this PC"),
]
PENDING_DANGER = None

def danger_of(line: str) -> str | None:
    for pattern, meaning in DANGER:
        if re.search(pattern, line, re.I):
            return meaning
    return None

def guard(line: str) -> bool:
    """True if we stopped a risky command and are waiting for a yes."""
    global PENDING_DANGER
    meaning = danger_of(line)
    if not meaning:
        return False
    PENDING_DANGER = line
    print(f"{RED}careful: this command {meaning}.{RESET}")
    say("type yes to run it anyway, anything else cancels. not sure? type: explain")
    return True

# ---- instant utilities (no AI) -----------------------------------------------------
def port_owners(port: str) -> dict:
    out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
                         creationflags=NO_WINDOW, errors="replace").stdout
    pids = {l.split()[-1] for l in out.splitlines()
            if "LISTENING" in l and re.search(rf":{port}\s", l.split()[1] + " ")}
    names = {}
    if pids:
        tl = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                            creationflags=NO_WINDOW, errors="replace").stdout
        for row in tl.splitlines():
            cols = row.strip('"').split('","')
            if len(cols) > 1 and cols[1] in pids:
                names[cols[1]] = cols[0]
    return {pid: names.get(pid, "?") for pid in pids}

def utility(low: str) -> bool:
    m = re.match(r"^(?:free|kill|close|stop)\s+(?:up\s+)?port\s+(\d{2,5})$", low)
    if m:
        owners = port_owners(m.group(1))
        if not owners:
            say(f"port {m.group(1)} is already free")
        for pid, name in owners.items():
            subprocess.run(["taskkill", "/PID", pid, "/F", "/T"], capture_output=True, creationflags=NO_WINDOW)
            say(f"port {m.group(1)} is free now (ended {name})")
        return True
    m = re.match(r"^(?:what(?:'s| is)?\s+(?:on|using)\s+|who(?:'s| is)?\s+(?:on|using)\s+)?port\s+(\d{2,5})\??$", low)
    if m:
        owners = port_owners(m.group(1))
        say(f"port {m.group(1)}: " + (", ".join(f"{n} (pid {p})" for p, n in owners.items()) if owners else "nothing is using it"))
        return True
    if low in ("disk", "disk space", "space", "storage", "free space", "how much space", "how much space is left"):
        for letter in "CDEFGHIJ":
            try:
                total, used, free = shutil.disk_usage(f"{letter}:\\")
            except OSError:
                continue
            filled = round(20 * used / total)
            colour = RED if used / total > 0.9 else ACCENT
            print(f"  {letter}:  {colour}{'█' * filled}{DIM}{'░' * (20 - filled)}{RESET}  "
                  f"{free / 2**30:.0f} GB free of {total / 2**30:.0f} GB")
        say("want to know what is taking the space? ask: what is eating my disk")
        return True
    if low in ("ip", "my ip", "ip address", "what is my ip", "what's my ip"):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.connect(("10.255.255.255", 1))
            print(f"  {sock.getsockname()[0]}  {DIM}(this PC on your network){RESET}"); sock.close()
        except OSError:
            say("not connected to a network")
        return True
    m = re.match(r"^install\s+([\w.+-]+)$", low)
    if m:
        install(m.group(1)); return True
    return False

# ---- recipes: a Claude task that worked, replayed instantly ---------------------------
def run_recipe(name: str) -> bool:
    recipe = {k.lower(): v for k, v in CFG.get("recipes", {}).items()}.get(name.lower())
    if not recipe:
        return False
    for tool, command in recipe["run"]:
        shell = ["powershell", "-NoProfile", "-NoLogo", "-Command", command] if tool != "Bash" else ["bash", "-lc", command]
        subprocess.run(shell, capture_output=True, creationflags=NO_WINDOW)
    say(f"done: {name}")
    return True

# ------------------------------------------------------------------ Claude path
BASE_PROMPT = r"""You are running inside Zap, a small terminal on the user's Windows PC. They type short instructions to get things done on their computer (open things, find files, change settings, close apps, quick questions). Act immediately with your tools - prefer one fast PowerShell command; do not ask questions unless truly needed. Use Start-Process to open apps, files and URLs. Answer in one or two short lines of plain text: no markdown, no headings, no code blocks, no bullet lists. Simple English.

SPEED RULES (they want answers in seconds): every command must finish in under 5 seconds. NEVER use Get-ChildItem -Recurse on big folders (the user folder, Desktop, C:\ or D:\) - it takes minutes. {finder}Do one search, act on the best match, and stop - no second confirming searches.

DO WHAT THEY WANT WITH THE FILE: if they want to put / paste / add / edit something in a file (an API key, a setting, a note), find it and OPEN it for them: Start-Process notepad '<file>'. If only an example file exists (.env.example, config.example.json), copy it to the real name (.env, config.json) next to it and open the copy. Say which file you opened and which line to fill in.

SECRETS: never print the contents or values of .env files, keys, passwords or tokens - list file names and variable names only. To show a file in its folder: Start-Process explorer -ArgumentList '/select,"<file>"'.

WINDOWS ONLY: this is Windows PowerShell. Never use Linux/bash syntax (no ls -la, grep, rm, cat, &&, export, ~/): use PowerShell cmdlets.

{safety}MEDIA AND DOWNLOADS: for video, audio and image jobs (convert, cut, resize, compress, make vertical, extract audio, download a video) use ffmpeg / yt-dlp when installed (check with Get-Command). If one is missing, say so in one line and tell them to type: install ffmpeg (or: install yt-dlp). These jobs may take longer than 5 seconds - that is fine. Never overwrite the original file: write the result next to it with a clear new name and say where it is.

NEWEST FILES: when asked for the latest / last / newest file, search all of these folders at once, newest first, and take the first result - never narrow to one folder on a guess: {roots}"""

SKIP = ("-g '!node_modules' -g '!AppData' -g '!.git' -g '!.venv' -g '!venv' -g '!.next' -g '!dist' -g '!build' "
        "-g '!.cache' -g '!site-packages' -g '!__pycache__' -g '!.turbo' -g '!.vercel' -g '!target'")
RG_FINDER = ("To find files use ripgrep (installed, about 1 second) on the folders listed below, never on the whole "
             "user folder or a drive, and ALWAYS with these skips (without them it takes 10x longer): "
             f"rg --files --no-ignore --hidden --sortr modified -g '*.mp4' {SKIP} <folders> | Select-Object -First 20 "
             "(--sortr modified = newest first; --hidden is needed for dot files like .env). To find files by what is "
             "INSIDE them, use -l -i '<text>' instead of --files, e.g. "
             f"rg -l -i --no-ignore --hidden --sortr modified -g '.env*' -g '*.env' {SKIP} 'gemini' <folders>. ")
PS_FINDER = ("To find files use Get-ChildItem -Recurse -Depth 4 -File -ErrorAction SilentlyContinue on the specific "
             "folders below, filtered with -Filter, sorted by LastWriteTime. ")

SAFETY = """SAFETY (the user may not be technical and must be able to trust you): before anything that deletes, overwrites, moves or renames files, uninstalls software or changes system settings, do NOT act yet. Reply with one plain line saying exactly what will happen (which folder, how many files, how much space) and end with: Go ahead? Act only after they answer yes. Never delete permanently - send things to the Recycle Bin: Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('<path>','OnlyErrorDialogs','SendToRecycleBin') (DeleteDirectory for folders), and tell them it can be restored from the Recycle Bin. Opening, finding, reading, showing and closing apps need no confirmation.

"""

def system_prompt() -> str:
    roots = " ".join(f'"{expand(r)}"' for r in CFG.get("search_roots", []))
    p = BASE_PROMPT.format(finder=RG_FINDER if shutil.which("rg") else PS_FINDER, roots=roots,
                           safety=SAFETY if CFG.get("confirm_changes", True) else "")
    if CFG.get("about_me"):
        p += "\n\nABOUT THIS USER AND PC:\n" + CFG["about_me"]
    return p

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

class Brain:
    """One Claude Code process kept warm (stream-json in/out), so each request skips the start-up cost."""

    def __init__(self):
        self.exe = shutil.which("claude") or str(Path.home() / ".local/bin/claude.exe")
        self.proc, self.sid = None, None
        self.done, self.lock, self.out_lock = threading.Event(), threading.Lock(), threading.Lock()
        self.err: list[str] = []
        self.at_line_start, self.busy, self.spinning = True, False, False
        self.last_out = 0.0
        self.last_request, self.last_commands = "", []
        self.turn_text, self.asked = "", False   # asked: Claude ended with a question

    def available(self) -> bool:
        return Path(self.exe).exists() or bool(shutil.which(self.exe))

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        with self.lock:
            if self.alive():
                return
            new = self.sid is None
            if new:
                self.sid = str(uuid.uuid4())
            cmd = [self.exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                   "--verbose", "--include-partial-messages",
                   "--model", CFG["model"], "--effort", CFG["effort"],
                   "--permission-mode", CFG["permission_mode"],
                   "--strict-mcp-config",                # no MCP servers: much faster start
                   # Claude's own Grep/Glob walk whole drives (incl. AppData) and take minutes;
                   # it must use one bounded ripgrep command instead. No sub-agents either.
                   "--disallowedTools", "Grep", "Glob", "Agent", "Task",
                   "--append-system-prompt", system_prompt()]
            cmd += ["--session-id", self.sid] if new else ["--resume", self.sid]
            self.err = []
            self.proc = subprocess.Popen(cmd, cwd=str(Path.home()), env=clean_env(), stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
            threading.Thread(target=self._read_out, args=(self.proc,), daemon=True).start()
            threading.Thread(target=self._read_err, args=(self.proc,), daemon=True).start()

    def warm(self):
        if self.available():
            threading.Thread(target=self.start, daemon=True).start()

    # ---- output
    def _read_err(self, p):
        for line in p.stderr:
            self.err.append(line); del self.err[:-30]

    def _write(self, s: str):
        if not s:
            return
        with self.out_lock:
            if self.spinning:
                sys.stdout.write(CLEAR_LINE); self.spinning = False
            sys.stdout.write(s); sys.stdout.flush()
            self.at_line_start = s.endswith("\n")
            self.last_out = time.time()

    def _spinner(self, t0: float):
        i = 0
        while self.busy:
            time.sleep(0.08)
            with self.out_lock:
                if self.busy and self.at_line_start and time.time() - self.last_out > 0.25:
                    sys.stdout.write(f"{CLEAR_LINE}{DIM}{SPIN[i % len(SPIN)]} {time.time() - t0:.1f}s{RESET}")
                    sys.stdout.flush(); self.spinning = True; i += 1
        with self.out_lock:
            if self.spinning:
                sys.stdout.write(CLEAR_LINE); sys.stdout.flush(); self.spinning = False

    def _read_out(self, p):
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("parent_tool_use_id"):
                continue
            t = ev.get("type")
            if t == "stream_event":
                e = ev.get("event", {})
                if e.get("type") == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                    self._write(e["delta"]["text"]); self.turn_text += e["delta"]["text"]
                elif e.get("type") == "content_block_stop" and not self.at_line_start:
                    self._write("\n")
            elif t == "assistant":
                for b in ev.get("message", {}).get("content", []):
                    if b.get("type") == "tool_use":
                        self._write(f"{DIM}  > {tool_summary(b)}{RESET}\n")
                        cmd = (b.get("input") or {}).get("command")
                        if cmd and b.get("name") in ("PowerShell", "Bash"):
                            self.last_commands.append([b["name"], cmd])
            elif t == "result":
                if ev.get("is_error"):
                    msg = str(ev.get("result") or "Claude hit an error")
                    if re.search(r"authenticat|oauth|not logged in|/login|api key", msg, re.I):
                        msg = "Claude Code is not signed in. Type /login once (a browser page opens)."
                    elif re.search(r"credit|usage limit|rate limit", msg, re.I):
                        msg = "Claude says: " + msg
                    self._write(f"{RED}{msg}{RESET}\n")
                if not self.at_line_start:
                    self._write("\n")
                self.done.set()
        if p is self.proc:
            blob = "".join(self.err).strip()
            if blob:
                self._write(f"{RED}Claude stopped: {blob[-300:]}{RESET}\n")
            self.proc = None
            self.done.set()

    # ---- requests
    def ask(self, text: str, remember: str = ""):
        self.last_request, self.last_commands = remember, []
        self.turn_text, self.asked = "", False
        if not self.available():
            print(f"{RED}Claude Code is not installed. Get it at https://claude.com/claude-code{RESET}"); return
        self.done.clear()
        for _ in (1, 2):
            self.start()
            try:
                self.proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
                self.proc.stdin.flush()
                break
            except (OSError, ValueError, AttributeError):
                self.kill()
        else:
            print(f"{RED}could not reach Claude{RESET}"); return
        t0 = self.last_out = time.time()
        self.busy = True
        spin = threading.Thread(target=self._spinner, args=(t0,), daemon=True); spin.start()
        try:
            while not self.done.wait(0.1):
                pass
        except KeyboardInterrupt:
            self.interrupt()
            self.done.wait(5)
            self._write(f"{DIM}stopped{RESET}\n")
        finally:
            self.busy = False
            spin.join(0.5)
        self.asked = self.turn_text.rstrip().endswith("?")
        if CFG.get("show_timing"):
            self._write(f"{DIM}{time.time() - t0:.1f}s{RESET}\n")

    def interrupt(self):
        try:
            self.proc.stdin.write(json.dumps({"type": "control_request", "request_id": uuid.uuid4().hex[:8],
                                              "request": {"subtype": "interrupt"}}) + "\n")
            self.proc.stdin.flush()
        except Exception:
            pass

    def kill(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, creationflags=NO_WINDOW)

    def reset(self):
        self.kill(); self.sid = None; self.warm()

def tool_summary(b: dict) -> str:
    i = b.get("input", {}) or {}
    v = i.get("command") or i.get("file_path") or i.get("pattern") or i.get("url") or i.get("description") or ""
    v = " ".join(str(v).split())
    return f"{b.get('name', 'tool')}: {v[:110]}{'...' if len(v) > 110 else ''}"

# ------------------------------------------------------------------ input
def help_text() -> str:
    a, d, r = ACCENT, DIM, RESET
    return f"""
{a}instant{r}   paint · open chrome · youtube · google cats · close spotify · force close X
          install ffmpeg · free port 3000 · port 3000 · disk · ip
{a}shell{r}     dir · git status · ping x            {d}normal commands just work{r}
{a}ask{r}       anything else, in plain words        {d}Claude does it on this PC, and asks before changing things{r}

{a}safe{r}      risky commands are explained first   {d}type yes to run, explain to learn more{r}
{a}fix{r}       after a command fails                {d}Claude finds the cause and repairs it{r}
{a}/save{r}     name the last task                   {d}next time it runs instantly, no AI{r}   {a}/recipes{r}  {a}/forget{r}

{a}/look{r}     change the look (type a number)      {a}/alias{r} name = target   {a}/aliases{r}
{a}/model{r}    haiku | sonnet | opus                {a}/new{r}   fresh Claude chat
{a}/apps{r}     rescan apps   {a}/config{r} settings   {a}/login{r}   {a}exit{r}
{a}!cmd{r} force shell   {a}?text{r} force Claude   {a}Ctrl+C{r} stop Claude   {a}↑ → Tab{r} history, suggestion, complete
{d}settings: {CONFIG_FILE}{r}
"""

COMMANDS = ["/help", "/look", "/save", "/recipes", "/forget", "/new", "/model", "/alias", "/aliases", "/apps", "/config", "/login", "/exit"]

def make_reader():
    """prompt_toolkit gives history, fish-style suggestions and Tab completion; plain input() otherwise."""
    if not sys.stdin.isatty():  # piped input (scripts, tests)
        return lambda prompt: input(prompt)
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.formatted_text import ANSI
        from prompt_toolkit.history import FileHistory
    except ImportError:
        return lambda prompt: input(prompt)

    class ZapCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            low = text.lower()
            if low.startswith("/"):
                for c in COMMANDS:
                    if c.startswith(low):
                        yield Completion(c, -len(text))
                return
            m = re.match(r"^((?:open|launch|start|close|quit|kill)\s+)?(.*)$", low)
            stem = m.group(2)
            if len(stem) < 2 or " " in stem and not m.group(1):
                return
            pool = list(CFG.get("recipes", {})) + list(CFG.get("aliases", {})) + list(ALIASES) + list(SITES) + list(APPS)
            seen = set()
            for name in sorted(pool, key=len):
                if name.lower().startswith(stem) and name.lower() not in seen:
                    seen.add(name.lower())
                    yield Completion(name, -len(stem))
                    if len(seen) >= 8:
                        break

    HOME.mkdir(parents=True, exist_ok=True)
    session = PromptSession(history=FileHistory(str(HISTORY)), auto_suggest=AutoSuggestFromHistory(),
                            completer=ZapCompleter(), complete_while_typing=False)
    return lambda prompt: session.prompt(ANSI(prompt))

# ------------------------------------------------------------------ dispatch
PICKING_LOOK = False
ANSWERS = {"yes", "y", "no", "n", "ok", "okay", "sure", "yep", "nope", "go", "go ahead", "do it", "cancel", "thanks", "thank you"}
TIPS_SHOWN = 0

def handle(line: str, brain: Brain) -> bool:
    """Run one line. Returns False when the user wants to leave."""
    line = line.strip()
    low = line.lower()
    if not line:
        return True
    if low in ("exit", "quit", "/exit"):
        return False
    if low in ("/help", "help", "?"):
        print(help_text()); return True
    global PICKING_LOOK, PENDING_DANGER, TIPS_SHOWN
    if PENDING_DANGER:
        cmd, PENDING_DANGER = PENDING_DANGER, None
        if low in ("yes", "y"):
            run_shell(cmd)
        elif low.startswith("explain"):
            brain.ask(f"Explain in two plain sentences what this command would do on my PC and whether it is safe. "
                      f"Do NOT run it or any part of it: {cmd}")
        else:
            say("cancelled, nothing ran")
        return True
    if PICKING_LOOK:
        PICKING_LOOK = False
        if low.isdigit() and set_look(low):
            return True
    m = re.match(r"^/?looks?(?:\s+(.+))?$", low) or re.match(r"^/(?:theme|themes|appearance)(?:\s+(.+))?$", low)
    if m:
        if m.group(1):
            if not set_look(m.group(1)):
                say("no such look. Type /look to see them.")
        else:
            show_looks(); PICKING_LOOK = True
        return True
    if low == "/new":
        brain.reset(); say("new conversation"); return True
    if low.startswith("/model"):
        parts = line.split()
        if len(parts) > 1:
            CFG["model"] = parts[1]; save_config(); brain.reset()
        say(f"model: {CFG['model']}"); return True
    if low == "/apps":
        refresh_apps(sync=True); say(f"{len(APPS)} apps indexed"); return True
    if low == "/aliases":
        for k, v in sorted(CFG.get("aliases", {}).items()):
            print(f"  {k} {DIM}-> {v}{RESET}")
        if not CFG.get("aliases"):
            say("no aliases yet. Example: /alias work = C:\\Projects")
        return True
    if low.startswith("/alias"):
        m = re.match(r"^/alias\s+(.+?)\s*=\s*(.*)$", line, re.I)
        if not m:
            say("usage: /alias name = target   (target: app, folder, file, URL; empty target removes it)")
            return True
        name, target = m.group(1).lower(), m.group(2).strip().strip('"')
        CFG.setdefault("aliases", {})
        if target:
            CFG["aliases"][name] = target; say(f"{name} -> {target}")
        else:
            CFG["aliases"].pop(name, None); say(f"removed {name}")
        save_config(); return True
    if low == "/config":
        launch(str(CONFIG_FILE)); say("opened settings (restart Zap after editing)"); return True
    if low == "/login":
        brain.kill(); subprocess.run([brain.exe, "auth", "login"], env=clean_env()); brain.reset(); say("signed in")
        return True
    if getattr(brain, "asked", False) and not line.startswith(("/", "!")):
        brain.ask(line)  # Claude asked something (e.g. "Go ahead?"): this line is the answer
        return True
    if low in ANSWERS:
        say("nothing is waiting for an answer"); return True
    if low == "fix":
        if not LAST_FAIL:
            say("nothing failed just now"); return True
        cmd, code, where = LAST_FAIL
        brain.ask(f"I ran this in PowerShell in the folder {where} and it failed (exit code {code}): {cmd}\n"
                  f"Run it again to see the error, tell me the cause in one plain sentence, then fix it if that is safe.")
        return True
    m = re.match(r"^/save\s+(.+)$", line, re.I)
    if m or low == "/save":
        if not brain.last_commands:
            say("nothing to save yet. ask Claude to do something first, then /save a-name"); return True
        if not m:
            say("usage: /save name   (then typing that name repeats the task instantly)"); return True
        name = m.group(1).strip().lower()
        CFG.setdefault("recipes", {})[name] = {"say": brain.last_request, "run": brain.last_commands}
        save_config(); say(f"saved. type {name} to do it again instantly"); return True
    if low == "/recipes":
        for k, v in sorted(CFG.get("recipes", {}).items()):
            print(f"  {ACCENT}{k}{RESET} {DIM}= {v.get('say', '')[:70]}{RESET}")
        if not CFG.get("recipes"):
            say("no recipes yet. after Claude does something for you, type /save a-name")
        return True
    m = re.match(r"^/forget\s+(.+)$", line, re.I)
    if m:
        gone = CFG.get("recipes", {}).pop(m.group(1).strip().lower(), None)
        save_config(); say("forgotten" if gone else "no recipe with that name"); return True
    if line.startswith("!"):
        if not guard(line[1:]):
            run_shell(line[1:])
        return True
    if line.startswith("?"):
        brain.ask(line[1:].strip()); return True
    if run_recipe(line) or utility(low):
        return True
    shellish = looks_like_shell(line)
    if (shellish or re.search(r"[|;&$(){}]", line)) and guard(line):
        return True
    if try_fast(line):
        return True
    if shellish:
        run_shell(line); return True
    brain.ask(f"[current folder: {os.getcwd()}] {line}", remember=line)
    if brain.last_commands and TIPS_SHOWN < 2 and sys.stdin.isatty():
        TIPS_SHOWN += 1
        say("tip: /save a-name makes this instant next time")
    return True

def main(argv: list[str]):
    keep = clean_env()
    for k in [k for k in os.environ if k not in keep]:
        del os.environ[k]
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__); return
    if argv and argv[0] in ("-v", "--version"):
        print(f"zap {__version__}"); return
    if not argv:
        os.system(f"title {CFG['title']}")  # first, so the window can be found right away
    load_apps()
    brain = Brain()
    if argv:  # one-shot:  zap open paint
        handle(" ".join(argv), brain); brain.kill(); return
    os.chdir(Path.home())
    brain.warm()
    read = make_reader()
    apply_look(int(CFG.get("look", 0) or 0))
    banner(brain.available())
    while True:
        try:
            line = read(prompt_text())
        except KeyboardInterrupt:
            continue  # like PowerShell: Ctrl+C clears the line
        except EOFError:
            break
        try:
            if not handle(line, brain):
                break
        except KeyboardInterrupt:
            print()
        except Exception as e:
            print(f"{RED}{type(e).__name__}: {e}{RESET}")
    brain.kill()
    if CFG.get("look"):
        apply_look(0)  # hand the window back in its own colours

if __name__ == "__main__":
    main(sys.argv[1:])
