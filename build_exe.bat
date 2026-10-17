@echo off
setlocal
rem Build AutoDim.exe (compiled with Nuitka, source code is not included).
rem Needs 64-bit Python 3.13 (py -3.13-64). First run downloads the Zig C compiler automatically.
rem Tip: keep this folder at a short path without spaces, e.g. C:\autodim
set "HERE=%~dp0"
pushd "%HERE%"
if not exist "%HERE%autocad_auto_dim.py" (
  echo [ERROR] autocad_auto_dim.py not found in %HERE%
  goto :fail
)
py -3.13-64 -m pip install --upgrade nuitka comtypes
py -3.13-64 -c "import licensing, sys; sys.exit(0 if licensing.PUBLIC_KEY else 1)"
if errorlevel 1 (
  echo [ERROR] Run "py -3.13 keygen.py init" first.
  goto :fail
)
py -3.13-64 -m nuitka --onefile --zig --assume-yes-for-downloads ^
  --include-package=comtypes ^
  --nofollow-import-to=comtypes.test --nofollow-import-to=unittest ^
  --include-module=licensing --include-module=ed25519_pure ^
  --output-dir="%HERE%build" ^
  --output-filename=AutoDim.exe ^
  "%HERE%autocad_auto_dim.py"
if errorlevel 1 (
  echo Build failed.
  goto :fail
)
if not exist "%HERE%dist" mkdir "%HERE%dist"
copy /y "%HERE%build\AutoDim.exe" "%HERE%dist\" >nul
copy /y "%HERE%autodim_toggle.lsp" "%HERE%dist\" >nul
copy /y "%HERE%*.txt" "%HERE%dist\" >nul
> "%HERE%dist\start_autodim.bat" echo @echo off
>> "%HERE%dist\start_autodim.bat" echo cd /d "%%~dp0"
>> "%HERE%dist\start_autodim.bat" echo AutoDim.exe --all
>> "%HERE%dist\start_autodim.bat" echo pause
echo.
echo Done. Give people ONLY the files in the "dist" folder.
echo Never give out keygen.py, licensing.py, ed25519_pure.py, autocad_auto_dim.py or private_key.pem.
popd
pause
exit /b 0
:fail
popd
pause
exit /b 1
