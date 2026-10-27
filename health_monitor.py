import asyncio
import logging
from datetime import datetime, time

from wa_sender import WhatsAppSender
from storage import Storage
from camera import capture_frame, capture_many, load_cameras, find_camera, camera_label, camera_slug

logger = logging.getLogger(__name__)


class HealthMonitor:
    def __init__(self, config: dict, ring_monitor=None):
        self.config = config
        self.wa_sender = WhatsAppSender(config)
        self.storage = Storage(config)
        self.ring_monitor = ring_monitor
        self.cameras = load_cameras(config["camera"])
        self.daily_report_time = config["monitoring"].get("daily_report_time", "08:00")
        self.silenced_until = None
        self.last_report_date = None
        self.wa_was_connected = True
        self.running = False

    def is_silenced(self) -> bool:
        if self.silenced_until is None:
            return False
        if datetime.now() < self.silenced_until:
            return True
        self.silenced_until = None
        return False

    def silence(self, hours: float):
        from datetime import timedelta
        self.silenced_until = datetime.now() + timedelta(hours=hours)
        logger.info(f"Alertas silenciadas por {hours} horas hasta {self.silenced_until.strftime('%H:%M')}")

    def build_daily_report(self) -> str:
        battery = "N/A"
        if self.ring_monitor and self.ring_monitor.last_battery is not None:
            battery = f"{self.ring_monitor.last_battery}%"

        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        wa_status = "✅ Conectado" if self.wa_sender.is_server_alive() else "❌ Desconectado"
        silenced_text = ""
        if self.is_silenced():
            silenced_text = f"\n⏸ Silenciado hasta {self.silenced_until.strftime('%H:%M')}"

        return (
            f"📊 Reporte RingAlert — {now}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔔 Timbre  : Puerta principal\n"
            f"🔋 Batería : {battery}\n"
            f"💬 WhatsApp: {wa_status}\n"
            f"📸 Fotos hoy: {self._photos_today()}"
            f"{silenced_text}"
        )

    def _photos_today(self) -> int:
        import os
        today = datetime.now().strftime("%Y/%m/%d").replace("/", os.sep)
        today_dir = os.path.join(self.storage.photos_dir, today)
        if not os.path.exists(today_dir):
            return 0
        return len([f for f in os.listdir(today_dir) if f.endswith(".jpg")])

    def _cameras_list(self) -> str:
        lines = []
        for c in self.cameras:
            alias = f" o FOTO {c['name'].upper()}" if c["name"] else ""
            lines.append(f"• {camera_label(c)} → FOTO{c['num']}{alias}")
        lines.append("• Todas → FOTO TODAS")
        return "\n".join(lines)

    async def _send_all_photos(self, reply):
        """Captura todas las cámaras en paralelo y envía una foto por cámara."""
        ffmpeg_path = self.config["camera"].get("ffmpeg_path", "ffmpeg")
        logger.info(f"Comando FOTO TODAS: capturando {len(self.cameras)} cámaras...")
        reply(f"⏳ Capturando {len(self.cameras)} cámaras...")

        results = await capture_many(
            self.cameras,
            lambda c: self.storage.get_photo_path(f"foto_{camera_slug(c)}"),
            ffmpeg_path
        )

        failed = []
        for camera, photo_path in results:
            if photo_path:
                reply(f"📸 Foto cámara {camera_label(camera)}:", image_path=photo_path)
            else:
                failed.append(camera_label(camera))
        if failed:
            logger.error(f"Comando FOTO TODAS: capturas fallidas en {', '.join(failed)}")
            reply(f"❌ No se pudo capturar: {', '.join(failed)}. Verifica las cámaras.")

    async def check_whatsapp_health(self):
        is_connected = self.wa_sender.is_server_alive()

        if self.wa_was_connected and not is_connected:
            logger.error("wa-gateway no disponible — las alertas de email las gestiona wa-gateway")
            self.wa_was_connected = False

        elif not self.wa_was_connected and is_connected:
            logger.info("wa-gateway reconectado")
            self.wa_was_connected = True

    async def check_daily_report(self):
        now = datetime.now()
        report_hour, report_minute = map(int, self.daily_report_time.split(":"))
        report_time = time(report_hour, report_minute)

        is_report_time = (
            now.hour == report_time.hour and
            now.minute == report_time.minute
        )
        already_sent_today = self.last_report_date == now.date()

        if is_report_time and not already_sent_today:
            if not self.is_silenced():
                report = self.build_daily_report()
                logger.info("Enviando reporte diario...")
                self.wa_sender.send_alert(report)

            # Limpieza diaria de fotos antiguas
            self.storage.cleanup_old_photos()
            self.last_report_date = now.date()

    def _is_authorized_sender(self, sender: str) -> bool:
        if not sender:
            return True
        # @lid = wa-gateway no pudo resolver el número aún; poll_inbox dispara refresh
        if sender.endswith('@lid'):
            return True
        sender_digits = ''.join(filter(str.isdigit, sender))
        authorized = [
            ''.join(filter(str.isdigit, r))
            for r in self.config.get("whatsapp", {}).get("recipients", [])
            if r and r.strip()
        ]
        return sender_digits in authorized

    async def process_whatsapp_command(self, message: str, sender: str = "") -> bool:
        """
        Procesa comandos recibidos por WhatsApp.
        Responde solo al remitente (sender). Retorna True si era un comando válido.
        """
        if not self._is_authorized_sender(sender):
            logger.warning(f"Comando ignorado — remitente no autorizado: {sender}")
            return False

        message = message.strip().upper()

        def reply(text, image_path=""):
            if sender:
                self.wa_sender.send_direct(sender, text, image_path)
            else:
                self.wa_sender.send_alert(text, image_path)

        if message.startswith("SILENCIAR"):
            try:
                parts = message.split()
                if len(parts) == 2:
                    value = parts[1]
                    if value.endswith("H"):
                        hours = float(value[:-1])
                    else:
                        hours = float(value)
                    self.silence(hours)
                    reply(
                        f"⏸ Alertas silenciadas por {hours} horas.\n"
                        f"Se reactivarán a las {self.silenced_until.strftime('%H:%M')}.\n"
                        f"Envía REACTIVAR para cancelar el silencio."
                    )
                    return True
            except Exception:
                pass

        elif message == "REACTIVAR":
            self.silenced_until = None
            reply("🔔 Alertas reactivadas.")
            return True

        elif message == "ESTADO":
            reply(self.build_daily_report())
            return True

        elif message == "CAMARAS" or message == "CÁMARAS":
            reply(f"📷 Cámaras disponibles:\n{self._cameras_list()}")
            return True

        elif message.startswith("FOTO"):
            # FOTO → cámara 1 · FOTO2 / FOTO 2 → cámara 2 · FOTO PATIO / FOTOPATIO → alias
            # FOTO TODAS → todas las cámaras
            key = message[4:].strip()
            if not self.cameras:
                reply("❌ No hay cámaras configuradas.")
                return True
            if key.replace(" ", "") == "TODAS":
                await self._send_all_photos(reply)
                return True
            camera = find_camera(self.cameras, key) if key else self.cameras[0]
            if camera is None:
                logger.warning(f"Comando FOTO: cámara '{key}' no existe")
                reply(
                    f"❌ La cámara '{key}' no existe.\n"
                    f"Cámaras disponibles:\n{self._cameras_list()}"
                )
                return True
            label = camera_label(camera)
            try:
                logger.info(f"Comando FOTO: iniciando captura de cámara {label}...")
                ffmpeg_path = self.config["camera"].get("ffmpeg_path", "ffmpeg")
                photo_path = self.storage.get_photo_path(f"foto_{camera_slug(camera)}")
                logger.info(f"Comando FOTO: capturando en {photo_path}")
                ok = capture_frame(camera["rtsp_url"], photo_path, ffmpeg_path)
                if ok:
                    logger.info("Comando FOTO: captura exitosa, enviando por WhatsApp...")
                    reply(f"📸 Foto cámara {label}:", image_path=photo_path)
                else:
                    logger.error(f"Comando FOTO: captura fallida en cámara {label}")
                    reply(f"❌ No se pudo capturar la foto de la cámara {label}. Verifica la cámara.")
            except Exception as e:
                logger.error(f"Comando FOTO: error inesperado — {e}")
                reply("❌ Error interno al procesar FOTO.")
            return True

        return False

    async def start(self):
        logger.info(f"Monitor de salud iniciado — reporte diario a las {self.daily_report_time}")
        self.running = True

        while self.running:
            try:
                await self.check_whatsapp_health()
                await self.check_daily_report()
            except Exception as e:
                logger.error(f"Error en health monitor: {e}")

            await asyncio.sleep(60)  # verificar cada minuto

    def stop(self):
        self.running = False


if __name__ == "__main__":
    import yaml
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    monitor = HealthMonitor(config)

    if "--report" in sys.argv:
        report = monitor.build_daily_report()
        print("=== Reporte generado ===")
        print(report)
        print("\nEnviando por WhatsApp...")
        ok = monitor.wa_sender.send_alert(report)
        print("✅ Enviado" if ok else "❌ Falló")

    elif "--test-email" in sys.argv:
        print("Enviando alerta de caída por email...")
        monitor.wa_sender.send_email_fallback(
            "⚠️ Test de alerta por email desde RingAlert"
        )
        print("✅ Revisa tu correo")