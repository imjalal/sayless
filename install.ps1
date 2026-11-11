<#
  Sayless installer. Run from the sayless folder:

    powershell -ExecutionPolicy Bypass -File install.ps1               # shortcuts + Terminal profile
    powershell -ExecutionPolicy Bypass -File install.ps1 -Hotkey       # also Ctrl+T from anywhere, at every login
    powershell -ExecutionPolicy Bypass -File install.ps1 -DefaultTerminal   # also make Sayless the default Terminal profile

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
$targets = @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Sayless.lnk", "$([Environment]::GetFolderPath('Desktop'))\Sayless.lnk")
foreach ($t in $targets) {
    $s = $shell.CreateShortcut($t)
    if ($wt) { $s.TargetPath = $wt; $s.Arguments = "-w new `"$py`" `"$dir\sayless.py`"" }
    else     { $s.TargetPath = $py; $s.Arguments = "`"$dir\sayless.py`"" }
    $s.WorkingDirectory = $env:USERPROFILE
    $s.IconLocation = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe,0"
    $s.Save()
}
Write-Host "Shortcuts: Start menu and Desktop ('Sayless')"

# 5. Windows Terminal profile (a fragment: your settings.json is not touched)
$guid = "{5a9c0de0-2a9f-4e1b-8f3a-7a9f00000001}"
if ($wt) {
    $frag = "$env:LOCALAPPDATA\Microsoft\Windows Terminal\Fragments\Sayless"
    New-Item -ItemType Directory -Force $frag | Out-Null
    @{ profiles = @(@{ guid = $guid; name = "Sayless"; commandline = "`"$py`" `"$dir\sayless.py`"";
        startingDirectory = "%USERPROFILE%";
        icon = "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" }) } |
        ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 "$frag\sayless.json"
    Write-Host "Windows Terminal: 'Sayless' profile added (pick it from the v menu next to +)"
}

# 6. Make it the default Terminal profile (new windows and tabs open Sayless)
if ($DefaultTerminal -and $wt) {
    $settings = Get-ChildItem "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal*\LocalState\settings.json" | Select-Object -First 1
    if ($settings) {
        Copy-Item $settings.FullName "$($settings.FullName).before-sayless" -Force
        $raw = Get-Content $settings.FullName -Raw
        $raw = $raw -replace '"defaultProfile"\s*:\s*"[^"]*"', "`"defaultProfile`": `"$guid`""
        Set-Content -Encoding utf8 $settings.FullName $raw
        Write-Host "Windows Terminal: Sayless is now the default profile (backup: settings.json.before-sayless)"
    }
}

# 7. Global hotkey at every login
if ($Hotkey) {
    $s = $shell.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Sayless hotkey.lnk")
    $s.TargetPath = $pyw; $s.Arguments = "`"$dir\sayless_hotkey.pyw`""; $s.WorkingDirectory = $dir; $s.Save()
    Start-Process $pyw -ArgumentList "`"$dir\sayless_hotkey.pyw`"" -WorkingDirectory $dir
    Write-Host "Hotkey: Ctrl+T opens Sayless from any window (change it in %USERPROFILE%\.sayless\config.json)"
}

Write-Host "`nDone. Open Sayless and type /login once if Claude Code isn't signed in yet." -ForegroundColor Green
