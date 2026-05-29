import asyncio
import logging
import yaml
from datetime import datetime

from ring_monitor import RingMonitor
from camera import capture_frame
from wa_sender import WhatsAppSender
from storage import Storage
from health_monitor import HealthMonitor

# Configurar logging
def setup_logging():
    import os
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
    else:
        logger.warning("⚠️ Servidor WhatsApp no disponible al iniciar")

    # Callback que se ejecuta cuando Ring detecta un evento
    async def on_ring_event(event_type: str, doorbell_name: str):
        logger.info(f"Procesando evento: [{event_type}] — {doorbell_name}")

        if health_monitor.is_silenced():
            logger.info("Alertas silenciadas — evento ignorado")
            return

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
        import urllib.request
        import json
        while True:
            try:
                req = urllib.request.Request(
                    f"{config['whatsapp']['server_url']}/inbox"
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                    for msg in data.get("messages", []):
                        body = msg.get("body", "").strip()
                        if body:
                            logger.info(f"Comando recibido: {body}")
                            await health_monitor.process_whatsapp_command(body)
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