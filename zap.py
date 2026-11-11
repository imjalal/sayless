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

import ctypes, difflib, json, os, re, shutil, subprocess, sys, threading, time, uuid, webbrowser
from pathlib import Path
from urllib.parse import quote_plus

__version__ = "1.0.0"

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
DIM, RED, GREEN, RESET, CLEAR_LINE = "\x1b[90m", "\x1b[31m", "\x1b[32m", "\x1b[0m", "\r\x1b[2K"

# ------------------------------------------------------------------ config
DEFAULTS = {
    "title": "Zap",
    "model": "haiku",                 # haiku = fastest; sonnet / opus = smarter
    "effort": "low",
    "permission_mode": "auto",        # Claude Code's own safety classifier decides what may run
    "show_timing": True,
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

def find_app(name: str) -> str | None:
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
    if " " not in name and len(name) > 2 and shutil.which(name):
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
    target = find_app(name)
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
    return bool(shutil.which(first)) and len(words) <= 3 and not SENTENCE.search(line)

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
    try:
        subprocess.run(["powershell", "-NoProfile", "-NoLogo", "-Command", s])
    except KeyboardInterrupt:
        pass

# ------------------------------------------------------------------ Claude path
BASE_PROMPT = r"""You are running inside Zap, a small terminal on the user's Windows PC. They type short instructions to get things done on their computer (open things, find files, change settings, close apps, quick questions). Act immediately with your tools - prefer one fast PowerShell command; do not ask questions unless truly needed. Use Start-Process to open apps, files and URLs. Answer in one or two short lines of plain text: no markdown, no headings, no code blocks, no bullet lists. Simple English.

SPEED RULES (they want answers in seconds): every command must finish in under 5 seconds. NEVER use Get-ChildItem -Recurse on big folders (the user folder, Desktop, C:\ or D:\) - it takes minutes. {finder}Do one search, act on the best match, and stop - no second confirming searches. To show a file in its folder: Start-Process explorer -ArgumentList '/select,"<file>"'.

NEWEST FILES: when asked for the latest / last / newest file, search all of these folders at once, newest first, and take the first result - never narrow to one folder on a guess: {roots}"""

RG_FINDER = ("To find files use ripgrep (installed, 1-2 seconds): rg --files --no-ignore --sortr modified "
             "-g '*.mp4' -g '!node_modules' -g '!AppData' <folders> | Select-Object -First 20 "
             "(--sortr modified = newest first; add -g patterns for other file types). ")
PS_FINDER = ("To find files use Get-ChildItem -Recurse -Depth 4 -File -ErrorAction SilentlyContinue on the specific "
             "folders below, filtered with -Filter, sorted by LastWriteTime. ")

def system_prompt() -> str:
    roots = " ".join(f'"{expand(r)}"' for r in CFG.get("search_roots", []))
    p = BASE_PROMPT.format(finder=RG_FINDER if shutil.which("rg") else PS_FINDER, roots=roots)
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
                    self._write(e["delta"]["text"])
                elif e.get("type") == "content_block_stop" and not self.at_line_start:
                    self._write("\n")
            elif t == "assistant":
                for b in ev.get("message", {}).get("content", []):
                    if b.get("type") == "tool_use":
                        self._write(f"{DIM}  > {tool_summary(b)}{RESET}\n")
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
    def ask(self, text: str):
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
HELP = f"""{DIM}Type an app (paint), a site (youtube), a search (google cats), close X, a command (dir), or just say what you want.
  !cmd   force shell            ?text  force Claude
  /new   fresh Claude chat      /model haiku|sonnet|opus     /alias name = target   /aliases
  /apps  rescan apps            /config  open settings       /login                 exit
Ctrl+C stops Claude mid-task. Settings: {CONFIG_FILE}{RESET}"""
COMMANDS = ["/help", "/new", "/model", "/alias", "/aliases", "/apps", "/config", "/login", "/exit"]

def make_reader():
    """prompt_toolkit gives history, fish-style suggestions and Tab completion; plain input() otherwise."""
    if not sys.stdin.isatty():  # piped input (scripts, tests)
        return lambda prompt: input(prompt)
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.completion import Completer, Completion
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
            pool = list(CFG.get("aliases", {})) + list(ALIASES) + list(SITES) + list(APPS)
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
    return lambda prompt: session.prompt(prompt)

# ------------------------------------------------------------------ dispatch
def handle(line: str, brain: Brain) -> bool:
    """Run one line. Returns False when the user wants to leave."""
    line = line.strip()
    low = line.lower()
    if not line:
        return True
    if low in ("exit", "quit", "/exit"):
        return False
    if low in ("/help", "help", "?"):
        print(HELP); return True
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
    if line.startswith("!"):
        run_shell(line[1:]); return True
    if line.startswith("?"):
        brain.ask(line[1:].strip()); return True
    if try_fast(line):
        return True
    if looks_like_shell(line):
        run_shell(line); return True
    brain.ask(f"[current folder: {os.getcwd()}] {line}")
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
    hint = "" if brain.available() else "  ·  Claude Code not found, only apps and commands will work"
    print(f"{DIM}{CFG['title']} - type an app, a command, or just say what you want - /help{hint}{RESET}\n")
    while True:
        try:
            line = read(f"PS {os.getcwd()}> ")
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

if __name__ == "__main__":
    main(sys.argv[1:])
