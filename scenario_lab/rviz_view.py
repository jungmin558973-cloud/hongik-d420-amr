"""Authenticated same-origin WebSocket bridge to the isolated local RViz display."""
import asyncio
from pathlib import Path
import secrets
from aiohttp import web, WSMsgType


def install(app,lab):
    sockets=set()
    async def shutdown(_):
        for ws in list(sockets):await ws.close()
    async def start(request):
        lab.validate_request(request)
        process=await asyncio.create_subprocess_exec('bash',str(Path(__file__).with_name('rviz_start.sh')),
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
        try:output,_=await asyncio.wait_for(process.communicate(),10)
        except asyncio.TimeoutError:
            process.kill();await process.wait();raise web.HTTPServiceUnavailable(text='RViz 준비 시간이 초과됐습니다.')
        if process.returncode:raise web.HTTPServiceUnavailable(text=output.decode(errors='replace')[-600:])
        return web.json_response({'ok':True})
    async def viewer(request):
        origin=request.headers.get('Origin')
        if (origin!='http://'+request.host or
                not secrets.compare_digest(request.query.get('token',''),lab.token)):
            raise web.HTTPForbidden(text='로컬 RViz 세션을 다시 열어 주세요.')
        try:reader,writer=await asyncio.wait_for(asyncio.open_connection('127.0.0.1',5908),timeout=2)
        except (OSError,asyncio.TimeoutError):raise web.HTTPServiceUnavailable(text='RViz 화면 준비 중입니다.')
        ws=web.WebSocketResponse(protocols=('binary',),max_msg_size=2*1024*1024)
        await ws.prepare(request)
        sockets.add(ws)
        async def outward():
            try:
                while True:
                    data=await reader.read(65536)
                    if not data:break
                    await ws.send_bytes(data)
            finally:await ws.close()
        task=asyncio.create_task(outward())
        try:
            async for msg in ws:
                if msg.type==WSMsgType.BINARY:writer.write(msg.data);await writer.drain()
                elif msg.type==WSMsgType.TEXT:break
        finally:
            sockets.discard(ws)
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            writer.close();await writer.wait_closed()
        return ws
    app.router.add_get('/api/rviz/socket',viewer)
    app.router.add_post('/api/rviz/start',start)
    app.on_shutdown.append(shutdown)
