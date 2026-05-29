import subprocess
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def capture_frame(rtsp_url: str, output_path: str, ffmpeg_path: str = "ffmpeg") -> bool:
    """
    Captura un frame de la cámara SWANN vía RTSP y lo guarda como JPG.
    Retorna True si fue exitoso, False si falló.
    """
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        cmd = [
            ffmpeg_path,
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-vframes", "1",
            "-q:v", "2",
            "-update", "1",
            output_path,
            "-y"
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            size_kb = os.path.getsize(output_path) / 1024
            logger.info(f"Frame capturado: {output_path} ({size_kb:.1f} KB)")
            return True
        else:
            logger.error(f"ffmpeg no generó imagen. stderr: {result.stderr[-300:]}")
            return False

    except subprocess.TimeoutExpired:
        logger.error("Timeout al capturar frame de la cámara")
        return False
    except FileNotFoundError:
        logger.error(f"ffmpeg no encontrado en: {ffmpeg_path}")
        return False
    except Exception as e:
        logger.error(f"Error capturando frame: {e}")
        return False


def build_photo_path(photos_dir: str, event_type: str) -> str:
    """
    Construye la ruta completa para guardar la foto con estructura de carpetas por fecha.
    Ejemplo: C:/ringalert/fotos/2026/05/28/ding_185334.jpg
    """
    now = datetime.now()
    folder = os.path.join(
        photos_dir,
        now.strftime("%Y"),
        now.strftime("%m"),
        now.strftime("%d")
    )
    filename = f"{event_type}_{now.strftime('%H%M%S')}.jpg"
    return os.path.join(folder, filename)


if __name__ == "__main__":
    # Test rápido
    import yaml

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)

    rtsp_url = config["camera"]["rtsp_url"]
    ffmpeg_path = config["camera"].get("ffmpeg_path", "ffmpeg")
    photos_dir = config["storage"]["photos_dir"]

    output_path = build_photo_path(photos_dir, "test")
    print(f"Capturando frame en: {output_path}")

    success = capture_frame(rtsp_url, output_path, ffmpeg_path)
    print("✅ Captura exitosa!" if success else "❌ Captura fallida")