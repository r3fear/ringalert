import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class Storage:
    def __init__(self, config: dict):
        self.photos_dir = config["storage"]["photos_dir"]
        self.retention_days = config["storage"].get("retention_days", 90)

    def get_photo_path(self, event_type: str) -> str:
        """
        Construye la ruta completa para guardar la foto.
        Ejemplo: C:/Projects/ringalert/fotos/2026/05/29/ding_101522.jpg
        """
        now = datetime.now()
        folder = os.path.join(
            self.photos_dir,
            now.strftime("%Y"),
            now.strftime("%m"),
            now.strftime("%d")
        )
        os.makedirs(folder, exist_ok=True)
        filename = f"{event_type}_{now.strftime('%H%M%S')}.jpg"
        return os.path.join(folder, filename)

    def cleanup_old_photos(self) -> int:
        """
        Elimina fotos con más de retention_days días de antigüedad.
        Retorna el número de archivos eliminados.
        """
        if self.retention_days == 0:
            return 0

        if not os.path.exists(self.photos_dir):
            return 0

        cutoff = datetime.now() - timedelta(days=self.retention_days)
        deleted = 0

        for root, dirs, files in os.walk(self.photos_dir, topdown=False):
            for filename in files:
                if not filename.endswith(".jpg"):
                    continue
                filepath = os.path.join(root, filename)
                try:
                    modified = datetime.fromtimestamp(os.path.getmtime(filepath))
                    if modified < cutoff:
                        os.remove(filepath)
                        deleted += 1
                        logger.debug(f"Eliminado: {filepath}")
                except Exception as e:
                    logger.error(f"Error eliminando {filepath}: {e}")

            # Eliminar carpetas vacías
            for folder in dirs:
                folder_path = os.path.join(root, folder)
                try:
                    if not os.listdir(folder_path):
                        os.rmdir(folder_path)
                        logger.debug(f"Carpeta vacía eliminada: {folder_path}")
                except Exception:
                    pass

        if deleted > 0:
            logger.info(f"Limpieza completada: {deleted} fotos eliminadas "
                       f"(más de {self.retention_days} días)")
        else:
            logger.info("Limpieza completada: no hay fotos antiguas que eliminar")

        return deleted

    def get_stats(self) -> dict:
        """
        Retorna estadísticas del historial local.
        """
        base = {
            "total_photos": 0,
            "total_size_mb": 0,
            "oldest": None,
            "newest": None,
            "retention_days": self.retention_days
        }

        if not os.path.exists(self.photos_dir):
            return base

        total_photos = 0
        total_size = 0
        oldest = None
        newest = None

        for root, _, files in os.walk(self.photos_dir):
            for filename in files:
                if not filename.endswith(".jpg"):
                    continue
                filepath = os.path.join(root, filename)
                try:
                    modified = datetime.fromtimestamp(os.path.getmtime(filepath))
                    size = os.path.getsize(filepath)
                    total_photos += 1
                    total_size += size
                    if oldest is None or modified < oldest:
                        oldest = modified
                    if newest is None or modified > newest:
                        newest = modified
                except Exception:
                    pass

        return {
            "total_photos": total_photos,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "oldest": oldest.strftime("%d/%m/%Y") if oldest else None,
            "newest": newest.strftime("%d/%m/%Y") if newest else None,
            "retention_days": self.retention_days
        }


if __name__ == "__main__":
    import yaml
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    storage = Storage(config)

    if "--cleanup" in sys.argv:
        print(f"Ejecutando limpieza (retención: {storage.retention_days} días)...")
        deleted = storage.cleanup_old_photos()
        print(f"✅ {deleted} fotos eliminadas")

    elif "--stats" in sys.argv:
        stats = storage.get_stats()
        print(f"📊 Estadísticas del historial:")
        print(f"   Total fotos : {stats['total_photos']}")
        print(f"   Tamaño total: {stats['total_size_mb']} MB")
        print(f"   Más antigua : {stats['oldest'] or 'N/A'}")
        print(f"   Más reciente: {stats['newest'] or 'N/A'}")
        print(f"   Retención   : {stats['retention_days']} días")

    else:
        # Mostrar ruta de prueba
        path = storage.get_photo_path("ding")
        print(f"✅ Ruta de ejemplo: {path}")
        stats = storage.get_stats()
        print(f"📊 Fotos en historial: {stats['total_photos']} ({stats['total_size_mb']} MB)")