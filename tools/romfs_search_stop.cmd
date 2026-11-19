@echo off
rem Stops the game file search that tools\romfs_search.cmd started; double-click it.
rem Arguments pass through, for example --port 8766 for a search started on that port.
title Stop Game File Search
cd /d "%~dp0.."

set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python 3.10 or later was not found.
    pause
    exit /b 1
)

%PY% tools\romfs_search.py --stop %*
timeout /t 3 >nul
