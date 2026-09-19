@echo off
rem ===================================================================
rem  ocr_pdf - package phone photos of documents into one PDF.
rem  Just double-click this file (or run it from cmd).
rem  Keep this .bat and ocr_pdf.py in the SAME folder as the photos.
rem ===================================================================
setlocal
title ocr_pdf - Make PDF
cd /d "%~dp0"

rem ---- locate Python ----
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY (
    echo [ERROR] Python not found.
    echo         Please install Python 3 from https://www.python.org/downloads/
    echo         and tick "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)

rem ---- Pillow / system OCR are auto-installed on first run (see ocr_pdf.py) ----
rem   By default every photo is auto-orientated with the Windows OCR engine:
rem   it tries upright / clockwise 90 / counter-clockwise 90 / upside-down and
rem   keeps whichever one gives the most readable text.

rem ---- Build the PDF ----
rem   Extra options can be appended here, e.g.:
rem     --show-rotation-scores  print the OCR score of each direction
rem     --no-detect-rotation    do not auto-detect, use --rotate-ccw instead
rem     --rotate-ccw ccw        fixed direction for landscape photos
rem     --bw                    pure black/white, smallest size
rem     --max-long 1800         compress harder
rem     -q 65                   lower JPEG quality
"%PY%" "%~dp0ocr_pdf.py" "%~dp0." %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [FAILED] exit code %RC% - see the messages above.
) else (
    echo Done. You can close this window.
)
echo.
pause
endlocal
exit /b %RC%
