# LyricSync setup - creates .venv with CUDA PyTorch for the RTX 50xx (Blackwell) and all deps.
# Run from the repo folder:  powershell -ExecutionPolicy Bypass -File .\setup.ps1
# Native tools write warnings to stderr; don't let PowerShell treat that as fatal,
# check exit codes explicitly instead.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Fail($msg) { Write-Host $msg -ForegroundColor Red; exit 1 }
function Test-Py312 {
    if (-not (Have py)) { return $false }
    & py -3.12 -c "import sys" *> $null
    return ($LASTEXITCODE -eq 0)
}

# 1. Python 3.12
if (-not (Test-Py312)) {
    Write-Host "Python 3.12 not found - installing via winget..." -ForegroundColor Yellow
    winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
    Write-Host "`nPython installed. Close this window, open a new PowerShell and run setup.ps1 again." -ForegroundColor Yellow
    exit 0
}

# 2. ffmpeg (Demucs uses ffmpeg/ffprobe to decode audio)
if (-not (Have ffmpeg) -and -not (Test-Path .\bin\ffmpeg.exe)) {
    Write-Host "ffmpeg not found - installing via winget..." -ForegroundColor Yellow
    winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
    Write-Host "(ffmpeg is on PATH in new windows; the app will find it after a restart of the terminal/app)" -ForegroundColor Yellow
}

# 3. venv
if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "Creating the virtual environment failed." }
}
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $py -m pip install -U pip wheel
if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed." }

# 4. CUDA PyTorch (Blackwell needs CUDA >= 12.8). Try newest CUDA wheels first.
$ok = $false
foreach ($cu in "cu132", "cu130", "cu128") {
    Write-Host "Installing torch from $cu index..." -ForegroundColor Cyan
    & $py -m pip install torch torchaudio --index-url "https://download.pytorch.org/whl/$cu"
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
}
if (-not $ok) { Fail "Could not install CUDA PyTorch." }

# 5. App deps. Demucs is installed without deps: its PyPI release pins an
#    ancient torchaudio that would downgrade torch to a build without RTX 50 support.
& $py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Installing requirements failed." }
& $py -m pip install --no-deps demucs==4.0.1
if ($LASTEXITCODE -ne 0) { Fail "Installing demucs failed." }

# 6. Sanity check
& $py -c "import torch; ok=torch.cuda.is_available(); print('torch', torch.__version__, '| CUDA:', ok, '|', torch.cuda.get_device_name(0) if ok else 'NO GPU - update the NVIDIA driver')"
Write-Host "`nDone. Start the app with run.bat" -ForegroundColor Green
