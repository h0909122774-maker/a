@echo off
rem Build AutoDim.exe (compiled with Nuitka, source code is not included).
rem Needs Python 3.13 (py -3.13). First run downloads a C compiler automatically.
cd /d "%~dp0"
py -3.13 -m pip install --upgrade nuitka comtypes
py -3.13 -c "import licensing,sys; sys.exit(0 if licensing.PUBLIC_KEY else 1)"
if errorlevel 1 (
  echo [ERROR] Run "py -3.13 keygen.py init" first.
  pause
  exit /b 1
)
py -3.13 -m nuitka --onefile --assume-yes-for-downloads ^
  --include-package=comtypes ^
  --include-module=licensing --include-module=ed25519_pure ^
  --output-filename=AutoDim.exe ^
  autocad_auto_dim.py
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
if not exist dist mkdir dist
copy /y AutoDim.exe dist\ >nul
copy /y autodim_toggle.lsp dist\ >nul
copy /y *.txt dist\ >nul
> dist\start_autodim.bat echo @echo off
>> dist\start_autodim.bat echo cd /d "%%~dp0"
>> dist\start_autodim.bat echo AutoDim.exe --all
>> dist\start_autodim.bat echo pause
echo.
echo Done. Give people ONLY the files in the "dist" folder.
echo Never give out keygen.py, licensing.py, autocad_auto_dim.py or private_key.pem.
pause
