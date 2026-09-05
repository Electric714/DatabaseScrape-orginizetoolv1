param([switch]$Repair, [switch]$SetupOnly)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$runtime = Join-Path $root '.runtime'
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $runtime, $logDir | Out-Null
$setupLog = Join-Path $logDir 'setup.log'
# Bound prior setup transcripts without touching application data.
if ((Test-Path -LiteralPath $setupLog) -and (Get-Item -LiteralPath $setupLog).Length -gt 2097152) {
    Move-Item -LiteralPath $setupLog -Destination (Join-Path $logDir 'setup.previous.log') -Force
}
$lock = $null
try {
    $lock = [IO.File]::Open((Join-Path $runtime 'setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    Write-Host 'Another setup is already running. Please wait for its window to finish.' -ForegroundColor Yellow
    exit 1
}
Start-Transcript -LiteralPath $setupLog -Append | Out-Null
$code = 0
try {
    Write-Host ''
    Write-Host '  PARALEGAL RESEARCH DESK' -ForegroundColor Cyan
    Write-Host '  Your workspace, ready in a few steps.' -ForegroundColor Gray
    Write-Host ''
    $env:UV_CACHE_DIR = Join-Path $runtime 'cache'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $runtime 'python'
    $env:UV_PYTHON_PREFERENCE = 'only-managed'
    $env:UV_PYTHON_BIN_DIR = Join-Path $runtime 'bin'
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $runtime 'browsers'
    $env:PYTHONUTF8 = '1'
    $toolsDir = Join-Path $runtime 'tools'
    $uv = Join-Path $toolsDir 'uv.exe'
    $python = Join-Path $runtime 'venv\Scripts\python.exe'
    $stateFile = Join-Path $runtime 'ready.txt'
    $fingerprint = 'launcher-v1:' + (Get-FileHash -LiteralPath (Join-Path $root 'requirements.txt') -Algorithm SHA256).Hash

    function Run-Step([string]$Program, [string[]]$Arguments) {
        # Native programs write progress to stderr; that alone is not a failure.
        $savedPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $Program @Arguments 2>&1 | ForEach-Object { Write-Host "$_" }
        $result = $LASTEXITCODE
        $ErrorActionPreference = $savedPreference
        if ($result -ne 0) { throw "A setup step failed (exit $result). See the lines above for the cause." }
    }

    $ready = -not $Repair -and (Test-Path -LiteralPath $python) -and (Test-Path -LiteralPath $stateFile)
    if ($ready) { $ready = (Get-Content -LiteralPath $stateFile -Raw).Trim() -eq $fingerprint }
    if ($ready) {
        & $python (Join-Path $PSScriptRoot 'run_app.py') --check 2>&1 | Out-Null
        $ready = $LASTEXITCODE -eq 0
    }
    if (-not $ready) {
        Write-Host '[1/4] Preparing the private installer...' -ForegroundColor Cyan
        if (-not (Test-Path -LiteralPath $uv)) {
            New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
            $arch = $env:PROCESSOR_ARCHITECTURE
            if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
            $target = switch ($arch) {
                'AMD64' { 'x86_64-pc-windows-msvc' }
                'ARM64' { 'aarch64-pc-windows-msvc' }
                default { throw 'This launcher requires 64-bit Windows (x64 or ARM64).' }
            }
            $version = '0.12.10'
            $asset = "uv-$target.zip"
            $url = "https://github.com/astral-sh/uv/releases/download/$version/$asset"
            $archive = Join-Path $toolsDir $asset
            $checksum = "$archive.sha256"
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive
            Invoke-WebRequest -UseBasicParsing -Uri "$url.sha256" -OutFile $checksum
            $expected = ((Get-Content -LiteralPath $checksum -Raw).Trim() -split '\s+')[0]
            if ($expected -notmatch '^[0-9a-fA-F]{64}$' -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
                throw 'Installer checksum verification failed. Nothing from the download was executed.'
            }
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $zip = [IO.Compression.ZipFile]::OpenRead($archive)
            try {
                $entry = @($zip.Entries | Where-Object { $_.FullName -eq 'uv.exe' })
                if ($entry.Count -ne 1) { throw 'The verified installer archive had an unexpected layout.' }
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry[0], $uv, $true)
            } finally { $zip.Dispose() }
        }
        Write-Host '[2/4] Preparing Python (no global install)...' -ForegroundColor Cyan
        Run-Step $uv @('python', 'install', '3.12')
        # Only this launcher's private environment is rebuilt; data is elsewhere.
        $venvPath = [IO.Path]::GetFullPath((Join-Path $runtime 'venv'))
        if (-not $venvPath.StartsWith([IO.Path]::GetFullPath($runtime) + [IO.Path]::DirectorySeparatorChar)) {
            throw 'Environment path is outside the application runtime folder.'
        }
        Run-Step $uv @('venv', '--clear', '--python', '3.12', $venvPath)
        Write-Host '[3/4] Installing application packages...' -ForegroundColor Cyan
        Run-Step $uv @('pip', 'install', '--python', $python, '-r', (Join-Path $root 'requirements.txt'))
        Write-Host '[4/4] Installing the browser used for JavaScript pages...' -ForegroundColor Cyan
        Run-Step $python @('-m', 'playwright', 'install', 'chromium')
        Run-Step $python @((Join-Path $PSScriptRoot 'run_app.py'), '--check')
        Set-Content -LiteralPath $stateFile -Value $fingerprint -Encoding ASCII
    } else {
        Write-Host 'Setup is already complete.' -ForegroundColor Green
    }
    Write-Host 'Your workspace is ready.' -ForegroundColor Green
    if (-not $SetupOnly) {
        $lock.Dispose()
        $lock = $null
        & $python (Join-Path $PSScriptRoot 'run_app.py')
        $code = $LASTEXITCODE
    }
} catch {
    $code = 1
    Write-Host ''
    Write-Host 'We could not finish this step.' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Yellow
    Write-Host 'Check your internet connection and retry. Data in the data folder is preserved.'
    Write-Host "Details are saved in $setupLog"
} finally {
    if ($lock) { $lock.Dispose() }
    Stop-Transcript | Out-Null
}
exit $code

