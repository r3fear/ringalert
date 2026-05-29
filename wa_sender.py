import json
import logging
import smtplib
import urllib.request
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


class WhatsAppSender:
    def __init__(self, config: dict):
        """
        config: config.yaml completo (necesita 'whatsapp' y 'email')
        """
        wa_config = config.get("whatsapp", {})
        self.server_url = wa_config.get("server_url", "http://127.0.0.1:3000")
        self.recipients = [
            r for r in wa_config.get("recipients", []) if r and r.strip()
        ]
        self.email_config = config.get("email", {})

    def is_server_alive(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.server_url}/status")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return data.get("connected", False)
        except Exception:
            return False

    def _send_request(self, to: str, message: str, image_path: str = "") -> bool:
        try:
            payload = json.dumps({
                "to": to,
                "message": message,
                "imagePath": image_path
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{self.server_url}/send",
                data=payload,
                headers={"Content-Type": "application/json"}
            )

            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                return result.get("ok", False)

        except Exception as e:
            logger.error(f"Error enviando a {to}: {e}")
            return False

    def send_alert(self, message: str, image_path: str = "") -> bool:
        """
        Envía mensaje y foto a todos los destinatarios.
        Retorna True si al menos uno recibió el mensaje.
        """
        if not self.recipients:
            logger.warning("No hay destinatarios configurados")
            return False

        if not self.is_server_alive():
            logger.error("Servidor WhatsApp no disponible — enviando alerta por email")
            self.send_email_fallback(message)
            return False

        success_count = 0
        for recipient in self.recipients:
            ok = self._send_request(recipient, message, image_path)
            if ok:
                success_count += 1
                logger.info(f"✅ Mensaje enviado a {recipient}")
            else:
                logger.error(f"❌ Fallo al enviar a {recipient}")

        return success_count > 0

    def send_email_fallback(self, message: str):
        """
        Envía email de respaldo cuando WhatsApp no está disponible.
        """
        try:
            smtp_server = self.email_config.get("smtp_server", "smtp.gmail.com")
            smtp_port = self.email_config.get("smtp_port", 587)
            sender = self.email_config.get("sender", "")
            password = self.email_config.get("password", "")
            recipients = self.email_config.get("recipients", [])

            if not sender or not password or not recipients:
                logger.error("Configuración de email incompleta")
                return

            subject = f"⚠️ RingAlert — WhatsApp caído {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            body = (
                f"El servicio de WhatsApp de RingAlert no está disponible.\n\n"
                f"Mensaje original:\n{message}\n\n"
                f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n\n"
                f"Revisa el servidor para restaurar el servicio."
            )

            msg = MIMEMultipart()
            msg["From"] = sender
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(sender, password)
                server.sendmail(sender, recipients, msg.as_string())

            logger.info(f"📧 Email de respaldo enviado a {recipients}")

        except Exception as e:
            logger.error(f"Error enviando email de respaldo: {e}")

    def build_message(self, event_type: str, doorbell_name: str) -> str:
        now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        if event_type == "ding":
            return f"🔔 Alguien tocó el timbre - {doorbell_name} - {now}"
        elif event_type == "motion":
            return f"🚶 Movimiento detectado - {doorbell_name} - {now}"
        else:
            return f"📡 Evento [{event_type}] - {doorbell_name} - {now}"


if __name__ == "__main__":
    import yaml
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    sender = WhatsAppSender(config)

    # Verificar estado del servidor
    print(f"Estado servidor WhatsApp: {'✅ Conectado' if sender.is_server_alive() else '❌ Desconectado'}")

    if "--test" in sys.argv:
        message = sender.build_message("ding", "Puerta principal")
        print(f"Enviando mensaje de prueba: {message}")
        ok = sender.send_alert(message)
        print("✅ Enviado correctamente" if ok else "❌ Falló el envío")

    elif "--test-email" in sys.argv:
        print("Enviando email de prueba...")
        sender.send_email_fallback("Mensaje de prueba desde RingAlert")
        print("✅ Email enviado (revisa tu bandeja)")