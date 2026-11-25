"""USB CDC transport — wraps serial_asyncio (extracted from serial_conn.py)."""

import asyncio
import glob
import logging

import serial_asyncio

from . import config, protocol
from .transport import Transport

log = logging.getLogger(__name__)


class UsbTransport(Transport):
    def __init__(self):
        self._reader = None
        self._writer = None
        self._pending = b""  # handshake reply, handed to the first readline()

    # ── Transport interface ────────────────────────────────────────

    async def connect(self) -> bool:
        self._pending = b""
        port = self._detect_port()
        if not port:
            log.warning("USB: no Flipper serial port found")
            return False
        try:
            self._reader, self._writer = await serial_asyncio.open_serial_connection(
                url=port, baudrate=config.SERIAL_BAUD
            )
        except Exception as e:
            log.error("USB: connect failed on %s: %s", port, e)
            return False
        # With the app closed or running on BLE the port is the Flipper CLI.
        # Staying on it would keep the bridge off BLE until the cable is pulled.
        if not await self._handshake():
            log.warning("USB: no reply from the app on %s (Flipper CLI?)", port)
            self.close()
            return False
        log.info("USB: connected on %s", port)
        return True

    async def readline(self) -> bytes:
        if self._pending:
            line, self._pending = self._pending, b""
            return line
        return await self._reader.readline()

    async def write(self, data: bytes) -> None:
        self._writer.write(data)

    async def drain(self) -> None:
        await self._writer.drain()

    def close(self) -> None:
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass

    @property
    def is_closing(self) -> bool:
        if self._writer:
            t = self._writer.transport
            return t is not None and t.is_closing()
        return False

    async def _handshake(self) -> bool:
        """Ping and wait for the app's hello/pong, keeping that line for the daemon."""

        async def wait_reply() -> bool:
            while True:
                line = await self._reader.readline()
                if not line:
                    return False
                msg = protocol.decode(line)
                # The CLI echoes our ping back, so only a reply counts.
                if msg and msg.get("t") in ("hello", "pong"):
                    self._pending = line
                    return True

        try:
            self._writer.write(protocol.ping_msg())
            await self._writer.drain()
            return await asyncio.wait_for(wait_reply(), config.USB_HANDSHAKE_TIMEOUT)
        except Exception as e:  # timeout, or the port vanished mid-handshake
            log.debug("USB: handshake failed: %r", e)
            return False

    # ── Port detection ─────────────────────────────────────────────

    def _detect_port(self) -> str | None:
        if config.SERIAL_PORT:
            return config.SERIAL_PORT
        ports = sorted(glob.glob(config.SERIAL_GLOB_PATTERN))
        # Dual CDC: channel 0 = CLI, channel 1 = app (higher suffix)
        return ports[-1] if ports else None
