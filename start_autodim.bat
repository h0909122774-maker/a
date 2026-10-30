@echo off
rem Double-click: watch AutoCAD and annotate every drawing you open (plus anything you draw later)
cd /d "%~dp0"
python autocad_auto_dim.py --all
pause
