import asyncio
import json
import logging
import os
import urllib.request
import yaml

from ring_monitor import RingMonitor
from camera import capture_frame
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

    # Verificar servidor WhatsApp al inicio
    if wa_sender.is_server_alive():
        logger.info("✅ Servidor WhatsApp conectado")
        # Registrar destinatarios para resolver @lid (WhatsApp multi-device)
        try:
            numbers = [r for r in config.get("whatsapp", {}).get("recipients", []) if r and r.strip()]
            payload = json.dumps({"numbers": numbers}).encode("utf-8")
            req = urllib.request.Request(
                f"{config['whatsapp']['server_url']}/register-recipients",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=10)
            logger.info(f"✅ Destinatarios registrados para resolución @lid ({len(numbers)} números)")
        except Exception as e:
            logger.warning(f"No se pudo registrar destinatarios en wa-server: {e}")
    else:
        logger.warning("⚠️ Servidor WhatsApp no disponible al iniciar")

    # Callback que se ejecuta cuando Ring detecta un evento
    async def on_ring_event(event_type: str, doorbell_name: str):
        logger.info(f"Procesando evento: [{event_type}] — {doorbell_name}")

        if health_monitor.is_silenced():
            logger.info("Alertas silenciadas — evento ignorado")
            return

        # Reproducir sonido en eventos ding (no bloquea el flujo principal)
        if event_type == "ding":
            asyncio.create_task(play_doorbell_sound(config))

        # 1. Capturar foto de la cámara SWANN
        photo_path = storage.get_photo_path(event_type)
        rtsp_url = config["camera"]["rtsp_url"]
        ffmpeg_path = config["camera"].get("ffmpeg_path", "ffmpeg")

        photo_ok = capture_frame(rtsp_url, photo_path, ffmpeg_path)

        if not photo_ok:
            logger.warning("No se pudo capturar foto — se enviará solo el texto")
            photo_path = ""

        # 2. Construir mensaje
        message = wa_sender.build_message(event_type, doorbell_name)

        # 3. Enviar por WhatsApp
        wa_sender.send_alert(message, photo_path)

    # Correr todo en paralelo
    ring_monitor = RingMonitor(config["ring"], on_ring_event)
    health_monitor.ring_monitor = ring_monitor

    logger.info("🚀 Sistema activo — escuchando eventos")

    async def poll_inbox():
        while True:
            try:
                req = urllib.request.Request(
                    f"{config['whatsapp']['server_url']}/inbox"
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                    for msg in data.get("messages", []):
                        body = msg.get("body", "").strip()
                        sender = msg.get("from", "")
                        if body:
                            logger.info(f"Comando recibido de {sender}: {body}")
                            await health_monitor.process_whatsapp_command(body, sender)
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