# RingAlert

Sistema de monitoreo de timbre Ring con notificaciones por WhatsApp y foto en tiempo real desde cámara SWANN.

## Descripción general

Cuando alguien toca el timbre o se detecta movimiento, el sistema:
1. Detecta el evento vía polling a la API de Ring
2. Captura un frame en tiempo real de la cámara SWANN vía RTSP
3. Envía un mensaje de texto por WhatsApp (primero el texto, luego la foto)
4. Guarda la foto en historial local organizado por fecha
5. Monitorea la salud del sistema y envía reporte diario

---

## Stack tecnológico

| Componente | Tecnología | Versión |
|---|---|---|
| Monitor Ring | Python | 3.11+ / 3.14+ |
| Librería Ring | ring-doorbell | 0.9.14 |
| Captura cámara | ffmpeg | 8.1.1 |
| Gateway WhatsApp | wa-gateway (Node.js + whatsapp-web.js) | servicio externo |
| Configuración | PyYAML | 6.0+ |
| Email fallback | smtplib (stdlib) | - |

---

## Hardware y red

| Dispositivo | Detalles |
|---|---|
| Servidor | Windows 11 Pro, encendido 24/7, sin monitor |
| Timbre | Ring Video Doorbell — "Puerta principal", batería |
| DVR cámaras | SWANN, IP `192.168.X.X`, puerto RTSP `554` |
| Cámara activa | CH02 — apunta a la puerta principal |
| Credenciales DVR | usuario: `XXX`, password: `XXX` |
| URL RTSP activa | `rtsp://usuario:password@IP:554/ch02/0` |
| WhatsApp remitente | Número secundario dedicado |

---

## Estructura del proyecto

```
C:\Projects\ringalert\
├── config.yaml              ← credenciales reales (NO en GitHub)
├── config.yaml.example      ← plantilla sin credenciales
├── requirements.txt
├── start_ringalert.bat      ← script de arranque
├── ring_token.cache         ← token de sesión Ring (NO en GitHub)
│
├── main.py                  ← orquestador principal
├── ring_monitor.py          ← polling Ring, detección de eventos
├── camera.py                ← captura de frame RTSP vía ffmpeg
├── wa_sender.py             ← cliente HTTP hacia wa-gateway + email de respaldo
├── storage.py               ← historial local y limpieza
├── health_monitor.py        ← reporte diario, alertas, comandos
│
├── logs/
│   └── ringalert.log        ← log principal (NO en GitHub)
│
└── fotos/                   ← historial (NO en GitHub)
    └── YYYY/MM/DD/
        ├── ding_HHMMSS.jpg
        └── motion_HHMMSS.jpg
```

---

## Arquitectura y flujo

```
[Ring Doorbell]
      │
      │ polling cada 5s (ring-doorbell lib)
      ▼
[ring_monitor.py]
      │ evento: ding | motion
      ▼
[main.py — on_ring_event()]
      ├──► [camera.py] ──► ffmpeg ──► [SWANN CH02 RTSP] ──► foto.jpg
      └──► [wa_sender.py]
                ├── is_server_alive() → POST /send (texto + foto)
                │         └──► [wa-gateway :3000] ──► WhatsApp
                └── si wa-gateway caído → send_email_fallback() ──► Gmail

[health_monitor.py] (loop cada 60s)
      ├── check_whatsapp_health() → loguea estado (emails los gestiona wa-gateway)
      ├── check_daily_report() → reporte por WA a hora configurada
      └── cleanup_old_photos() → limpieza diaria de fotos antiguas

[main.py — poll_inbox()] (loop cada 5s)
      └── GET /inbox ──► [wa-gateway] ──► health_monitor.process_whatsapp_command()

[wa-gateway] (servicio independiente — ver repo wa-gateway)
      ├── Gestiona conexión WhatsApp Web + sesión QR
      ├── Envía email de alerta en desconexión/reconexión de WhatsApp
      └── Expone API REST: /send, /status, /inbox, /register-numbers, /health
```

---

## Módulos — descripción detallada

### `main.py`
Punto de entrada. Orquesta todos los módulos con `asyncio.gather`. Contiene:
- `setup_logging()`: configura log a archivo y consola
- `play_doorbell_sound(config)`: reproduce el sonido configurado vía `asyncio.create_subprocess_exec` + PowerShell `System.Media.SoundPlayer`; solo ejecuta en eventos `ding`, corre en segundo plano sin bloquear; solo soporta WAV
- `on_ring_event(event_type, doorbell_name)`: callback que dispara el sonido (ding), captura foto y envía WA
- `poll_inbox()`: loop que lee mensajes entrantes de WA; pasa `body` y `sender` a `process_whatsapp_command`
- Al arrancar (si WA conectado): llama `POST /register-numbers` con la lista de recipients para resolver @lid de WhatsApp multi-device

### `ring_monitor.py`
Funciones de módulo:
- `token_updated(token)`: callback que serializa y guarda el token actualizado a `ring_token.cache`
- `get_ring_auth()`: carga `Auth` desde `ring_token.cache`; lanza `FileNotFoundError` si no existe (indica que hay que correr `--setup` primero)
- `get_doorbell(ring)`: retorna el primer timbre de `ring.devices().doorbells`

Clase `RingMonitor`:
- Conecta a Ring vía token en `ring_token.cache`
- Al iniciar, lee el último evento conocido y lo guarda en `self.last_event_id` para evitar falsa alerta al arrancar el servicio
- Hace polling a `doorbell.async_history(limit=1)` cada `polling_interval` segundos
- Detecta eventos nuevos comparando IDs
- Filtra por `min_time_between_alerts` para evitar spam
- Guarda `self.last_battery` con el nivel de batería actual del timbre
- Función `setup_ring()`: autenticación interactiva con 2FA (correr con `--setup`)

### `camera.py`
Función `capture_frame(rtsp_url, output_path, ffmpeg_path)`:
- Ejecuta ffmpeg como subprocess con timeout de 30s
- Flags importantes: `-rtsp_transport tcp`, `-vframes 1`, `-update 1`
- Retorna `True/False` según éxito
- Función `build_photo_path()` — **deprecada**, usar `Storage.get_photo_path()` en su lugar

### `wa_sender.py`
Clase `WhatsAppSender`:
- `is_server_alive()`: GET `/status` a wa-gateway; retorna `True` solo si `connected: true`
- `send_alert(message, image_path)`: broadcast a todos los recipients activos; si wa-gateway no responde, llama `send_email_fallback`
- `send_direct(to, message, image_path)`: envía a un destinatario específico (número o JID completo con `@`); usado por `process_whatsapp_command` para responder solo al remitente
- `_send_request(to, message, imagePath)`: POST `/send` a wa-gateway (método interno)
- `send_email_fallback(message)`: SMTP Gmail; se activa cuando wa-gateway no está disponible (caída del proceso) — las alertas de desconexión interna de WhatsApp las gestiona wa-gateway
- `build_message(event_type, doorbell_name)`: formatea el texto del mensaje
- Recipients: filtra strings vacíos automáticamente (soporta 1-3 números)

### `storage.py`
Clase `Storage`:
- `get_photo_path(event_type)`: construye ruta `fotos/YYYY/MM/DD/tipo_HHMMSS.jpg` y crea carpetas
- `cleanup_old_photos()`: elimina JPGs más antiguos que `retention_days`, borra carpetas vacías
- `get_stats()`: retorna dict con total_photos, total_size_mb, oldest, newest, retention_days

### `health_monitor.py`
Clase `HealthMonitor`:
- `check_whatsapp_health()`: detecta desconexión/reconexión de wa-gateway y loguea el cambio de estado (los emails de alerta los gestiona wa-gateway)
- `check_daily_report()`: envía reporte a `daily_report_time` una vez por día
- `_photos_today()`: cuenta fotos del día actual
- `build_daily_report()`: genera texto del reporte con batería, estado WA, fotos del día
- `_is_authorized_sender(sender)`: valida que el remitente (número o JID) esté en la lista de `recipients`; extrae dígitos para comparar — rechaza mensajes de números no autorizados
- `process_whatsapp_command(message, sender)`: procesa comandos (ESTADO, SILENCIAR, REACTIVAR, FOTO); rechaza si el sender no está autorizado; responde solo al remitente vía `send_direct`; importa `capture_frame` de `camera.py` para FOTO
- `silence(hours)`: silencia alertas por N horas
- `is_silenced()`: verifica si las alertas están silenciadas

### wa-gateway (servicio externo)

El módulo de WhatsApp corre como servicio independiente. Ver su propio repositorio para documentación completa. RingAlert lo consume vía HTTP en `server_url` (por defecto `http://127.0.0.1:3000`).

Endpoints utilizados por RingAlert:
- `POST /send` — envía texto y/o imagen
- `POST /register-numbers` — registra destinatarios para resolver `@lid` (WhatsApp multi-device)
- `GET /status` — verifica conexión WhatsApp (`connected: true/false`)
- `GET /inbox` — retorna y vacía mensajes entrantes

---

## Configuración — `config.yaml`

```yaml
ring:
  polling_interval: 5           # segundos entre consultas al timbre
  min_time_between_alerts: 60   # segundos mínimos entre notificaciones

camera:
  rtsp_url: "rtsp://usuario:password@IP:554/ch02/0"
  ffmpeg_path: "ffmpeg"         # o ruta completa si no está en PATH

whatsapp:
  server_url: "http://127.0.0.1:3000"
  recipients:
    - "521XXXXXXXXXX"           # número 1 (requerido, con código de país)
    - ""                        # número 2 (opcional)
    - ""                        # número 3 (opcional)

storage:
  photos_dir: "C:\\ruta\\para\\fotos"
  retention_days: 90            # días de retención; 0 = nunca

monitoring:
  daily_report_time: "08:00"    # HH:MM

sound:
  enabled: false                # true para reproducir sonido en eventos ding
  doorbell_file: "C:\\ruta\\al\\sonido.wav"  # solo WAV; vacío = deshabilitado

email:
  smtp_server: "smtp.gmail.com"
  smtp_port: 587
  sender: "tucorreo@gmail.com"
  password: "app_password_aqui" # contraseña de aplicación Gmail (no la normal)
  recipients:
    - "tucorreo@gmail.com"
```

---

## Comandos WhatsApp disponibles

Enviar desde un número autorizado (debe estar en `recipients`) al número secundario (remitente). El bot responde **solo al número que envió el comando**, no a todos los recipients. Mensajes de números no autorizados se ignoran sin respuesta.

| Comando | Efecto |
|---|---|
| `ESTADO` | Reporte inmediato del sistema |
| `SILENCIAR 2h` | Silencia alertas por 2 horas |
| `SILENCIAR 0.5h` | Silencia alertas por 30 minutos |
| `REACTIVAR` | Cancela el silencio activo |
| `FOTO` | Captura y envía una foto en tiempo real de la cámara |

---

## Reconexión de sesión WhatsApp

La sesión de WhatsApp la gestiona **wa-gateway**. Cuando se pierde la sesión, el log de wa-gateway muestra:

```
ERROR - Fallo de autenticacion WhatsApp: ...
ERROR - La sesion se perdio — ejecuta setup-whatsapp.bat en el servidor para reconectar
```

### Proceso de reconexión

1. Conectarse al servidor por AnyDesk
2. Ir al directorio de wa-gateway
3. Ejecutar **`setup-whatsapp.bat`** de wa-gateway (opción 1 — Reconectar sesión)
4. Escanear el QR con el número secundario
5. wa-gateway retoma automáticamente; RingAlert no requiere reinicio

Ver documentación completa en el README de wa-gateway.

---

## Arranque automático

`start_ringalert.bat` registrado en **Task Scheduler** de Windows como tarea al inicio:
- Nombre de tarea: `RingAlert`
- Inicia `main.py` en ventana minimizada
- wa-gateway corre como servicio NSSM independiente (debe estar activo antes)

### NSSM

Si se usa **NSSM** (Non-Sucking Service Manager) en lugar de Task Scheduler para `main.py`, el ejecutable real de Python en el servidor es:

```
C:\Users\AppData\Local\Python\pythoncore-3.14-64\python.exe
```

Ejemplo de configuración NSSM para `main.py`:

```powershell
nssm install RingAlertMain "C:\Users\AppData\Local\Python\pythoncore-3.14-64\python.exe"
nssm set RingAlertMain AppParameters "C:\Projects\ringalert\main.py"
nssm set RingAlertMain AppDirectory "C:\Projects\ringalert"
nssm set RingAlertMain AppStdout "C:\Projects\ringalert\logs\ringalert.log"
nssm set RingAlertMain AppStderr "C:\Projects\ringalert\logs\ringalert.log"
nssm set RingAlertMain Start SERVICE_AUTO_START
```

---

Comandos útiles en el servidor:

```powershell
# Ver procesos activos
Get-Process | Where-Object {$_.Name -match "node|python"}

# Ver log en tiempo real
Get-Content C:\Projects\ringalert\logs\ringalert.log -Wait

# Reiniciar sistema completo
Stop-Process -Name "node","python" -Force
Start-ScheduledTask -TaskName "RingAlert"

# Verificar tarea programada
Get-ScheduledTask -TaskName "RingAlert"
```

---

## Flujo de desarrollo

```
[Laptop - VS Code] ──push──► [GitHub: r3fear/ringalert] ──pull──► [Servidor - AnyDesk]
```

1. Desarrollar en laptop con VS Code
2. `git add . && git commit -m "descripción" && git push origin main`
3. Conectar al servidor por AnyDesk
4. `Stop-Process -Name "node","python" -Force`
5. `cd C:\Projects\ringalert && git pull origin main`
6. `Start-ScheduledTask -TaskName "RingAlert"`

---

## Archivos que NO están en GitHub (.gitignore)

- `config.yaml` — credenciales reales
- `ring_token*.cache` — token de sesión Ring
- `fotos/` — historial de fotos
- `logs/` — archivos de log
- `__pycache__/` — cache Python

---

## Dependencias

### Python (`requirements.txt`)
```
ring-doorbell==0.9.14
pyyaml>=6.0
```
Instalar: `py -m pip install -r requirements.txt`

### Sistema
- ffmpeg en PATH (instalado con `winget install --id Gyan.FFmpeg`)
- wa-gateway corriendo como servicio NSSM (ver su propio repositorio)

---

## Notas importantes para IA

- La librería `ring-doorbell 0.9.14` es **completamente asíncrona** — todos los métodos son `async/await`
- `ring.video_doorbells` **no existe** en esta versión — usar `ring.devices().doorbells`
- Los snapshots y descarga de videos requieren **suscripción Ring** — no disponible en esta instalación
- **wa-gateway debe estar corriendo** antes de iniciar `main.py` — es un servicio NSSM independiente
- El número de WhatsApp debe tener código de país sin `+`: ejemplo `5215512345678`
- `async_update_health_data()` debe llamarse antes de leer `battery_life`
- La sesión `aiohttp` de `ring-doorbell` se cierra explícitamente en el `finally` de `RingMonitor.start()` — el warning `Unclosed client session` ya no debe aparecer al detener el servicio
- Las cámaras SWANN tienen 4 canales: `ch01/0` a `ch04/0` (main stream) y `ch01/1` a `ch04/1` (sub stream)
- Solo CH02 apunta a la puerta principal — los demás canales están disponibles pero no configurados
- Python real en el servidor: `C:\Users\AppData\Local\Python\pythoncore-3.14-64\python.exe` — el launcher `py` de Windows resuelve esto automáticamente; usar la ruta completa si se configura NSSM o Task Scheduler directamente
- El comando `FOTO` captura vía RTSP y guarda el archivo como `foto_HHMMSS.jpg` en el historial; sigue el mismo mecanismo y retención que los eventos `ding`/`motion`
- `play_doorbell_sound` usa `subprocess` + PowerShell `System.Media.SoundPlayer` para reproducir audio — no usa librerías Python de audio para garantizar compatibilidad con Session 0 (NSSM); las APIs de audio de Python (`playsound`, `pygame`, `winsound`) requieren una sesión de usuario activa y fallan silenciosamente en servicios de Windows
- `System.Media.SoundPlayer` solo soporta WAV; para MP3 habría que usar `MediaPlayer` de WPF (`System.Windows.Media.MediaPlayer`) con su propia complejidad
- El archivo WAV se pasa con comillas simples al comando PowerShell; las comillas simples en la ruta se escapan como `''` (convención PowerShell)
- WhatsApp multi-device usa `@lid` (Linked Device ID) como identificador del remitente — wa-gateway resuelve esto internamente via `POST /register-numbers`; RingAlert llama este endpoint al arrancar con la lista de `recipients`
- Los comandos de WhatsApp responden solo al remitente (`send_direct`) y no a todos los recipients (`send_alert`); la autorización se valida comparando dígitos del sender contra la lista de `recipients` en config
- `health_monitor.check_whatsapp_health()` ya no envía emails — solo loguea el cambio de estado; los emails de alerta por desconexión/reconexión de WhatsApp los gestiona wa-gateway internamente
- `wa_sender.send_email_fallback()` se activa únicamente cuando wa-gateway no responde en HTTP (proceso caído); en ese caso wa-gateway tampoco puede mandar su propio email, así que RingAlert es la única notificación
- Esta rama (`conexion-wa-gateway`) reemplaza el módulo `whatsapp/` local por el servicio externo wa-gateway; la rama `main` conserva el módulo embebido como respaldo
