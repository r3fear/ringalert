import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from ring_doorbell import Ring, Auth

logger = logging.getLogger(__name__)

TOKEN_FILE = "ring_token.cache"

def token_updated(token):
    Path(TOKEN_FILE).write_text(json.dumps(token))

async def get_ring_auth() -> Auth:
    if Path(TOKEN_FILE).exists():
        token = json.loads(Path(TOKEN_FILE).read_text())
        auth = Auth("RingAlert/1.0", token, token_updated)
        logger.info("Token Ring cargado desde cache")
    else:
        raise FileNotFoundError(
            "No se encontró ring_token.cache — "
            "corre setup_ring.py primero para autenticarte"
        )
    return auth

async def get_doorbell(ring: Ring):
    devices = ring.devices()
    doorbells = devices.doorbells
    if not doorbells:
        raise RuntimeError("No se encontró ningún timbre Ring en la cuenta")
    return doorbells[0]

class RingMonitor:
    def __init__(self, config: dict, event_callback):
        """
        config: sección 'ring' del config.yaml
        event_callback: función async que se llama con (event_type, doorbell_name)
                        event_type: 'ding' o 'motion'
        """
        self.polling_interval = config.get("polling_interval", 5)
        self.min_time_between_alerts = config.get("min_time_between_alerts", 60)
        self.event_callback = event_callback
        self.last_event_id = None
        self.last_alert_time = None
        self.running = False
        self.last_battery = None

    async def start(self):
        logger.info("Iniciando monitor Ring...")
        auth = await get_ring_auth()
        ring = Ring(auth)
        await ring.async_update_data()
        doorbell = await get_doorbell(ring)

        logger.info(f"Monitoreando: '{doorbell.name}' | "
                    f"Polling cada {self.polling_interval}s | "
                    f"Mínimo entre alertas: {self.min_time_between_alerts}s")

        self.running = True

        while self.running:
            try:
                await ring.async_update_data()
                await doorbell.async_update_health_data()

                # Guardar batería actualizada
                try:
                    self.last_battery = doorbell.battery_life
                except Exception:
                    self.last_battery = None

                historial = await doorbell.async_history(limit=1)

                if historial:
                    evento = historial[0]
                    evento_id = evento.get("id")
                    kind = evento.get("kind", "")

                    if evento_id != self.last_event_id:
                        self.last_event_id = evento_id

                        if kind in ("ding", "motion"):
                            now = datetime.now()

                            if self.last_alert_time is None or \
                               (now - self.last_alert_time).total_seconds() >= self.min_time_between_alerts:

                                self.last_alert_time = now
                                logger.info(f"Evento detectado: [{kind}] en '{doorbell.name}'")
                                await self.event_callback(kind, doorbell.name)
                            else:
                                segundos = (now - self.last_alert_time).total_seconds()
                                logger.debug(f"Evento [{kind}] ignorado — "
                                           f"faltan {self.min_time_between_alerts - segundos:.0f}s")

            except Exception as e:
                logger.error(f"Error en polling Ring: {e}")

            await asyncio.sleep(self.polling_interval)

    def stop(self):
        self.running = False
        logger.info("Monitor Ring detenido")


# Setup inicial — correr una sola vez para autenticarse
async def setup_ring():
    import getpass
    from ring_doorbell import Auth

    print("=== Setup Ring ===")
    email = input("Email Ring: ")
    password = getpass.getpass("Contraseña Ring: ")

    auth = Auth("RingAlert/1.0", None, token_updated)
    try:
        await auth.async_fetch_token(email, password)
    except Exception:
        codigo = input("Código 2FA: ")
        await auth.async_fetch_token(email, password, codigo)

    print(f"✅ Token guardado en {TOKEN_FILE}")

    # Verificar conexión
    ring = Ring(auth)
    await ring.async_update_data()
    devices = ring.devices()
    doorbells = devices.doorbells
    print(f"✅ Timbres encontrados: {len(doorbells)}")
    for d in doorbells:
        print(f"   - {d.name} ({d.model})")


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    if "--setup" in sys.argv:
        asyncio.run(setup_ring())
    else:
        async def test_callback(event_type, doorbell_name):
            print(f"\n🔔 EVENTO: [{event_type}] en '{doorbell_name}' — {datetime.now()}")

        async def test():
            import yaml
            with open("config.yaml") as f:
                config = yaml.safe_load(f)

            monitor = RingMonitor(config["ring"], test_callback)
            print("Escuchando eventos... (Ctrl+C para salir)")
            try:
                await monitor.start()
            finally:
                # Cerrar sesión aiohttp limpiamente
                import aiohttp
                await asyncio.sleep(0.25)

        try:
            asyncio.run(test())
        except KeyboardInterrupt:
            print("\n👋 Detenido")