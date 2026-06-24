@echo off
title RingAlert - Sesion WhatsApp
cd /d "%~dp0whatsapp"

echo.
echo ================================================================
echo   RingAlert - Configuracion de sesion WhatsApp
echo ================================================================
echo.
echo Este script detenara los procesos Node y Chrome activos
echo para evitar conflictos con el servicio en ejecucion.
echo.
echo Si el servicio RingAlert esta corriendo como tarea programada,
echo detenlo primero desde Task Scheduler o con:
echo   Stop-Process -Name node -Force  (PowerShell)
echo.
pause

echo.
echo Deteniendo procesos existentes...
taskkill /F /IM node.exe /T >nul 2>&1
taskkill /F /IM chrome.exe /T >nul 2>&1
timeout /t 2 /nobreak >nul

echo.
node wa-login.js

echo.
echo ================================================================
echo   Listo. Reinicia el servicio RingAlert para continuar.
echo ================================================================
echo.
pause
