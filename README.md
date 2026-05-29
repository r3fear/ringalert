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
| Servidor WhatsApp | Node.js + whatsapp-web.js | 24.x / latest |
| Configuración | PyYAML | 6.0+ |
| Email fallback | smtplib (stdlib) | - |

---

## Hardware y red

| Dispositivo | Detalles |
|---|---|
| Servidor | Windows 11 Pro, encendido 24/7, sin monitor |
| Timbre | Ring Video Doorbell — "Puerta principal", batería |
| DVR cámaras | SWANN, IP `192.168.1.10`, puerto RTSP `554` |
| Cámara activa | CH02 — apunta a la puerta principal |
| Credenciales DVR | usuario: `admin`, password: `admin1` |
| URL RTSP activa | `rtsp://admin:admin1@192.168.1.10:554/ch02/0` |
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
├── wa_sender.py             ← envío WhatsApp + email de respaldo
├── storage.py               ← historial local y limpieza
├── health_monitor.py        ← reporte diario, alertas, comandos
│
├── whatsapp/
│   ├── wa-server.js         ← servidor HTTP + cliente WhatsApp Web
│   └── package.json
│
├── logs/
│   ├── ringalert.log        ← log principal (NO en GitHub)
│   └── whatsapp.log         ← log del servidor WA (NO en GitHub)
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
                │         └──► [wa-server.js :3000] ──► WhatsApp
                └── si WA caído → send_email_fallback() ──► Gmail

[health_monitor.py] (loop cada 60s)
      ├── check_whatsapp_health() → email si WA se cae/restaura
      ├── check_daily_report() → reporte por WA a hora configurada
      └── cleanup_old_photos() → limpieza diaria de fotos antiguas

[main.py — poll_inbox()] (loop cada 5s)
      └── GET /inbox ──► [wa-server.js] ──► health_monitor.process_whatsapp_command()
```

---

## Módulos — descripción detallada

### `main.py`
Punto de entrada. Orquesta todos los módulos con `asyncio.gather`. Contiene:
- `setup_logging()`: configura log a archivo y consola
- `on_ring_event(event_type, doorbell_name)`: callback que captura foto y envía WA
- `poll_inbox()`: loop que lee mensajes entrantes de WA para procesar comandos

### `ring_monitor.py`
Clase `RingMonitor`:
- Conecta a Ring vía token en `ring_token.cache`
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
- `is_server_alive()`: GET `/status` al servidor Node
- `send_alert(message, image_path)`: envía a todos los recipients activos
- `_send_request(to, message, imagePath)`: POST `/send` al servidor Node
- `send_email_fallback(message)`: SMTP Gmail, solo para caídas de WA
- `build_message(event_type, doorbell_name)`: formatea el texto del mensaje
- Recipients: filtra strings vacíos automáticamente (soporta 1-3 números)

### `storage.py`
Clase `Storage`:
- `get_photo_path(event_type)`: construye ruta `fotos/YYYY/MM/DD/tipo_HHMMSS.jpg` y crea carpetas
- `cleanup_old_photos()`: elimina JPGs más antiguos que `retention_days`, borra carpetas vacías
- `get_stats()`: retorna dict con total_photos, total_size_mb, oldest, newest, retention_days

### `health_monitor.py`
Clase `HealthMonitor`:
- `check_whatsapp_health()`: detecta desconexión/reconexión de WA y envía email
- `check_daily_report()`: envía reporte a `daily_report_time` una vez por día
- `_photos_today()`: cuenta fotos del día actual
- `build_daily_report()`: genera texto del reporte con batería, estado WA, fotos del día
- `process_whatsapp_command(message)`: procesa comandos entrantes (ver sección Comandos)
- `silence(hours)`: silencia alertas por N horas
- `is_silenced()`: verifica si las alertas están silenciadas

### `wa-server.js`
Servidor HTTP en puerto 3000 (solo localhost). Endpoints:
- `POST /send` — body: `{to, message, imagePath}` — envía texto y/o imagen
- `GET /status` — retorna `{ok, connected}`
- `GET /inbox` — retorna y vacía la cola de mensajes entrantes

Usa `whatsapp-web.js` con `LocalAuth` (sesión guardada en `.wwebjs_auth/`).
Sesión vinculada escaneando QR con número secundario dedicado.
Chrome instalado en `C:\Program Files\Google\Chrome\Application\chrome.exe`.

---

## Configuración — `config.yaml`

```yaml
ring:
  polling_interval: 5           # segundos entre consultas al timbre
  min_time_between_alerts: 60   # segundos mínimos entre notificaciones

camera:
  rtsp_url: "rtsp://admin:admin1@192.168.1.10:554/ch02/0"
  ffmpeg_path: "ffmpeg"         # o ruta completa si no está en PATH

whatsapp:
  server_url: "http://127.0.0.1:3000"
  recipients:
    - "521XXXXXXXXXX"           # número 1 (requerido, con código de país)
    - ""                        # número 2 (opcional)
    - ""                        # número 3 (opcional)

storage:
  photos_dir: "C:\\Projects\\ringalert\\fotos"
  retention_days: 90            # días de retención, configurable

monitoring:
  daily_report_time: "08:00"    # HH:MM

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

Enviar desde cualquier número destinatario al número secundario (remitente):

| Comando | Efecto |
|---|---|
| `ESTADO` | Reporte inmediato del sistema |
| `SILENCIAR 2h` | Silencia alertas por 2 horas |
| `SILENCIAR 0.5h` | Silencia alertas por 30 minutos |
| `REACTIVAR` | Cancela el silencio activo |
| `FOTO` | Captura y envía una foto en tiempo real de la cámara |

---

## Arranque automático

`start_ringalert.bat` registrado en **Task Scheduler** de Windows como tarea al inicio:
- Nombre de tarea: `RingAlert`
- Inicia `wa-server.js` en ventana minimizada con log a `logs/whatsapp.log`
- Espera 10 segundos
- Inicia `main.py` en ventana minimizada

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
- `whatsapp/_IGNORE_*` — sesión antigua
- `whatsapp/.wwebjs_auth/` — sesión WhatsApp Web
- `whatsapp/.wwebjs_cache/` — cache WhatsApp
- `fotos/` — historial de fotos
- `logs/` — archivos de log
- `node_modules/` — dependencias npm
- `__pycache__/` — cache Python

---

## Dependencias

### Python (`requirements.txt`)
```
ring-doorbell==0.9.14
pyyaml>=6.0
```
Instalar: `py -m pip install -r requirements.txt`

### Node.js (`whatsapp/package.json`)
```
whatsapp-web.js
qrcode-terminal
```
Instalar: `cd whatsapp && npm install`

### Sistema
- ffmpeg en PATH (instalado con `winget install --id Gyan.FFmpeg`)
- Google Chrome en `C:\Program Files\Google\Chrome\Application\chrome.exe`

---

## Notas importantes para IA

- La librería `ring-doorbell 0.9.14` es **completamente asíncrona** — todos los métodos son `async/await`
- `ring.video_doorbells` **no existe** en esta versión — usar `ring.devices().doorbells`
- Los snapshots y descarga de videos requieren **suscripción Ring** — no disponible en esta instalación
- El servidor WhatsApp **debe estar corriendo** antes de iniciar `main.py`
- El número de WhatsApp debe tener código de país sin `+`: ejemplo `5215512345678`
- `async_update_health_data()` debe llamarse antes de leer `battery_life`
- El warning `Unclosed client session` de aiohttp al cerrar con Ctrl+C es **cosmético**, no es un error funcional
- Las cámaras SWANN tienen 4 canales: `ch01/0` a `ch04/0` (main stream) y `ch01/1` a `ch04/1` (sub stream)
- Solo CH02 apunta a la puerta principal — los demás canales están disponibles pero no configurados
