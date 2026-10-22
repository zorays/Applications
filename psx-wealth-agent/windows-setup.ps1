# Run from this folder in Windows PowerShell after installing Python 3.11+ and Ollama.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements-local.txt
New-Item -ItemType Directory -Force -Path "screenshots", "vision_output", ".private" | Out-Null
Write-Host "Environment installed. Start Ollama and run: ollama pull llama3.2-vision"
Write-Host "Place screenshots as screenshots\SAZEW__page01.png (ticker, two underscores, label)."
Write-Host "Use windows-run-vision.ps1 -UploadMode oauth for Google Drive upload."
