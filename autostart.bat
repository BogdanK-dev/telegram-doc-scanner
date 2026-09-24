@echo off
rem ===== Turns autostart ON/OFF: the bot starts (minimized) when you log in to Windows =====
cd /d "%~dp0"
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ScanBot.lnk"
set "TARGET=%~dp0start.bat"
set "WORKDIR=%~dp0"

if exist "%LNK%" (
    del "%LNK%"
    echo Autostart is now OFF.
    pause
    exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath = $env:TARGET; $s.WorkingDirectory = $env:WORKDIR; $s.WindowStyle = 7; $s.Save()"
if exist "%LNK%" (
    echo Autostart is now ON: the bot will start minimized every time you log in to Windows.
    echo Run autostart.bat again to turn it off.
) else (
    echo Could not create the shortcut. Do it by hand: press Win+R, type shell:startup,
    echo and put a shortcut to start.bat into the folder that opens.
)
pause
