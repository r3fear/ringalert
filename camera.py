import asyncio
import subprocess
import os
import logging
import re
import unicodedata
from datetime import datetime

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    """Mayúsculas, sin acentos ni espacios: 'Cochera Atrás' → 'COCHERAATRAS'."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", "", text).upper()


def load_cameras(camera_config: dict) -> list:
    """
    Lee la lista de cámaras de la sección `camera` del config.
    Retorna [{"num": 1, "name": "frente", "rtsp_url": "..."}, ...].
    El número es la posición en la lista (1-based).
    Compatibilidad: si no hay `cameras` pero sí `rtsp_url`, se usa como cámara 1.
    """
    entries = camera_config.get("cameras")
    if not entries:
        rtsp_url = camera_config.get("rtsp_url")
        return [{"num": 1, "name": "", "rtsp_url": rtsp_url}] if rtsp_url else []

    cameras = []
    for i, entry in enumerate(entries, start=1):
        if not entry or not entry.get("rtsp_url"):
            logger.warning(f"Cámara #{i} sin rtsp_url — se omite")
            continue
        cameras.append({
            "num": i,
            "name": str(entry.get("name") or "").strip(),
            "rtsp_url": entry["rtsp_url"],
        })
    return cameras


def find_camera(cameras: list, key) -> dict | None:
    """Busca una cámara por número ('3') o alias ('patio', sin importar mayúsculas/acentos)."""
    key = _normalize(key)
    if not key:
        return None
    if key.isdigit():
        num = int(key)
        return next((c for c in cameras if c["num"] == num), None)
    return next((c for c in cameras if c["name"] and _normalize(c["name"]) == key), None)


def resolve_cameras(cameras: list, spec) -> tuple:
    """
    Resuelve una selección de cámaras del config: número, alias, 'todas' o lista de ellos.
    Retorna (cámaras encontradas sin duplicados, claves que no existen).
    """
    keys = spec if isinstance(spec, list) else [spec]
    selected, unknown = [], []
    for key in keys:
        if _normalize(key) == "TODAS":
            matches = cameras
        else:
            camera = find_camera(cameras, key)
            matches = [camera] if camera else []
            if not camera:
                unknown.append(str(key))
        selected += [c for c in matches if c not in selected]
    return selected, unknown


async def capture_many(cameras: list, path_for, ffmpeg_path: str = "ffmpeg") -> list:
    """
    Captura varias cámaras en paralelo.
    path_for(camera) → ruta destino. Retorna [(camera, ruta | None), ...] en el mismo orden.
    """
    async def capture(camera):
        try:
            photo_path = path_for(camera)
            ok = await asyncio.to_thread(capture_frame, camera["rtsp_url"], photo_path, ffmpeg_path)
            return camera, photo_path if ok else None
        except Exception as e:
            logger.error(f"Error capturando cámara {camera_label(camera)}: {e}")
            return camera, None

    return await asyncio.gather(*(capture(c) for c in cameras))


def camera_label(camera: dict) -> str:
    """'#2 (patio)' o '#2' si no tiene alias."""
    return f"#{camera['num']} ({camera['name']})" if camera["name"] else f"#{camera['num']}"


def camera_slug(camera: dict) -> str:
    """Fragmento seguro para nombre de archivo: 'cam2_patio'."""
    name = re.sub(r"[^a-z0-9]+", "", _normalize(camera["name"]).lower())
    return f"cam{camera['num']}_{name}" if name else f"cam{camera['num']}"

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
    # Test rápido: py camera.py [número|alias]  (sin argumento prueba todas)
    import sys
    import yaml

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    ffmpeg_path = config["camera"].get("ffmpeg_path", "ffmpeg")
    photos_dir = config["storage"]["photos_dir"]
    cameras = load_cameras(config["camera"])

    if len(sys.argv) > 1:
        cam = find_camera(cameras, " ".join(sys.argv[1:]))
        if not cam:
            print(f"❌ Cámara no encontrada. Disponibles: {', '.join(camera_label(c) for c in cameras)}")
            sys.exit(1)
        cameras = [cam]

    for cam in cameras:
        output_path = build_photo_path(photos_dir, f"test_{camera_slug(cam)}")
        print(f"Cámara {camera_label(cam)} → {output_path}")
        success = capture_frame(cam["rtsp_url"], output_path, ffmpeg_path)
        print("✅ Captura exitosa!" if success else "❌ Captura fallida")