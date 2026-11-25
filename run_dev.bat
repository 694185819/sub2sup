@echo off
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
py -3 -m sub2sup
