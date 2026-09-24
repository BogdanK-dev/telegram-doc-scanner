@echo off
rem ===== Installs Tesseract OCR (needed for the "Recognize text" button) =====
cd /d "%~dp0"
title ScanBot - install Tesseract OCR

if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" (
    echo Tesseract OCR is already installed: "%ProgramFiles%\Tesseract-OCR"
    goto done
)
where winget >nul 2>&1 || goto nowinget

echo Installing Tesseract OCR... Windows may ask for permission - click "Yes".
winget install -e --id tesseract-ocr.tesseract --accept-source-agreements --accept-package-agreements
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" goto done
winget install -e --id UB-Mannheim.TesseractOCR --accept-source-agreements --accept-package-agreements
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" goto done
echo.
echo Could not install automatically. Download the installer manually:
echo https://github.com/UB-Mannheim/tesseract/wiki
start "" https://github.com/UB-Mannheim/tesseract/wiki
pause
exit /b 1

:done
echo.
echo Done! Now restart the bot: close its window and run start.bat again.
echo The bot will download Russian language data by itself on the first start.
pause
exit /b 0

:nowinget
echo winget is not available. Download the Tesseract installer manually:
echo https://github.com/UB-Mannheim/tesseract/wiki
start "" https://github.com/UB-Mannheim/tesseract/wiki
pause
exit /b 1
