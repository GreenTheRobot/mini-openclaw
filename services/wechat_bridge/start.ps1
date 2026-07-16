$ErrorActionPreference = "Stop"

$BridgeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $BridgeDir "..\..")
$Venv = Join-Path $BridgeDir ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Requirements = Join-Path $BridgeDir "requirements.txt"
$Server = Join-Path $RepoRoot "services\wx_file_transfer_server.py"

function Update-PathFromRegistry {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = @($machinePath, $userPath) -join ";"
}

function Get-PythonMinor {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$PrefixArgs = @()
    )
    $code = "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    try {
        $output = & $Exe @PrefixArgs -c $code 2>$null
    } catch {
        return $null
    }
    if ($LASTEXITCODE -ne 0 -or !$output) {
        return $null
    }
    return ($output | Select-Object -First 1).Trim()
}

function Get-Python312Candidate {
    $candidates = @()
    if ($env:WECHAT_BRIDGE_PYTHON) {
        $candidates += @{ Exe = $env:WECHAT_BRIDGE_PYTHON; Args = @(); Name = "WECHAT_BRIDGE_PYTHON" }
    }
    $localPython312 = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $localPython312) {
        $candidates += @{ Exe = $localPython312; Args = @(); Name = "local Python312" }
    }
    $candidates += @{ Exe = "python3.12"; Args = @(); Name = "python3.12" }
    $candidates += @{ Exe = "python312"; Args = @(); Name = "python312" }
    $candidates += @{ Exe = "python"; Args = @(); Name = "python" }
    $candidates += @{ Exe = "py"; Args = @("-3.12"); Name = "py -3.12" }

    foreach ($candidate in $candidates) {
        $version = Get-PythonMinor -Exe $candidate.Exe -PrefixArgs $candidate.Args
        if ($version -eq "3.12") {
            Write-Output "[wechat bridge] using Windows Python 3.12 from $($candidate.Name)"
            return $candidate
        }
    }

    return $null
}

function Install-Python312 {
    if (!(Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Windows Python 3.12 is required, but winget is not available. Install Python 3.12 manually or set WECHAT_BRIDGE_PYTHON."
    }

    Write-Output "[wechat bridge] installing Windows Python 3.12 with winget"
    & winget install --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) {
        throw "winget failed to install Python.Python.3.12"
    }
    Update-PathFromRegistry
}

function Find-Python312 {
    Update-PathFromRegistry
    $candidate = Get-Python312Candidate
    if ($candidate) {
        return $candidate
    }

    Install-Python312
    $candidate = Get-Python312Candidate
    if ($candidate) {
        return $candidate
    }

    throw "Windows Python 3.12 was installed but could not be found. Reopen the terminal or set WECHAT_BRIDGE_PYTHON to the installed python.exe."
}

function Test-VenvReady {
    if (!(Test-Path $Python)) {
        return $false
    }
    $version = Get-PythonMinor -Exe $Python
    if ($version -ne "3.12") {
        Write-Output "[wechat bridge] existing venv Python is $version, expected 3.12"
        return $false
    }
    & $Python -c "import wxauto4" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Output "[wechat bridge] existing venv does not have wxauto4"
        return $false
    }
    return $true
}

function New-BridgeVenv {
    if (Test-Path $Venv) {
        Write-Output "[wechat bridge] recreating venv at $Venv"
        Remove-Item -LiteralPath $Venv -Recurse -Force
    } else {
        Write-Output "[wechat bridge] creating venv at $Venv"
    }

    $creator = Find-Python312
    $venvArgs = @($creator.Args) + @("-m", "venv", $Venv)
    & $creator.Exe @venvArgs
    if ($LASTEXITCODE -ne 0 -or !(Test-Path $Python)) {
        throw "failed to create venv Python at $Python"
    }

    & $Python -m pip install --index-url https://pypi.org/simple -U pip
    if ($LASTEXITCODE -ne 0) {
        throw "failed to upgrade pip in $Venv"
    }
    & $Python -m pip install --index-url https://pypi.org/simple -r $Requirements
    if ($LASTEXITCODE -ne 0) {
        throw "failed to install bridge requirements from $Requirements"
    }
    & $Python -c "import wxauto4"
    if ($LASTEXITCODE -ne 0) {
        throw "wxauto4 import check failed in $Venv"
    }
}

Write-Output "[wechat bridge] start.ps1 invoked at $(Get-Date -Format o)"

if (!(Test-VenvReady)) {
    New-BridgeVenv
}

Set-Location $RepoRoot
Write-Output "[wechat bridge] launching $Server"
& $Python $Server --quiet
