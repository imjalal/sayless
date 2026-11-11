# ⚡ Zap

**A Windows terminal that does what you say.** It looks and works like a normal PowerShell prompt, but you can type plain words and they happen.

```
PS C:\Users\you> paint
opened paint                                   ← 60 ms, no AI
PS C:\Users\you> google cheap flights to lisbon
google: cheap flights to lisbon
PS C:\Users\you> close spotify
closed spotify
PS C:\Users\you> git status                    ← normal shell command
On branch main ...
PS C:\Users\you> find my newest video and show it in its folder
  > PowerShell: rg --files --no-ignore --sortr modified -g '*.mp4' ...
  > PowerShell: Start-Process explorer -ArgumentList '/select,"C:\Users\you\Videos\demo.mp4"'
Opened the folder with demo.mp4 selected, your newest video (today, 18:43).
4.2s
```

Press **Ctrl+T** from any window and it pops up.

## Why it's fast

Most things never touch the AI:

| You type | What happens | Time |
|---|---|---|
| `paint`, `open chrome`, `spotfy` (typo) | opened from a local index of every Start-menu and Store app | ~60 ms |
| `youtube`, `gmail`, `google cats`, `yt lofi`, `maps cafe` | site or search opens in your browser | ~60 ms |
| `close spotify`, `quit paint` | the app is asked to close | ~0.7 s |
| `dir`, `git status`, `ping x`, `npm run dev` | runs as a normal command | as usual |
| anything else | [Claude Code](https://claude.com/claude-code) does it on your PC | 3–10 s |

For the last row, Zap keeps **one Claude Code process warm** in the background (stream-json in and out), so a request skips the start-up cost. It runs without MCP servers, uses Haiku by default, finds files with ripgrep instead of slow recursive searches, and is told to finish every command in under 5 seconds.

## Install

Needs Windows 10/11, [Python 3.10+](https://www.python.org/downloads/) and, for the AI part, [Claude Code](https://claude.com/claude-code) (any plan that can run `claude`).

```powershell
git clone https://github.com/imjalal/zap
cd zap
powershell -ExecutionPolicy Bypass -File install.ps1 -Hotkey
```

- `install.ps1` adds Start-menu and Desktop shortcuts and a **Zap** profile in Windows Terminal.
- `-Hotkey` makes **Ctrl+T** open Zap from any window, at every login.
- `-DefaultTerminal` makes every new Windows Terminal window and tab open Zap (it keeps a backup).
- `uninstall.ps1` removes everything.

Then open Zap and type `/login` once if Claude Code isn't signed in yet.
Optional but recommended: `winget install BurntSushi.ripgrep.MSVC` (file search goes from minutes to ~1 s).

You can also run one line from any shell: `python zap.py open paint`.

## Using it

| | |
|---|---|
| `!cmd` | force it to run as a shell command |
| `?text` | force it to go to Claude |
| `/alias work = C:\Projects` | your own instant shortcut (apps, folders, files, URLs). `/aliases` lists them |
| `/model sonnet` | smarter Claude (`haiku` is fastest, `opus` is smartest) |
| `/new` | start a fresh Claude conversation (Claude remembers the chat until then) |
| `/apps` | rescan installed apps |
| `/config` | open the settings file |
| `Ctrl+C` | stop Claude mid-task (at the prompt it just clears the line) |
| `↑` / `→` / `Tab` | history, accept the grey suggestion, complete app names |

## Settings

`%USERPROFILE%\.zap\config.json` (created on first run):

```json
{
  "title": "Zap",
  "model": "haiku",
  "effort": "low",
  "permission_mode": "auto",
  "show_timing": true,
  "aliases": { "work": "C:\\Projects", "notes": "obsidian" },
  "sites": { "jira": "https://yourteam.atlassian.net" },
  "search_roots": ["~/Desktop", "~/Downloads", "~/Documents", "~/Videos", "~/Pictures"],
  "about_me": "My projects live in D:\\Code. Renders go to out\\ folders.",
  "hotkey": "ctrl+t",
  "hotkey_pass_through": []
}
```

- `about_me` is added to Claude's instructions. Tell it where your stuff lives and it gets faster and more accurate.
- `hotkey` can be things like `ctrl+t`, `alt+space`, `ctrl+shift+z`, `ctrl+backtick`.
- `hotkey_pass_through` lists apps that keep their own shortcut, e.g. `["chrome.exe"]` so Ctrl+T still opens a browser tab there.

## Safety

Claude runs with Claude Code's `auto` permission mode: its own safety check approves routine actions and blocks risky ones (there is no one to ask in a terminal, so a blocked action is simply not done). Every command Claude runs is printed in grey before its answer. Set `"permission_mode": "default"` for a read-mostly assistant. Zap itself sends nothing anywhere; the only network traffic is Claude Code's own.

## How it works

`zap.py` is one stdlib Python file (plus optional `prompt_toolkit` for history and completion):

1. **Router**: aliases → sites/searches → `close X` → app index (exact, prefix, word, then typo match) → shell detection → Claude.
2. **App index**: Start-menu shortcuts plus `Get-StartApps` (Store apps), cached in `~/.zap/apps.json` and refreshed in the background.
3. **Launching** goes through a fully detached `start`, so apps never print their logs into your terminal.
4. **Brain**: `claude -p --input-format stream-json --output-format stream-json` kept alive; text streams as it's written, tool calls are shown as one grey line each.

`zap_hotkey.pyw` is a tiny low-level keyboard hook (ctypes, no dependencies) that opens Zap or brings it to the front.

Run the tests with `python -m pytest tests`.

## License

MIT. Not affiliated with or endorsed by Anthropic. Claude and Claude Code are trademarks of Anthropic.
