from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable


async def run_health_server(port: int, readiness: Callable[[], bool]) -> asyncio.AbstractServer:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.read(2048), timeout=5)
            first_line = request.decode("latin-1", errors="ignore").splitlines()[0] if request else ""
            path = first_line.split(" ")[1] if len(first_line.split(" ")) > 1 else "/"
            healthy = path == "/health" and readiness()
            body = json.dumps({"status": "ok" if healthy else "starting"}).encode()
            status = "200 OK" if healthy else "503 Service Unavailable"
            response = (
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            ).encode() + body
            writer.write(response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    return await asyncio.start_server(handle, "0.0.0.0", port)