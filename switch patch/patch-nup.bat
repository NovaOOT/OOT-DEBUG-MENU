@echo off
rem Writes a copy of a .nup dump of Ocarina of Time with the OOT-PC debug tools in it (switch patch\README.md):
rem main patched with the frame hook and the debug module added as exefs\subsdk9, for each game build in the .nup
rem that the patch supports (1.1.1, 1.0.0). The .nup given is only read, never changed. The copy goes next to it,
rem named "... (OOT-PC debug).nup", and needs about as much free space as the .nup itself.
rem
rem Use: drag the .nup onto this file, or start it and paste the .nup's path.
rem Set NUP_DRY_RUN=1 to only check the .nup and print what it would write.
rem
rem Needs uv (it fetches Python 3.14 and the zbic package the first time) and, to build the module, LLVM
rem (scoop install llvm). The work is done by tools\build.py and tools\patch_nup.py.
setlocal DisableDelayedExpansion
title Patch an Ocarina of Time .nup with the OOT-PC debug tools
set "HERE=%~dp0"
set "NUP_IN=%~f1"
if "%~1"=="" (
    echo Drag your .nup file onto this batch file, or paste its path here and press Enter.
    set /p "NUP_IN=Path: "
)
if not defined NUP_IN exit /b 1
set "NUP_IN=%NUP_IN:"=%"

where uv >nul 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: uv is not installed. Install it from https://docs.astral.sh/uv/ and run this again.
    set "RC=1"
    goto done
)
set "PY=uv run --no-project --python 3.14 --with zbic==1.0.1.post1 python -I"

echo Building the debug module and the patches...
%PY% "%HERE%tools\build.py"
if errorlevel 1 (
    if exist "%HERE%..\build\switch-patch\subsdk9" (
        echo The build failed; using the module built before.
    ) else (
        set "RC=1"
        goto done
    )
)
echo.

set "EXTRA="
if "%NUP_DRY_RUN%"=="1" set "EXTRA=--dry-run"
%PY% "%HERE%tools\patch_nup.py" %EXTRA% "%NUP_IN%"
set "RC=%ERRORLEVEL%"

:done
echo.
if not defined NUP_NOPAUSE pause
exit /b %RC%
