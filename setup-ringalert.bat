@echo off
color 07
title RingAlert - Panel de Control
cd /d "%~dp0"
set "SERVICE=RingAlert-Main"

:: ================================================================
:MENU
cls
echo.
echo  ============================================================
echo     RingAlert  -  Panel de Control
echo  ============================================================
echo.
echo   SESION DE RING
echo   ------------------------------------------------------------
echo    [1] Autenticar con Ring       primera vez o token expirado
echo    [2] Cerrar sesion             eliminar token (cambiar cuenta)
echo.
echo   SERVICIO DE WINDOWS
echo   ------------------------------------------------------------
echo    [3] Instalar servicio         NSSM
echo    [4] Iniciar servicio
echo    [5] Detener servicio
echo    [6] Reiniciar servicio
echo.
echo   DIAGNOSTICO
echo   ------------------------------------------------------------
echo    [7] Ver estado del token      verifica ring_token.cache
echo    [8] Ver log en tiempo real
echo.
echo   ------------------------------------------------------------
echo    [0] Salir
echo  ============================================================
echo.
set "OPCION="
set /p OPCION="   Selecciona una opcion [0-8]: "

if "%OPCION%"=="1" goto AUTENTICAR
if "%OPCION%"=="2" goto CERRAR_SESION
if "%OPCION%"=="3" goto INSTALAR_SERVICIO
if "%OPCION%"=="4" goto INICIAR
if "%OPCION%"=="5" goto DETENER
if "%OPCION%"=="6" goto REINICIAR
if "%OPCION%"=="7" goto VER_TOKEN
if "%OPCION%"=="8" goto VER_LOG
if "%OPCION%"=="0" goto SALIR

echo.
echo   Opcion invalida. Intenta de nuevo.
timeout /t 2 /nobreak >nul
goto MENU

:: ================================================================
:AUTENTICAR
cls
echo.
echo  ============================================================
echo     Autenticar con Ring
echo  ============================================================
echo.
echo   Este proceso obtiene el token de acceso a la API de Ring.
echo   Solo es necesario la primera vez, o si el token expira.
echo.
echo   Necesitaras:
echo     - Email y contrasena de tu cuenta Ring
echo     - Codigo 2FA (si tienes verificacion en dos pasos)
echo.
echo   El token se guardara en: ring_token.cache
echo.
echo   IMPORTANTE: si RingAlert esta corriendo como servicio,
echo   no es necesario detenerlo para autenticar — el token
echo   se renueva automaticamente mientras el servicio esta activo.
echo.
pause

echo.
echo   Iniciando autenticacion...
echo.
py ring_monitor.py --setup
echo.
if exist ring_token.cache (
    echo  ============================================================
    echo     Token guardado correctamente en ring_token.cache
    echo     Ya puedes iniciar el servicio RingAlert.
    echo  ============================================================
) else (
    echo  ============================================================
    echo     [!] No se genero ring_token.cache
    echo     Revisa los datos ingresados e intenta de nuevo.
    echo  ============================================================
)
echo.
pause
goto MENU

:: ================================================================
:CERRAR_SESION
cls
echo.
echo  ============================================================
echo     Cerrar sesion Ring
echo  ============================================================
echo.
echo   Esta accion elimina ring_token.cache.
echo   RingAlert dejara de funcionar hasta que vuelvas a
echo   autenticarte con la opcion 1.
echo.
echo   Usa esta opcion para cambiar de cuenta Ring o revocar
echo   el acceso desde este equipo.
echo.
nssm status %SERVICE% >nul 2>&1
if %errorlevel% equ 0 (
    echo   El servicio %SERVICE% sera detenido automaticamente.
    echo.
)
set "CONFIRMAR="
set /p CONFIRMAR="   Confirmar cierre de sesion? [s/n]: "
if /i not "%CONFIRMAR%"=="s" (
    echo.
    echo   Operacion cancelada.
    timeout /t 2 /nobreak >nul
    goto MENU
)
echo.
echo   Deteniendo servicio...
nssm stop %SERVICE% >nul 2>&1
timeout /t 2 /nobreak >nul
echo   Eliminando ring_token.cache...
if exist ring_token.cache (
    del ring_token.cache
    if not exist ring_token.cache (
        echo.
        echo  ============================================================
        echo     Sesion cerrada. ring_token.cache eliminado.
        echo     Usa la opcion 1 para autenticarte con otra cuenta.
        echo  ============================================================
    ) else (
        echo   [!] No se pudo eliminar ring_token.cache.
        echo       Intenta eliminarlo manualmente.
    )
) else (
    echo   [?] ring_token.cache no existia — nada que eliminar.
)
echo.
pause
goto MENU

:: ================================================================
:INSTALAR_SERVICIO
cls
echo.
echo  ============================================================
echo     Instalar RingAlert como servicio de Windows (NSSM)
echo  ============================================================
echo.
echo   Verificando requisitos...
echo.

:: 1. Permisos de Administrador
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo   [!] Se requieren permisos de Administrador.
    echo       Cierra este bat y ejecutalo con clic derecho
    echo       "Ejecutar como administrador".
    goto FIN_INSTALAR
)
echo   [OK] Permisos de Administrador

:: 2. Python
set "PYTHON_EXE="
for /f "tokens=* delims=" %%i in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%i"
if not defined PYTHON_EXE (
    echo   [!] Python no encontrado. Instala Python 3.11+ desde https://python.org
    goto FIN_INSTALAR
)
echo   [OK] Python: %PYTHON_EXE%

:: 3. NSSM
set "NSSM_EXE="
for /f "tokens=* delims=" %%i in ('where nssm 2^>nul') do if not defined NSSM_EXE set "NSSM_EXE=%%i"
if not defined NSSM_EXE (
    echo   [!] NSSM no encontrado en PATH.
    echo       Descarga nssm.exe desde: https://nssm.cc/download
    echo       Copia nssm.exe a: C:\Windows\System32\
    goto FIN_INSTALAR
)
echo   [OK] NSSM: %NSSM_EXE%

:: 4. config.yaml
if not exist "%~dp0config.yaml" (
    echo   [!] config.yaml no encontrado.
    echo       Copia config.yaml.example a config.yaml y configura.
    goto FIN_INSTALAR
)
echo   [OK] config.yaml

:: 5. ring_token.cache
if not exist "%~dp0ring_token.cache" (
    echo   [?] ring_token.cache no encontrado.
    echo       El servicio no podra iniciar hasta que te autentiques.
    echo       Usa la opcion 1 para autenticarte primero.
    echo.
    set "CONTINUAR="
    set /p CONTINUAR="   Continuar de todas formas? [s/n]: "
    if /i not "%CONTINUAR%"=="s" goto FIN_INSTALAR
) else (
    echo   [OK] ring_token.cache
)

:: 6. wa-gateway (informativo — no bloquea la instalacion)
set "WA_URL=http://127.0.0.1:3000"
if exist "%~dp0config.yaml" (
    for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "$m=(sls 'server_url' '%~dp0config.yaml').Line; if($m -match '""([^""]+)""'){$matches[1]}else{'http://127.0.0.1:3000'}"`) do set "WA_URL=%%i"
)
curl -s "%WA_URL%/status" >nul 2>&1
if %errorlevel% neq 0 (
    echo   [?] wa-gateway no responde en %WA_URL%
    echo       Las alertas de timbre se enviaran por email hasta que
    echo       wa-gateway este disponible — el servicio se instala igual.
) else (
    echo   [OK] wa-gateway disponible en %WA_URL%
)

:: 7. Servicio ya instalado?
nssm status %SERVICE% >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo   [!] El servicio "%SERVICE%" ya esta instalado.
    set "REINSTALAR="
    set /p REINSTALAR="   Deseas reinstalarlo? [s/n]: "
    if /i not "%REINSTALAR%"=="s" goto FIN_INSTALAR
    echo.
    echo   Removiendo servicio existente...
    nssm stop %SERVICE% >nul 2>&1
    nssm remove %SERVICE% confirm >nul 2>&1
)

:: 8. Preparar ruta del directorio (sin backslash final)
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"

echo.
echo   Todas las verificaciones completadas.
echo   Procediendo con la instalacion...
echo.
pause

:: 9. Crear carpeta logs
if not exist "%APP_DIR%\logs" mkdir "%APP_DIR%\logs"
echo   [OK] Carpeta logs\ creada.

:: 10. Instalar y configurar servicio NSSM
echo.
echo   Instalando servicio NSSM...
nssm install %SERVICE% "%PYTHON_EXE%"
nssm set %SERVICE% AppParameters "%APP_DIR%\main.py"
nssm set %SERVICE% AppDirectory "%APP_DIR%"
nssm set %SERVICE% AppStdout "%APP_DIR%\logs\ringalert.log"
nssm set %SERVICE% AppStderr "%APP_DIR%\logs\ringalert.log"
nssm set %SERVICE% AppRotateFiles 1
nssm set %SERVICE% Start SERVICE_AUTO_START
echo   [OK] Servicio configurado.

echo.
echo   Iniciando servicio...
nssm start %SERVICE%
if %errorlevel% neq 0 (
    echo.
    echo   [!] El servicio se instalo pero no pudo iniciarse.
    echo       Revisa el log: %APP_DIR%\logs\ringalert.log
    echo       Verifica que ring_token.cache exista (opcion 1).
) else (
    echo   [OK] Servicio iniciado.
    echo.
    echo  ============================================================
    echo     Instalacion completada.
    echo     Servicio: %SERVICE%
    echo     Log:      %APP_DIR%\logs\ringalert.log
    echo.
    echo     Ver log en tiempo real (PowerShell):
    echo     Get-Content %APP_DIR%\logs\ringalert.log -Wait
    echo  ============================================================
)

:FIN_INSTALAR
echo.
pause
goto MENU

:: ================================================================
:INICIAR
cls
echo.
echo   Iniciando servicio %SERVICE%...
echo.
where nssm >nul 2>&1
if %errorlevel% neq 0 (
    echo   [!] NSSM no encontrado. Instala el servicio primero (opcion 3).
    pause
    goto MENU
)
nssm start %SERVICE%
echo.
pause
goto MENU

:: ================================================================
:DETENER
cls
echo.
echo   Deteniendo servicio %SERVICE%...
echo.
where nssm >nul 2>&1
if %errorlevel% neq 0 (
    echo   [!] NSSM no encontrado.
    echo       Para detener manualmente: Stop-Process -Name python -Force
    pause
    goto MENU
)
nssm stop %SERVICE%
echo.
pause
goto MENU

:: ================================================================
:REINICIAR
cls
echo.
echo   Reiniciando servicio %SERVICE%...
echo.
where nssm >nul 2>&1
if %errorlevel% neq 0 (
    echo   [!] NSSM no encontrado.
    pause
    goto MENU
)
nssm restart %SERVICE%
echo.
pause
goto MENU

:: ================================================================
:VER_TOKEN
cls
echo.
echo  ============================================================
echo     Estado del token Ring
echo  ============================================================
echo.
if exist ring_token.cache (
    echo   [OK] ring_token.cache encontrado
    for %%q in (ring_token.cache) do echo   Ultima modificacion: %%~tq
    echo.
    echo   El token se renueva automaticamente mientras el servicio
    echo   esta corriendo. Si lleva tiempo detenido, puede que
    echo   necesites re-autenticar (opcion 1).
) else (
    echo   [!] ring_token.cache NO encontrado.
    echo.
    echo   Debes autenticarte primero con la opcion 1.
)
echo.
pause
goto MENU

:: ================================================================
:VER_LOG
cls
echo.
echo  ============================================================
echo     Log en tiempo real  --  Ctrl+C para salir
echo  ============================================================
echo.
if not exist "logs\ringalert.log" (
    echo   [!] El archivo de log no existe todavia.
    echo       Inicia el servicio primero (opcion 4).
    echo.
    pause
    goto MENU
)
powershell -NoProfile -Command "Get-Content 'logs\ringalert.log' -Wait -Tail 30"
goto MENU

:: ================================================================
:SALIR
exit /b 0
