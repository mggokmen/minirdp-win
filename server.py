#!/usr/bin/env python3
"""MiniRDP - aynı ağdaki bir tarayıcıdan bu Windows bilgisayarı görüntüleyip kontrol etmek için mini uzak masaüstü sunucusu."""
import asyncio
import hashlib
import hmac
import io
import ipaddress
import json
import logging
import logging.handlers
import secrets
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import win32  # noqa: I001  - DPI farkındalığı mss'ten önce ayarlanmalı
import mss
from aiohttp import WSMsgType, web
from PIL import Image

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
STATIC = BASE / "static"
CERTS = BASE / "certs"
TLS = (CERTS / "server.crt").exists() and (CERTS / "server.key").exists()
SCHEME = "https" if TLS else "http"
COOKIE = "minirdp_session"
SESSION_TTL = 7 * 24 * 3600

log = logging.getLogger("minirdp")


def setup_logging():
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handlers = [logging.handlers.RotatingFileHandler(BASE / "minirdp.log", maxBytes=1 << 20, backupCount=2, encoding="utf-8")]
    if sys.stderr:  # pythonw.exe ile çalışırken konsol yoktur
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h.setFormatter(fmt)
        logging.root.addHandler(h)
    logging.root.setLevel(logging.INFO)


# ---------------------------------------------------------------- yapılandırma / şifre

def load_config():
    cfg = {"host": "0.0.0.0", "port": 8765, "quality": 60, "max_width": 1600}
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return salt, digest


def check_password(cfg, password):
    if "password_hash" not in cfg:
        return False
    _, digest = hash_password(password, cfg["password_salt"])
    return hmac.compare_digest(digest, cfg["password_hash"])


CFG = load_config()
SESSIONS_PATH = BASE / "sessions.json"


def _pw_tag():
    # Oturumlar şifreye bağlı: şifre değişirse eski oturumların hepsi geçersiz olur.
    return hashlib.sha256(CFG.get("password_hash", "").encode()).hexdigest()[:16]


def load_sessions():
    try:
        data = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
        if data.get("pw") != _pw_tag():
            return {}
        now = time.time()
        return {k: v for k, v in data["sessions"].items() if v > now}
    except (OSError, ValueError, KeyError, AttributeError):
        return {}


def save_sessions():
    SESSIONS_PATH.write_text(json.dumps({"pw": _pw_tag(), "sessions": SESSIONS}), encoding="utf-8")


SESSIONS = load_sessions()  # token -> son kullanma zamanı
FAILS = {}  # ip -> [başarısız deneme zamanları]


def valid_session(request):
    token = request.cookies.get(COOKIE)
    exp = SESSIONS.get(token) if token else None
    return exp is not None and exp > time.time()


# ---------------------------------------------------------------- ekran yakalama

_executor = ThreadPoolExecutor(max_workers=1)  # mss nesnesi iş parçacığına bağlı; hep aynı iş parçacığı kullanılır
_tls = {}


def _grab_raw():
    w, h = win32.primary_size()
    if _tls.get("dims") != (w, h):
        if "dims" in _tls:
            log.info("Ekran boyutu değişti: %sx%s", w, h)
        _tls["dims"] = (w, h)
    sct = _tls.get("sct")
    if sct is None:
        sct = _tls["sct"] = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
    shot = sct.grab({"left": 0, "top": 0, "width": w, "height": h})
    return shot.size, shot.bgra


def _encode(size, raw, quality, max_width):
    im = Image.frombytes("RGB", size, raw, "raw", "BGRX")
    if max_width and im.width > max_width:
        h = round(im.height * max_width / im.width)
        im = im.resize((max_width, h), Image.BILINEAR)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def capture(last_hash, quality, max_width):
    """Değişiklik yoksa None döner; varsa (hash, jpeg)."""
    size, raw = _grab_raw()
    h = hashlib.blake2b(raw, digest_size=16).digest()
    if h == last_hash:
        return None
    return h, _encode(size, raw, quality, max_width)


# ---------------------------------------------------------------- giriş olayları (fare / klavye)

INPUT = win32.InputController()


def handle_input(m):
    t = m.get("t")
    if t == "mm":
        INPUT.mouse_move(m["x"], m["y"])
    elif t == "md":
        INPUT.mouse_button(int(m["b"]), True, m["x"], m["y"])
    elif t == "mu":
        INPUT.mouse_button(int(m["b"]), False, m["x"], m["y"])
    elif t == "wh":
        INPUT.wheel(m.get("dx", 0), m.get("dy", 0))
    elif t == "kd":
        INPUT.key(m["code"], True)
    elif t == "ku":
        INPUT.key(m["code"], False)
    elif t == "tx":
        INPUT.type_text(m["s"])


# ---------------------------------------------------------------- web

def _host_allowed(host):
    """DNS rebinding'e karşı: yalnızca IP adresi, localhost veya .local adlarıyla erişime izin ver."""
    name = host.rsplit(":", 1)[0].strip("[]").lower() if host else ""
    if name == "localhost" or name.endswith(".local"):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


@web.middleware
async def security(request, handler):
    # Yalnızca yerel ağdan gelen bağlantılar (192.168.x.x, 10.x.x.x, 172.16-31.x.x, localhost).
    try:
        ip = ipaddress.ip_address(request.remote or "")
    except ValueError:
        raise web.HTTPForbidden()
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not (ip.is_private or ip.is_loopback or ip.is_link_local):
        log.warning("Yerel ağ dışından erişim reddedildi: %s", ip)
        raise web.HTTPForbidden()
    if not _host_allowed(request.host):
        raise web.HTTPForbidden()
    # Başka bir siteden yapılan istekleri (CSRF / WebSocket ele geçirme) reddet.
    origin = request.headers.get("Origin")
    if (request.method != "GET" or request.path == "/ws") and origin != f"{SCHEME}://{request.host}":
        log.warning("Geçersiz Origin reddedildi: %s (%s)", origin, ip)
        raise web.HTTPForbidden()
    return await handler(request)


async def security_headers(request, response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' blob: data:; connect-src 'self' {'wss' if TLS else 'ws'}://{request.host}; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"


LOGIN_HTML = (STATIC / "login.html").read_text(encoding="utf-8")
INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")


async def index(request):
    if not valid_session(request):
        return web.Response(text=LOGIN_HTML, content_type="text/html")
    return web.Response(text=INDEX_HTML, content_type="text/html")


async def login(request):
    ip = request.remote or "?"
    now = time.time()
    recent = [t for t in FAILS.get(ip, []) if now - t < 300]
    if len(recent) >= 5:
        return web.Response(status=429, text="Çok fazla hatalı deneme. 5 dakika sonra tekrar deneyin.")
    data = await request.post()
    if not check_password(CFG, data.get("password", "")):
        recent.append(now)
        FAILS[ip] = recent
        log.warning("Hatalı şifre denemesi: %s", ip)
        await asyncio.sleep(1)
        raise web.HTTPFound("/?hata=1")
    FAILS.pop(ip, None)
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = now + SESSION_TTL
    save_sessions()
    log.info("Giriş başarılı: %s", ip)
    resp = web.HTTPFound("/")
    resp.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="Strict", secure=TLS)
    raise resp


async def logout(request):
    SESSIONS.pop(request.cookies.get(COOKIE), None)
    save_sessions()
    resp = web.HTTPFound("/")
    resp.del_cookie(COOKIE)
    raise resp


async def ca_cert(request):
    # İstemciye kurulacak yerel CA sertifikası (gizli değil; özel anahtar bu bilgisayarda kalır).
    return web.FileResponse(CERTS / "minirdp-ca.crt", headers={
        "Content-Type": "application/x-x509-ca-cert",
        "Content-Disposition": 'attachment; filename="minirdp-ca.crt"',
    })


CLIP_MAX = 256 * 1024  # karakter


async def clipboard_get(request):
    if not valid_session(request):
        raise web.HTTPUnauthorized()
    return web.Response(text=win32.clip_read())


async def clipboard_set(request):
    if not valid_session(request):
        raise web.HTTPUnauthorized()
    win32.clip_write((await request.text())[:CLIP_MAX])
    return web.Response(text="ok")


async def ws_handler(request):
    if not valid_session(request):
        raise web.HTTPUnauthorized()
    ws = web.WebSocketResponse(max_msg_size=1 << 20, heartbeat=20)
    await ws.prepare(request)
    log.info("Bağlantı açıldı: %s", request.remote)
    if CFG.get("resolution"):
        try:
            win32.apply_resolution(CFG["resolution"])
        except Exception:
            log.exception("Çözünürlük ayarlanamadı")

    state = {"credits": 2, "quality": CFG["quality"], "max_width": CFG["max_width"], "force": True}
    wake = asyncio.Event()
    wake.set()
    loop = asyncio.get_running_loop()

    async def sender():
        last, failing = None, False
        while not ws.closed:
            await wake.wait()
            if state["credits"] <= 0:
                wake.clear()
                continue
            if state["force"]:
                last, state["force"] = None, False
            try:
                res = await loop.run_in_executor(_executor, capture, last, state["quality"], state["max_width"])
                failing = False
            except Exception:
                # Kilit ekranı / UAC gibi güvenli masaüstündeyken ekran yakalanamaz; geri dönünce devam edilir.
                if not failing:
                    log.exception("Ekran yakalanamadı")
                failing = True
                await asyncio.sleep(1)
                continue
            if res is None:
                await asyncio.sleep(0.04)
                continue
            last, jpeg = res
            state["credits"] -= 1
            try:
                await ws.send_bytes(jpeg)
            except (ConnectionResetError, RuntimeError):
                break

    async def clip_watcher():
        # Bu bilgisayarın panosu değişince metni istemciye gönder (uzak → yerel).
        count = win32.clip_count()
        while not ws.closed:
            await asyncio.sleep(0.4)
            c = win32.clip_count()
            if c == count:
                continue
            count = c
            text = win32.clip_read()
            if text and text != state["clip"] and len(text) <= CLIP_MAX:
                state["clip"] = text
                await ws.send_str(json.dumps({"t": "clip", "s": text}))

    state["clip"] = win32.clip_read()
    task = asyncio.create_task(sender())
    clip_task = asyncio.create_task(clip_watcher())
    blocked = False
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            t = m.get("t")
            if t == "ack":
                state["credits"] = min(state["credits"] + 1, 2)
                wake.set()
            elif t == "cfg":
                state["quality"] = int(min(max(int(m.get("quality", 60)), 10), 95))
                state["max_width"] = int(m.get("max_width", 0)) or 0
                state["force"] = True
                wake.set()
            elif t == "clip":
                # Yerel → uzak. Aynı metni geri göndermemek için state["clip"]'e yazılır.
                text = str(m.get("s", ""))[:CLIP_MAX]
                if text and text != state["clip"]:
                    state["clip"] = text
                    win32.clip_write(text)
            else:
                try:
                    handle_input(m)
                    blocked = False
                except OSError:
                    if not blocked:
                        log.warning("Giriş olayı engellendi (yönetici penceresi veya güvenli masaüstü olabilir)")
                    blocked = True
                except Exception:
                    log.exception("Giriş olayı işlenemedi: %s", m)
    finally:
        task.cancel()
        clip_task.cancel()
        INPUT.release_all()
        log.info("Bağlantı kapandı: %s", request.remote)
    return ws


def main():
    setup_logging()
    if "password_hash" not in CFG:
        raise SystemExit("Şifre ayarlanmamış. Önce: venv\\Scripts\\python set_password.py")
    if not win32.is_admin():
        log.warning("Yönetici olarak çalışmıyor - yönetici yetkisiyle açılmış pencerelerde fare/klavye çalışmaz.")
    win32.keep_awake()
    app = web.Application(middlewares=[security], client_max_size=1 << 20)
    app.on_response_prepare.append(security_headers)
    app.add_routes([
        web.get("/", index),
        web.post("/login", login),
        web.get("/logout", logout),
        web.get("/ws", ws_handler),
        web.get("/clipboard", clipboard_get),
        web.post("/clipboard", clipboard_set),
        web.get("/minirdp-ca.crt", ca_cert),
    ])
    ctx = None
    if TLS:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(CERTS / "server.crt", CERTS / "server.key")
    log.info("MiniRDP dinliyor: %s://%s:%s", SCHEME, CFG["host"], CFG["port"])
    web.run_app(app, host=CFG["host"], port=CFG["port"], ssl_context=ctx, print=None, access_log=None)


if __name__ == "__main__":
    main()
