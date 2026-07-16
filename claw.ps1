$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
python -m agent.cli @args
