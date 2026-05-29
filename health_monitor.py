import asyncio
import json
import logging
from datetime import datetime, time

from wa_sender import WhatsAppSender
from storage import Storage
from camera import capture_frame

logger = logging.getLogger(__name__)


class HealthMonitor:
    def __init__(self, config: dict, ring_monitor=None):
        self.config = config
        self.wa_sender = WhatsAppSender(config)
        self.storage = Storage(config)
        self.ring_monitor = ring_monitor
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

    async def check_whatsapp_health(self):
        is_connected = self.wa_sender.is_server_alive()

        if self.wa_was_connected and not is_connected:
            logger.error("WhatsApp se desconectó — enviando alerta por email")
            self.wa_sender.send_email_fallback(
                "⚠️ El servicio de WhatsApp de RingAlert se ha desconectado.\n"
                f"Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
                "Revisa el servidor y reinicia wa-server.js si es necesario.\n\n"
                "Para silenciar estas alertas responde SILENCIAR Xh por WhatsApp "
                "cuando el servicio se restaure."
            )
            self.wa_was_connected = False

        elif not self.wa_was_connected and is_connected:
            logger.info("WhatsApp reconectado — notificando por email")
            self.wa_sender.send_email_fallback(
                "✅ El servicio de WhatsApp de RingAlert se ha restaurado.\n"
                f"Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
            )
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

    async def process_whatsapp_command(self, message: str) -> bool:
        """
        Procesa comandos recibidos por WhatsApp.
        Retorna True si el mensaje era un comando válido.
        """
        message = message.strip().upper()

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
                    self.wa_sender.send_alert(
                        f"⏸ Alertas silenciadas por {hours} horas.\n"
                        f"Se reactivarán a las {self.silenced_until.strftime('%H:%M')}.\n"
                        f"Envía REACTIVAR para cancelar el silencio."
                    )
                    return True
            except Exception:
                pass

        elif message == "REACTIVAR":
            self.silenced_until = None
            self.wa_sender.send_alert("🔔 Alertas reactivadas.")
            return True

        elif message == "ESTADO":
            report = self.build_daily_report()
            self.wa_sender.send_alert(report)
            return True

        elif message == "FOTO":
            try:
                logger.info("Comando FOTO: iniciando captura...")
                rtsp_url = self.config["camera"]["rtsp_url"]
                ffmpeg_path = self.config["camera"].get("ffmpeg_path", "ffmpeg")
                photo_path = self.storage.get_photo_path("foto")
                logger.info(f"Comando FOTO: capturando en {photo_path}")
                ok = capture_frame(rtsp_url, photo_path, ffmpeg_path)
                if ok:
                    logger.info("Comando FOTO: captura exitosa, enviando por WhatsApp...")
                    self.wa_sender.send_alert("📸 Foto capturada:", image_path=photo_path)
                else:
                    logger.error("Comando FOTO: captura fallida")
                    self.wa_sender.send_alert("❌ No se pudo capturar la foto. Verifica la cámara.")
            except Exception as e:
                logger.error(f"Comando FOTO: error inesperado — {e}")
                self.wa_sender.send_alert("❌ Error interno al procesar FOTO.")
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