import asyncio
import logging
import re
import threading
from typing import Callable, Optional
from urllib.parse import unquote

from app.config import config_manager

logger = logging.getLogger("parking.ftp")

MAX_SNAPSHOT_BYTES = 15 * 1024 * 1024


class InMemoryFTPProtocol:
    """
    Lightweight, zero-disk asyncio FTP receiver tailored for IP cameras
    (TP-LINK / Hikvision / Dahua scheduled & motion-triggered FTP snapshot uploads).
    Accepts PASV/EPSV + STOR straight into RAM (`bytearray`), invokes `on_snapshot`,
    and immediately frees the image buffer.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        on_snapshot: Callable[[str, bytes, str], None],
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.on_snapshot = on_snapshot
        self.authenticated = False
        self.pending_user = ""
        self.cwd = "/"
        self.pasv_server: Optional[asyncio.AbstractServer] = None
        self.data_Buffer: Optional[bytearray] = None
        self.data_ready = asyncio.Event()
        self.data_done = asyncio.Event()

    async def send(self, line: str) -> None:
        self.writer.write((line + "\r\n").encode("utf-8", errors="ignore"))
        await self.writer.drain()

    async def handle(self) -> None:
        await self.send("220 Community-Parking Zero-Disk Memory FTP Ready")
        try:
            while True:
                raw = await asyncio.wait_for(self.reader.readline(), timeout=120.0)
                if not raw:
                    break
                cmdline = raw.decode("utf-8", errors="replace").strip()
                if not cmdline:
                    continue
                parts = cmdline.split(" ", 1)
                cmd = parts[0].upper()
                arg = parts[1].strip() if len(parts) > 1 else ""

                if cmd == "USER":
                    self.pending_user = arg
                    await self.send("331 User name okay, need password")
                elif cmd == "PASS":
                    cfg = config_manager.get()
                    expected_u = cfg.get("ftp_user", "camera")
                    expected_p = cfg.get("ftp_password", "parking2026")
                    if (self.pending_user in (expected_u, "anonymous")) and (
                        arg == expected_p or self.pending_user == "anonymous"
                    ):
                        self.authenticated = True
                        await self.send("230 Login successful (Zero-Disk Mode)")
                    else:
                        await self.send("530 Authentication failed")
                elif cmd == "SYST":
                    await self.send("215 UNIX Type: L8")
                elif cmd == "FEAT":
                    await self.send("211-Features:\r\n PASV\r\n EPSV\r\n UTF8\r\n211 End")
                elif cmd == "OPTS":
                    await self.send("200 OPTS UTF8 ON")
                elif cmd in ("NOOP", "TYPE", "MODE", "STRU", "ALLO"):
                    await self.send("200 OK")
                elif cmd in ("PWD", "XPWD"):
                    await self.send(f'257 "{self.cwd}" is current directory')
                elif cmd in ("CWD", "XCWD"):
                    if arg.startswith("/"):
                        self.cwd = arg
                    else:
                        self.cwd = (self.cwd.rstrip("/") + "/" + arg) or "/"
                    await self.send(f'250 Directory changed to {self.cwd}')
                elif cmd in ("CDUP", "XCUP"):
                    self.cwd = "/"
                    await self.send("250 Directory changed to /")
                elif cmd in ("MKD", "XMKD"):
                    await self.send(f'257 "{arg}" virtual directory acknowledged')
                elif cmd in ("PASV", "EPSV"):
                    await self._open_passive(epsv=(cmd == "EPSV"))
                elif cmd == "STOR":
                    if not self.authenticated:
                        await self.send("530 Please login first")
                        continue
                    await self._handle_stor(arg)
                elif cmd == "QUIT":
                    await self.send("221 Goodbye")
                    break
                else:
                    await self.send("202 Command ignored in zero-disk snapshot mode")
        except Exception as exc:
            logger.debug("FTP session ended: %s", exc)
        finally:
            await self._close_pasv()
            try:
                self.writer.close()
            except Exception:
                pass

    async def _open_passive(self, epsv: bool = False) -> None:
        await self._close_pasv()
        self.data_Buffer = bytearray()
        self.data_ready.clear()
        self.data_done.clear()

        async def _on_data_conn(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            self.data_ready.set()
            try:
                while True:
                    chunk = await r.read(65536)
                    if not chunk:
                        break
                    if self.data_Buffer is not None:
                        if len(self.data_Buffer) + len(chunk) <= MAX_SNAPSHOT_BYTES:
                            self.data_Buffer.extend(chunk)
                        else:
                            logger.warning("FTP upload exceeded MAX_SNAPSHOT_BYTES (%d), aborting stream", MAX_SNAPSHOT_BYTES)
                            self.data_Buffer = None
                            break
            finally:
                try:
                    w.close()
                except Exception:
                    pass
                self.data_done.set()

        sock_host = self.writer.get_extra_info("sockname")[0]
        bind_ip = "0.0.0.0" if ":" not in str(sock_host) else "::"
        self.pasv_server = await asyncio.start_server(_on_data_conn, host=bind_ip, port=0)
        port = self.pasv_server.sockets[0].getsockname()[1]

        if epsv:
            await self.send(f"229 Entering Extended Passive Mode (|||{port}|)")
        else:
            ip_str = str(sock_host) if "." in str(sock_host) else "127.0.0.1"
            h1, h2, h3, h4 = ip_str.split(".")
            p1, p2 = port // 256, port % 256
            await self.send(f"227 Entering Passive Mode ({h1},{h2},{h3},{h4},{p1},{p2})")

    async def _handle_stor(self, filename: str) -> None:
        if self.pasv_server is None:
            await self.send("425 Use PASV or EPSV first")
            return
        await self.send("150 Opening binary memory stream for snapshot")
        try:
            await asyncio.wait_for(self.data_ready.wait(), timeout=15.0)
            await asyncio.wait_for(self.data_done.wait(), timeout=30.0)
            raw_bytes = bytes(self.data_Buffer or b"")
            # Immediately release temporary buffer
            self.data_Buffer = None
            await self._close_pasv()

            full_vpath = f"{self.cwd.rstrip('/')}/{filename}"
            cam_id = self._infer_camera_id(full_vpath)
            if raw_bytes:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.on_snapshot, cam_id, raw_bytes, full_vpath)
            del raw_bytes
            await self.send("226 Snapshot processed in RAM and destroyed (0 bytes on disk)")
        except Exception as exc:
            logger.warning("FTP STOR failed: %s", exc)
            await self._close_pasv()
            await self.send("451 Failed to process in-memory snapshot")

    def _infer_camera_id(self, virtual_path: str) -> str:
        decoded_path = unquote(virtual_path)
        # Match e.g. /CAM_3F_WIN/snap.jpg or CAM_3F_WIN_2026.jpg
        m = re.search(r"(CAM_[A-Za-z0-9_-]+)", decoded_path)
        if m:
            return m.group(1)
        parts = [p for p in decoded_path.split("/") if p]
        if len(parts) >= 2:
            return parts[0]
        return "CAM_3F_WIN"

    async def _close_pasv(self) -> None:
        if self.pasv_server is not None:
            self.pasv_server.close()
            try:
                await self.pasv_server.wait_closed()
            except Exception:
                pass
            self.pasv_server = None


class BackgroundFTPServer:
    def __init__(self, on_snapshot: Callable[[str, bytes, str], None], port: int = 2121) -> None:
        self.on_snapshot = on_snapshot
        self.port = port
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.running = False

    def start(self) -> None:
        if self.running:
            return

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            async def _client_cb(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
                proto = InMemoryFTPProtocol(r, w, self.on_snapshot)
                await proto.handle()

            try:
                srv = loop.run_until_complete(
                    asyncio.start_server(_client_cb, host="0.0.0.0", port=self.port)
                )
                self.running = True
                logger.info("Zero-Disk FTP Snapshot Receiver listening on 0.0.0.0:%d", self.port)
                loop.run_forever()
                srv.close()
            except Exception as exc:
                logger.warning("FTP server could not bind port %d: %s", self.port, exc)
                self.running = False

        self._thread = threading.Thread(target=_runner, daemon=True, name="zero-disk-ftp")
        self._thread.start()
