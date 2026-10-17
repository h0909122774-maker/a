@echo off
rem Double-click: watch AutoCAD and annotate every drawing you open (plus anything you draw later)
cd /d "%~dp0"
if exist AutoDim.exe (AutoDim.exe --all) else (python autocad_auto_dim.py --all)
pause
