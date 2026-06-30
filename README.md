<div align="center">

![RingAlert](https://img.shields.io/badge/RingAlert-Monitoreo_de_Timbre-1A1A2E?style=for-the-badge&logoColor=white)

*Sistema de monitoreo de timbre Ring con notificaciones por WhatsApp y captura RTSP en tiempo real*

</div>

---

## Descripción general

Cuando alguien toca el timbre o se detecta movimiento, el sistema:

1. Detecta el evento vía polling a la **API de Ring**
2. Captura un frame en tiempo real de la cámara SWANN vía RTSP usando ffmpeg
3. Envía mensaje de texto y foto por WhatsApp a través de **[wa-gateway](https://github.com/r3fear/wa-gateway)**
4. Guarda la foto en historial local organizado por fecha con retención configurable
5. Monitorea la salud del sistema y envía reporte diario

---

## Stack tecnológico

| Componente | Tecnología | Versión |
|---|---|---|
| Orquestador | Python | 3.11+ |
| Librería Ring | [ring-doorbell](https://github.com/tchellomello/python-ring-doorbell) | 0.9.14 |
| Configuración | PyYAML | 6.0+ |
| Captura cámara | ffmpeg | cualquiera con RTSP |
| Gateway WhatsApp | [wa-gateway](https://github.com/r3fear/wa-gateway) (Node.js + whatsapp-web.js) | servicio externo |
| Email fallback | smtplib (stdlib) | — |

---

## Arquitectura y flujo

```
[Ring Doorbell]
      │
      │  polling cada 5s (ring-doorbell lib)
      ▼
[ring_monitor.py]
      │ evento: ding | motion
      ▼
[main.py — on_ring_event()]
      ├──► [camera.py] ──► ffmpeg ──► [SWANN RTSP] ──► foto.jpg
      └──► [wa_sender.py]
                ├── is_server_alive() → POST /send ──► [wa-gateway :3000] ──► WhatsApp
                └── si proceso caído → send_email_fallback() ──► Gmail SMTP (HTML + foto)

[health_monitor.py] (loop cada 60s)
      ├── check_whatsapp_health() → loguea estado (emails los gestiona wa-gateway)
      └── check_daily_report()   → reporte diario + limpieza de fotos antiguas

[main.py — poll_inbox()] (loop cada 5s)
      └── GET /inbox?consumer=ringalert ──► [wa-gateway] ──► health_monitor.process_whatsapp_command()

[wa-gateway] (servicio independiente — ver repo wa-gateway)
      ├── Gestiona conexión WhatsApp Web + sesión QR
      ├── Envía email de alerta en desconexión/reconexión de WhatsApp
      ├── Cola dedicada: consumer "ringalert" aislado de otros proyectos
      └── Expone API REST: /send, /status, /inbox, /register-numbers, /register-consumer, /health
```

---

## Estructura del proyecto

```
ringalert/
├── config.yaml              ← credenciales reales (NO en GitHub)
├── config.yaml.example      ← plantilla sin credenciales
├── requirements.txt
├── start_ringalert.bat      ← script de arranque
├── ring_token.cache         ← token de sesión Ring (NO en GitHub)
│
├── main.py                  ← orquestador principal
├── ring_monitor.py          ← polling Ring, detección de eventos
├── camera.py                ← captura de frame RTSP vía ffmpeg
├── wa_sender.py             ← cliente HTTP hacia wa-gateway + email de respaldo HTML
├── storage.py               ← historial local y limpieza
├── health_monitor.py        ← reporte diario, alertas, comandos WhatsApp
│
├── sounds/
│   └── doorbell_pcm.wav     ← sonido del timbre (solo WAV)
│
├── logs/
│   └── ringalert.log        ← log principal (NO en GitHub)
│
└── fotos/                   ← historial de fotos (NO en GitHub)
    └── YYYY/MM/DD/
        ├── ding_HHMMSS.jpg
        └── motion_HHMMSS.jpg
```

---

## Módulos — descripción detallada

### `main.py`

Punto de entrada. Orquesta todos los módulos con `asyncio.gather`. Contiene:

- `setup_logging()`: configura log a archivo `logs/ringalert.log` y consola simultáneamente. Formato `YYYY-MM-DD HH:MM:SS - LEVEL - mensaje`, encoding UTF-8. Crea `logs/` si no existe.
- `play_doorbell_sound(config)`: reproduce el sonido WAV configurado vía `asyncio.create_subprocess_exec` + PowerShell `System.Media.SoundPlayer`; solo en eventos `ding`; corre en segundo plano sin bloquear.
- `on_ring_event(event_type, doorbell_name)`: callback que dispara captura de foto y envío por WhatsApp. Si la captura RTSP falla, agrega `⚠️ No se pudo capturar foto de la cámara.` al mensaje.
- `poll_inbox()`: loop cada 5 segundos; lee `GET /inbox?consumer=ringalert`; si wa-gateway responde con error de consumer no registrado, llama `register_consumer()` y reintenta una vez; detecta senders `@lid` y llama `POST /register-numbers` para refrescar el mapa en wa-gateway.
- Al arrancar (si wa-gateway disponible): llama `POST /register-consumer` para asegurar cola dedicada, luego `POST /register-numbers` con la lista de recipients para resolver `@lid`.

### `ring_monitor.py`

Clase `RingMonitor`:

- Conecta a Ring vía token en `ring_token.cache`
- Al iniciar, lee el último evento conocido y guarda `self.last_event_id` para evitar falsa alerta al arrancar
- Hace polling a `doorbell.async_history(limit=1)` cada `polling_interval` segundos
- Detecta eventos nuevos comparando IDs
- Filtra por `min_time_between_alerts` para evitar spam
- Guarda `self.last_battery` con el nivel de batería actual del timbre

Función `setup_ring()`: autenticación interactiva con 2FA (correr con `--setup`).

### `camera.py`

Función `capture_frame(rtsp_url, output_path, ffmpeg_path)`:
- Ejecuta ffmpeg como subprocess con timeout de 30s
- Flags: `-rtsp_transport tcp -vframes 1 -update 1 -y`
- Retorna `True/False`
- Maneja `FileNotFoundError`, `TimeoutExpired` y cualquier excepción sin lanzarla

### `wa_sender.py`

Clase `WhatsAppSender`. Usa exclusivamente `urllib.request` de la stdlib.

- `register_consumer()`: `POST /register-consumer` con `{"consumer": "ringalert"}`; crea cola de mensajes dedicada en wa-gateway. Seguro llamarlo en cada arranque — si ya existe, wa-gateway responde `ok` sin borrar la cola.
- `is_server_alive()`: `GET /status`; retorna `True` solo si `connected: true`.
- `send_alert(message, image_path)`: broadcast a todos los recipients configurados. Si wa-gateway no responde en HTTP (proceso caído), llama `send_email_fallback(message, image_path)`.
- `send_direct(to, message, image_path)`: envía a un destinatario específico (número o JID). Usado para responder comandos — no hace broadcast ni activa fallback de email.
- `send_email_fallback(message, image_path)`: SMTP Gmail con `starttls`. Envía email HTML con foto embebida inline (`MIMEImage` + `cid:`) cuando el archivo existe. Se activa **solo** cuando wa-gateway no responde en HTTP — los emails de desconexión interna de WhatsApp los gestiona wa-gateway.
- `build_message(event_type, doorbell_name)`: formatea el texto del mensaje con emoji y fecha.

### `storage.py`

Clase `Storage`:
- `get_photo_path(event_type)`: construye ruta `fotos/YYYY/MM/DD/tipo_HHMMSS.jpg` y crea carpetas
- `cleanup_old_photos()`: elimina JPGs más antiguos que `retention_days`, borra carpetas vacías
- `get_stats()`: retorna dict con total_photos, total_size_mb, oldest, newest

### `health_monitor.py`

Clase `HealthMonitor`. Loop cada 60 segundos.

- `check_whatsapp_health()`: detecta desconexión/reconexión de wa-gateway y loguea el cambio de estado. **Los emails de alerta por desconexión de WhatsApp los gestiona wa-gateway** — este método solo loguea.
- `check_daily_report()`: envía reporte a `daily_report_time` una vez por día; ejecuta limpieza de fotos antiguas.
- `build_daily_report()`: genera texto con batería del timbre, estado de wa-gateway y fotos del día.
- `_is_authorized_sender(sender)`: extrae dígitos del sender y compara contra `recipients`. Acepta temporalmente senders `@lid` — wa-gateway aún no resolvió el número; `poll_inbox()` dispara el refresh.
- `process_whatsapp_command(message, sender)`: procesa `ESTADO`, `SILENCIAR Nh`, `REACTIVAR`, `FOTO`; rechaza si el sender no está autorizado; responde solo al remitente vía `send_direct`.
- `silence(hours)` / `is_silenced()`: gestión de silencio temporal.

### wa-gateway (servicio externo)

El módulo de WhatsApp corre como servicio NSSM independiente. Ver su propio repositorio para documentación completa. RingAlert lo consume vía HTTP en `server_url` (por defecto `http://127.0.0.1:3000`).

Endpoints utilizados por RingAlert:

| Endpoint | Uso |
|---|---|
| `POST /register-consumer` | Registra cola de mensajes dedicada `"ringalert"` |
| `POST /register-numbers` | Registra destinatarios para resolver `@lid` (WhatsApp multi-device) |
| `POST /send` | Envía texto y/o imagen |
| `GET /status` | Verifica conexión WhatsApp (`connected: true/false`) |
| `GET /inbox?consumer=ringalert` | Retorna y vacía la cola de mensajes entrantes de este proyecto |

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
  server_url: "http://127.0.0.1:3000"  # URL del servicio wa-gateway
  recipients:
    - "521XXXXXXXXXX"           # número 1 (requerido, con código de país)
    - ""                        # número 2 (opcional)
    - ""                        # número 3 (opcional)

storage:
  photos_dir: "C:\\ruta\\para\\fotos"
  retention_days: 90            # días de retención; 0 = nunca eliminar

monitoring:
  daily_report_time: "08:00"    # hora del reporte diario HH:MM

sound:
  enabled: false                # true para reproducir sonido en eventos ding
  doorbell_file: "C:\\ruta\\al\\sonido.wav"  # solo WAV (limitación de SoundPlayer)

email:
  smtp_server: "smtp.gmail.com"
  smtp_port: 587
  sender: "tucorreo@gmail.com"
  password: "app_password_aqui" # contraseña de aplicación Gmail
  recipients:
    - "tucorreo@gmail.com"
```

---

## Comandos WhatsApp disponibles

Enviar desde un número autorizado (debe estar en `recipients`). El bot responde **solo al número que envió el comando**. Mensajes de números no autorizados se ignoran sin respuesta.

| Comando | Efecto |
|---|---|
| `ESTADO` | Reporte inmediato del sistema |
| `SILENCIAR 2h` | Silencia alertas por 2 horas |
| `SILENCIAR 0.5h` | Silencia alertas por 30 minutos |
| `REACTIVAR` | Cancela el silencio activo |
| `FOTO` | Captura y envía foto en tiempo real de la cámara |

---

## Comportamiento cuando wa-gateway no está disponible

| Situación | Timbre detectado | Comandos WA |
|---|---|---|
| wa-gateway OK + WA conectado | ✅ WhatsApp + foto | ✅ funciona |
| wa-gateway OK + WA desconectado | 📧 Email HTML + foto | ❌ no hay inbox |
| wa-gateway caído | 📧 Email HTML + foto | ❌ no hay inbox |

El email de respaldo incluye la foto embebida inline cuando está disponible. Si la captura RTSP también falla, el email llega con aviso de foto no disponible.

El reporte diario sigue el mismo comportamiento: si wa-gateway no responde a la hora configurada, el reporte llega por email.

---

## Reconexión de sesión WhatsApp

La sesión de WhatsApp la gestiona **wa-gateway**. Cuando se pierde:

1. Detener wa-gateway: `nssm stop wa-gateway`
2. Ejecutar `setup-whatsapp.bat` de wa-gateway → opción **Reconectar sesión**
3. Escanear el QR con el número secundario
4. Iniciar wa-gateway: `nssm start wa-gateway`

RingAlert no requiere reinicio — detecta la reconexión automáticamente en el siguiente ciclo.

---

## Instalación como servicio Windows

**wa-gateway** debe estar activo antes de iniciar RingAlert. Ver documentación en el repositorio wa-gateway.

### RingAlert (`main.py`)

```powershell
nssm install RingAlertMain "C:\ruta\a\python.exe"
nssm set RingAlertMain AppParameters "C:\Projects\ringalert\main.py"
nssm set RingAlertMain AppDirectory "C:\Projects\ringalert"
nssm set RingAlertMain AppStdout "C:\Projects\ringalert\logs\ringalert.log"
nssm set RingAlertMain AppStderr "C:\Projects\ringalert\logs\ringalert.log"
nssm set RingAlertMain AppRotateFiles 1
nssm set RingAlertMain Start SERVICE_AUTO_START
nssm start RingAlertMain
```

### Gestión del servicio

```powershell
nssm start RingAlertMain
nssm stop RingAlertMain
nssm restart RingAlertMain
nssm status RingAlertMain
nssm remove RingAlertMain confirm

# Ver log en tiempo real
Get-Content C:\Projects\ringalert\logs\ringalert.log -Wait -Tail 50
```

---

## Archivos que NO están en GitHub (.gitignore)

- `config.yaml` — credenciales reales
- `ring_token*.cache` — token de sesión Ring
- `fotos/` — historial de fotos
- `logs/` — archivos de log
- `__pycache__/` — caché Python

---

## Dependencias

### Python (`requirements.txt`)

```
ring-doorbell==0.9.14
pyyaml>=6.0
```

Instalar: `py -m pip install -r requirements.txt`

### Sistema

| Dependencia | Uso | Instalación |
|---|---|---|
| ffmpeg | Captura frames RTSP | `winget install --id Gyan.FFmpeg` |
| [wa-gateway](https://github.com/r3fear/wa-gateway) | Envío de WhatsApp | Ver repositorio wa-gateway |
| NSSM | Servicio Windows | [nssm.cc](https://nssm.cc/download) |

---

## Notas para IA

- La librería [`ring-doorbell`](https://github.com/tchellomello/python-ring-doorbell) `0.9.14` es **completamente asíncrona** — todos los métodos son `async/await`
- `ring.video_doorbells` **no existe** en esta versión — usar `ring.devices().doorbells`
- Los snapshots y descarga de videos requieren **suscripción Ring** — no disponible en esta instalación
- **wa-gateway debe estar corriendo antes** de iniciar `main.py` — es un servicio NSSM independiente
- El número de WhatsApp debe tener código de país sin `+`: ejemplo `5215512345678`
- `async_update_health_data()` debe llamarse antes de leer `battery_life`
- La sesión `aiohttp` de [`ring-doorbell`](https://github.com/tchellomello/python-ring-doorbell) se cierra explícitamente en el `finally` de `RingMonitor.start()` para evitar el warning `Unclosed client session`
- WhatsApp multi-device usa `@lid` como identificador del remitente. `_is_authorized_sender()` acepta `@lid` sin validar (wa-gateway aún no resolvió el número); `poll_inbox()` detecta `@lid` en inbox y llama `POST /register-numbers` para refrescar el mapa en wa-gateway
- RingAlert usa el sistema de consumers de wa-gateway con el nombre `"ringalert"`. Llama `POST /register-consumer` al arrancar y `GET /inbox?consumer=ringalert` en el loop. Si wa-gateway se reinicia, el consumer se pierde; `poll_inbox()` lo detecta (respuesta `ok: false` con "no registrado") y lo re-registra automáticamente
- `health_monitor.check_whatsapp_health()` solo loguea cambios de estado — no envía emails. Los emails de desconexión/reconexión de WhatsApp los gestiona wa-gateway internamente
- `send_email_fallback(message, image_path)` solo se activa cuando wa-gateway no responde en HTTP. Envía email HTML con foto embebida inline (`MIMEImage` + `Content-ID: ringalert_photo`) cuando la foto existe en disco
- `send_alert()` pasa `image_path` a `send_email_fallback()` — la foto va incluida en el email si existe
- Si la captura RTSP falla, `on_ring_event()` agrega `⚠️ No se pudo capturar foto de la cámara.` al mensaje, tanto para WhatsApp como para el email de respaldo
- `play_doorbell_sound` usa PowerShell `System.Media.SoundPlayer` para reproducir audio en Session 0 (NSSM) — solo soporta WAV; las rutas con comillas simples se escapan como `''`
- Los comandos de WhatsApp responden solo al remitente (`send_direct`) — no a todos los recipients (`send_alert`)
- Esta rama (`conexion-wa-gateway`) reemplaza el módulo `whatsapp/` local por el servicio externo wa-gateway. La rama `main` conserva el módulo embebido como respaldo histórico

---

## Créditos

RingAlert está construido sobre [python-ring-doorbell](https://github.com/tchellomello/python-ring-doorbell), una librería no oficial para la API de Ring desarrollada por [tchellomello](https://github.com/tchellomello) y sus colaboradores. Esta librería gestiona la autenticación, el token OAuth2, el polling de eventos y la comunicación con la API de Ring.

> **Nota:** python-ring-doorbell no esta afiliado ni respaldado por Ring, Amazon, WhatsApp ni Meta.
