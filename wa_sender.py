import json
import logging
import os
import smtplib
import urllib.request
from datetime import datetime
from email.mime.image import MIMEImage
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

    def send_direct(self, to: str, message: str, image_path: str = "") -> bool:
        """Envía mensaje a un destinatario específico (número o JID completo con @)."""
        if not self.is_server_alive():
            logger.error("Servidor WhatsApp no disponible para respuesta directa")
            return False
        ok = self._send_request(to, message, image_path)
        if ok:
            logger.info(f"✅ Respuesta directa enviada a {to}")
        return ok

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
            self.send_email_fallback(message, image_path)
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

    def send_email_fallback(self, message: str, image_path: str = ""):
        """
        Envía email de respaldo cuando WhatsApp no está disponible.
        Incluye la foto adjunta embebida si está disponible.
        """
        try:
            smtp_server = self.email_config.get("smtp_server", "smtp.gmail.com")
            smtp_port = self.email_config.get("smtp_port", 587)
            sender_addr = self.email_config.get("sender", "")
            password = self.email_config.get("password", "")
            recipients = self.email_config.get("recipients", [])

            if not sender_addr or not password or not recipients:
                logger.error("Configuración de email incompleta")
                return

            has_photo = bool(image_path and os.path.exists(image_path))
            subject = f"🔔 RingAlert — {datetime.now().strftime('%d/%m/%Y %H:%M')} (WhatsApp no disponible)"
            message_html = message.replace("\n", "<br>")

            photo_section = (
                '<img src="cid:ringalert_photo" style="max-width:100%;border-radius:8px;margin-top:12px;">'
                if has_photo else
                '<p style="color:#aaa;font-size:13px;text-align:center;margin:16px 0;">📷 Foto no disponible</p>'
            )

            html = f"""<html><body style="margin:0;padding:0;background:#f0f0f0;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f0f0;padding:24px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:white;border-radius:12px;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,0.12);">
        <tr><td style="background:#1a1a2e;padding:24px;text-align:center;">
          <div style="font-size:28px;">🔔</div>
          <div style="color:white;font-size:20px;font-weight:bold;margin-top:6px;">RingAlert</div>
          <div style="color:#aaa;font-size:13px;margin-top:4px;">Notificación por email — WhatsApp no disponible</div>
        </td></tr>
        <tr><td style="padding:28px 32px;">
          <p style="font-size:16px;color:#222;margin:0 0 20px;line-height:1.6;">{message_html}</p>
          <div style="text-align:center;">{photo_section}</div>
        </td></tr>
        <tr><td style="background:#f8f8f8;padding:16px 32px;border-top:1px solid #eee;text-align:center;">
          <p style="color:#aaa;font-size:12px;margin:0;">
            Generado automáticamente por RingAlert<br>
            Restaura <strong>wa-gateway</strong> para volver a recibir alertas por WhatsApp.
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

            if has_photo:
                msg = MIMEMultipart("related")
                msg.attach(MIMEText(html, "html", "utf-8"))
                with open(image_path, "rb") as f:
                    img = MIMEImage(f.read())
                    img.add_header("Content-ID", "<ringalert_photo>")
                    img.add_header("Content-Disposition", "inline", filename="foto.jpg")
                    msg.attach(img)
            else:
                msg = MIMEMultipart()
                msg.attach(MIMEText(html, "html", "utf-8"))

            msg["From"] = sender_addr
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject

            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(sender_addr, password)
                server.sendmail(sender_addr, recipients, msg.as_string())

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