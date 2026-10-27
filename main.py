import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
import yaml

from ring_monitor import RingMonitor
from camera import capture_many, load_cameras, resolve_cameras, camera_label, camera_slug
from wa_sender import WhatsAppSender
from storage import Storage
from health_monitor import HealthMonitor

# Configurar logging
def setup_logging():
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("logs/ringalert.log", encoding="utf-8"),
            logging.StreamHandler()
        ]
    )

logger = logging.getLogger(__name__)


def load_config() -> dict:
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def play_doorbell_sound(config: dict):
    sound_cfg = config.get("sound", {})
    if not sound_cfg.get("enabled", False):
        return
    sound_file = sound_cfg.get("doorbell_file", "")
    if not sound_file:
        return
    if not os.path.exists(sound_file):
        logger.error(f"Archivo de sonido no encontrado: {sound_file}")
        return
    try:
        # Usar PowerShell + SoundPlayer para compatibilidad con Session 0 (NSSM)
        safe_path = sound_file.replace("'", "''")
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            f"(New-Object System.Media.SoundPlayer '{safe_path}').PlaySync()",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0 and stderr:
            logger.error(f"Error en SoundPlayer: {stderr.decode().strip()}")
    except asyncio.TimeoutError:
        logger.error("Timeout reproduciendo sonido — archivo demasiado largo o PowerShell no responde")
        proc.kill()
    except Exception as e:
        logger.error(f"Error reproduciendo sonido: {e}")


async def main():
    setup_logging()
    logger.info("=" * 50)
    logger.info("  RingAlert iniciando...")
    logger.info("=" * 50)

    config = load_config()

    wa_sender = WhatsAppSender(config)
    storage = Storage(config)
    health_monitor = HealthMonitor(config)

    # Cámaras por tipo de evento (event_cameras); si un evento no está definido usa doorbell_camera
    cameras = load_cameras(config["camera"])
    event_specs = config["camera"].get("event_cameras") or {}
    default_spec = config["camera"].get("doorbell_camera", 1)

    def cameras_for(event: str) -> list:
        spec = event_specs.get(event, default_spec)
        if spec is None or spec == []:
            return []  # explícitamente sin foto
        selected, unknown = resolve_cameras(cameras, spec)
        if unknown:
            logger.warning(f"Cámaras inexistentes para '{event}': {', '.join(unknown)}")
        if not selected and cameras:
            selected = cameras[:1]
            logger.warning(f"Ninguna cámara válida para '{event}' — usando cámara {camera_label(selected[0])}")
        return selected

    event_cameras = {event: cameras_for(event) for event in ("ding", "motion")}
    if not cameras:
        logger.warning("⚠️ No hay cámaras configuradas — las alertas se enviarán sin foto")
    else:
        logger.info(f"📷 {len(cameras)} cámara(s) configurada(s)")
        for event, selected in event_cameras.items():
            labels = ", ".join(camera_label(c) for c in selected) or "sin foto"
            logger.info(f"📷 Evento {event}: {labels}")

    # Verificar servidor WhatsApp al inicio
    if wa_sender.is_server_alive():
        logger.info("✅ Servidor WhatsApp conectado")
        # Registrar consumer para cola de mensajes dedicada
        wa_sender.register_consumer()
        # Registrar destinatarios para resolver @lid (WhatsApp multi-device)
        try:
            numbers = [r for r in config.get("whatsapp", {}).get("recipients", []) if r and r.strip()]
            payload = json.dumps({"numbers": numbers}).encode("utf-8")
            req = urllib.request.Request(
                f"{config['whatsapp']['server_url']}/register-numbers",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=10)
            logger.info(f"✅ Destinatarios registrados para resolución @lid ({len(numbers)} números)")
        except Exception as e:
            logger.warning(f"No se pudo registrar destinatarios en wa-server: {e}")
    else:
        logger.warning("⚠️ Servidor WhatsApp no disponible al iniciar.")

    # Callback que se ejecuta cuando Ring detecta un evento
    async def on_ring_event(event_type: str, doorbell_name: str):
        logger.info(f"Procesando evento: [{event_type}] — {doorbell_name}")

        if health_monitor.is_silenced():
            logger.info("Alertas silenciadas — evento ignorado")
            return

        # Reproducir sonido en eventos ding (no bloquea el flujo principal)
        if event_type == "ding":
            asyncio.create_task(play_doorbell_sound(config))

        # 1. Capturar en paralelo las cámaras asignadas a este tipo de evento
        selected = event_cameras.get(event_type)
        if selected is None:
            selected = cameras_for(event_type)
        ffmpeg_path = config["camera"].get("ffmpeg_path", "ffmpeg")
        results = await capture_many(
            selected,
            lambda c: storage.get_photo_path(f"{event_type}_{camera_slug(c)}"),
            ffmpeg_path
        )
        captured = [(c, path) for c, path in results if path]
        failed = [camera_label(c) for c, path in results if not path]

        # 2. Construir mensaje
        message = wa_sender.build_message(event_type, doorbell_name)
        if failed:
            logger.warning(f"No se pudo capturar foto de: {', '.join(failed)}")
            if captured:
                message += f"\n⚠️ No se pudo capturar: {', '.join(failed)}"
            else:
                message += "\n⚠️ No se pudo capturar foto de la cámara."
        if len(selected) > 1 and captured:
            message += f"\n📸 Cámara {camera_label(captured[0][0])}"

        # 3. Enviar: la primera foto va con el mensaje (y al email de respaldo); el resto solo por WhatsApp
        photo_path = captured[0][1] if captured else ""
        extra_images = [(f"📸 Cámara {camera_label(c)}", path) for c, path in captured[1:]]
        wa_sender.send_alert(message, photo_path, extra_images)

    # Correr todo en paralelo
    ring_monitor = RingMonitor(config["ring"], on_ring_event)
    health_monitor.ring_monitor = ring_monitor

    logger.info("🚀 Sistema activo — escuchando eventos")

    async def poll_inbox():
        last_registration = [0.0]
        while True:
            try:
                req = urllib.request.Request(
                    f"{config['whatsapp']['server_url']}/inbox?consumer=ringalert"
                )
                try:
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = json.loads(resp.read())
                except urllib.error.HTTPError as http_err:
                    try:
                        data = json.loads(http_err.read())
                    except Exception:
                        data = {"ok": False, "error": str(http_err)}

                # Consumer no registrado (wa-gateway reiniciado): registrar con cooldown
                if not data.get("ok") and "no registrado" in data.get("error", "").lower():
                    now = time.time()
                    if now - last_registration[0] > 60:
                        wa_sender.register_consumer()
                        last_registration[0] = now
                    data = {"messages": []}

                lid_detected = False
                for msg in data.get("messages", []):
                    body = msg.get("body", "").strip()
                    sender = msg.get("from", "")
                    if sender.endswith("@lid"):
                        lid_detected = True
                    if body:
                        logger.info(f"Comando recibido de {sender}: {body}")
                        await health_monitor.process_whatsapp_command(body, sender)
                if lid_detected:
                    try:
                        numbers = [r for r in config.get("whatsapp", {}).get("recipients", []) if r and r.strip()]
                        payload = json.dumps({"numbers": numbers}).encode("utf-8")
                        req2 = urllib.request.Request(
                            f"{config['whatsapp']['server_url']}/register-numbers",
                            data=payload,
                            headers={"Content-Type": "application/json"}
                        )
                        urllib.request.urlopen(req2, timeout=10)
                        logger.info("Mapa @lid actualizado — próximos mensajes llegarán con número resuelto")
                    except Exception as e:
                        logger.warning(f"No se pudo actualizar mapa @lid: {e}")
            except Exception as e:
                logger.error(f"Error en poll_inbox: {e}")
            await asyncio.sleep(5)

    try:
        await asyncio.gather(
            ring_monitor.start(),
            health_monitor.start(),
            poll_inbox()
        )
    except asyncio.CancelledError:
        pass
    finally:
        ring_monitor.stop()
        health_monitor.stop()
        await asyncio.sleep(0.25)  # dar tiempo a aiohttp para cerrar
        logger.info("RingAlert detenido")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 RingAlert detenido")