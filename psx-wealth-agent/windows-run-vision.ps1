param(
  [ValidateSet("none","oauth","sync")] [string]$UploadMode = "none",
  [string]$SyncFolder = "",
  [string]$ApproveSymbol = ""
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Run windows-setup.ps1 first." }
$Args = @("--images","screenshots","--out","vision_output","--watchlist","watchlist.txt","--upload",$UploadMode)
if ($UploadMode -eq "sync") {
  if (-not $SyncFolder) { throw "Pass -SyncFolder to your Google Drive for Desktop synced folder." }
  $Args += @("--sync-folder",$SyncFolder)
}
if ($ApproveSymbol) {
  Write-Host "Review every line of vision_output\$ApproveSymbol.csv against Trade Cast before approving."
  $confirmation = Read-Host "Type VERIFIED to confirm your manual OHLCV comparison"
  if ($confirmation -ne "VERIFIED") { throw "Approval cancelled." }
  & $Python "local_vision_ingest.py" "approve" "--symbol" $ApproveSymbol @Args
} else {
  & $Python "local_vision_ingest.py" "scan" @Args
}
if ($LASTEXITCODE -ne 0) { throw "Ollama/OHLCV uploader reported errors. Check messages above." }
