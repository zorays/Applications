# Optional: register a Windows Task Scheduler job for user-approved screenshots.
# Run in Windows PowerShell on your own computer after completing one interactive OAuth upload.
param(
  [ValidateSet("oauth","sync")] [string]$UploadMode = "oauth",
  [string]$SyncFolder = "",
  [string]$At = "16:45"
)
$ErrorActionPreference = "Stop"
$Runner = Join-Path $PSScriptRoot "windows-run-vision.ps1"
if (-not (Test-Path $Runner)) { throw "Run from the PSX agent folder." }
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -UploadMode $UploadMode"
if ($UploadMode -eq "sync") {
  if (-not $SyncFolder) { throw "Specify -SyncFolder for Drive Desktop" }
  $Arguments += " -SyncFolder `"$SyncFolder`""
}
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments -WorkingDirectory $PSScriptRoot
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $At
Register-ScheduledTask -TaskName "PSX Vision Screenshot CSV Sync" -Action $Action -Trigger $Trigger -Description "Locally transcribe only screenshots intentionally saved in the PSX screenshots folder. GitHub imports only manually reviewed OHLCV." -Force | Out-Null
Write-Host "Task created for weekdays at $At. The PC and local Ollama service must be running."
Write-Host "First configure Google OAuth interactively, then check Task Scheduler history."
