# Removes everything install.ps1 added. Your settings in %USERPROFILE%\.zap are kept (delete that folder by hand if you want).
$ErrorActionPreference = "SilentlyContinue"
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "zap_hotkey\.pyw" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Zap hotkey.lnk"
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Zap.lnk"
Remove-Item "$([Environment]::GetFolderPath('Desktop'))\Zap.lnk"
Remove-Item -Recurse "$env:LOCALAPPDATA\Microsoft\Windows Terminal\Fragments\Zap"
$settings = Get-ChildItem "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal*\LocalState\settings.json.before-zap" | Select-Object -First 1
if ($settings) {
    Copy-Item $settings.FullName ($settings.FullName -replace '\.before-zap$', '') -Force
    Write-Host "Windows Terminal settings restored from the backup."
}
Write-Host "Zap removed."
