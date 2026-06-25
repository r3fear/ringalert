@echo off
cd /d C:\Projects\ringalert

:: Iniciar monitor principal (WhatsApp corre como servicio independiente: wa-gateway)
start "RingAlert-Main" /min cmd /c "cd C:\Projects\ringalert && py main.py"
