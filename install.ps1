<#
  Zap installer. Run from the zap folder:

    powershell -ExecutionPolicy Bypass -File install.ps1               # shortcuts + Terminal profile
    powershell -ExecutionPolicy Bypass -File install.ps1 -Hotkey       # also Ctrl+T from anywhere, at every login
    powershell -ExecutionPolicy Bypass -File install.ps1 -DefaultTerminal   # also make Zap the default Terminal profile

  Undo everything with uninstall.ps1.
#>
param([switch]$Hotkey, [switch]$DefaultTerminal)
$ErrorActionPreference = "Stop"
$dir = $PSScriptRoot

# 1. Python
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py -or $py -like "*WindowsApps\python.exe") {
    $pyl = (Get-Command py -ErrorAction SilentlyContinue).Source
    if ($pyl) { $py = (& $pyl -3 -c "import sys; print(sys.executable)").Trim() }
}
if (-not $py) { throw "Python 3.10+ is needed: https://www.python.org/downloads/ (tick 'Add to PATH')" }
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
Write-Host "Python: $py"

# 2. Optional nicer prompt (history, suggestions, Tab completion)
& $py -m pip install --user --quiet --disable-pip-version-check -r (Join-Path $dir "requirements.txt")

# 3. Claude Code
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Warning "Claude Code was not found. Apps and commands work; for the AI part install it: https://claude.com/claude-code"
}
if (-not (Get-Command rg -ErrorAction SilentlyContinue)) {
    Write-Host "Tip: install ripgrep for 30x faster file finding:  winget install BurntSushi.ripgrep.MSVC"
}

# 4. Shortcuts (Start menu + Desktop)
$wt = (Get-Command wt -ErrorAction SilentlyContinue).Source
$shell = New-Object -ComObject WScript.Shell
$targets = @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Zap.lnk", "$([Environment]::GetFolderPath('Desktop'))\Zap.lnk")
foreach ($t in $targets) {
    $s = $shell.CreateShortcut($t)
    if ($wt) { $s.TargetPath = $wt; $s.Arguments = "-w new `"$py`" `"$dir\zap.py`"" }
    else     { $s.TargetPath = $py; $s.Arguments = "`"$dir\zap.py`"" }
    $s.WorkingDirectory = $env:USERPROFILE
    $s.IconLocation = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe,0"
    $s.Save()
}
Write-Host "Shortcuts: Start menu and Desktop ('Zap')"

# 5. Windows Terminal profile (a fragment: your settings.json is not touched)
$guid = "{5a9c0de0-2a9f-4e1b-8f3a-7a9f00000001}"
if ($wt) {
    $frag = "$env:LOCALAPPDATA\Microsoft\Windows Terminal\Fragments\Zap"
    New-Item -ItemType Directory -Force $frag | Out-Null
    @{ profiles = @(@{ guid = $guid; name = "Zap"; commandline = "`"$py`" `"$dir\zap.py`"";
        startingDirectory = "%USERPROFILE%";
        icon = "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" }) } |
        ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 "$frag\zap.json"
    Write-Host "Windows Terminal: 'Zap' profile added (pick it from the v menu next to +)"
}

# 6. Make it the default Terminal profile (new windows and tabs open Zap)
if ($DefaultTerminal -and $wt) {
    $settings = Get-ChildItem "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal*\LocalState\settings.json" | Select-Object -First 1
    if ($settings) {
        Copy-Item $settings.FullName "$($settings.FullName).before-zap" -Force
        $raw = Get-Content $settings.FullName -Raw
        $raw = $raw -replace '"defaultProfile"\s*:\s*"[^"]*"', "`"defaultProfile`": `"$guid`""
        Set-Content -Encoding utf8 $settings.FullName $raw
        Write-Host "Windows Terminal: Zap is now the default profile (backup: settings.json.before-zap)"
    }
}

# 7. Global hotkey at every login
if ($Hotkey) {
    $s = $shell.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Zap hotkey.lnk")
    $s.TargetPath = $pyw; $s.Arguments = "`"$dir\zap_hotkey.pyw`""; $s.WorkingDirectory = $dir; $s.Save()
    Start-Process $pyw -ArgumentList "`"$dir\zap_hotkey.pyw`"" -WorkingDirectory $dir
    Write-Host "Hotkey: Ctrl+T opens Zap from any window (change it in %USERPROFILE%\.zap\config.json)"
}

Write-Host "`nDone. Open Zap and type /login once if Claude Code isn't signed in yet." -ForegroundColor Green
