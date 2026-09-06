from __future__ import annotations

from aiohttp import web


async def health(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def run_health_server(port: int) -> web.AppRunner:
    application = web.Application()
    application.router.add_get("/health", health)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner


async def stop_health_server(runner: web.AppRunner | None) -> None:
    if runner:
        await runner.cleanup()