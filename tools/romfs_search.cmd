@echo off
rem Starts the game file search (tools\romfs_search.py) and opens it in the browser; double-click it.
rem When the search is already running it only opens the page. Closing this window stops the search.
rem Arguments pass through, for example: romfs_search.cmd --update
title Game File Search
cd /d "%~dp0.."

set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python 3.10 or later was not found. Install it from python.org and start this again.
    pause
    exit /b 1
)

set PYTHONIOENCODING=utf-8
%PY% tools\romfs_search.py %*
if errorlevel 1 pause
