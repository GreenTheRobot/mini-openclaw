@echo off
setlocal
set "ROOT=%~dp0"
pushd "%ROOT%" >nul
python -m agent.cli %*
set "STATUS=%ERRORLEVEL%"
popd >nul
exit /b %STATUS%
