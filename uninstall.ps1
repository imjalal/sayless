# Removes everything install.ps1 added. Your settings in %USERPROFILE%\.sayless are kept (delete that folder by hand if you want).
$ErrorActionPreference = "SilentlyContinue"
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "sayless_hotkey\.pyw" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Sayless hotkey.lnk"
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Sayless.lnk"
Remove-Item "$([Environment]::GetFolderPath('Desktop'))\Sayless.lnk"
Remove-Item -Recurse "$env:LOCALAPPDATA\Microsoft\Windows Terminal\Fragments\Sayless"
$settings = Get-ChildItem "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal*\LocalState\settings.json.before-sayless" | Select-Object -First 1
if ($settings) {
    Copy-Item $settings.FullName ($settings.FullName -replace '\.before-sayless$', '') -Force
    Write-Host "Windows Terminal settings restored from the backup."
}
Write-Host "Sayless removed."
