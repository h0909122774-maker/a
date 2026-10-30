@echo off
setlocal
rem Make AutoDim_source.zip: the Python (source) version for people who run it with Python.
rem Includes your licensing.py (public key only). Never includes keygen.py or private_key.pem.
set "HERE=%~dp0"
pushd "%HERE%"
py -3.13 -c "import licensing, sys; sys.exit(0 if licensing.PUBLIC_KEY else 1)" 2>nul || python -c "import licensing, sys; sys.exit(0 if licensing.PUBLIC_KEY else 1)"
if errorlevel 1 (
  echo [ERROR] licensing.py has no public key. Run "py -3.13 keygen.py init" first.
  goto :end
)
if exist "%HERE%source_pkg" rmdir /s /q "%HERE%source_pkg"
mkdir "%HERE%source_pkg\AutoDim"
for %%F in (autocad_auto_dim.py licensing.py ed25519_pure.py start_autodim.bat autodim_toggle.lsp) do copy /y "%HERE%%%F" "%HERE%source_pkg\AutoDim\" >nul
copy /y "%HERE%*.txt" "%HERE%source_pkg\AutoDim\" >nul
if exist "%HERE%AutoDim_source.zip" del "%HERE%AutoDim_source.zip"
powershell -NoProfile -Command "Compress-Archive -Path '%HERE%source_pkg\AutoDim' -DestinationPath '%HERE%AutoDim_source.zip'"
rmdir /s /q "%HERE%source_pkg"
echo.
echo Done: AutoDim_source.zip
:end
popd
pause
