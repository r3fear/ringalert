@echo off
cd /d C:\Projects\ringalert

:: Iniciar servidor WhatsApp en background
start "RingAlert-WhatsApp" /min cmd /c "cd C:\Projects\ringalert\whatsapp && node wa-server.js >> C:\Projects\ringalert\logs\whatsapp.log 2>&1"

:: Esperar 10 segundos a que WhatsApp inicie
timeout /t 10 /nobreak

:: Iniciar monitor principal
start "RingAlert-Main" /min cmd /c "cd C:\Projects\ringalert && py main.py"