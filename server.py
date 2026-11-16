"""Telefon Stream Deck - Windows
Kurulum:  pip install flask winsdk pyautogui pywebview pystray pillow
Calistir: server.pyw'ye cift tikla (veya: pythonw server.py)  -> terminal acilmaz
          Terminalde gormek istersen: python server.py
Butonlari duzenlemek icin buttons.json dosyasini degistir (yeniden baslatmaya gerek yok).
"""
import asyncio, gzip, json, logging, os, re, shutil, socket, subprocess, sys, threading, time, uuid, webbrowser
from datetime import datetime, timezone
# konsolsuz (pythonw / PyInstaller --noconsole) calisirken stdout/stderr None olur; Flask cokmesin
if sys.stdout is None: sys.stdout = open(os.devnull, "w")
if sys.stderr is None: sys.stderr = open(os.devnull, "w")
from flask import Flask, Response, jsonify, request
import pyautogui
from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MM
from winsdk.windows.storage.streams import DataReader

pyautogui.FAILSAFE = False
BASE = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.environ.get("APPDATA") or BASE, "phonedeck")   # C:\Users\<kullanıcı>\AppData\Roaming\phonedeck
os.makedirs(DATA, exist_ok=True)
for _n in ("buttons.json", "settings.json", "background.img", "presets.json", "icons"):   # eski konumdaki verileri bir kez taşı
    _o, _d = os.path.join(BASE, _n), os.path.join(DATA, _n)
    try:
        if os.path.exists(_o) and not os.path.exists(_d):
            shutil.copytree(_o, _d) if os.path.isdir(_o) else shutil.copy2(_o, _d)
    except Exception:
        pass
CFG = os.path.join(DATA, "buttons.json")
SET = os.path.join(DATA, "settings.json")
IMG = os.path.join(DATA, "background.img")
DEF_SET = {"mode": "cover", "preset": 0, "color": "#0b0d12", "blur": 55, "dim": 62, "ver": 0,
           "theme": "glass", "perf": 1, "kscale": 100, "uscale": 100, "lang": "auto", "tc": {}}
THEMES = {"glass", "frost", "amoled", "neon", "light", "flat", "terminal", "pastel", "mono", "mocha"}
LANGS = {"auto", "tr", "en"}
app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.ERROR)
thumb = {"key": "", "data": b""}

DEFAULT = [
    {"label": "Ses +", "icon": "🔊", "type": "key", "keys": ["volumeup"]},
    {"label": "Ses -", "icon": "🔉", "type": "key", "keys": ["volumedown"]},
    {"label": "Sessiz", "icon": "🔇", "type": "key", "keys": ["volumemute"]},
    {"label": "Masaüstü", "icon": "🖥️", "type": "key", "keys": ["win", "d"]},
    {"label": "Spotify", "icon": "🎧", "type": "open", "target": "spotify:"},
    {"label": "YouTube", "icon": "▶️", "type": "open", "target": "https://youtube.com"},
    {"label": "Discord", "icon": "💬", "type": "open", "target": "discord:"},
    {"label": "Ekran Alıntısı", "icon": "✂️", "type": "key", "keys": ["win", "shift", "s"]},
    {"label": "Hesap Mak.", "icon": "🧮", "type": "open", "target": "calc"},
    {"label": "Kopyala", "icon": "📋", "type": "key", "keys": ["ctrl", "c"]},
    {"label": "Yapıştır", "icon": "📌", "type": "key", "keys": ["ctrl", "v"]},
    {"label": "Görev Yön.", "icon": "📊", "type": "key", "keys": ["ctrl", "shift", "esc"]},
]
if not os.path.exists(CFG):
    json.dump(DEFAULT, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()   # her istekte yeni döngü kurmak yerine tek döngü
_mgr = {"m": None}


def arun(coro, timeout=4):
    f = asyncio.run_coroutine_threadsafe(coro, _loop)
    try:
        return f.result(timeout)
    except Exception:
        f.cancel()
        raise


SEL = {"id": None}   # elle seçilen medya kaynağı (None = Windows'un seçtiği)


async def session():
    if _mgr["m"] is None:
        _mgr["m"] = await MM.request_async()   # Windows medya yöneticisi bir kez alınır
    if SEL["id"]:
        for x in _mgr["m"].get_sessions():
            if x.source_app_user_model_id == SEL["id"]:
                return x
        SEL["id"] = None   # seçilen kaynak kapandı: otomatiğe dön
    return _mgr["m"].get_current_session()


async def source_ids():
    if _mgr["m"] is None:
        _mgr["m"] = await MM.request_async()
    out = []
    for x in _mgr["m"].get_sessions():
        try:
            if int(x.get_playback_info().playback_status) != 0:   # 0 = kapalı
                out.append(x.source_app_user_model_id)
        except Exception:
            pass
    return sorted(set(out))


async def cycle_source():
    ids = await source_ids()
    if len(ids) < 2:
        return
    cur = await session()
    cid = cur.source_app_user_model_id if cur else None
    SEL["id"] = ids[(ids.index(cid) + 1) % len(ids)] if cid in ids else ids[0]


def pretty_src(sid):
    x = (sid or "").split("!")[0]
    if x.lower().endswith(".exe"):
        x = x[:-4]
    x = x.split("_")[0].split(".")[-1] or x
    return {"msedge": "Edge", "chrome": "Chrome", "firefox": "Firefox"}.get(x.lower(), x[:1].upper() + x[1:] if x.islower() else x)


async def media_info():
    s = await session()
    if not s:
        return {"active": False}
    ids = await source_ids()
    sid = s.source_app_user_model_id
    p = await s.try_get_media_properties_async()
    pb, tl = s.get_playback_info(), s.get_timeline_properties()
    key = f"{p.artist}|{p.title}"
    if key != thumb["key"]:
        thumb["key"], thumb["data"] = key, b""
        if p.thumbnail:
            try:
                st = await p.thumbnail.open_read_async()
                r = DataReader(st)
                await r.load_async(st.size)
                buf = bytearray(st.size)
                r.read_bytes(buf)
                thumb["data"] = bytes(buf)
            except Exception:
                pass
    t0 = tl.start_time.total_seconds()
    end = tl.end_time.total_seconds() - t0
    pos = tl.position.total_seconds() - t0
    playing = int(pb.playback_status) == 4
    if playing:   # Spotify/Chrome konumu sadece değişiklikte günceller: son güncellemeden bu yana geçen süreyi ekle
        try:
            lu = tl.last_updated_time
            if lu.tzinfo is None:
                lu = lu.replace(tzinfo=timezone.utc)
            el = (datetime.now(timezone.utc) - lu).total_seconds()
            if 0 <= el < 6 * 3600:
                pos += el
        except Exception:
            pass
    pos = max(0.0, min(pos, end)) if end > 0 else max(0.0, pos)
    try:
        seekable = bool(pb.controls.is_playback_position_enabled)
    except Exception:
        seekable = True
    return {"active": True, "title": p.title, "artist": p.artist, "playing": playing,
            "pos": pos, "end": end, "seekable": seekable,
            "cover": bool(thumb["data"]), "key": key,
            "src": pretty_src(sid), "n": len(ids), "idx": (ids.index(sid) + 1) if sid in ids else 1}


async def media_cmd(cmd):
    s = await session()
    if not s:
        return
    if cmd == "toggle": await s.try_toggle_play_pause_async()
    elif cmd == "next": await s.try_skip_next_async()
    elif cmd == "prev": await s.try_skip_previous_async()
    elif cmd == "play": await s.try_play_async()
    elif cmd == "pause": await s.try_pause_async()


async def media_seek(sec):
    s = await session()
    if s:
        await s.try_change_playback_position_async(int(sec * 10_000_000))   # 100 ns birimi


_mc = {"t": 0.0, "d": None}
_mlock = threading.Lock()


@app.get("/api/media")
def api_media():
    if _mc["d"] is None or time.time() - _mc["t"] >= 0.8:
        with _mlock:   # birden fazla cihaz aynı anda sorarsa tek sorgu yapılır
            if _mc["d"] is None or time.time() - _mc["t"] >= 0.8:
                try:
                    d = arun(media_info())
                except Exception:
                    d = {"active": False}
                    _mgr["m"] = None   # yönetici bayatlamış olabilir: sonraki istekte yeniden al
                _mc["t"], _mc["d"] = time.time(), d
    d = dict(_mc["d"])
    if d.get("playing") and d.get("end", 0) > 0:   # önbellekte beklediği süre kadar ileri al
        d["pos"] = min(d["end"], d["pos"] + (time.time() - _mc["t"]))
    return jsonify(d)


@app.post("/api/mediasrc")
def api_mediasrc():
    try:
        arun(cycle_source())
    except Exception:
        return "hata", 500
    _mc["t"] = 0.0
    return "ok"


@app.post("/api/media/<cmd>")
def api_media_cmd(cmd):
    do_media(cmd)
    return "ok"


def do_media(cmd):
    _mc["t"] = 0.0   # komuttan sonra taze bilgi al
    keys = {"vup": "volumeup", "vdown": "volumedown", "mute": "volumemute"}
    if cmd in keys:
        pyautogui.press(keys[cmd])
    else:
        arun(media_cmd(cmd))


VCOND = threading.Condition()
VER = {"s": 0, "b": 0}


def bump(k):
    with VCOND:
        VER[k] += 1
        VCOND.notify_all()


@app.after_request
def _bump(r):
    if request.method in ("POST", "PUT") and r.status_code < 300:
        if request.path in ("/api/settings", "/api/bgimage"):
            bump("s")
        elif request.path == "/api/buttons":
            bump("b")
    return r


@app.get("/api/wait")
def api_wait():
    try:
        seen = int(request.args.get("v", -1))
    except ValueError:
        seen = -1
    with VCOND:
        if seen == VER["s"] + VER["b"]:
            VCOND.wait(timeout=20)
        return jsonify({"v": VER["s"] + VER["b"], **VER})


@app.post("/api/seek")
def api_seek():
    try:
        sec = float((request.get_json() or {}).get("pos", 0))
        arun(media_seek(max(0.0, sec)))
    except Exception:
        return "hata", 400
    _mc["t"] = 0.0
    return "ok"


SHOW = {"fn": None}


@app.post("/api/quit")
def api_quit():
    if not local():
        return "yasak", 403
    import threading
    threading.Timer(0.2, lambda: os._exit(0)).start()
    return "ok"


@app.post("/api/show")
def api_show():
    if not local():
        return "yasak", 403
    if SHOW["fn"]:
        SHOW["fn"]()
    return "ok"


def local():
    # USB (adb reverse) ile gelen telefon da 127.0.0.1 görünür; yönetim uçlarını sadece PC penceresine aç
    return request.remote_addr in ("127.0.0.1", "::1") and "Android" not in request.headers.get("User-Agent", "")


@app.put("/api/buttons")
def api_save():
    if not local():
        return "yasak", 403
    json.dump(request.get_json(), open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return "ok"


@app.get("/admin")
def admin():
    if not local():
        return "Bu sayfa sadece PC'den acilir", 403
    return Response(ADMIN.replace('<html lang="tr">', f'<html lang="tr" data-lang="{get_set()["lang"]}">', 1), mimetype="text/html")


@app.get("/api/cover")
def api_cover():
    return Response(thumb["data"], mimetype="image/jpeg")


@app.get("/api/buttons")
def api_buttons():
    return jsonify(json.load(open(CFG, encoding="utf-8")))


@app.post("/api/press/<int:i>")
def api_press(i):
    b = json.load(open(CFG, encoding="utf-8"))[i]
    t = b["type"]
    if t == "key": pyautogui.hotkey(*b["keys"])
    elif t == "open": subprocess.Popen(f'start "" "{b["target"]}"', shell=True, creationflags=0x08000000)  # CMD penceresi cakmasin
    elif t == "media": do_media(b["cmd"])
    elif t == "text": pyautogui.write(b["text"], interval=0.01)
    return "ok"


_sc = {"m": None, "d": None}


def get_set():
    try:
        m = os.path.getmtime(SET)
        if _sc["m"] != m:
            _sc["d"] = {**DEF_SET, **json.load(open(SET, encoding="utf-8"))}
            _sc["m"] = m
        return dict(_sc["d"])
    except Exception:
        return dict(DEF_SET)


def put_set(d):
    json.dump(d, open(SET, "w", encoding="utf-8"))
    _sc["m"] = None


@app.get("/api/settings")
def api_get_set():
    return jsonify({**get_set(), "has_image": os.path.exists(IMG), "anim": img_kind()[1] and os.path.exists(SHEET)})


@app.post("/api/settings")
def api_set():
    d = get_set()
    for k, v in (request.get_json() or {}).items():
        if k not in DEF_SET or k == "ver":
            continue
        try:
            if k == "lang":
                if v not in LANGS: continue
            elif k == "theme":
                if v not in THEMES: continue
            elif k == "tc":
                v = {t: c for t, c in (v or {}).items() if t in THEMES and re.fullmatch(r"#[0-9a-fA-F]{6}", str(c))}
            elif k == "perf": v = max(0, min(2, int(v)))
            elif k == "kscale": v = max(50, min(160, int(v)))
            elif k == "uscale": v = max(50, min(140, int(v)))
            elif k in ("blur", "dim", "preset"): v = int(v)
        except (TypeError, ValueError):
            continue
        d[k] = v
    put_set(d)
    return "ok"


def img_kind():
    try:
        with open(IMG, "rb") as f:
            h = f.read(64)
    except OSError:
        return "", False
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", True
    if h[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", False
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "webp", b"ANIM" in h
    return "jpeg", False


SHEET = os.path.join(DATA, "bgsheet.jpg")
SHEETJ = os.path.join(DATA, "bgsheet.json")


def build_sheet():
    """Hareketli GIF'i küçük karelerden oluşan tek bir şeride çevirir (telefon kareleri kendi oynatır: her cihazda aynı çalışır, hafif)."""
    for f in (SHEET, SHEETJ):
        try:
            os.remove(f)
        except OSError:
            pass
    if not img_kind()[1]:
        return
    from PIL import Image, ImageSequence
    im = Image.open(IMG)
    fr, du = [], []
    for x in ImageSequence.Iterator(im):
        f = x.convert("RGB"); f.thumbnail((640, 360))
        fr.append(f); du.append(int(x.info.get("duration", 100) or 100))
    if len(fr) < 2:
        return
    if len(fr) > 90:   # çok kare varsa seyrelt, süreleri topla
        k = -(-len(fr) // 90)
        fr, du = fr[::k], [sum(du[i:i + k]) for i in range(0, len(du), k)]
    fw, fh = fr[0].size
    cols = max(1, min(len(fr), 2400 // fw)); rows = -(-len(fr) // cols)
    sh = Image.new("RGB", (cols * fw, rows * fh))
    for i, f in enumerate(fr):
        sh.paste(f.resize((fw, fh)), ((i % cols) * fw, (i // cols) * fh))
    sh.save(SHEET, "JPEG", quality=82)
    json.dump({"fw": fw, "fh": fh, "cols": cols, "n": len(fr), "durs": du}, open(SHEETJ, "w"))


@app.get("/api/bgsheet")
def api_bgsheet():
    if not os.path.exists(SHEET):
        return "", 404
    return Response(open(SHEET, "rb").read(), mimetype="image/jpeg", headers={"Cache-Control": "max-age=31536000"})


@app.get("/api/bgsheet.json")
def api_bgsheet_json():
    if not os.path.exists(SHEETJ):
        return "", 404
    return Response(open(SHEETJ, "rb").read(), mimetype="application/json", headers={"Cache-Control": "no-cache"})


@app.post("/api/bgimage")
def api_bgimg():
    if not local():
        return "yasak", 403
    data = request.get_data()
    if len(data) > 16 * 1024 * 1024:
        return "büyük", 413
    open(IMG, "wb").write(data)
    try:
        build_sheet()
    except Exception:
        for f in (SHEET, SHEETJ):
            try:
                os.remove(f)
            except OSError:
                pass
    d = get_set(); d["ver"] += 1; d["mode"] = "image"; put_set(d)
    return "ok"


@app.get("/api/bgimage")
def api_bgimg_get():
    if not os.path.exists(IMG):
        return "", 404
    return Response(open(IMG, "rb").read(), mimetype="image/" + (img_kind()[0] or "jpeg"), headers={"Cache-Control": "max-age=31536000"})


ICONS = os.path.join(DATA, "icons")


@app.post("/api/icon")
def api_icon_up():
    if not local():
        return "yasak", 403
    os.makedirs(ICONS, exist_ok=True)
    name = uuid.uuid4().hex[:12] + ".png"
    open(os.path.join(ICONS, name), "wb").write(request.get_data())
    return name


@app.get("/api/icon/<name>")
def api_icon(name):
    p = os.path.join(ICONS, name)
    if not re.fullmatch(r"[0-9a-f]{12}\.png", name) or not os.path.exists(p):
        return "", 404
    return Response(open(p, "rb").read(), mimetype="image/png", headers={"Cache-Control": "max-age=86400"})


def _lan_ip_raw():
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        u.connect(("8.8.8.8", 80))
        return u.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        u.close()


_ipc = {"t": 0.0, "v": ""}


def lan_ip():
    if _ipc["v"] and time.time() - _ipc["t"] < 30:
        return _ipc["v"]
    _ipc["v"], _ipc["t"] = _lan_ip_raw(), time.time()
    return _ipc["v"]


def lan_ips():
    main = lan_ip()
    try:
        extra = [r[4][0] for r in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except Exception:
        extra = []
    return [main] + [x for x in dict.fromkeys(extra) if x != main and not x.startswith(("127.", "169.254."))]


@app.get("/api/info")
def api_info():
    return jsonify({"name": socket.gethostname(), "ip": lan_ip(), "ips": lan_ips()})


LOGOS = os.path.join(DATA, "logos")
LOGO_RE = re.compile(r"exe:[a-z0-9_.-]{1,40}|[a-z0-9.-]{3,60}")
_lfail = {}


def resolve_exe(name):
    p = shutil.which(name) or shutil.which(name + ".exe")
    if p:
        return p
    if os.name == "nt":
        try:
            import winreg
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s.exe" % name) as k:
                        v = (winreg.QueryValue(k, None) or "").strip('"')
                        if v and os.path.exists(v):
                            return v
                except OSError:
                    pass
        except Exception:
            pass
    return None


def exe_logo(name):
    p = resolve_exe(name)
    if p and p.lower().endswith(("code.cmd", "code.bat")):   # VS Code: bin\code.cmd -> Code.exe
        p = os.path.join(os.path.dirname(os.path.dirname(p)), "Code.exe")
    if not p or not p.lower().endswith(".exe") or not os.path.exists(p):
        return None
    return exe_icon_png(p)


def get_logo(lid):
    """Uygulamanın kendi simgesi (exe:ad) ya da sitenin logosu (alan adı). Bir kez alınır, diske kaydedilir."""
    lid = (lid or "").lower()
    if not LOGO_RE.fullmatch(lid):
        return None
    fp = os.path.join(LOGOS, re.sub(r"[^a-z0-9._-]", "_", lid) + ".png")
    if os.path.exists(fp):
        return open(fp, "rb").read()
    if time.time() - _lfail.get(lid, 0) < 300:
        return None
    data = None
    try:
        if lid.startswith("exe:"):
            data = exe_logo(lid[4:])
        else:
            import urllib.request
            data = urllib.request.urlopen(f"https://www.google.com/s2/favicons?domain={lid}&sz=128", timeout=5).read()
    except Exception:
        data = None
    if data:
        os.makedirs(LOGOS, exist_ok=True)
        open(fp, "wb").write(data)
    else:
        _lfail[lid] = time.time()
    return data


@app.get("/api/logo/<path:lid>")
def api_logo(lid):
    if not local():
        return "", 403
    d = get_logo(lid)
    if not d:
        return "", 404
    return Response(d, mimetype="image/png", headers={"Cache-Control": "max-age=86400"})


@app.post("/api/logoprefetch")
def api_logoprefetch():
    if not local():
        return "yasak", 403
    ids = [x for x in (request.get_json(silent=True) or []) if isinstance(x, str)][:200]

    def work():
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(get_logo, ids))
    threading.Thread(target=work, daemon=True).start()
    return "ok"


@app.post("/api/favicon")
def api_favicon():
    """Hazır ayarı eklerken logoyu buton ikonu olarak kaydeder (logo önbelleğinden)."""
    if not local():
        return "yasak", 403
    d = get_logo(request.args.get("d", ""))
    if not d:
        return "", 502
    os.makedirs(ICONS, exist_ok=True)
    name = uuid.uuid4().hex[:12] + ".png"
    open(os.path.join(ICONS, name), "wb").write(d)
    return name


_pg = {}


@app.get("/")
def index():
    s = get_set()
    th0 = s["theme"] if s["theme"] in THEMES else "glass"
    tcv = (s.get("tc") or {}).get(th0, "")
    key = (s["theme"], int(s["perf"]), s["lang"], int(s["kscale"]), int(s["uscale"]), tcv)
    ent = _pg.get(key)
    if ent is None:   # sayfa ayar başına bir kez üretilir ve sıkıştırılır
        th = s["theme"] if s["theme"] in THEMES else "glass"
        h = HTML.replace('<html lang="tr">',
                         f'<html lang="tr" data-theme="{th}" data-perf="{int(s["perf"])}" data-lang="{s["lang"]}" '
                         f'style="--ks:{int(s["kscale"]) / 100};--us:{int(s["uscale"]) / 100}' + (f';--tc:{tcv}' if tcv else '') + '">', 1)
        raw = h.encode("utf-8")
        if len(_pg) > 12:
            _pg.clear()
        ent = _pg[key] = (raw, gzip.compress(raw, 6))
    gz = "gzip" in request.headers.get("Accept-Encoding", "")
    r = Response(ent[1] if gz else ent[0], mimetype="text/html", headers={"Cache-Control": "no-cache", "Vary": "Accept-Encoding"})
    if gz:
        r.headers["Content-Encoding"] = "gzip"
    return r


HTML = r"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<title>Deck</title><style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none}
/* ===== TEMA DEĞİŞKENLERİ (varsayılan = Liquid Glass) ===== */
html{background:#0b0d12;--ks:1;--us:1;--shade:0;
 --tx:#fff;--mut:rgba(255,255,255,.72);--track:rgba(255,255,255,.2);--fillc:#fff;
 --btn:rgba(255,255,255,.12);--btnon:rgba(255,255,255,.4);--btnbd:rgba(255,255,255,.3);
 --surf:linear-gradient(135deg,rgba(255,255,255,.22),rgba(255,255,255,.05));
 --ksurf:linear-gradient(135deg,color-mix(in srgb,var(--c,var(--tc,#fff)) 34%,transparent),rgba(255,255,255,.05));
 --big:linear-gradient(135deg,rgba(255,255,255,.5),rgba(255,255,255,.18));
 --bd:rgba(255,255,255,.24);
 --insh:inset 0 1.5px 0 rgba(255,255,255,.5),inset 0 -1px 0 rgba(255,255,255,.1);
 --osh:0 12px 34px rgba(0,0,0,.35);--kosh:0 10px 26px rgba(0,0,0,.32);
 --bf:blur(26px) saturate(1.9);--kr:26px;--cr:44px;--gloss:.26;
 --hmfill:linear-gradient(180deg,rgba(255,255,255,.95),rgba(255,255,255,.3) 55%,rgba(255,255,255,.6));
 --hmstroke:1.5px rgba(255,255,255,.7);--hmfx:drop-shadow(0 8px 22px rgba(0,0,0,.45));--ifx:drop-shadow(0 4px 8px #0006);
 --grad:radial-gradient(60vmax 60vmax at 15% 20%,#3b4cca99,transparent),radial-gradient(60vmax 60vmax at 85% 80%,#b0369a88,transparent),#0b0d12}
html[data-theme=frost]{--surf:rgba(255,255,255,.13);--ksurf:color-mix(in srgb,var(--c,var(--tc,#fff)) 24%,rgba(255,255,255,.08));--big:rgba(255,255,255,.3);
 --bd:rgba(255,255,255,.16);--insh:inset 0 1px 0 rgba(255,255,255,.22);--osh:0 8px 22px rgba(0,0,0,.25);--kosh:0 6px 16px rgba(0,0,0,.22);
 --bf:blur(14px);--kr:22px;--cr:34px;--gloss:0;--hmfill:#fff;--hmstroke:0 transparent;--hmfx:none}
html[data-theme=amoled]{--tx:#f2f3f7;--mut:#9aa0b0;--track:#25262e;--fillc:#f2f3f7;--btn:#17181f;--btnon:#2b2e3b;--btnbd:#2a2c36;
 --surf:#0a0a0c;--ksurf:color-mix(in srgb,var(--c,var(--tc,#7c9cff)) 16%,#0a0a0c);--big:#1d1e26;--bd:#23232a;
 --insh:inset 0 0 0 0 transparent;--osh:0 0 0 0 transparent;--kosh:0 0 0 0 transparent;
 --bf:none;--kr:22px;--cr:30px;--gloss:0;--hmfill:#f2f3f7;--hmstroke:0 transparent;--hmfx:none;--ifx:none}
html[data-theme=amoled]{background:#000}
html[data-theme=amoled] body::before{background:#000}
html[data-theme=amoled] .bg,html[data-theme=amoled] .shade{display:none}
html[data-theme=neon]{--nc:var(--tc,#22d3ee);--mut:color-mix(in srgb,var(--nc) 55%,#fff);--track:rgba(255,255,255,.14);--fillc:var(--nc);--btn:color-mix(in srgb,var(--nc) 10%,transparent);--btnon:color-mix(in srgb,var(--nc) 35%,transparent);--btnbd:color-mix(in srgb,var(--nc) 50%,transparent);
 --surf:rgba(8,10,28,.62);--ksurf:rgba(8,10,28,.62);--big:color-mix(in srgb,var(--nc) 28%,transparent);--bd:color-mix(in srgb,var(--nc) 40%,transparent);
 --insh:inset 0 0 0 0 transparent;--osh:0 0 22px color-mix(in srgb,var(--nc) 25%,transparent);
 --bf:blur(10px);--kr:20px;--cr:28px;--gloss:0;--hmfill:color-mix(in srgb,var(--nc) 12%,#fff);--hmstroke:0 transparent;--hmfx:drop-shadow(0 0 16px var(--nc))}
html[data-theme=neon] .shade{background:rgba(2,4,20,.6)}
html[data-theme=light]{--tx:#14161c;--mut:rgba(20,22,28,.62);--track:rgba(0,0,0,.12);--fillc:#14161c;--btn:rgba(0,0,0,.06);--btnon:rgba(0,0,0,.18);--btnbd:rgba(0,0,0,.14);
 --surf:rgba(255,255,255,.62);--ksurf:color-mix(in srgb,var(--c,var(--tc,#fff)) 22%,rgba(255,255,255,.72));--big:rgba(255,255,255,.95);--bd:rgba(255,255,255,.9);
 --insh:inset 0 1px 0 #fff;--osh:0 8px 24px rgba(30,40,80,.14);--kosh:0 6px 16px rgba(30,40,80,.12);
 --bf:blur(18px) saturate(1.4);--kr:26px;--cr:40px;--gloss:.5;--hmfill:linear-gradient(180deg,#1b1e27,#4a5168);--hmstroke:0 transparent;--hmfx:none;--ifx:drop-shadow(0 2px 4px #0003)}
html[data-theme=light]{background:#eef2ff}
html[data-theme=light] body::before{background:linear-gradient(160deg,#eef2ff,#e6f4f1 50%,#fdeef4)}
html[data-theme=light] .bg,html[data-theme=light] .shade{display:none}
html[data-theme=flat]{--surf:rgba(255,255,255,.12);--ksurf:color-mix(in srgb,var(--c,var(--tc,#2b3140)) 72%,#000);--big:rgba(255,255,255,.24);--bd:transparent;
 --insh:inset 0 0 0 0 transparent;--osh:0 0 0 0 transparent;--kosh:0 0 0 0 transparent;
 --bf:none;--kr:18px;--cr:28px;--gloss:0;--hmfill:#fff;--hmstroke:0 transparent;--hmfx:none;--ifx:none}
/* ===== YENİ TEMALAR ===== */
html[data-theme=terminal]{--nc:var(--tc,#39ff7a);--tx:var(--nc);--mut:color-mix(in srgb,var(--nc) 65%,#000);--track:color-mix(in srgb,var(--nc) 20%,transparent);--fillc:var(--nc);--btn:color-mix(in srgb,var(--nc) 8%,#000);--btnon:color-mix(in srgb,var(--nc) 28%,#000);--btnbd:color-mix(in srgb,var(--nc) 55%,transparent);
 --surf:#040a06;--ksurf:#050d08;--big:color-mix(in srgb,var(--nc) 22%,#000);--bd:color-mix(in srgb,var(--nc) 45%,transparent);--insh:inset 0 0 0 0 transparent;--osh:0 0 0 0 transparent;--kosh:0 0 12px color-mix(in srgb,var(--nc) 25%,transparent);
 --bf:none;--kr:6px;--cr:8px;--gloss:0;--hmfill:var(--nc);--hmstroke:0 transparent;--hmfx:drop-shadow(0 0 10px color-mix(in srgb,var(--nc) 60%,transparent));--ifx:none}
html[data-theme=terminal]{background:#020604}html[data-theme=terminal] body::before{background:#020604}html[data-theme=terminal] .bg{display:none}
html[data-theme=terminal] .shade{background:repeating-linear-gradient(0deg,rgba(0,0,0,.22) 0 1px,transparent 1px 3px)}
html[data-theme=terminal] body,html[data-theme=terminal] button{font-family:ui-monospace,Consolas,"Courier New",monospace}
html[data-theme=terminal] .k{--kbd:color-mix(in srgb,var(--c,var(--nc)) 70%,transparent);--ksurf:color-mix(in srgb,var(--c,var(--nc)) 10%,#030805)}
html[data-theme=pastel]{--tx:#3b3553;--mut:rgba(59,53,83,.62);--track:rgba(59,53,83,.14);--fillc:#8b7cf6;--btn:rgba(255,255,255,.55);--btnon:rgba(255,255,255,.95);--btnbd:rgba(139,124,246,.3);
 --surf:rgba(255,255,255,.6);--ksurf:color-mix(in srgb,var(--c,var(--tc,#c9b8ff)) 55%,#fff);--big:rgba(255,255,255,.95);--bd:rgba(255,255,255,.9);--insh:inset 0 2px 0 rgba(255,255,255,.9);--osh:0 10px 30px rgba(139,124,246,.18);--kosh:0 8px 22px rgba(139,124,246,.22);
 --bf:blur(14px);--kr:30px;--cr:44px;--gloss:.35;--hmfill:linear-gradient(180deg,#6d5be8,#c26bd6);--hmstroke:0 transparent;--hmfx:none;--ifx:drop-shadow(0 2px 4px rgba(80,60,160,.25))}
html[data-theme=pastel]{background:#f6f0ff}
html[data-theme=pastel] body::before{background:radial-gradient(70vmax 60vmax at 10% 10%,#ffd6e8,transparent),radial-gradient(70vmax 60vmax at 90% 20%,#d6e8ff,transparent),radial-gradient(70vmax 60vmax at 50% 100%,#e4d6ff,transparent),#f6f0ff}
html[data-theme=pastel] .bg,html[data-theme=pastel] .shade{display:none}
html[data-theme=mono]{--tx:#fff;--mut:rgba(255,255,255,.6);--track:rgba(255,255,255,.16);--fillc:#fff;--btn:transparent;--btnon:rgba(255,255,255,.18);--btnbd:rgba(255,255,255,.6);
 --surf:transparent;--ksurf:transparent;--big:rgba(255,255,255,.14);--bd:rgba(255,255,255,.55);--insh:inset 0 0 0 0 transparent;--osh:0 0 0 0 transparent;--kosh:0 0 0 0 transparent;
 --bf:none;--kr:14px;--cr:22px;--gloss:0;--hmfill:#fff;--hmstroke:0 transparent;--hmfx:none;--ifx:none}
html[data-theme=mono]{background:#000}html[data-theme=mono] body::before{background:#000}html[data-theme=mono] .bg,html[data-theme=mono] .shade{display:none}
html[data-theme=mono] #hm{font-weight:200}html[data-theme=mono] .k{--kbd:color-mix(in srgb,var(--c,var(--tc,#fff)) 85%,transparent);--ksurf:color-mix(in srgb,var(--c,transparent) 14%,transparent)}
html[data-theme=mocha]{--tx:#f4e8da;--mut:rgba(244,232,218,.66);--track:rgba(244,232,218,.16);--fillc:#e0b48a;--btn:rgba(244,232,218,.08);--btnon:rgba(244,232,218,.26);--btnbd:rgba(224,180,138,.35);
 --surf:linear-gradient(145deg,rgba(94,66,48,.62),rgba(54,38,30,.62));--ksurf:linear-gradient(145deg,color-mix(in srgb,var(--c,var(--tc,#b07a52)) 55%,#3a2820),color-mix(in srgb,var(--c,var(--tc,#b07a52)) 30%,#241812));
 --big:rgba(224,180,138,.35);--bd:rgba(224,180,138,.22);--insh:inset 0 1px 0 rgba(255,236,214,.18);--osh:0 12px 30px rgba(0,0,0,.4);--kosh:0 10px 24px rgba(0,0,0,.4);
 --bf:blur(12px);--kr:24px;--cr:38px;--gloss:.12;--hmfill:linear-gradient(180deg,#fbeedd,#d9a77a);--hmstroke:0 transparent;--hmfx:drop-shadow(0 6px 16px rgba(0,0,0,.45));--ifx:drop-shadow(0 3px 6px #0006)}
html[data-theme=mocha]{background:#1a1210}
html[data-theme=mocha] body::before{background:radial-gradient(70vmax 60vmax at 15% 10%,#6b4630aa,transparent),radial-gradient(70vmax 60vmax at 90% 90%,#8a3a2a66,transparent),#1a1210}
html[data-theme=mocha] .bg,html[data-theme=mocha] .shade{display:none}
/* ===== PERFORMANS ===== */
html[data-perf="0"]{--bf:none;--osh:0 0 0 0 transparent;--kosh:0 0 0 0 transparent;--hmfx:none;--hmstroke:0 transparent;--ifx:none}
html[data-perf="1"] .k,html[data-perf="1"] .pill,html[data-perf="1"] .ctrl button,html[data-perf="1"] #dots{backdrop-filter:none;-webkit-backdrop-filter:none}
html,body{margin:0;height:100%;overflow:hidden;color:var(--tx);font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
body::before{content:"";position:fixed;inset:0;z-index:-3;background:var(--grad)}
/* arka plan: küçük canvas + büyütme = ücretsiz blur (GPU'yu yormaz) */
.bg{position:fixed;left:0;top:0;width:100%;height:100%;z-index:-2;opacity:0;transition:opacity 1s}
.shade{position:fixed;inset:0;z-index:-1;background:linear-gradient(rgba(0,0,0,var(--shade)),rgba(0,0,0,var(--shade))),radial-gradient(120% 90% at 50% 0%,transparent,#0006)}
.glass{background:var(--surf);backdrop-filter:var(--bf);-webkit-backdrop-filter:var(--bf);border:1px solid var(--bd);box-shadow:var(--insh),var(--osh)}
#pages{display:flex;height:100dvh;transition:transform .4s cubic-bezier(.2,.8,.2,1)}
.page{min-width:100vw;height:100dvh;padding:max(env(safe-area-inset-top),16px) max(env(safe-area-inset-right),24px) 56px max(env(safe-area-inset-left),24px);display:flex;flex-direction:column;justify-content:center;align-items:center;gap:2.2vmin}
#dots{position:fixed;bottom:max(env(safe-area-inset-bottom),12px);left:50%;transform:translateX(-50%);display:flex;gap:8px;padding:8px 12px;border-radius:20px}
#dots i{width:7px;height:7px;border-radius:9px;background:var(--track);transition:.3s}#dots i.on{background:var(--tx);width:22px}
.pill{border-radius:999px;padding:.7vmin 2.4vmin}
#src{display:none;align-items:center;gap:8px;padding:8px 14px;font-size:calc(13px*var(--us));color:inherit;cursor:pointer;margin-bottom:2px}#src svg{width:16px;height:16px;fill:currentColor}#src.one svg{opacity:.35}
#card{display:flex;flex-direction:column;align-items:center;gap:2.4vmin;padding:3.2vmin 5vmin;border-radius:var(--cr)}.row{display:flex;gap:1.6vmin;align-items:center;flex-wrap:wrap;justify-content:center}
#hm{font-size:calc(min(26vw,34vh)*var(--us));font-weight:700;line-height:1;letter-spacing:-.02em;font-variant-numeric:tabular-nums;background:var(--hmfill);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;-webkit-text-stroke:var(--hmstroke);filter:var(--hmfx)}
#sec{font-size:calc(min(3.8vw,4.4vh)*var(--us));font-weight:600;min-width:2.4em;text-align:center;font-variant-numeric:tabular-nums}
#date{font-size:calc(min(3.8vw,4.4vh)*var(--us));font-weight:500}
#day{width:min(70vw,520px);display:flex;flex-direction:column;gap:6px;font-size:calc(13px*var(--us));color:var(--mut)}
#day .t{height:6px;background:var(--track);border-radius:6px;overflow:hidden}#dayfill{height:100%;width:100%;transform-origin:0 50%;transform:scaleX(0);background:var(--fillc);border-radius:6px}
#mini{font-size:14px;max-width:80vw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.music{display:flex;flex-direction:column;align-items:center;gap:3vmin;width:100%}
#cover{width:calc(min(72vw,40vh)*var(--us));aspect-ratio:1;border-radius:7%;background:center/cover;display:flex;align-items:center;justify-content:center;font-size:80px;padding:0}
.info{display:flex;flex-direction:column;align-items:center;text-align:center;width:min(86vw,460px);gap:6px}
#title{font-size:calc(min(6vw,4.4vh)*var(--us));font-weight:650}#artist{color:var(--mut);font-size:calc(min(4vw,3.2vh)*var(--us))}
#seek{position:relative;width:100%;padding:12px 0;margin-top:-2px;touch-action:none;cursor:pointer}#seek.ro{pointer-events:none}#bar{width:100%;height:6px;background:var(--track);border-radius:6px;overflow:hidden;transition:height .15s}#seek.on #bar{height:10px}#fill{height:100%;width:100%;transform-origin:0 50%;transform:scaleX(0);background:var(--fillc)}#knob{position:absolute;top:50%;left:0;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;background:var(--fillc);box-shadow:0 2px 8px rgba(0,0,0,.4);transform:scale(.7);transition:transform .15s}#seek.on #knob{transform:scale(1.15)}#seek.ro #knob{display:none}
.tm{display:flex;justify-content:space-between;width:100%;font-size:12px;color:var(--mut)}
.ctrl{display:flex;gap:calc(2.4vmin*var(--us));align-items:center;margin-top:10px}
.ctrl button{width:calc(clamp(48px,9vmin,66px)*var(--us));aspect-ratio:1;border-radius:50%;color:var(--tx);font-size:calc(clamp(18px,3.4vmin,26px)*var(--us));cursor:pointer;padding:0;display:flex;align-items:center;justify-content:center}
.ctrl button.big{width:calc(clamp(64px,13vmin,92px)*var(--us));font-size:calc(clamp(26px,5vmin,40px)*var(--us));background:var(--big)}
.ctrl button:active{transform:scale(.9)}.i{width:1em;height:1em;fill:currentColor;display:block}
.page.g{overflow-y:auto;justify-content:flex-start;-webkit-overflow-scrolling:touch}
#wgrid{margin:auto 0;display:grid;grid-template-columns:repeat(auto-fill,minmax(calc(clamp(92px,15vmin,150px)*var(--ks)),1fr));gap:clamp(10px,2vmin,18px);width:min(100%,920px)}
.k.w img{width:clamp(44px,11vmin,84px);height:clamp(44px,11vmin,84px);border-radius:16px;object-fit:contain;filter:drop-shadow(0 4px 8px #0006)}
.k.w{font-size:clamp(10px,1.8vmin,13px);line-height:1.15}.k.w b{display:block;max-width:92%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500}
.k.w.act{box-shadow:0 0 0 2px var(--fillc),0 10px 26px rgba(0,0,0,.32)}#wempty{opacity:.7;text-align:center;margin:auto 0}
#grid{margin:auto 0;display:grid;grid-template-columns:repeat(auto-fill,minmax(calc(clamp(92px,15vmin,150px)*var(--ks)),1fr));gap:clamp(10px,2vmin,18px);width:min(100%,920px)}
.k{aspect-ratio:1;border:1px solid var(--kbd,var(--bd));border-radius:var(--kr);color:var(--tx);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;font-size:calc(clamp(11px,2vmin,15px)*var(--ks));font-weight:500;position:relative;overflow:hidden;padding:4px;text-align:center;
 background:var(--ksurf);backdrop-filter:var(--bf);-webkit-backdrop-filter:var(--bf);
 box-shadow:var(--insh),var(--kosh);transition:transform .12s}
.k::after{content:"";position:absolute;inset:0;border-radius:inherit;background:linear-gradient(180deg,rgba(255,255,255,var(--gloss)),transparent 48%);pointer-events:none}
.k span{font-size:calc(clamp(28px,6vmin,52px)*var(--ks));filter:var(--ifx)}
.k img{width:calc(clamp(34px,8vmin,64px)*var(--ks));height:calc(clamp(34px,8vmin,64px)*var(--ks));object-fit:contain;border-radius:14px;filter:var(--ifx)}
.k:active,.k.hit{transform:scale(.93)}.k.hit::after{background:rgba(255,255,255,.3)}
#sheet{position:fixed;left:50%;bottom:64px;transform:translateX(-50%);width:min(92vw,420px);max-height:calc(100dvh - 80px);overflow-y:auto;padding:16px;border-radius:26px;z-index:20;flex-direction:column;gap:12px}
#sheet{overscroll-behavior:contain}.sl{touch-action:pan-y;padding:12px 0;margin:-6px 0}#sheet .sl input{pointer-events:none}
#off{position:fixed;inset:0;z-index:50;display:none;flex-direction:column;align-items:center;justify-content:center;gap:10px;text-align:center;background:rgba(8,10,20,.9);color:#fff;padding:24px}#off h2{margin:6px 0 0}#off p{margin:0;opacity:.75}#off button{margin-top:14px;padding:12px 22px;border-radius:14px;border:0;background:#7c9cff;color:#000;font-weight:600;font-size:15px}.spin{width:34px;height:34px;border-radius:50%;border:3px solid rgba(255,255,255,.25);border-top-color:#fff;animation:sp 1s linear infinite}@keyframes sp{to{transform:rotate(360deg)}}
#sheet small{font-size:11px;color:var(--mut);margin-top:-6px}
.seg{display:flex;gap:8px;flex-wrap:wrap}
.seg button,#sheet>button{flex:1 1 28%;padding:10px;border-radius:14px;border:1px solid var(--btnbd);background:var(--btn);color:var(--tx);font-size:13px}
.seg button.on{background:var(--btnon)}#sheet>button.dc{background:rgba(255,80,100,.28);border-color:rgba(255,120,130,.6)}#sheet label{font-size:12px;display:flex;flex-direction:column;gap:4px}#sheet input{width:100%}
@media (orientation:landscape){
 .music{flex-direction:row;justify-content:center;gap:6vw}
 #cover{width:calc(min(62vh,34vw)*var(--us))}.info{align-items:flex-start;text-align:left;width:min(46vw,460px)}
 .page{padding-bottom:48px}
}

/* buton rengi (--c) her butonda ayrı çözülür */
html .k{--ksurf:linear-gradient(135deg,color-mix(in srgb,var(--c,var(--tc,#fff)) 34%,transparent),rgba(255,255,255,.05))}
html[data-theme=frost] .k{--ksurf:color-mix(in srgb,var(--c,var(--tc,#fff)) 24%,rgba(255,255,255,.08))}
html[data-theme=amoled] .k{--ksurf:color-mix(in srgb,var(--c,var(--tc,#7c9cff)) 16%,#0a0a0c)}
html[data-theme=light] .k{--ksurf:color-mix(in srgb,var(--c,var(--tc,#fff)) 22%,rgba(255,255,255,.72))}
html[data-theme=flat] .k{--ksurf:color-mix(in srgb,var(--c,var(--tc,#2b3140)) 72%,#000)}

html[data-theme=neon] .k{--kbd:color-mix(in srgb,var(--c,var(--nc)) 80%,transparent);--kosh:0 0 14px color-mix(in srgb,var(--c,var(--nc)) 45%,transparent);--ksurf:color-mix(in srgb,var(--c,transparent) 12%,rgba(8,10,28,.62))}
html[data-perf="0"] .k{--kosh:0 0 0 0 transparent}   /* Hafif: gölge/parlama yok (değişmedi) */
/* ===== SİMGELER ===== */
.ic{width:1em;height:1em;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;flex:none}
.k svg.ic{width:calc(clamp(28px,6vmin,52px)*var(--ks));height:calc(clamp(28px,6vmin,52px)*var(--ks));filter:var(--ifx)}
/* ===== PERFORMANS: Dengeli ve Yüksek (Hafif'e dokunulmaz) ===== */
@keyframes tin{from{opacity:0;transform:translateY(12px) scale(.96)}}
@keyframes drift{from{transform:translate3d(-3%,-2%,0) scale(1.14)}to{transform:translate3d(3%,2%,0) scale(1.14)}}
@keyframes kb{from{transform:scale(1.04)}to{transform:scale(1.16) translate(-1.5%,1%)}}
@keyframes sheen{from{transform:rotate(20deg) translateX(-120%)}to{transform:rotate(20deg) translateX(420%)}}
html[data-perf="1"] #pages,html[data-perf="2"] #pages{will-change:transform}
html[data-perf="1"] .k,html[data-perf="2"] .k{contain:layout paint;animation:tin .38s cubic-bezier(.2,.8,.2,1) backwards}
html[data-perf="2"] .k:nth-child(3n+2){animation-delay:.04s}html[data-perf="2"] .k:nth-child(3n){animation-delay:.08s}
html[data-perf="2"] #pages{transition:transform .55s cubic-bezier(.3,1.2,.4,1)}
html[data-perf="2"] body::before{animation:drift 45s ease-in-out infinite alternate;will-change:transform}
html[data-perf="2"] .bg{animation:kb 36s ease-in-out infinite alternate;will-change:transform}
html[data-perf="2"] .k::before{content:"";position:absolute;top:-30%;left:-40%;width:35%;height:160%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.38),transparent);pointer-events:none;transform:rotate(20deg) translateX(-120%)}
html[data-perf="2"] .k.hit::before{animation:sheen .65s ease-out}
html[data-perf="2"] #fill,html[data-perf="2"] #dayfill{box-shadow:0 0 12px var(--fillc)}
html[data-perf="2"] .k.w.act{box-shadow:0 0 0 2px var(--fillc),0 0 22px var(--fillc)}
</style></head><body>
<canvas class="bg" id="bg0"></canvas><canvas class="bg" id="bg1"></canvas><div class="shade"></div>
<svg width="0" height="0" style="position:absolute"><defs><symbol id="ic-playpause" viewBox="0 0 24 24"><path d="M3 5l7 7-7 7z"/><path d="M15 5v14M20 5v14"/></symbol><symbol id="ic-play" viewBox="0 0 24 24"><path d="M7 4l13 8-13 8z"/></symbol><symbol id="ic-pause" viewBox="0 0 24 24"><path d="M8 5v14M16 5v14"/></symbol><symbol id="ic-next" viewBox="0 0 24 24"><path d="M5 5l10 7-10 7z"/><path d="M19 5v14"/></symbol><symbol id="ic-prev" viewBox="0 0 24 24"><path d="M19 5L9 12l10 7z"/><path d="M5 5v14"/></symbol><symbol id="ic-volup" viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 010 6M19 6a8 8 0 010 12"/></symbol><symbol id="ic-voldown" viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 010 6"/></symbol><symbol id="ic-mute" viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M17 9.5l4.5 5M21.5 9.5l-4.5 5"/></symbol><symbol id="ic-desktop" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></symbol><symbol id="ic-switch" viewBox="0 0 24 24"><rect x="3" y="8" width="13" height="11" rx="2"/><path d="M8 8V6a2 2 0 012-2h9a2 2 0 012 2v9a2 2 0 01-2 2h-3"/></symbol><symbol id="ic-taskview" viewBox="0 0 24 24"><rect x="3" y="4" width="8" height="7" rx="1.5"/><rect x="13" y="4" width="8" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/></symbol><symbol id="ic-maximize" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2"/></symbol><symbol id="ic-minimize" viewBox="0 0 24 24"><path d="M5 18h14"/></symbol><symbol id="ic-snapl" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/><path d="M4 5h7v14H4z" fill="currentColor" stroke="none" opacity=".4"/></symbol><symbol id="ic-snapr" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/><path d="M13 5h7v14h-7z" fill="currentColor" stroke="none" opacity=".4"/></symbol><symbol id="ic-closewin" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></symbol><symbol id="ic-minall" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 15h8"/></symbol><symbol id="ic-newdesk" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4M12 7v6M9 10h6"/></symbol><symbol id="ic-nextdesk" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4M9 10h6M13 8l2 2-2 2"/></symbol><symbol id="ic-prevdesk" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4M15 10H9M11 8l-2 2 2 2"/></symbol><symbol id="ic-lock" viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/></symbol><symbol id="ic-project" viewBox="0 0 24 24"><rect x="2.5" y="5" width="12" height="9" rx="1.5"/><rect x="9.5" y="10" width="12" height="9" rx="1.5"/></symbol><symbol id="ic-copy" viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 00-2-2H6a2 2 0 00-2 2v8a2 2 0 002 2h2"/></symbol><symbol id="ic-paste" viewBox="0 0 24 24"><rect x="5" y="4" width="14" height="17" rx="2"/><rect x="9" y="2" width="6" height="4" rx="1"/></symbol><symbol id="ic-cut" viewBox="0 0 24 24"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.1 15.9M14.5 14.5L20 20M8.1 8.1L12 12"/></symbol><symbol id="ic-undo" viewBox="0 0 24 24"><path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/></symbol><symbol id="ic-redo" viewBox="0 0 24 24"><path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 000 12h3"/></symbol><symbol id="ic-selectall" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2" stroke-dasharray="3 3"/><path d="M9 12l2 2 4-4"/></symbol><symbol id="ic-save" viewBox="0 0 24 24"><path d="M5 3h11l4 4v13a1 1 0 01-1 1H5a1 1 0 01-1-1V4a1 1 0 011-1z"/><path d="M8 3v5h7V3M8 21v-7h8v7"/></symbol><symbol id="ic-find" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></symbol><symbol id="ic-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></symbol><symbol id="ic-print" viewBox="0 0 24 24"><path d="M7 9V3h10v6"/><rect x="4" y="9" width="16" height="8" rx="2"/><path d="M7 14h10v7H7z"/></symbol><symbol id="ic-newfile" viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5M12 12v6M9 15h6"/></symbol><symbol id="ic-file" viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></symbol><symbol id="ic-folder" viewBox="0 0 24 24"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></symbol><symbol id="ic-trash" viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13a1 1 0 001 1h8a1 1 0 001-1l1-13M9 7V4h6v3"/></symbol><symbol id="ic-enter" viewBox="0 0 24 24"><path d="M20 5v8a2 2 0 01-2 2H5M9 11l-4 4 4 4"/></symbol><symbol id="ic-esc" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/></symbol><symbol id="ic-newtab" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M12 13v4M10 15h4"/></symbol><symbol id="ic-closetab" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M10 13l4 4M14 13l-4 4"/></symbol><symbol id="ic-reopentab" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M12 17v-4M10 15l2-2 2 2"/></symbol><symbol id="ic-nexttab" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M9 15h6M13 13l2 2-2 2"/></symbol><symbol id="ic-prevtab" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M15 15H9M11 13l-2 2 2 2"/></symbol><symbol id="ic-incognito" viewBox="0 0 24 24"><path d="M3 13h18M5.5 13l2-8h9l2 8"/><circle cx="8" cy="17.5" r="2.5"/><circle cx="16" cy="17.5" r="2.5"/><path d="M10.5 17h3"/></symbol><symbol id="ic-reload" viewBox="0 0 24 24"><path d="M20 12a8 8 0 10-2.3 5.7"/><path d="M20 5v5h-5"/></symbol><symbol id="ic-hardreload" viewBox="0 0 24 24"><path d="M20 12a8 8 0 10-2.3 5.7M20 5v5h-5"/><path d="M12.5 8l-2.5 4h4l-2.5 4"/></symbol><symbol id="ic-fullscreen" viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></symbol><symbol id="ic-zoomin" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5M11 8v6M8 11h6"/></symbol><symbol id="ic-zoomout" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5M8 11h6"/></symbol><symbol id="ic-zoomreset" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/><circle cx="11" cy="11" r="1.6"/></symbol><symbol id="ic-back" viewBox="0 0 24 24"><path d="M19 12H5M11 6l-6 6 6 6"/></symbol><symbol id="ic-forward" viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></symbol><symbol id="ic-addrbar" viewBox="0 0 24 24"><rect x="3" y="8" width="18" height="8" rx="4"/><path d="M8 12h8"/></symbol><symbol id="ic-snip" viewBox="0 0 24 24"><path d="M6 3v13a2 2 0 002 2h13"/><path d="M3 6h13a2 2 0 012 2v13"/></symbol><symbol id="ic-camera" viewBox="0 0 24 24"><path d="M4 8a2 2 0 012-2h2l1.5-2h5L16 6h2a2 2 0 012 2v9a2 2 0 01-2 2H6a2 2 0 01-2-2z"/><circle cx="12" cy="12.5" r="3.5"/></symbol><symbol id="ic-clipboard" viewBox="0 0 24 24"><rect x="5" y="4" width="14" height="17" rx="2"/><rect x="9" y="2" width="6" height="4" rx="1"/><path d="M9 11h6M9 15h4"/></symbol><symbol id="ic-smile" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8.5 14a4 4 0 007 0M9 9.5h.01M15 9.5h.01"/></symbol><symbol id="ic-run" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 10l3 2-3 2M12.5 15H17"/></symbol><symbol id="ic-terminal" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 10l3 2-3 2M12.5 15H17"/></symbol><symbol id="ic-settings" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/></symbol><symbol id="ic-bell" viewBox="0 0 24 24"><path d="M6 16v-5a6 6 0 0112 0v5l2 2H4z"/><path d="M10 21h4"/></symbol><symbol id="ic-gamepad" viewBox="0 0 24 24"><rect x="3" y="7" width="18" height="11" rx="5"/><path d="M8 10.5v5M5.5 13h5M15.5 12h.01M18 14h.01"/></symbol><symbol id="ic-record" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5" fill="currentColor"/></symbol><symbol id="ic-pulse" viewBox="0 0 24 24"><path d="M3 12h4l2-6 4 12 2-6h6"/></symbol><symbol id="ic-app" viewBox="0 0 24 24"><rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/></symbol><symbol id="ic-globe" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 010 18M12 3a14 14 0 000 18"/></symbol><symbol id="ic-mail" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></symbol><symbol id="ic-home" viewBox="0 0 24 24"><path d="M4 11l8-7 8 7v8a1 1 0 01-1 1h-4v-6H9v6H5a1 1 0 01-1-1z"/></symbol><symbol id="ic-music" viewBox="0 0 24 24"><path d="M9 18V6l11-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/></symbol><symbol id="ic-video" viewBox="0 0 24 24"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3z"/></symbol><symbol id="ic-link" viewBox="0 0 24 24"><path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/></symbol><symbol id="ic-keyboard" viewBox="0 0 24 24"><rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></symbol><symbol id="ic-chat" viewBox="0 0 24 24"><path d="M4 5h16a1 1 0 011 1v10a1 1 0 01-1 1h-8l-5 4v-4H4a1 1 0 01-1-1V6a1 1 0 011-1z"/></symbol><symbol id="ic-phone" viewBox="0 0 24 24"><rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M11 18.5h2"/></symbol><symbol id="ic-code" viewBox="0 0 24 24"><path d="M9 7l-5 5 5 5M15 7l5 5-5 5M13.5 5l-3 14"/></symbol><symbol id="ic-cloud" viewBox="0 0 24 24"><path d="M7 18a4 4 0 01-.6-7.95A6 6 0 0118 9.5 4.3 4.3 0 0117.5 18z"/></symbol><symbol id="ic-cart" viewBox="0 0 24 24"><circle cx="9" cy="20" r="1.5"/><circle cx="17" cy="20" r="1.5"/><path d="M3 4h2.5l2.2 11h10l2-8H7"/></symbol><symbol id="ic-calendar" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></symbol><symbol id="ic-clock" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></symbol><symbol id="ic-pin" viewBox="0 0 24 24"><path d="M12 21s7-6.2 7-12a7 7 0 00-14 0c0 5.8 7 12 7 12z"/><circle cx="12" cy="9" r="2.5"/></symbol><symbol id="ic-bookmark" viewBox="0 0 24 24"><path d="M7 4h10a1 1 0 011 1v16l-6-4-6 4V5a1 1 0 011-1z"/></symbol><symbol id="ic-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/></symbol><symbol id="ic-moon" viewBox="0 0 24 24"><path d="M20 14.5A8.5 8.5 0 019.5 4 8.5 8.5 0 1020 14.5z"/></symbol><symbol id="ic-power" viewBox="0 0 24 24"><path d="M12 3v9"/><path d="M6.3 6.8a8 8 0 1011.4 0"/></symbol><symbol id="ic-rocket" viewBox="0 0 24 24"><path d="M5 19c0-3 1-4 3-4l1 1c0 2-1 3-4 3zM9 15l6-6a8 8 0 004-6 8 8 0 00-6 4l-6 6z"/><circle cx="15" cy="9" r="1.2"/></symbol><symbol id="ic-wrench" viewBox="0 0 24 24"><path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z"/></symbol><symbol id="ic-bulb" viewBox="0 0 24 24"><path d="M9 18h6M10 21h4M12 3a6 6 0 00-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0012 3z"/></symbol><symbol id="ic-heart" viewBox="0 0 24 24"><path d="M12 20s-8-5-8-11a4.5 4.5 0 018-2.8A4.5 4.5 0 0120 9c0 6-8 11-8 11z"/></symbol><symbol id="ic-user" viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0116 0"/></symbol><symbol id="ic-users" viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0113 0"/><circle cx="17" cy="9" r="2.5"/><path d="M17 14a5 5 0 015 5"/></symbol><symbol id="ic-image" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="M21 16l-5-5-8 9"/></symbol><symbol id="ic-mic" viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0014 0M12 18v3"/></symbol><symbol id="ic-headphones" viewBox="0 0 24 24"><path d="M4 15v-3a8 8 0 0116 0v3"/><rect x="3" y="14" width="4" height="7" rx="1.5"/><rect x="17" y="14" width="4" height="7" rx="1.5"/></symbol><symbol id="ic-wifi" viewBox="0 0 24 24"><path d="M3 9a14 14 0 0118 0M6 12.5a9 9 0 0112 0M9 16a4.5 4.5 0 016 0"/><circle cx="12" cy="19.5" r=".8" fill="currentColor"/></symbol><symbol id="ic-star" viewBox="0 0 24 24"><path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/></symbol><symbol id="ic-bolt" viewBox="0 0 24 24"><path d="M13 2L4 14h7l-1 8 9-12h-7z"/></symbol><symbol id="ic-book" viewBox="0 0 24 24"><path d="M5 4h11a3 3 0 013 3v13H8a3 3 0 01-3-3z"/><path d="M5 17a3 3 0 013-3h11"/></symbol><symbol id="ic-brush" viewBox="0 0 24 24"><path d="M18 3l3 3-9.5 9.5-3-3z"/><path d="M8.5 12.5C6 12.5 5 14 5 16c0 1.5-1 2.5-2 3 3 1 7 .5 8-2.5"/></symbol><symbol id="ic-send" viewBox="0 0 24 24"><path d="M21 3L3 11l7 3 3 7z"/><path d="M10 14l11-11"/></symbol><symbol id="ic-tag" viewBox="0 0 24 24"><path d="M3 12V4a1 1 0 011-1h8l9 9-9 9z"/><circle cx="8" cy="8" r="1.5"/></symbol><symbol id="ic-ban" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/></symbol><symbol id="ic-download" viewBox="0 0 24 24"><path d="M12 4v11M7 11l5 5 5-5M4 20h16"/></symbol><symbol id="ic-gem" viewBox="0 0 24 24"><path d="M6 4h12l3 5-9 11L3 9z"/><path d="M3 9h18M9 4l-1 5 4 11M15 4l1 5-4 11"/></symbol></defs></svg>
<svg width="0" height="0" style="position:absolute"><defs>
<symbol id="i-play" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></symbol>
<symbol id="i-pause" viewBox="0 0 24 24"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></symbol>
<symbol id="i-prev" viewBox="0 0 24 24"><path d="M6 6h2v12H6zM9.5 12 18 18V6z"/></symbol>
<symbol id="i-next" viewBox="0 0 24 24"><path d="M16 6h2v12h-2zM6 18l8.5-6L6 6z"/></symbol>
<symbol id="i-vup" viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4zM14 3.2v2.1a7 7 0 0 1 0 13.4v2.1A9 9 0 0 0 14 3.2z"/></symbol>
<symbol id="i-vdown" viewBox="0 0 24 24"><path d="M18.5 12A4.5 4.5 0 0 0 16 8v8a4.5 4.5 0 0 0 2.5-4zM5 9v6h4l5 5V4L9 9H5z"/></symbol>
<symbol id="i-swap" viewBox="0 0 24 24"><path d="M6.99 11L3 15l3.99 4v-3H14v-2H6.99v-3zM21 9l-3.99-4v3H10v2h7.01v3L21 9z"/></symbol>
</defs></svg>
<div id="pages">
 <div class="page">
  <div id="card" class="glass"><div id="hm">--:--</div><div class="row"><div id="date" class="glass pill"></div><div id="sec" class="glass pill">00</div></div>
  <div id="day"><div style="display:flex;justify-content:space-between"><span>Gün ilerlemesi</span><span id="daytxt"></span></div><div class="t"><div id="dayfill"></div></div></div></div>
  <div id="mini" class="glass pill" style="display:none"></div>
 </div>
 <div class="page"><div class="music">
  <div id="cover" class="glass">🎵</div>
  <div class="info"><button id="src" class="glass pill" onclick="cycleSrc()"><svg class="i"><use href="#i-swap"/></svg><span id="srct"></span></button><div id="title">Çalan bir şey yok</div><div id="artist">Spotify veya YouTube'dan bir şey aç</div>
   <div id="seek"><div id="bar"><div id="fill"></div></div><div id="knob"></div></div><div class="tm"><span id="t1">0:00</span><span id="t2">0:00</span></div>
   <div class="ctrl"><button class="glass" onclick="m('vdown')"><svg class="i"><use href="#i-vdown"/></svg></button><button class="glass" onclick="m('prev')"><svg class="i"><use href="#i-prev"/></svg></button><button class="glass big" id="pp" onclick="m('toggle')"><svg class="i"><use href="#i-play"/></svg></button><button class="glass" onclick="m('next')"><svg class="i"><use href="#i-next"/></svg></button><button class="glass" onclick="m('vup')"><svg class="i"><use href="#i-vup"/></svg></button></div>
  </div></div></div>
 <div class="page g"><div id="grid"></div></div>
 <div class="page g"><div id="wgrid"></div><div id="wempty" style="display:none">Açık uygulama yok</div></div>
</div><div id="sheet" class="glass" style="display:none"><b>Tema</b>
<div class="seg" id="th"></div><small>Bazı temalar (AMOLED, Açık, Terminal, Pastel, Mono, Mocha) kendi arka planını kullanır.</small>
<b>Performans</b><div class="seg" id="pf"></div><small>Telefon kasıyorsa "Hafif" seç.</small>
<b>Ölçek</b>
<label>Buton boyutu: <span id="vk"></span>%<input type="range" min="60" max="150" step="5" id="rk"></label>
<label>Saat / müzik boyutu: <span id="vu"></span>%<input type="range" min="60" max="130" step="5" id="ru"></label>
<b>Arka plan</b>
<div class="seg"><button data-m="cover">Kapak</button><button data-m="preset">Gradyan</button><button data-m="image">Resim</button></div>
<div class="seg" id="pre"></div>
<label>Bulanıklık<input type="range" min="0" max="100" id="rb"></label>
<label>Karartma<input type="range" min="20" max="100" id="rd"></label>
<b>Dil</b><div class="seg" id="lg"></div>
<button id="dc" class="dc" style="display:none" onclick="window.DeckApp&&DeckApp.disconnect()">Bağlantıyı kes</button><button onclick="document.getElementById('sheet').style.display='none'">Tamam</button></div>
<div id="off"><div class="spin"></div><h2>Bağlantı koptu</h2><p>Bilgisayar yeniden bağlanınca otomatik devam edilecek.</p><button id="offb" onclick="window.DeckApp&&DeckApp.disconnect()">Bilgisayar seç</button></div>
<div id="dots" class="glass"><i></i><i></i><i></i><i></i></div>
<script>
const EN={"Tema": "Theme", "Dil": "Language", "AMOLED ve Açık temalar kendi arka planını kullanır.": "AMOLED and Light themes use their own background.", "Performans": "Performance", "Telefon kasıyorsa \"Hafif\" seç.": "If the phone lags, choose \"Light\".", "Ölçek": "Scale", "Buton boyutu:": "Button size:", "Saat / müzik boyutu:": "Clock / music size:", "Arka plan": "Background", "Kapak": "Cover", "Gradyan": "Gradient", "Resim": "Image", "Bulanıklık": "Blur", "Karartma": "Dim", "Bağlantıyı kes": "Disconnect", "Tamam": "Done", "Gün ilerlemesi": "Day progress", "Çalan bir şey yok": "Nothing playing", "Spotify veya YouTube'dan bir şey aç": "Play something on Spotify or YouTube", "PC'den yükle": "Upload from PC", "Buzlu Cam": "Frosted Glass", "Açık": "Light", "Düz": "Flat", "Hafif": "Light", "Dengeli": "Balanced", "Yüksek": "High", "Bağlantı koptu": "Connection lost", "Bilgisayar yeniden bağlanınca otomatik devam edilecek.": "It will resume automatically when the computer is back.", "Bilgisayar seç": "Choose computer", "Phone Deck · Atamalar": "Phone Deck · Shortcuts", "Telefon adresi:": "Phone address:", "Telefondaki uygulamaya bu adresi gir": "Enter this address in the phone app", "Programı kapat": "Quit program", "Butona tıkla ve düzenle. Sürükleyip bırakarak yerini değiştir. Değişiklikler telefonda anında görünür.": "Click a button to edit it. Drag and drop to reorder. Changes appear on the phone instantly.", "ile hazır ayarlardan (Spotify, YouTube, Discord…) tek tıkla ekleyebilirsin.": "lets you add one from the presets (Spotify, YouTube, Discord…) with a single click.", "Telefonun arka planını buradan ayarla. Telefonda saat ekranına basılı tutarak da değiştirebilirsin.": "Set the phone background here. You can also change it by long-pressing the clock screen on the phone.", "+ Boş buton": "+ Blank button", "Ara… (spotify, kopyala, ses)": "Search… (spotify, copy, volume)", "Kapat": "Close", "Sonuç yok": "No results", "Görünüm ve arka plan": "Appearance & background", "Mod": "Mode", "Albüm kapağı (otomatik)": "Album cover (automatic)", "Kendi resmim": "My own image", "Düz renk": "Solid color", "Mor-mavi": "Purple-blue", "Gün batımı": "Sunset", "Okyanus": "Ocean", "Grafit": "Graphite", "Bulanıklık:": "Blur:", "Karartma:": "Dim:", "Kendi resmin": "Your image", "AMOLED (saf siyah)": "AMOLED (pure black)", "Hafif (en akıcı)": "Light (smoothest)", "Yüksek (en güzel)": "High (prettiest)", "İsim": "Name", "İkon (emoji)": "Icon (emoji)", "Görsel ikon (PNG / JPG, emojinin yerine geçer)": "Image icon (PNG / JPG, replaces the emoji)", "Kaldır": "Remove", "Renk": "Color", "Eylem": "Action", "Program / site aç": "Open app / site", "Tuş kombinasyonu": "Key combination", "Müzik / ses": "Music / volume", "Yazı yaz": "Type text", "Kaydet": "Save", "Sil": "Delete", "Düzenlemek için bir butona tıkla ya da + ile yenisini ekle.": "Click a button to edit it, or add a new one with +.", "Program adı / yolu veya site adresi": "App name / path or website address", "Tuşlar (örn: ctrl+shift+s)": "Keys (e.g. ctrl+shift+s)", "🎹 Tuşa basarak kaydet": "🎹 Record by pressing keys", "Komut": "Command", "Yazılacak metin": "Text to type", "Oynat / Duraklat": "Play / Pause", "Duraklat": "Pause", "Oynat": "Play", "Sonraki şarkı": "Next track", "Önceki şarkı": "Previous track", "Sessiz": "Mute", "Ses +": "Volume +", "Ses -": "Volume -", "Kaydedildi ✓": "Saved ✓", "Resim yüklendi ✓": "Image uploaded ✓", "Silinsin mi?": "Delete this?", "Tuşlara bas…": "Press the keys…", "Phone Deck tamamen kapatılsın mı? Telefon bağlantısı da kesilir.": "Quit Phone Deck completely? The phone will be disconnected.", "Uygulamalar": "Apps", "Siteler": "Sites", "Müzik ve ses": "Music & volume", "Kısayollar · Pencere ve masaüstü": "Shortcuts · Windows & desktops", "Kısayollar · Düzenleme": "Shortcuts · Editing", "Kısayollar · Tarayıcı": "Shortcuts · Browser", "Kısayollar · Windows araçları": "Shortcuts · Windows tools", "Haritalar": "Maps", "Çeviri": "Translate", "Takvim": "Calendar", "Not Defteri": "Notepad", "Hesap Mak.": "Calculator", "Dosyalar": "Files", "Ayarlar": "Settings", "Görev Yön.": "Task Manager", "Komut İstemi": "Command Prompt", "Ekran Alıntısı": "Screen Snip", "Masaüstü": "Desktop", "Pencere Değiş": "Switch Window", "Görev Görünümü": "Task View", "Büyüt": "Maximize", "Küçült": "Minimize", "Sola Yasla": "Snap Left", "Sağa Yasla": "Snap Right", "Pencereyi Kapat": "Close Window", "Tümünü Küçült": "Minimize All", "Yeni Masaüstü": "New Desktop", "Sonraki Masaüstü": "Next Desktop", "Önceki Masaüstü": "Previous Desktop", "Kilitle": "Lock", "Ekranı Yansıt": "Project", "Kopyala": "Copy", "Yapıştır": "Paste", "Kes": "Cut", "Geri Al": "Undo", "Yinele": "Redo", "Tümünü Seç": "Select All", "Bul": "Find", "Yazdır": "Print", "Yeni": "New", "Aç": "Open", "Yeni Sekme": "New Tab", "Sekmeyi Kapat": "Close Tab", "Kapatılanı Aç": "Reopen Tab", "Sonraki Sekme": "Next Tab", "Önceki Sekme": "Previous Tab", "Gizli Pencere": "Incognito", "Yenile": "Reload", "Zorla Yenile": "Hard Reload", "Tam Ekran": "Fullscreen", "Yakınlaştır": "Zoom In", "Uzaklaştır": "Zoom Out", "Zoom Sıfırla": "Reset Zoom", "Geri": "Back", "İleri": "Forward", "Adres Çubuğu": "Address Bar", "Ekran Görüntüsü": "Screenshot", "Pano Geçmişi": "Clipboard", "Arama": "Search", "Çalıştır": "Run", "Dosya Gezgini": "File Explorer", "Bildirimler": "Notifications", "Oyun Çubuğu": "Game Bar", "Ekran Kaydı": "Screen Record", "Büyüteç": "Magnifier", "Oynat/Duraklat": "Play/Pause", "Sonraki": "Next", "Önceki": "Previous", "Açık uygulamalar": "Running apps", "Açık uygulama yok": "No running apps", "Pencere listesi yalnızca Windows'ta çalışır.": "The window list only works on Windows.", "USB: adb bulunamadı": "USB: adb not found", "USB: cihaz yok": "USB: no device", "USB: telefonda izin ver": "USB: allow on the phone", "USB: bağlı ✓": "USB: connected ✓", "Veri klasörü": "Data folder", "Telefonu USB ile bağla, USB hata ayıklamayı aç, telefon uygulamasında “USB ile bağlan”a bas.": "Plug in the phone, turn on USB debugging, then tap “Connect via USB” in the phone app.", "Android SDK platform-tools (adb) bulunamadı. Android Studio kurulu olmalı.": "Android SDK platform-tools (adb) not found. Android Studio must be installed.", "Butonlar": "Buttons", "Görünüm": "Appearance", "Bağlantı": "Connection", "Butona tıkla ve düzenle, sürükleyerek sırala. Değişiklikler telefonda anında görünür.": "Click a button to edit it, drag to reorder. Changes appear on the phone instantly.", "+ Hazır kısayol ekle": "+ Add from presets", "Hazır kısayollar": "Presets", "Görsel ikon": "Image icon", "Resim seç": "Choose image", "Renksiz": "No color", "Özel renk": "Custom color", "Telefondaki görünümü buradan ayarla. Telefonda saat ekranına basılı tutarak da değiştirebilirsin.": "Set the phone's look here. You can also change it by long-pressing the clock screen on the phone.", "Boyut ve performans": "Size & performance", "Bilgisayar adresi": "Computer address", "Telefondaki uygulamaya bu adreslerden birini gir. Genelde ilkini kullan.": "Enter one of these addresses in the phone app. Usually the first one works.", "Kopyalandı ✓": "Copied ✓", "Önerilen": "Recommended", "Göster / gizle": "Show / hide", "USB bağlantısı": "USB connection", "Telefonda Geliştirici seçenekleri → USB hata ayıklamayı aç, kabloyu tak ve çıkan izne “İzin ver” de. Sonra telefon uygulamasında “USB ile bağlan”a bas.": "On the phone, turn on Developer options → USB debugging, plug in the cable and tap “Allow”. Then tap “Connect via USB” in the phone app.", "Sistem": "System", "Veri klasörünü aç": "Open data folder", "Tümü": "All", "Kısayollar": "Shortcuts", "İkon": "Icon", "Değiştir": "Change", "İkon seç": "Choose icon", "Tuşa basarak kaydet": "Record by pressing keys", "Tema rengi": "Theme color", "Varsayılan": "Default", "Düz temada rengi olmayan butonların, Neon ve Terminal temalarında çerçeve ve parlamanın rengi. Diğer temalarda buton tonu.": "Flat: color of buttons without their own color. Neon and Terminal: border and glow color. Other themes: button tint.", "Dengeli: yumuşak geçişler. Yüksek: bulanıklık, hareketli arka plan ve parlama efektleri.": "Balanced: smooth transitions. High: blur, animated background and shine effects.", "Resim / GIF seç": "Choose image / GIF", "GIF desteklenir (en fazla 16 MB). Hareketli arka planda bulanıklık ayarı da çalışır.": "GIFs are supported (max 16 MB). The blur setting also works on animated backgrounds.", "GIF çok büyük (en fazla 16 MB)": "GIF is too large (max 16 MB)", "Bazı temalar (AMOLED, Açık, Terminal, Pastel, Mono, Mocha) kendi arka planını kullanır.": "Some themes (AMOLED, Light, Terminal, Pastel, Mono, Mocha) use their own background.", "Tarayıcılar": "Browsers", "Geliştirme": "Development", "Oyun": "Games", "Yapay zeka": "AI", "Denetim Masası": "Control Panel", "Aygıt Yöneticisi": "Device Manager", "Kayıt Defteri": "Registry Editor", "Hizmetler": "Services", "Kaynak İzleyici": "Resource Monitor", "Sistem Bilgisi": "System Info", "Uzak Masaüstü": "Remote Desktop", "Disk Temizleme": "Disk Cleanup", "Mağaza": "Store", "Fotoğraflar": "Photos", "Saat": "Clock", "İndirilenler": "Downloads", "Belgeler": "Documents", "Resimler": "Pictures", "Geri Dönüşüm": "Recycle Bin", "Ses ayarları": "Sound settings", "Wi-Fi ayarları": "Wi-Fi settings", "Ekran ayarları": "Display settings", "Hazırlanıyor…": "Preparing…"};
const LG=(()=>{const l=document.documentElement.dataset.lang;return l==='tr'||l==='en'?l:((navigator.language||'en').toLowerCase().startsWith('tr')?'tr':'en')})();
const t=s=>LG==='en'&&EN[s]?EN[s]:s;
if(LG==='en'){
 const tx=n=>{const v=n.nodeValue,k=v.trim();if(k&&EN[k]){const o=v.replace(k,()=>EN[k]);if(o!==v)n.nodeValue=o}};
 const at=e=>['placeholder','title'].forEach(a=>{const v=e.getAttribute&&e.getAttribute(a);if(v&&EN[v])e.setAttribute(a,EN[v])});
 const walk=r=>{if(r.nodeType===3){tx(r);return}if(r.nodeType!==1)return;at(r);const w=document.createTreeWalker(r,NodeFilter.SHOW_TEXT);let n;while(n=w.nextNode())tx(n);r.querySelectorAll('[placeholder],[title]').forEach(at)};
 walk(document.body);
 new MutationObserver(ms=>ms.forEach(m=>{if(m.type==='characterData')tx(m.target);m.addedNodes.forEach(walk)})).observe(document.body,{childList:true,characterData:true,subtree:true});
}

const $=i=>document.getElementById(i);
const ico=n=>`<svg class="i"><use href="#i-${n}"/></svg>`;
const TH=[['glass','Liquid Glass'],['frost','Buzlu Cam'],['amoled','AMOLED'],['neon','Neon'],['light','Açık'],['flat','Düz'],['terminal','Terminal'],['pastel','Pastel'],['mono','Mono'],['mocha','Mocha']];
const PF=[['0','Hafif'],['1','Dengeli'],['2','Yüksek']];
let cur=0,x0=null,last='',pos=0,end=0,playing=false,t0=0,bi=0,bgImg=null,rp,lm='',gifT=null;
const D=[...document.querySelectorAll('#dots i')],bgs=[$('bg0'),$('bg1')];
function go(n){cur=Math.max(0,Math.min(3,n));$('pages').style.transform=`translateX(${-cur*100}vw)`;D.forEach((d,i)=>d.classList.toggle('on',i==cur));
 if(cur==2)loadBtns();if(cur==3)loadWins();if(cur==0)tick();if(cur==1)prog()}go(0);
addEventListener('touchstart',e=>x0=e.touches[0].clientX,{passive:true});
addEventListener('touchend',e=>{if(x0===null)return;const d=e.changedTouches[0].clientX-x0;if(Math.abs(d)>60)go(cur+(d<0?1:-1));x0=null},{passive:true});
addEventListener('keydown',e=>{if(e.key=='ArrowRight')go(cur+1);if(e.key=='ArrowLeft')go(cur-1)});
/* Saat: sadece görünürken güncelle; tarih/gün çubuğu dakikada bir */
function tick(){if(cur!==0||document.hidden)return;const n=new Date(),p=v=>String(v).padStart(2,'0');
 $('sec').textContent=p(n.getSeconds());const hm=p(n.getHours())+':'+p(n.getMinutes());
 if(hm!==lm){lm=hm;$('hm').textContent=hm;
  $('date').textContent=n.toLocaleDateString(LG==='en'?'en-US':'tr-TR',{weekday:'long',day:'numeric',month:'long',year:'numeric'});
  const d=(n.getHours()*3600+n.getMinutes()*60+n.getSeconds())/86400;$('dayfill').style.transform=`scaleX(${d})`;$('daytxt').textContent='%'+Math.round(d*100)}}
tick();setInterval(tick,1000);
/* ---- Arka plan: küçük canvas'a çiz, CSS ile büyüt (bulanık görünür, GPU blur yok) ---- */
function bgSize(){return Math.round((10+(100-S.blur)*.5)*[1,1,1.35][S.perf|0])}   // Hafif aynı; Yüksek daha ince çizim
function paint(c,im,sx,sy,sw0,sh0){const ar=innerWidth/innerHeight,h=bgSize(),w=Math.max(4,Math.round(h*ar));
 const mk=(a,b)=>{const k=document.createElement('canvas');k.width=a;k.height=b;const x=k.getContext('2d');x.imageSmoothingEnabled=true;x.imageSmoothingQuality='high';return [k,x]};
 const [k1,x1]=mk(w*4,h*4),[k2,x2]=mk(w,h);
 const iw=sw0||im.naturalWidth,ih=sh0||im.naturalHeight,sc=Math.max(w/iw,h/ih),sw=w/sc,sh=h/sc;
 x1.drawImage(im,(sx||0)+(iw-sw)/2,(sy||0)+(ih-sh)/2,sw,sh,0,0,w*4,h*4);x2.drawImage(k1,0,0,w,h);
 const d=x2.getImageData(0,0,w,h),p=d.data;
 for(let i=0;i<p.length;i+=4){const g=.3*p[i]+.59*p[i+1]+.11*p[i+2];p[i]=Math.min(255,g+(p[i]-g)*1.7);p[i+1]=Math.min(255,g+(p[i+1]-g)*1.7);p[i+2]=Math.min(255,g+(p[i+2]-g)*1.7)}
 x2.putImageData(d,0,0);
 c.width=w*4;c.height=h*4;const y=c.getContext('2d');y.imageSmoothingEnabled=true;y.imageSmoothingQuality='high';y.drawImage(k2,0,0,c.width,c.height)}
/* GIF: sunucunun hazırladığı kare şeridi canvas'a sırayla çizilir (bulanıklık ayarı da çalışır) */
let animTok=0;
function stopAnim(){animTok++;clearTimeout(gifT);gifT=null}
async function startAnim(c){stopAnim();if(!(S.anim&&S.mode=='image'))return;const tok=animTok;
 try{const meta=await (await fetch('/api/bgsheet.json?v='+S.ver)).json();const im=new Image();im.src='/api/bgsheet?v='+S.ver;await im.decode();if(tok!==animTok)return;bgImg=null;
  let k=0;const minMs=[120,80,55][S.perf|0]||80;
  const step=()=>{if(tok!==animTok)return;
   if(!document.hidden&&getComputedStyle(c).display!=='none')paint(c,im,(k%meta.cols)*meta.fw,((k/meta.cols)|0)*meta.fh,meta.fw,meta.fh);
   const d=Math.max(minMs,meta.durs[k]||100);k=(k+1)%meta.n;gifT=setTimeout(step,d)};step()}catch(e){}}
function setBg(u){stopAnim();bi^=1;const a=bgs[bi],o=bgs[bi^1];
 if(!u){stopAnim();a.style.opacity=0;o.style.opacity=0;bgImg=null;return}
 const im=new Image();im.onload=()=>{if(bgs[bi]!==a)return;bgImg=im;paint(a,im);a.style.opacity=1;o.style.opacity=0;startAnim(a)};im.src=u}
function repaintSoon(){clearTimeout(rp);rp=setTimeout(()=>{if(bgImg)paint(bgs[bi],bgImg)},150)}
addEventListener('resize',repaintSoon);
let S={mode:'cover',preset:0,color:'#0b0d12',blur:55,dim:62,ver:0,has_image:false,theme:'glass',perf:1,kscale:100,uscale:100,tc:{}},coverUrl=null,curBg='x',lastBlur=55,sj='';
const PR=['radial-gradient(60vmax 60vmax at 15% 20%,#3b4cca99,transparent),radial-gradient(60vmax 60vmax at 85% 80%,#b0369a88,transparent),#0b0d12',
 'radial-gradient(60vmax 60vmax at 80% 15%,#14b8a688,transparent),radial-gradient(60vmax 60vmax at 10% 90%,#2563eb77,transparent),#06121a',
 'radial-gradient(60vmax 60vmax at 20% 15%,#f97316aa,transparent),radial-gradient(60vmax 60vmax at 85% 85%,#7c3aed88,transparent),#140a14',
 'radial-gradient(70vmax 70vmax at 50% 0%,#0ea5e9aa,transparent),#020617','linear-gradient(160deg,#2a2d36,#0b0d12)'];
function applyTheme(){const h=document.documentElement;const tc=(S.tc||{})[S.theme];if(tc)h.style.setProperty('--tc',tc);else h.style.removeProperty('--tc');h.dataset.theme=S.theme;h.dataset.perf=S.perf;h.style.setProperty('--ks',S.kscale/100);h.style.setProperty('--us',S.uscale/100)}
function applyBg(){const r=document.documentElement.style;
 let u=null,g=PR[S.preset]||PR[0];if(S.mode=='image'&&S.has_image)u='/api/bgimage?v='+S.ver;else if(S.mode=='cover')u=coverUrl;
 if(S.mode=='color')g=S.color;r.setProperty('--grad',g);r.setProperty('--shade',u?1-S.dim/100:0);
 if(u!==curBg){curBg=u;setBg(u)}else if(S.blur!==lastBlur)repaintSoon();lastBlur=S.blur}
async function loadSet(){if($('sheet').style.display=='flex'||document.hidden)return;
 try{const t=await (await fetch('/api/settings')).text();if(t===sj)return;sj=t;S=JSON.parse(t);if(S.lang!==document.documentElement.dataset.lang){location.reload();return}applyTheme();applyBg()}catch(e){}}
let sv=null,pend={};
function saveSet(p){Object.assign(S,p);applyTheme();applyBg();Object.assign(pend,p);clearTimeout(sv);
 sv=setTimeout(()=>{fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pend)});pend={}},250)}
function mark(){document.querySelectorAll('#sheet [data-m]').forEach(b=>b.classList.toggle('on',b.dataset.m==S.mode));
 document.querySelectorAll('#sheet [data-t]').forEach(b=>b.classList.toggle('on',b.dataset.t==S.theme));
 document.querySelectorAll('#sheet [data-f]').forEach(b=>b.classList.toggle('on',b.dataset.f==String(S.perf)));
 document.querySelectorAll('#sheet [data-l]').forEach(b=>b.classList.toggle('on',b.dataset.l==S.lang))}
function openSheet(){$('sheet').style.display='flex';$('dc').style.display=window.DeckApp?'block':'none';$('rb').value=S.blur;$('rd').value=S.dim;
 $('rk').value=S.kscale;$('ru').value=S.uscale;$('vk').textContent=S.kscale;$('vu').textContent=S.uscale;
 $('th').innerHTML=TH.map(([k,n])=>`<button data-t="${k}">${n}</button>`).join('');
 $('pf').innerHTML=PF.map(([k,n])=>`<button data-f="${k}">${n}</button>`).join('');
 $('lg').innerHTML=[['auto','Auto'],['tr','Türkçe'],['en','English']].map(([k,n])=>`<button data-l="${k}">${n}</button>`).join('');
 $('pre').innerHTML=PR.map((g,i)=>`<button data-p="${i}" style="background:${g};flex:0 0 44px;height:44px;padding:0"></button>`).join('');mark()}
$('sheet').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
 if(b.dataset.m){if(b.dataset.m=='image'&&!S.has_image){b.textContent="PC'den yükle";return}saveSet({mode:b.dataset.m});mark()}
 if(b.dataset.p!==undefined){saveSet({preset:+b.dataset.p,mode:'preset'});mark()}
 if(b.dataset.t){saveSet({theme:b.dataset.t});mark()}
 if(b.dataset.f!==undefined){saveSet({perf:+b.dataset.f});mark()}
 if(b.dataset.l){saveSet({lang:b.dataset.l});mark();setTimeout(()=>location.reload(),900)}});
$('rb').oninput=e=>saveSet({blur:+e.target.value});$('rd').oninput=e=>saveSet({dim:+e.target.value});
$('rk').oninput=e=>{$('vk').textContent=e.target.value;saveSet({kscale:+e.target.value})};
$('ru').oninput=e=>{$('vu').textContent=e.target.value;saveSet({uscale:+e.target.value})};
['touchstart','touchend'].forEach(t=>$('sheet').addEventListener(t,e=>e.stopPropagation(),{passive:true}));
document.querySelectorAll('#sheet input[type=range]').forEach(r=>{const w=document.createElement('div');w.className='sl';r.parentNode.insertBefore(w,r);w.appendChild(r);
 let x0=null,y0=0,v0=0,on=false;
 w.addEventListener('pointerdown',e=>{x0=e.clientX;y0=e.clientY;v0=+r.value;on=false});
 w.addEventListener('pointermove',e=>{if(x0===null)return;const dx=e.clientX-x0,dy=e.clientY-y0;
  if(!on){if(Math.abs(dx)>10&&Math.abs(dx)>Math.abs(dy)*1.5){on=true;try{w.setPointerCapture(e.pointerId)}catch(_){}}else return}
  const mn=+r.min,mx=+r.max,st=+r.step||1;let v=v0+dx/w.getBoundingClientRect().width*(mx-mn);v=Math.max(mn,Math.min(mx,Math.round(v/st)*st));
  if(v!=+r.value){r.value=v;r.dispatchEvent(new Event('input'))}});
 ['pointerup','pointercancel','lostpointercapture'].forEach(t=>w.addEventListener(t,()=>{x0=null;on=false}))});
let lp;const pg=$('pages').firstElementChild;
pg.addEventListener('touchstart',()=>{lp=setTimeout(openSheet,650)},{passive:true});
['touchend','touchmove','touchcancel'].forEach(t=>pg.addEventListener(t,()=>clearTimeout(lp),{passive:true}));
pg.addEventListener('contextmenu',e=>{e.preventDefault();openSheet()});
function m(c){fetch('/api/media/'+c,{method:'POST'});setTimeout(poll,400)}
async function poll(){if(document.hidden)return;try{const d=await (await fetch('/api/media')).json();const mini=$('mini');
 if(!d.active){$('src').style.display='none';$('title').textContent='Çalan bir şey yok';$('artist').textContent="Spotify veya YouTube'dan bir şey aç";$('cover').style.backgroundImage='';$('cover').textContent='🎵';coverUrl=null;applyBg();end=0;$('fill').style.transform='scaleX(0)';mini.style.display='none';last='';return}
 $('title').textContent=d.title||'—';$('src').style.display='flex';$('srct').textContent=(d.src||'')+(d.n>1?' · '+d.idx+'/'+d.n:'');$('src').classList.toggle('one',!(d.n>1));$('artist').textContent=d.artist||'';$('pp').innerHTML=ico(d.playing?'pause':'play');
 mini.style.display='block';mini.textContent=(d.playing?'♪ ':'‖ ')+(d.title||'')+(d.artist?' · '+d.artist:'');
 const now=Date.now(),ex=pos+(playing?(now-t0)/1000:0);   // yerel saati koru, sadece parça/durum değişince ya da sapma >2.5 sn ise düzelt
 if(sk===null&&(d.key!==last||d.playing!==playing||Math.abs(d.pos-ex)>2.5)){pos=d.pos;t0=now}
 end=d.end;playing=d.playing;canSeek=d.seekable!==false;sb.classList.toggle('ro',!canSeek);
 if(d.key!==last){last=d.key;if(d.cover){const u='/api/cover?'+Date.now();$('cover').style.backgroundImage=`url(${u})`;$('cover').textContent='';coverUrl=u;applyBg()}else{$('cover').style.backgroundImage='';$('cover').textContent='🎵';coverUrl=null;applyBg()}}
 prog()}catch(e){}}
const f=s=>{s=Math.max(0,s|0);return (s/60|0)+':'+String(s%60).padStart(2,'0')};
function cycleSrc(){fetch('/api/mediasrc',{method:'POST'}).then(()=>setTimeout(poll,150))}
let sk=null,canSeek=true;
function prog(){if(cur!==1||end<=0||document.hidden)return;
 const p=sk!==null?sk*end:Math.min(end,pos+(playing?(Date.now()-t0)/1000:0)),r=p/end;
 $('fill').style.transform=`scaleX(${r})`;$('knob').style.left=r*100+'%';$('t1').textContent=f(p);$('t2').textContent=f(end)}
const sb=$('seek'),fr=e=>{const b=$('bar').getBoundingClientRect();return Math.max(0,Math.min(1,(e.clientX-b.left)/b.width))};
sb.addEventListener('pointerdown',e=>{if(end<=0||!canSeek)return;sb.setPointerCapture(e.pointerId);sb.classList.add('on');sk=fr(e);prog()});
sb.addEventListener('pointermove',e=>{if(sk===null)return;sk=fr(e);prog()});
function endSeek(ok){if(sk===null)return;const t=sk*end;sk=null;sb.classList.remove('on');if(!ok)return;pos=t;t0=Date.now();
 fetch('/api/seek',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pos:t})});setTimeout(poll,700);prog()}
sb.addEventListener('pointerup',()=>endSeek(true));sb.addEventListener('pointercancel',()=>endSeek(false));
['touchstart','touchend'].forEach(t=>sb.addEventListener(t,e=>e.stopPropagation(),{passive:true}));
let wj='';
const esc=x=>String(x).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function loadWins(){fetch('/api/windows',{cache:'no-store'}).then(r=>r.json()).then(a=>{const j=JSON.stringify(a);if(j===wj)return;wj=j;
 $('wempty').style.display=a.length?'none':'block';
 $('wgrid').innerHTML=a.map(w=>`<button class="k w${w.active?' act':''}" onclick="focusWin(${w.id},this)"><img src="/api/winicon/${w.key}" onerror="this.style.visibility='hidden'" alt=""><b>${esc(w.name)}</b></button>`).join('')}).catch(()=>{})}
function focusWin(id,el){el.classList.add('hit');setTimeout(()=>el.classList.remove('hit'),160);fetch('/api/focus/'+id,{method:'POST'}).then(()=>setTimeout(loadWins,350));navigator.vibrate&&navigator.vibrate(15)}
setInterval(()=>{if(cur===3&&!document.hidden)loadWins()},2000);
let ws=-1,wb=-1,down=false;
async function watch(){for(;;){try{const c=new AbortController(),to=setTimeout(()=>c.abort(),30000);
 const d=await (await fetch('/api/wait?v='+(ws+wb),{cache:'no-store',signal:c.signal})).json();clearTimeout(to);
 if(down){location.reload();return}
 if(ws!==-1){if(d.s!==ws)loadSet();if(d.b!==wb)loadBtns()}
 ws=d.s;wb=d.b}catch(e){if(!down){down=true;$('offb').style.display=window.DeckApp?'':'none';$('off').style.display='flex'}await new Promise(r=>setTimeout(r,1500))}}}
setInterval(prog,500);setInterval(poll,2500);poll();loadSet();setInterval(loadSet,30000);watch();
let bj='';
const icHtml=v=>v&&v.startsWith('ic:')?`<svg class="ic"><use href="#ic-${v.slice(3)}"/></svg>`:`<span>${v||'⬛'}</span>`;
function loadBtns(){fetch('/api/buttons').then(r=>r.text()).then(t=>{if(t===bj)return;bj=t;const b=JSON.parse(t);
 $('grid').innerHTML=b.map((k,i)=>`<button class="k" style="${k.color&&k.color!='#1d2231'?'--c:'+k.color:''}" onclick="press(${i},this)">${k.img?`<img src="/api/icon/${k.img}">`:`${icHtml(k.icon)}`}${k.label}</button>`).join('')})}
function press(i,el){el.classList.add('hit');setTimeout(()=>el.classList.remove('hit'),140);fetch('/api/press/'+i,{method:'POST'});navigator.vibrate&&navigator.vibrate(15)}
document.addEventListener('dblclick',()=>document.documentElement.requestFullscreen&&document.documentElement.requestFullscreen());
async function lock(){try{navigator.wakeLock&&await navigator.wakeLock.request('screen')}catch(e){}}lock();
document.addEventListener('visibilitychange',()=>{if(!document.hidden){lock();poll();loadSet();tick()}});
</script></body></html>"""

ADMIN = r"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>Phone Deck</title><style>
:root{--ac:#7c9cff;--bd:rgba(255,255,255,.1);--tx:#e8ecf4;--mut:#8d96ad}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;display:flex;color:var(--tx);font:14px/1.4 "Segoe UI",system-ui,sans-serif;overflow:hidden;background:radial-gradient(900px 600px at 8% -5%,#2c3c9a55,transparent),radial-gradient(800px 600px at 100% 105%,#8a2b7a44,transparent),#0b0d12}
button{font:inherit;border:0;cursor:pointer;color:inherit;background:none;padding:0}
input:not([type=range]):not([type=color]):not([type=file]){width:100%;padding:10px 12px;border-radius:12px;border:1px solid var(--bd);background:rgba(255,255,255,.05);color:var(--tx);font:inherit;outline:none;transition:.15s}
input:not([type=range]):not([type=color]):not([type=file]):focus{border-color:var(--ac);background:rgba(124,156,255,.08)}
::-webkit-scrollbar{width:10px}::-webkit-scrollbar-thumb{background:rgba(255,255,255,.14);border-radius:10px;border:2px solid transparent;background-clip:padding-box}
#side{width:228px;flex:none;display:flex;flex-direction:column;gap:4px;padding:18px 14px;border-right:1px solid var(--bd);background:rgba(255,255,255,.03)}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:16px;padding:2px 8px 18px}
.brand i{width:32px;height:32px;border-radius:10px;background:linear-gradient(135deg,#7c9cff,#b36bff);display:grid;place-items:center;box-shadow:0 6px 16px rgba(124,156,255,.35)}.brand svg{width:17px;height:17px;fill:#fff}
.nav{display:flex;align-items:center;gap:11px;width:100%;padding:11px 12px;border-radius:12px;color:var(--mut);font-weight:600;text-align:left;transition:.15s}
.nav svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;flex:none}
.nav:hover{background:rgba(255,255,255,.06);color:var(--tx)}.nav.on{color:#fff;background:rgba(124,156,255,.18);box-shadow:inset 0 0 0 1px rgba(124,156,255,.4)}
.foot{margin-top:auto;display:flex;flex-direction:column;gap:8px}
.chipb{display:flex;flex-direction:column;align-items:flex-start;gap:2px;width:100%;padding:10px 12px;border-radius:12px;background:rgba(255,255,255,.05);box-shadow:inset 0 0 0 1px var(--bd);color:var(--mut);font-size:12px;text-align:left;position:relative}
button.chipb:hover{background:rgba(255,255,255,.09)}
#addr{font:600 13px Consolas,monospace;color:#9fb4ff;user-select:text}#eye{position:absolute;right:10px;top:50%;margin-top:-9px}
#eye svg{width:18px;height:18px;fill:none;stroke:var(--mut);stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
main{flex:1;min-width:0;overflow:auto;padding:26px 30px 40px}.tab{display:none}.tab.on{display:block}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:18px}h1{margin:0 0 2px;font-size:24px}p.s{color:var(--mut);margin:0}
.card{background:linear-gradient(135deg,rgba(255,255,255,.09),rgba(255,255,255,.03));border:1px solid var(--bd);border-radius:20px;padding:20px 22px;box-shadow:inset 0 1px 0 rgba(255,255,255,.12),0 10px 30px rgba(0,0,0,.25)}
h2.c{margin:0 0 14px;font-size:15px;font-weight:650}
.cols{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:18px;align-items:start}.ed{position:sticky;top:0}
.cards2{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:18px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:10px 16px;border-radius:12px;background:linear-gradient(135deg,#7c9cff,#a78bfa);color:#0b0d12;font-weight:700;transition:.15s}
.btn:hover{filter:brightness(1.1)}.btn.g{background:rgba(255,255,255,.08);color:var(--tx);box-shadow:inset 0 0 0 1px var(--bd)}
.btn.d{background:rgba(255,90,110,.16);color:#ff9aa8;box-shadow:inset 0 0 0 1px rgba(255,90,110,.35)}.btn.sm{padding:7px 12px;font-size:13px}
.fb{position:relative;overflow:hidden}.fb input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%}
.fl{font-size:12px;color:var(--mut);margin:14px 0 6px}.fr{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.fr.end{margin-top:18px}
#grid{display:grid;grid-template-columns:repeat(auto-fill,108px);gap:14px}
.k{height:108px;border-radius:18px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;cursor:pointer;text-align:center;padding:4px;border:2px solid transparent;
 background:linear-gradient(145deg,var(--c,#2b3140),color-mix(in srgb,var(--c,#2b3140) 72%,#000));box-shadow:inset 0 1px 0 rgba(255,255,255,.22),0 6px 16px rgba(0,0,0,.3);transition:.12s}
.k:hover{transform:translateY(-2px)}.k.sel{border-color:#fff}
.k em,.pk em{font-style:normal;font-size:12px;max-width:94%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.k span,.pk span{font-size:32px;line-height:1}.k img,.pk img{width:46px;height:46px;object-fit:contain;border-radius:11px;filter:drop-shadow(0 3px 6px rgba(0,0,0,.35))}
.k.add{background:none;border:2px dashed rgba(255,255,255,.2);box-shadow:none;color:var(--mut);font-size:30px}
.edh{display:flex;align-items:center;gap:12px;margin-bottom:2px}
.pvk{width:54px;height:54px;border-radius:15px;display:grid;place-items:center;background:linear-gradient(145deg,var(--c,#2b3140),color-mix(in srgb,var(--c,#2b3140) 70%,#000))}
.pvk img{width:34px;height:34px;object-fit:contain}.pvk span{font-size:26px}.empty{color:var(--mut);padding:24px 6px;text-align:center}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{display:inline-flex;align-items:center;gap:8px;padding:8px 13px;border-radius:999px;background:rgba(255,255,255,.06);box-shadow:inset 0 0 0 1px var(--bd);color:var(--tx);font-size:13px;transition:.15s}
.chip:hover{background:rgba(255,255,255,.11)}.chip.on{background:rgba(124,156,255,.22);box-shadow:inset 0 0 0 1.5px var(--ac)}
.chip i{width:16px;height:16px;border-radius:50%;box-shadow:inset 0 0 0 1px rgba(255,255,255,.3)}
.chips.sw .chip{padding:5px}.chips.sw .chip i{width:34px;height:34px}.chips.sw .chip.on{box-shadow:0 0 0 2px var(--ac)}
.sws{display:flex;flex-wrap:wrap;gap:8px}.sw1{width:28px;height:28px;border-radius:50%;box-shadow:inset 0 0 0 1px rgba(255,255,255,.28);color:#fff;font-size:16px;display:grid;place-items:center}
.sw1.on{box-shadow:0 0 0 2px #0b0d12,0 0 0 4px #fff}.sw1.cust{background:conic-gradient(red,yellow,lime,cyan,blue,magenta,red)}
.hid{position:absolute;width:0;height:0;opacity:0;pointer-events:none}
.rg{margin-top:8px}
input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:6px;border-radius:6px;background:linear-gradient(90deg,var(--ac) var(--p,50%),rgba(255,255,255,.14) var(--p,50%));outline:none;padding:0;margin:10px 0;cursor:pointer}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;background:#fff;box-shadow:0 2px 8px rgba(0,0,0,.5)}
.iprow{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:12px;background:rgba(255,255,255,.05);margin-top:8px}
.iprow code{font:600 14px Consolas,monospace;color:#9fb4ff;flex:1;user-select:text}.tag{font-size:11px;padding:2px 8px;border-radius:999px;background:rgba(111,227,161,.18);color:#6fe3a1}
#gal{position:fixed;inset:0;background:rgba(5,7,14,.72);backdrop-filter:blur(6px);display:none;align-items:center;justify-content:center;z-index:50}
#gc{width:min(860px,94vw);max-height:88vh;display:flex;flex-direction:column;gap:12px;background:#12162a;border:1px solid var(--bd);border-radius:22px;padding:22px;box-shadow:0 30px 80px rgba(0,0,0,.6)}
.gh{display:flex;justify-content:space-between;align-items:center}.gs{display:flex;gap:8px}#gbody{overflow:auto;flex:1;padding-right:6px}
#gbody h3{margin:16px 0 10px;font-size:12px;letter-spacing:.04em;color:var(--mut);font-weight:600}
.pg{display:grid;grid-template-columns:repeat(auto-fill,102px);gap:10px}
.pk{height:100px;border-radius:16px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;cursor:pointer;text-align:center;position:relative;padding:4px;border:1.5px solid transparent;transition:.12s;
 background:linear-gradient(145deg,color-mix(in srgb,var(--c,#3a4056) 45%,#10131c),color-mix(in srgb,var(--c,#3a4056) 20%,#10131c))}
.pk:hover{border-color:var(--ac);transform:translateY(-2px)}.ck{position:absolute;top:5px;right:8px;color:#6fe3a1;font-style:normal;font-weight:700}
.ic{width:1em;height:1em;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;flex:none}
.k svg.ic,.pk svg.ic{width:34px;height:34px}.pvk svg.ic{width:30px;height:30px}.icbtn svg{width:20px;height:20px}.sw1 svg{width:15px;height:15px}
#icp{position:fixed;inset:0;background:rgba(5,7,14,.72);backdrop-filter:blur(6px);display:none;align-items:center;justify-content:center;z-index:60}
#icc{width:min(580px,92vw);max-height:80vh;display:flex;flex-direction:column;gap:12px;background:#12162a;border:1px solid var(--bd);border-radius:22px;padding:20px}
#icgrid{display:grid;grid-template-columns:repeat(auto-fill,52px);gap:8px;overflow:auto;padding:2px}
#icgrid button{width:52px;height:52px;border-radius:14px;background:rgba(255,255,255,.06);display:grid;place-items:center;box-shadow:inset 0 0 0 1px var(--bd)}
#icgrid button:hover{background:rgba(124,156,255,.25)}#icgrid svg{width:26px;height:26px}
#msg{position:fixed;left:50%;bottom:26px;transform:translate(-50%,20px);padding:10px 18px;border-radius:999px;background:rgba(20,30,28,.94);border:1px solid rgba(111,227,161,.4);color:#6fe3a1;opacity:0;pointer-events:none;transition:.2s;z-index:80}
#msg.on{opacity:1;transform:translate(-50%,0)}
</style></head><body>
<svg width="0" height="0" style="position:absolute"><defs><symbol id="ic-playpause" viewBox="0 0 24 24"><path d="M3 5l7 7-7 7z"/><path d="M15 5v14M20 5v14"/></symbol><symbol id="ic-play" viewBox="0 0 24 24"><path d="M7 4l13 8-13 8z"/></symbol><symbol id="ic-pause" viewBox="0 0 24 24"><path d="M8 5v14M16 5v14"/></symbol><symbol id="ic-next" viewBox="0 0 24 24"><path d="M5 5l10 7-10 7z"/><path d="M19 5v14"/></symbol><symbol id="ic-prev" viewBox="0 0 24 24"><path d="M19 5L9 12l10 7z"/><path d="M5 5v14"/></symbol><symbol id="ic-volup" viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 010 6M19 6a8 8 0 010 12"/></symbol><symbol id="ic-voldown" viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 010 6"/></symbol><symbol id="ic-mute" viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M17 9.5l4.5 5M21.5 9.5l-4.5 5"/></symbol><symbol id="ic-desktop" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></symbol><symbol id="ic-switch" viewBox="0 0 24 24"><rect x="3" y="8" width="13" height="11" rx="2"/><path d="M8 8V6a2 2 0 012-2h9a2 2 0 012 2v9a2 2 0 01-2 2h-3"/></symbol><symbol id="ic-taskview" viewBox="0 0 24 24"><rect x="3" y="4" width="8" height="7" rx="1.5"/><rect x="13" y="4" width="8" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/></symbol><symbol id="ic-maximize" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2"/></symbol><symbol id="ic-minimize" viewBox="0 0 24 24"><path d="M5 18h14"/></symbol><symbol id="ic-snapl" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/><path d="M4 5h7v14H4z" fill="currentColor" stroke="none" opacity=".4"/></symbol><symbol id="ic-snapr" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/><path d="M13 5h7v14h-7z" fill="currentColor" stroke="none" opacity=".4"/></symbol><symbol id="ic-closewin" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></symbol><symbol id="ic-minall" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 15h8"/></symbol><symbol id="ic-newdesk" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4M12 7v6M9 10h6"/></symbol><symbol id="ic-nextdesk" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4M9 10h6M13 8l2 2-2 2"/></symbol><symbol id="ic-prevdesk" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4M15 10H9M11 8l-2 2 2 2"/></symbol><symbol id="ic-lock" viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/></symbol><symbol id="ic-project" viewBox="0 0 24 24"><rect x="2.5" y="5" width="12" height="9" rx="1.5"/><rect x="9.5" y="10" width="12" height="9" rx="1.5"/></symbol><symbol id="ic-copy" viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 00-2-2H6a2 2 0 00-2 2v8a2 2 0 002 2h2"/></symbol><symbol id="ic-paste" viewBox="0 0 24 24"><rect x="5" y="4" width="14" height="17" rx="2"/><rect x="9" y="2" width="6" height="4" rx="1"/></symbol><symbol id="ic-cut" viewBox="0 0 24 24"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.1 15.9M14.5 14.5L20 20M8.1 8.1L12 12"/></symbol><symbol id="ic-undo" viewBox="0 0 24 24"><path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/></symbol><symbol id="ic-redo" viewBox="0 0 24 24"><path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 000 12h3"/></symbol><symbol id="ic-selectall" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2" stroke-dasharray="3 3"/><path d="M9 12l2 2 4-4"/></symbol><symbol id="ic-save" viewBox="0 0 24 24"><path d="M5 3h11l4 4v13a1 1 0 01-1 1H5a1 1 0 01-1-1V4a1 1 0 011-1z"/><path d="M8 3v5h7V3M8 21v-7h8v7"/></symbol><symbol id="ic-find" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></symbol><symbol id="ic-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></symbol><symbol id="ic-print" viewBox="0 0 24 24"><path d="M7 9V3h10v6"/><rect x="4" y="9" width="16" height="8" rx="2"/><path d="M7 14h10v7H7z"/></symbol><symbol id="ic-newfile" viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5M12 12v6M9 15h6"/></symbol><symbol id="ic-file" viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></symbol><symbol id="ic-folder" viewBox="0 0 24 24"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></symbol><symbol id="ic-trash" viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13a1 1 0 001 1h8a1 1 0 001-1l1-13M9 7V4h6v3"/></symbol><symbol id="ic-enter" viewBox="0 0 24 24"><path d="M20 5v8a2 2 0 01-2 2H5M9 11l-4 4 4 4"/></symbol><symbol id="ic-esc" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/></symbol><symbol id="ic-newtab" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M12 13v4M10 15h4"/></symbol><symbol id="ic-closetab" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M10 13l4 4M14 13l-4 4"/></symbol><symbol id="ic-reopentab" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M12 17v-4M10 15l2-2 2 2"/></symbol><symbol id="ic-nexttab" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M9 15h6M13 13l2 2-2 2"/></symbol><symbol id="ic-prevtab" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M15 15H9M11 13l-2 2 2 2"/></symbol><symbol id="ic-incognito" viewBox="0 0 24 24"><path d="M3 13h18M5.5 13l2-8h9l2 8"/><circle cx="8" cy="17.5" r="2.5"/><circle cx="16" cy="17.5" r="2.5"/><path d="M10.5 17h3"/></symbol><symbol id="ic-reload" viewBox="0 0 24 24"><path d="M20 12a8 8 0 10-2.3 5.7"/><path d="M20 5v5h-5"/></symbol><symbol id="ic-hardreload" viewBox="0 0 24 24"><path d="M20 12a8 8 0 10-2.3 5.7M20 5v5h-5"/><path d="M12.5 8l-2.5 4h4l-2.5 4"/></symbol><symbol id="ic-fullscreen" viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></symbol><symbol id="ic-zoomin" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5M11 8v6M8 11h6"/></symbol><symbol id="ic-zoomout" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5M8 11h6"/></symbol><symbol id="ic-zoomreset" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/><circle cx="11" cy="11" r="1.6"/></symbol><symbol id="ic-back" viewBox="0 0 24 24"><path d="M19 12H5M11 6l-6 6 6 6"/></symbol><symbol id="ic-forward" viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></symbol><symbol id="ic-addrbar" viewBox="0 0 24 24"><rect x="3" y="8" width="18" height="8" rx="4"/><path d="M8 12h8"/></symbol><symbol id="ic-snip" viewBox="0 0 24 24"><path d="M6 3v13a2 2 0 002 2h13"/><path d="M3 6h13a2 2 0 012 2v13"/></symbol><symbol id="ic-camera" viewBox="0 0 24 24"><path d="M4 8a2 2 0 012-2h2l1.5-2h5L16 6h2a2 2 0 012 2v9a2 2 0 01-2 2H6a2 2 0 01-2-2z"/><circle cx="12" cy="12.5" r="3.5"/></symbol><symbol id="ic-clipboard" viewBox="0 0 24 24"><rect x="5" y="4" width="14" height="17" rx="2"/><rect x="9" y="2" width="6" height="4" rx="1"/><path d="M9 11h6M9 15h4"/></symbol><symbol id="ic-smile" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8.5 14a4 4 0 007 0M9 9.5h.01M15 9.5h.01"/></symbol><symbol id="ic-run" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 10l3 2-3 2M12.5 15H17"/></symbol><symbol id="ic-terminal" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 10l3 2-3 2M12.5 15H17"/></symbol><symbol id="ic-settings" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/></symbol><symbol id="ic-bell" viewBox="0 0 24 24"><path d="M6 16v-5a6 6 0 0112 0v5l2 2H4z"/><path d="M10 21h4"/></symbol><symbol id="ic-gamepad" viewBox="0 0 24 24"><rect x="3" y="7" width="18" height="11" rx="5"/><path d="M8 10.5v5M5.5 13h5M15.5 12h.01M18 14h.01"/></symbol><symbol id="ic-record" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5" fill="currentColor"/></symbol><symbol id="ic-pulse" viewBox="0 0 24 24"><path d="M3 12h4l2-6 4 12 2-6h6"/></symbol><symbol id="ic-app" viewBox="0 0 24 24"><rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/></symbol><symbol id="ic-globe" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 010 18M12 3a14 14 0 000 18"/></symbol><symbol id="ic-mail" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></symbol><symbol id="ic-home" viewBox="0 0 24 24"><path d="M4 11l8-7 8 7v8a1 1 0 01-1 1h-4v-6H9v6H5a1 1 0 01-1-1z"/></symbol><symbol id="ic-music" viewBox="0 0 24 24"><path d="M9 18V6l11-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/></symbol><symbol id="ic-video" viewBox="0 0 24 24"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3z"/></symbol><symbol id="ic-link" viewBox="0 0 24 24"><path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/></symbol><symbol id="ic-keyboard" viewBox="0 0 24 24"><rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></symbol><symbol id="ic-chat" viewBox="0 0 24 24"><path d="M4 5h16a1 1 0 011 1v10a1 1 0 01-1 1h-8l-5 4v-4H4a1 1 0 01-1-1V6a1 1 0 011-1z"/></symbol><symbol id="ic-phone" viewBox="0 0 24 24"><rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M11 18.5h2"/></symbol><symbol id="ic-code" viewBox="0 0 24 24"><path d="M9 7l-5 5 5 5M15 7l5 5-5 5M13.5 5l-3 14"/></symbol><symbol id="ic-cloud" viewBox="0 0 24 24"><path d="M7 18a4 4 0 01-.6-7.95A6 6 0 0118 9.5 4.3 4.3 0 0117.5 18z"/></symbol><symbol id="ic-cart" viewBox="0 0 24 24"><circle cx="9" cy="20" r="1.5"/><circle cx="17" cy="20" r="1.5"/><path d="M3 4h2.5l2.2 11h10l2-8H7"/></symbol><symbol id="ic-calendar" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></symbol><symbol id="ic-clock" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></symbol><symbol id="ic-pin" viewBox="0 0 24 24"><path d="M12 21s7-6.2 7-12a7 7 0 00-14 0c0 5.8 7 12 7 12z"/><circle cx="12" cy="9" r="2.5"/></symbol><symbol id="ic-bookmark" viewBox="0 0 24 24"><path d="M7 4h10a1 1 0 011 1v16l-6-4-6 4V5a1 1 0 011-1z"/></symbol><symbol id="ic-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/></symbol><symbol id="ic-moon" viewBox="0 0 24 24"><path d="M20 14.5A8.5 8.5 0 019.5 4 8.5 8.5 0 1020 14.5z"/></symbol><symbol id="ic-power" viewBox="0 0 24 24"><path d="M12 3v9"/><path d="M6.3 6.8a8 8 0 1011.4 0"/></symbol><symbol id="ic-rocket" viewBox="0 0 24 24"><path d="M5 19c0-3 1-4 3-4l1 1c0 2-1 3-4 3zM9 15l6-6a8 8 0 004-6 8 8 0 00-6 4l-6 6z"/><circle cx="15" cy="9" r="1.2"/></symbol><symbol id="ic-wrench" viewBox="0 0 24 24"><path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z"/></symbol><symbol id="ic-bulb" viewBox="0 0 24 24"><path d="M9 18h6M10 21h4M12 3a6 6 0 00-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0012 3z"/></symbol><symbol id="ic-heart" viewBox="0 0 24 24"><path d="M12 20s-8-5-8-11a4.5 4.5 0 018-2.8A4.5 4.5 0 0120 9c0 6-8 11-8 11z"/></symbol><symbol id="ic-user" viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0116 0"/></symbol><symbol id="ic-users" viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0113 0"/><circle cx="17" cy="9" r="2.5"/><path d="M17 14a5 5 0 015 5"/></symbol><symbol id="ic-image" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="M21 16l-5-5-8 9"/></symbol><symbol id="ic-mic" viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0014 0M12 18v3"/></symbol><symbol id="ic-headphones" viewBox="0 0 24 24"><path d="M4 15v-3a8 8 0 0116 0v3"/><rect x="3" y="14" width="4" height="7" rx="1.5"/><rect x="17" y="14" width="4" height="7" rx="1.5"/></symbol><symbol id="ic-wifi" viewBox="0 0 24 24"><path d="M3 9a14 14 0 0118 0M6 12.5a9 9 0 0112 0M9 16a4.5 4.5 0 016 0"/><circle cx="12" cy="19.5" r=".8" fill="currentColor"/></symbol><symbol id="ic-star" viewBox="0 0 24 24"><path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/></symbol><symbol id="ic-bolt" viewBox="0 0 24 24"><path d="M13 2L4 14h7l-1 8 9-12h-7z"/></symbol><symbol id="ic-book" viewBox="0 0 24 24"><path d="M5 4h11a3 3 0 013 3v13H8a3 3 0 01-3-3z"/><path d="M5 17a3 3 0 013-3h11"/></symbol><symbol id="ic-brush" viewBox="0 0 24 24"><path d="M18 3l3 3-9.5 9.5-3-3z"/><path d="M8.5 12.5C6 12.5 5 14 5 16c0 1.5-1 2.5-2 3 3 1 7 .5 8-2.5"/></symbol><symbol id="ic-send" viewBox="0 0 24 24"><path d="M21 3L3 11l7 3 3 7z"/><path d="M10 14l11-11"/></symbol><symbol id="ic-tag" viewBox="0 0 24 24"><path d="M3 12V4a1 1 0 011-1h8l9 9-9 9z"/><circle cx="8" cy="8" r="1.5"/></symbol><symbol id="ic-ban" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/></symbol><symbol id="ic-download" viewBox="0 0 24 24"><path d="M12 4v11M7 11l5 5 5-5M4 20h16"/></symbol><symbol id="ic-gem" viewBox="0 0 24 24"><path d="M6 4h12l3 5-9 11L3 9z"/><path d="M3 9h18M9 4l-1 5 4 11M15 4l1 5-4 11"/></symbol></defs></svg>
<aside id="side">
 <div class="brand"><i><svg viewBox="0 0 24 24"><path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"/></svg></i>Phone Deck</div>
 <button class="nav on" data-t="btns" onclick="tab('btns')"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></svg>Butonlar</button>
 <button class="nav" data-t="look" onclick="tab('look')"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="8" cy="10" r="1.2"/><circle cx="12" cy="7.5" r="1.2"/><circle cx="16" cy="10" r="1.2"/><path d="M12 21c-1.5 0-2-1-2-2 0-1.5 1.5-2 1.5-3.5S10 14 12 14h3a6 6 0 0 0 6-3"/></svg>Görünüm</button>
 <button class="nav" data-t="conn" onclick="tab('conn')"><svg viewBox="0 0 24 24"><path d="M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19" r="1"/></svg>Bağlantı</button>
 <div class="foot">
  <button id="ipchip" class="chipb" onclick="toggleIp()" title="Göster / gizle"><span>PC IP:</span><b id="addr">…</b><span id="eye"></span></button>
  <div id="usb" class="chipb">USB: …</div>
 </div>
</aside>
<main>
<section id="tab-btns" class="tab on">
 <div class="top"><div><h1>Butonlar</h1><p class="s">Butona tıkla ve düzenle, sürükleyerek sırala. Değişiklikler telefonda anında görünür.</p></div><button class="btn" onclick="openGal()">+ Hazır kısayol ekle</button></div>
 <div class="cols"><div class="card"><div id="grid"></div></div><div class="card ed"><div id="form"></div></div></div>
</section>
<section id="tab-look" class="tab">
 <div class="top"><div><h1>Görünüm</h1><p class="s">Telefondaki görünümü buradan ayarla. Telefonda saat ekranına basılı tutarak da değiştirebilirsin.</p></div></div>
 <div class="cards2">
  <div class="card"><h2 class="c">Tema</h2><select id="s_theme" class="ch"><option value="glass" data-pv="linear-gradient(135deg,#6a7cff,#c04fb0)">Liquid Glass</option><option value="frost" data-pv="linear-gradient(135deg,#8aa0c8,#5b6b8f)">Buzlu Cam</option><option value="amoled" data-pv="#000">AMOLED (saf siyah)</option><option value="neon" data-pv="linear-gradient(135deg,#22d3ee,#e879f9)">Neon</option><option value="light" data-pv="linear-gradient(135deg,#fff,#dfe5f0)">Açık</option><option value="flat" data-pv="#2b3140">Düz</option><option value="terminal" data-pv="linear-gradient(135deg,#041a0c,#39ff7a)">Terminal</option><option value="pastel" data-pv="linear-gradient(135deg,#ffd6e8,#d6e8ff)">Pastel</option><option value="mono" data-pv="linear-gradient(135deg,#000,#fff)">Mono</option><option value="mocha" data-pv="linear-gradient(135deg,#6b4630,#e0b48a)">Mocha</option></select>
   <div class="fl" style="margin-top:18px">Tema rengi</div><input type="color" id="s_tc"><div class="fr" style="margin-top:10px"><button class="btn g sm" id="tcreset">Varsayılan</button></div>
   <p class="s" style="font-size:12px;margin-top:10px">Düz temada rengi olmayan butonların, Neon ve Terminal temalarında çerçeve ve parlamanın rengi. Diğer temalarda buton tonu.</p></div>
  <div class="card"><h2 class="c">Arka plan</h2>
   <select id="s_mode" class="ch"><option value="cover">Albüm kapağı (otomatik)</option><option value="preset">Gradyan</option><option value="image">Kendi resmim</option><option value="color">Düz renk</option></select>
   <div id="m_preset"><div class="fl">Gradyan</div><select id="s_preset" class="ch sw"><option value="0" data-pv="linear-gradient(135deg,#3b4cca,#b0369a)">Mor-mavi</option><option value="1" data-pv="linear-gradient(135deg,#14b8a6,#2563eb)">Aurora</option><option value="2" data-pv="linear-gradient(135deg,#f97316,#7c3aed)">Gün batımı</option><option value="3" data-pv="linear-gradient(135deg,#0ea5e9,#020617)">Okyanus</option><option value="4" data-pv="linear-gradient(135deg,#2a2d36,#0b0d12)">Grafit</option></select></div>
   <div id="m_color"><div class="fl">Düz renk</div><input type="color" id="s_color"></div>
   <div id="m_image"><div class="fl">Kendi resmin</div><label class="btn g sm fb">Resim / GIF seç<input type="file" accept="image/*" id="s_file"></label><p class="s" style="font-size:12px;margin-top:8px">GIF desteklenir (en fazla 16 MB). Hareketli arka planda bulanıklık ayarı da çalışır.</p></div>
   <div class="rg"><div class="fl">Bulanıklık: <span id="vb"></span></div><input type="range" min="0" max="100" id="s_blur"></div>
   <div class="rg"><div class="fl">Karartma: <span id="vd"></span></div><input type="range" min="20" max="100" id="s_dim"></div>
   <p class="s" style="margin-top:12px;font-size:12px">Bazı temalar (AMOLED, Açık, Terminal, Pastel, Mono, Mocha) kendi arka planını kullanır.</p></div>
  <div class="card"><h2 class="c">Boyut ve performans</h2>
   <div class="rg"><div class="fl">Buton boyutu: <span id="vk"></span>%</div><input type="range" min="60" max="150" step="5" id="s_ks"></div>
   <div class="rg"><div class="fl">Saat / müzik boyutu: <span id="vu"></span>%</div><input type="range" min="60" max="130" step="5" id="s_us"></div>
   <div class="fl">Performans</div><select id="s_perf" class="ch"><option value="0">Hafif (en akıcı)</option><option value="1">Dengeli</option><option value="2">Yüksek (en güzel)</option></select><p class="s" style="font-size:12px;margin-top:10px">Dengeli: yumuşak geçişler. Yüksek: bulanıklık, hareketli arka plan ve parlama efektleri.</p></div>
  <div class="card"><h2 class="c">Dil / Language</h2><select id="s_lang" class="ch"><option value="auto">Auto</option><option value="tr">Türkçe</option><option value="en">English</option></select></div>
 </div>
</section>
<section id="tab-conn" class="tab">
 <div class="top"><div><h1>Bağlantı</h1></div></div>
 <div class="cards2">
  <div class="card"><h2 class="c">Bilgisayar adresi</h2><p class="s">Telefondaki uygulamaya bu adreslerden birini gir. Genelde ilkini kullan.</p><div id="iplist"></div></div>
  <div class="card"><h2 class="c">USB bağlantısı</h2><div id="usb2" class="chipb" style="margin-bottom:12px">USB: …</div><p class="s">Telefonda Geliştirici seçenekleri → USB hata ayıklamayı aç, kabloyu tak ve çıkan izne “İzin ver” de. Sonra telefon uygulamasında “USB ile bağlan”a bas.</p></div>
  <div class="card"><h2 class="c">Sistem</h2><div class="fr"><button class="btn g" onclick="fetch('/api/opendata',{method:'POST'})">Veri klasörünü aç</button><button class="btn d" onclick="if(confirm(t('Phone Deck tamamen kapatılsın mı? Telefon bağlantısı da kesilir.')))fetch('/api/quit',{method:'POST'})">Programı kapat</button></div></div>
 </div>
</section>
</main>
<div id="gal"><div id="gc">
 <div class="gh"><h2 class="c" style="margin:0">Hazır kısayollar</h2><button class="btn g sm" onclick="closeGal()">Kapat</button></div>
 <div class="gs"><input id="gq" placeholder="Ara… (spotify, kopyala, ses)"><button class="btn sm" style="white-space:nowrap" onclick="addBlank()">+ Boş buton</button></div>
 <div class="chips" id="gtabs"></div><div id="gbody"></div>
</div></div>
<div id="icp"><div id="icc"><div class="gh"><h2 class="c" style="margin:0">İkon seç</h2><button class="btn g sm" onclick="closeIcp()">Kapat</button></div><div id="icgrid"></div></div></div>
<div id="msg"></div>
<script>
const EN={"Tema": "Theme", "Dil": "Language", "AMOLED ve Açık temalar kendi arka planını kullanır.": "AMOLED and Light themes use their own background.", "Performans": "Performance", "Telefon kasıyorsa \"Hafif\" seç.": "If the phone lags, choose \"Light\".", "Ölçek": "Scale", "Buton boyutu:": "Button size:", "Saat / müzik boyutu:": "Clock / music size:", "Arka plan": "Background", "Kapak": "Cover", "Gradyan": "Gradient", "Resim": "Image", "Bulanıklık": "Blur", "Karartma": "Dim", "Bağlantıyı kes": "Disconnect", "Tamam": "Done", "Gün ilerlemesi": "Day progress", "Çalan bir şey yok": "Nothing playing", "Spotify veya YouTube'dan bir şey aç": "Play something on Spotify or YouTube", "PC'den yükle": "Upload from PC", "Buzlu Cam": "Frosted Glass", "Açık": "Light", "Düz": "Flat", "Hafif": "Light", "Dengeli": "Balanced", "Yüksek": "High", "Bağlantı koptu": "Connection lost", "Bilgisayar yeniden bağlanınca otomatik devam edilecek.": "It will resume automatically when the computer is back.", "Bilgisayar seç": "Choose computer", "Phone Deck · Atamalar": "Phone Deck · Shortcuts", "Telefon adresi:": "Phone address:", "Telefondaki uygulamaya bu adresi gir": "Enter this address in the phone app", "Programı kapat": "Quit program", "Butona tıkla ve düzenle. Sürükleyip bırakarak yerini değiştir. Değişiklikler telefonda anında görünür.": "Click a button to edit it. Drag and drop to reorder. Changes appear on the phone instantly.", "ile hazır ayarlardan (Spotify, YouTube, Discord…) tek tıkla ekleyebilirsin.": "lets you add one from the presets (Spotify, YouTube, Discord…) with a single click.", "Telefonun arka planını buradan ayarla. Telefonda saat ekranına basılı tutarak da değiştirebilirsin.": "Set the phone background here. You can also change it by long-pressing the clock screen on the phone.", "+ Boş buton": "+ Blank button", "Ara… (spotify, kopyala, ses)": "Search… (spotify, copy, volume)", "Kapat": "Close", "Sonuç yok": "No results", "Görünüm ve arka plan": "Appearance & background", "Mod": "Mode", "Albüm kapağı (otomatik)": "Album cover (automatic)", "Kendi resmim": "My own image", "Düz renk": "Solid color", "Mor-mavi": "Purple-blue", "Gün batımı": "Sunset", "Okyanus": "Ocean", "Grafit": "Graphite", "Bulanıklık:": "Blur:", "Karartma:": "Dim:", "Kendi resmin": "Your image", "AMOLED (saf siyah)": "AMOLED (pure black)", "Hafif (en akıcı)": "Light (smoothest)", "Yüksek (en güzel)": "High (prettiest)", "İsim": "Name", "İkon (emoji)": "Icon (emoji)", "Görsel ikon (PNG / JPG, emojinin yerine geçer)": "Image icon (PNG / JPG, replaces the emoji)", "Kaldır": "Remove", "Renk": "Color", "Eylem": "Action", "Program / site aç": "Open app / site", "Tuş kombinasyonu": "Key combination", "Müzik / ses": "Music / volume", "Yazı yaz": "Type text", "Kaydet": "Save", "Sil": "Delete", "Düzenlemek için bir butona tıkla ya da + ile yenisini ekle.": "Click a button to edit it, or add a new one with +.", "Program adı / yolu veya site adresi": "App name / path or website address", "Tuşlar (örn: ctrl+shift+s)": "Keys (e.g. ctrl+shift+s)", "🎹 Tuşa basarak kaydet": "🎹 Record by pressing keys", "Komut": "Command", "Yazılacak metin": "Text to type", "Oynat / Duraklat": "Play / Pause", "Duraklat": "Pause", "Oynat": "Play", "Sonraki şarkı": "Next track", "Önceki şarkı": "Previous track", "Sessiz": "Mute", "Ses +": "Volume +", "Ses -": "Volume -", "Kaydedildi ✓": "Saved ✓", "Resim yüklendi ✓": "Image uploaded ✓", "Silinsin mi?": "Delete this?", "Tuşlara bas…": "Press the keys…", "Phone Deck tamamen kapatılsın mı? Telefon bağlantısı da kesilir.": "Quit Phone Deck completely? The phone will be disconnected.", "Uygulamalar": "Apps", "Siteler": "Sites", "Müzik ve ses": "Music & volume", "Kısayollar · Pencere ve masaüstü": "Shortcuts · Windows & desktops", "Kısayollar · Düzenleme": "Shortcuts · Editing", "Kısayollar · Tarayıcı": "Shortcuts · Browser", "Kısayollar · Windows araçları": "Shortcuts · Windows tools", "Haritalar": "Maps", "Çeviri": "Translate", "Takvim": "Calendar", "Not Defteri": "Notepad", "Hesap Mak.": "Calculator", "Dosyalar": "Files", "Ayarlar": "Settings", "Görev Yön.": "Task Manager", "Komut İstemi": "Command Prompt", "Ekran Alıntısı": "Screen Snip", "Masaüstü": "Desktop", "Pencere Değiş": "Switch Window", "Görev Görünümü": "Task View", "Büyüt": "Maximize", "Küçült": "Minimize", "Sola Yasla": "Snap Left", "Sağa Yasla": "Snap Right", "Pencereyi Kapat": "Close Window", "Tümünü Küçült": "Minimize All", "Yeni Masaüstü": "New Desktop", "Sonraki Masaüstü": "Next Desktop", "Önceki Masaüstü": "Previous Desktop", "Kilitle": "Lock", "Ekranı Yansıt": "Project", "Kopyala": "Copy", "Yapıştır": "Paste", "Kes": "Cut", "Geri Al": "Undo", "Yinele": "Redo", "Tümünü Seç": "Select All", "Bul": "Find", "Yazdır": "Print", "Yeni": "New", "Aç": "Open", "Yeni Sekme": "New Tab", "Sekmeyi Kapat": "Close Tab", "Kapatılanı Aç": "Reopen Tab", "Sonraki Sekme": "Next Tab", "Önceki Sekme": "Previous Tab", "Gizli Pencere": "Incognito", "Yenile": "Reload", "Zorla Yenile": "Hard Reload", "Tam Ekran": "Fullscreen", "Yakınlaştır": "Zoom In", "Uzaklaştır": "Zoom Out", "Zoom Sıfırla": "Reset Zoom", "Geri": "Back", "İleri": "Forward", "Adres Çubuğu": "Address Bar", "Ekran Görüntüsü": "Screenshot", "Pano Geçmişi": "Clipboard", "Arama": "Search", "Çalıştır": "Run", "Dosya Gezgini": "File Explorer", "Bildirimler": "Notifications", "Oyun Çubuğu": "Game Bar", "Ekran Kaydı": "Screen Record", "Büyüteç": "Magnifier", "Oynat/Duraklat": "Play/Pause", "Sonraki": "Next", "Önceki": "Previous", "Açık uygulamalar": "Running apps", "Açık uygulama yok": "No running apps", "Pencere listesi yalnızca Windows'ta çalışır.": "The window list only works on Windows.", "USB: adb bulunamadı": "USB: adb not found", "USB: cihaz yok": "USB: no device", "USB: telefonda izin ver": "USB: allow on the phone", "USB: bağlı ✓": "USB: connected ✓", "Veri klasörü": "Data folder", "Telefonu USB ile bağla, USB hata ayıklamayı aç, telefon uygulamasında “USB ile bağlan”a bas.": "Plug in the phone, turn on USB debugging, then tap “Connect via USB” in the phone app.", "Android SDK platform-tools (adb) bulunamadı. Android Studio kurulu olmalı.": "Android SDK platform-tools (adb) not found. Android Studio must be installed.", "Butonlar": "Buttons", "Görünüm": "Appearance", "Bağlantı": "Connection", "Butona tıkla ve düzenle, sürükleyerek sırala. Değişiklikler telefonda anında görünür.": "Click a button to edit it, drag to reorder. Changes appear on the phone instantly.", "+ Hazır kısayol ekle": "+ Add from presets", "Hazır kısayollar": "Presets", "Görsel ikon": "Image icon", "Resim seç": "Choose image", "Renksiz": "No color", "Özel renk": "Custom color", "Telefondaki görünümü buradan ayarla. Telefonda saat ekranına basılı tutarak da değiştirebilirsin.": "Set the phone's look here. You can also change it by long-pressing the clock screen on the phone.", "Boyut ve performans": "Size & performance", "Bilgisayar adresi": "Computer address", "Telefondaki uygulamaya bu adreslerden birini gir. Genelde ilkini kullan.": "Enter one of these addresses in the phone app. Usually the first one works.", "Kopyalandı ✓": "Copied ✓", "Önerilen": "Recommended", "Göster / gizle": "Show / hide", "USB bağlantısı": "USB connection", "Telefonda Geliştirici seçenekleri → USB hata ayıklamayı aç, kabloyu tak ve çıkan izne “İzin ver” de. Sonra telefon uygulamasında “USB ile bağlan”a bas.": "On the phone, turn on Developer options → USB debugging, plug in the cable and tap “Allow”. Then tap “Connect via USB” in the phone app.", "Sistem": "System", "Veri klasörünü aç": "Open data folder", "Tümü": "All", "Kısayollar": "Shortcuts", "İkon": "Icon", "Değiştir": "Change", "İkon seç": "Choose icon", "Tuşa basarak kaydet": "Record by pressing keys", "Tema rengi": "Theme color", "Varsayılan": "Default", "Düz temada rengi olmayan butonların, Neon ve Terminal temalarında çerçeve ve parlamanın rengi. Diğer temalarda buton tonu.": "Flat: color of buttons without their own color. Neon and Terminal: border and glow color. Other themes: button tint.", "Dengeli: yumuşak geçişler. Yüksek: bulanıklık, hareketli arka plan ve parlama efektleri.": "Balanced: smooth transitions. High: blur, animated background and shine effects.", "Resim / GIF seç": "Choose image / GIF", "GIF desteklenir (en fazla 16 MB). Hareketli arka planda bulanıklık ayarı da çalışır.": "GIFs are supported (max 16 MB). The blur setting also works on animated backgrounds.", "GIF çok büyük (en fazla 16 MB)": "GIF is too large (max 16 MB)", "Bazı temalar (AMOLED, Açık, Terminal, Pastel, Mono, Mocha) kendi arka planını kullanır.": "Some themes (AMOLED, Light, Terminal, Pastel, Mono, Mocha) use their own background.", "Tarayıcılar": "Browsers", "Geliştirme": "Development", "Oyun": "Games", "Yapay zeka": "AI", "Denetim Masası": "Control Panel", "Aygıt Yöneticisi": "Device Manager", "Kayıt Defteri": "Registry Editor", "Hizmetler": "Services", "Kaynak İzleyici": "Resource Monitor", "Sistem Bilgisi": "System Info", "Uzak Masaüstü": "Remote Desktop", "Disk Temizleme": "Disk Cleanup", "Mağaza": "Store", "Fotoğraflar": "Photos", "Saat": "Clock", "İndirilenler": "Downloads", "Belgeler": "Documents", "Resimler": "Pictures", "Geri Dönüşüm": "Recycle Bin", "Ses ayarları": "Sound settings", "Wi-Fi ayarları": "Wi-Fi settings", "Ekran ayarları": "Display settings", "Hazırlanıyor…": "Preparing…"};
const LG=(()=>{const l=document.documentElement.dataset.lang;return l==='tr'||l==='en'?l:((navigator.language||'en').toLowerCase().startsWith('tr')?'tr':'en')})();
const t=s=>LG==='en'&&EN[s]?EN[s]:s;
if(LG==='en'){
 const tx=n=>{const v=n.nodeValue,k=v.trim();if(k&&EN[k]){const o=v.replace(k,()=>EN[k]);if(o!==v)n.nodeValue=o}};
 const at=e=>['placeholder','title'].forEach(a=>{const v=e.getAttribute&&e.getAttribute(a);if(v&&EN[v])e.setAttribute(a,EN[v])});
 const walk=r=>{if(r.nodeType===3){tx(r);return}if(r.nodeType!==1)return;at(r);const w=document.createTreeWalker(r,NodeFilter.SHOW_TEXT);let n;while(n=w.nextNode())tx(n);r.querySelectorAll('[placeholder],[title]').forEach(at)};
 walk(document.body);
 new MutationObserver(ms=>ms.forEach(m=>{if(m.type==='characterData')tx(m.target);m.addedNodes.forEach(walk)})).observe(document.body,{childList:true,characterData:true,subtree:true});
}
const $=id=>document.getElementById(id);const E=s=>(s||'').replace(/"/g,'&quot;');
const esc=x=>String(x==null?'':x).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let B=[],sel=-1,drag=-1,lastPush=0,ips=[],gtab='all',TCS={},ipHidden=localStorage.getItem('ipHidden')==='1';
const toast=m=>{const e=$('msg');e.textContent=t(m);e.classList.add('on');clearTimeout(e._t);e._t=setTimeout(()=>e.classList.remove('on'),1600)};
const msgOk=()=>toast('Kaydedildi ✓');
function tab(n){document.querySelectorAll('.tab').forEach(e=>e.classList.toggle('on',e.id==='tab-'+n));document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('on',e.dataset.t===n));localStorage.setItem('tab',n)}
/* ---- kendi bileşenlerimiz: seçenek çipleri, renk paleti, kaydırıcı ---- */
const PAL=['#1db954','#5865f2','#ff0033','#d97757','#4285f4','#25d366','#f97316','#a855f7','#ec4899','#14b8a6','#eab308','#64748b','#1d2231'];
const TCPAL=['#22d3ee','#39ff7a','#ff2bd6','#fb923c','#facc15','#a78bfa','#f43f5e','#60a5fa','#2b3140','#ffffff'],DEFTC={neon:'#22d3ee',terminal:'#39ff7a',flat:'#2b3140'};
const BGP=['#0b0d12','#1a1033','#0f2a1d','#2a1015','#101c3a','#262626','#ffffff','#efe6d8'];
function chipify(s){const sw=s.classList.contains('sw'),c=document.createElement('div');c.className='chips'+(sw?' sw':'');
 [...s.options].forEach(o=>{const b=document.createElement('button');b.type='button';b.className='chip';b.dataset.v=o.value;b.title=o.textContent;
  if(o.dataset.pv){const i=document.createElement('i');i.style.background=o.dataset.pv;b.appendChild(i)}
  if(!sw)b.appendChild(document.createTextNode(o.textContent));
  b.onclick=()=>{s.value=o.value;s.dispatchEvent(new Event('input'));s.dispatchEvent(new Event('change'));markS(s)};c.appendChild(b)});
 s.style.display='none';s.after(c);s._c=c;markS(s)}
const markS=s=>s._c.querySelectorAll('.chip').forEach(b=>b.classList.toggle('on',b.dataset.v===s.value));
function colorize(inp,pal){const w=document.createElement('div');w.className='sws';
 pal.forEach(c=>{const b=document.createElement('button');b.type='button';b.className='sw1';b.style.background=c;b.dataset.c=c;b.title=c==='#1d2231'?t('Renksiz'):c;if(c==='#1d2231')b.innerHTML='<svg class="ic"><use href="#ic-ban"/></svg>';
  b.onclick=()=>{inp.value=c;inp.dispatchEvent(new Event('input'));markC(inp)};w.appendChild(b)});
 const u=document.createElement('button');u.type='button';u.className='sw1 cust';u.title=t('Özel renk');u.onclick=()=>inp.click();w.appendChild(u);
 inp.classList.add('hid');inp.after(w);inp._w=w;inp.addEventListener('input',()=>markC(inp));markC(inp)}
const markC=i=>i._w.querySelectorAll('.sw1').forEach(b=>b.classList.toggle('on',!!b.dataset.c&&b.dataset.c.toLowerCase()===i.value.toLowerCase()));
const fillR=r=>r.style.setProperty('--p',((r.value-r.min)/(r.max-r.min)*100)+'%');
function enhance(root){root.querySelectorAll('select.ch:not([data-e])').forEach(s=>{s.dataset.e=1;chipify(s)});
 root.querySelectorAll('input[type=color]:not([data-e])').forEach(i=>{i.dataset.e=1;colorize(i,i.id==='s_color'?BGP:i.id==='s_tc'?TCPAL:PAL)});
 root.querySelectorAll('input[type=range]:not([data-e])').forEach(r=>{r.dataset.e=1;r.addEventListener('input',()=>fillR(r));fillR(r)})}
function syncUI(){document.querySelectorAll('select.ch').forEach(s=>s._c&&markS(s));document.querySelectorAll('input[type=color]').forEach(i=>i._w&&markC(i));document.querySelectorAll('input[type=range]').forEach(fillR);modeVis();tcSync()}
function tcSync(){const th=$('s_theme').value,i=$('s_tc');i.value=TCS[th]||DEFTC[th]||'#ffffff';markC(i)}
function modeVis(){const m=$('s_mode').value;$('m_preset').style.display=m==='preset'?'':'none';$('m_color').style.display=m==='color'?'':'none';$('m_image').style.display=m==='image'?'':'none'}
/* ---- butonlar ---- */
const icHtml=v=>v&&v.startsWith('ic:')?`<svg class="ic"><use href="#ic-${v.slice(3)}"/></svg>`:`<span>${esc(v||'')}</span>`;
const tileIcon=k=>k.img?`<img src="/api/icon/${k.img}">`:icHtml(k.icon||'ic:app');
const cvar=k=>k.color&&k.color!='#1d2231'?'--c:'+k.color:'';
const EMO={'📝':'file','📄':'file','📁':'folder','📂':'folder','📋':'copy','📌':'pin','🎧':'headphones','🎵':'music','🎶':'music','🎮':'gamepad','🕹':'gamepad','🌐':'globe','✉':'mail','📧':'mail','⚙':'settings','🔒':'lock','📷':'camera','📸':'camera','🔍':'search','🖥':'desktop','💻':'code','⭐':'star','✨':'star','⚡':'bolt','💬':'chat','📞':'phone','▶':'play','⏸':'pause','⏭':'next','⏮':'prev','🔊':'volup','🔉':'voldown','🔇':'mute','📊':'pulse','🗑':'trash','🏠':'home','🚀':'rocket','🔧':'wrench','💡':'bulb','❤':'heart','👤':'user','👥':'users','🖼':'image','🎤':'mic','📶':'wifi','🛒':'cart','📅':'calendar','⏰':'clock','📍':'pin','🔖':'bookmark','☀':'sun','🌙':'moon','📚':'book','🎨':'brush','🔗':'link','⌨':'keyboard','☁':'cloud','🎬':'video','🎥':'video','🏷':'tag'};
async function load(){B=await (await fetch('/api/buttons')).json();let ch=false;   // eski emoji ikonları hazır ayardaki simgeye çevir
 B.forEach(b=>{if(!b.icon||!b.icon.startsWith('ic:')){const p=PRESETS.flatMap(g=>g.items).find(x=>x.label===b.label);if(p){b.icon=p.icon;ch=true}else{const m=EMO[(b.icon||'').replace(/\uFE0F/g,'')];if(m){b.icon='ic:'+m;ch=true}else if(!b.icon){b.icon='ic:app';ch=true}}}});
 if(ch){lastPush=Date.now();fetch('/api/buttons',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(B)})}
 draw();form()}
function draw(){$('grid').innerHTML=B.map((k,i)=>`<div class="k${i==sel?' sel':''}" draggable="true" data-i="${i}" style="${cvar(k)}">${tileIcon(k)}<em>${esc(k.label)}</em></div>`).join('')+'<div class="k add" id="add">+</div>';
 document.querySelectorAll('.k[data-i]').forEach(e=>{const i=+e.dataset.i;e.onclick=()=>{sel=i;draw();form()};e.ondragstart=()=>drag=i;e.ondragover=ev=>ev.preventDefault();e.ondrop=()=>{const[m]=B.splice(drag,1);B.splice(i,0,m);sel=i;save();draw()}});
 $('add').onclick=openGal}
function form(){const f=$('form'),k=B[sel];
 if(!k){f.innerHTML='<div class="empty">Düzenlemek için bir butona tıkla ya da + ile yenisini ekle.</div>';return}
 f.innerHTML=`<div class="edh"><div class="pvk" style="${cvar(k)}">${tileIcon(k)}</div><b>${esc(k.label)}</b></div>
 <div class="fl">İsim</div><input id="f_label" value="${E(k.label)}">
 <div class="fl">İkon</div><div class="fr"><button class="btn g sm icbtn" onclick="openIcp()">${icHtml(k.icon||'ic:app')}<span>Değiştir</span></button></div>
 <div class="fl">Görsel ikon</div><div class="fr"><label class="btn g sm fb">Resim seç<input type="file" accept="image/*" id="f_img"></label>${k.img?'<button class="btn d sm" onclick="rmImg()">Kaldır</button>':''}</div>
 <div class="fl">Renk</div><input id="f_color" type="color" value="${k.color||'#1d2231'}">
 <div class="fl">Eylem</div><select id="f_type" class="ch"><option value="open">Program / site aç</option><option value="key">Tuş kombinasyonu</option><option value="media">Müzik / ses</option><option value="text">Yazı yaz</option></select>
 <div id="f_extra"></div><div class="fr end"><button class="btn" onclick="apply()">Kaydet</button><button class="btn d" onclick="del()">Sil</button></div>`;
 $('f_img').onchange=upImg;$('f_type').value=k.type;$('f_type').onchange=()=>{k.type=$('f_type').value;extra()};extra();enhance(f)}
function extra(){const k=B[sel],ty=k.type,x=$('f_extra');
 if(ty=='open')x.innerHTML=`<div class="fl">Program adı / yolu veya site adresi</div><input id="f_v" placeholder="chrome  ·  spotify:  ·  https://youtube.com" value="${E(k.target)}">`;
 if(ty=='key')x.innerHTML=`<div class="fl">Tuşlar (örn: ctrl+shift+s)</div><input id="f_v" value="${E((k.keys||[]).join('+'))}"><div class="fr" style="margin-top:8px"><button class="btn g sm" onclick="rec()"><svg class="ic"><use href="#ic-keyboard"/></svg> Tuşa basarak kaydet</button></div>`;
 if(ty=='media')x.innerHTML=`<div class="fl">Komut</div><select id="f_v" class="ch">${[['toggle','Oynat / Duraklat'],['pause','Duraklat'],['play','Oynat'],['next','Sonraki şarkı'],['prev','Önceki şarkı'],['mute','Sessiz'],['vup','Ses +'],['vdown','Ses -']].map(([v,n])=>`<option value="${v}" ${k.cmd==v?'selected':''}>${n}</option>`).join('')}</select>`;
 if(ty=='text')x.innerHTML=`<div class="fl">Yazılacak metin</div><input id="f_v" value="${E(k.text)}">`;
 enhance(x)}
function rec(){const i=$('f_v');i.value=t('Tuşlara bas…');const h=e=>{e.preventDefault();if(['Control','Shift','Alt','Meta'].includes(e.key))return;const m=[];if(e.ctrlKey)m.push('ctrl');if(e.shiftKey)m.push('shift');if(e.altKey)m.push('alt');if(e.metaKey)m.push('win');let k=e.key.toLowerCase();k={' ':'space',arrowup:'up',arrowdown:'down',arrowleft:'left',arrowright:'right'}[k]||k;i.value=[...m,k].join('+');removeEventListener('keydown',h,true)};addEventListener('keydown',h,true)}
function apply(){const k=B[sel];k.label=$('f_label').value;k.color=$('f_color').value;const v=$('f_v').value.trim();
 delete k.target;delete k.keys;delete k.cmd;delete k.text;
 if(k.type=='open')k.target=v;if(k.type=='key')k.keys=v.toLowerCase().split('+').map(s=>s.trim()).filter(Boolean);if(k.type=='media')k.cmd=v;if(k.type=='text')k.text=v;save();draw();form()}
function del(){if(confirm(t('Silinsin mi?'))){B.splice(sel,1);sel=-1;save();draw();form()}}
function save(){lastPush=Date.now();fetch('/api/buttons',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(B)}).then(msgOk)}
async function upImg(e){const f=e.target.files[0];if(!f)return;const k=B[sel];k.label=$('f_label').value;k.color=$('f_color').value;
 const bm=await createImageBitmap(f);const sc=Math.min(1,256/Math.max(bm.width,bm.height));const c=document.createElement('canvas');
 c.width=Math.round(bm.width*sc);c.height=Math.round(bm.height*sc);c.getContext('2d').drawImage(bm,0,0,c.width,c.height);
 c.toBlob(async b=>{k.img=await (await fetch('/api/icon',{method:'POST',body:b})).text();save();draw();form()},'image/png')}
function rmImg(){delete B[sel].img;save();draw();form()}
/* ---- ayarlar ---- */
async function loadSet(){const T=await (await fetch('/api/settings')).json();
 s_mode.value=T.mode;s_preset.value=T.preset;s_color.value=T.color;s_blur.value=T.blur;s_dim.value=T.dim;s_theme.value=T.theme;s_lang.value=T.lang;s_perf.value=T.perf;TCS=T.tc||{};s_ks.value=T.kscale;s_us.value=T.uscale;
 vb.textContent=T.blur;vd.textContent=T.dim;vk.textContent=T.kscale;vu.textContent=T.uscale;syncUI()}
function pushSet(){lastPush=Date.now();const p={tc:TCS,mode:s_mode.value,preset:+s_preset.value,color:s_color.value,blur:+s_blur.value,dim:+s_dim.value,theme:s_theme.value,lang:s_lang.value,perf:+s_perf.value,kscale:+s_ks.value,uscale:+s_us.value};
 vb.textContent=p.blur;vd.textContent=p.dim;vk.textContent=p.kscale;vu.textContent=p.uscale;modeVis();tcSync();
 fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});msgOk()}
['s_mode','s_preset','s_color','s_blur','s_dim','s_theme','s_lang','s_perf','s_ks','s_us'].forEach(i=>$(i).oninput=pushSet);
$('s_tc').oninput=()=>{TCS[$('s_theme').value]=$('s_tc').value;pushSet()};
$('tcreset').onclick=()=>{delete TCS[$('s_theme').value];pushSet();tcSync()};
$('s_file').onchange=async e=>{const f=e.target.files[0];if(!f)return;
 if(f.type==='image/gif'||/\.gif$/i.test(f.name)){if(f.size>16*1024*1024){toast('GIF çok büyük (en fazla 16 MB)');return}toast('Hazırlanıyor…');await fetch('/api/bgimage',{method:'POST',body:f});await loadSet();toast('Resim yüklendi ✓');return}
 const bm=await createImageBitmap(f);const sc=Math.min(1,1920/Math.max(bm.width,bm.height));
 const c=document.createElement('canvas');c.width=bm.width*sc;c.height=bm.height*sc;c.getContext('2d').drawImage(bm,0,0,c.width,c.height);
 c.toBlob(async b=>{await fetch('/api/bgimage',{method:'POST',body:b});await loadSet();toast('Resim yüklendi ✓')},'image/jpeg',.88)};
/* ---- hazır kısayol galerisi ---- */
const site=(label,icon,color,target,fav)=>({label,icon:'ic:'+icon,color,type:'open',target,fav});
const app_=(label,icon,target,color,fav)=>({label,icon:'ic:'+icon,color,type:'open',target,fav});
const keys=(label,icon,combo)=>({label,icon:'ic:'+icon,type:'key',keys:combo.split('+')});
const media=(label,icon,cmd)=>({label,icon:'ic:'+icon,type:'media',cmd});
const PRESETS=[
{g:"Tarayıcılar",items:[
  app_("Chrome","globe","chrome","#4285f4","chrome.com"),
  app_("Edge","globe","msedge","#0a84ff","microsoftedge.microsoft.com"),
  app_("Firefox","globe","firefox","#ff7139","firefox.com"),
  app_("Brave","globe","brave","#fb542b","brave.com"),
  app_("Opera","globe","opera","#ff1b2d","opera.com"),
  app_("Vivaldi","globe","vivaldi","#ef3939","vivaldi.com")]},
{g:"Uygulamalar",items:[
  app_("Spotify","music","spotify:","#1db954","spotify.com"),
  app_("Discord","chat","discord:","#5865f2","discord.com"),
  app_("WhatsApp","chat","whatsapp:","#25d366","whatsapp.com"),
  app_("Telegram","send","tg:","#2aabee","telegram.org"),
  app_("Claude","star","claude:","#d97757","claude.ai"),
  app_("Slack","chat","slack:","#611f69","slack.com"),
  app_("Zoom","video","zoommtg:","#2d8cff","zoom.us"),
  app_("Teams","users","msteams:","#6264a7","teams.microsoft.com"),
  app_("Skype","phone","skype:","#00aff0","skype.com"),
  app_("Notion","file","notion:","#888888","notion.so"),
  app_("Obsidian","gem","obsidian:","#7c3aed","obsidian.md"),
  app_("Figma","brush","figma:","#a259ff","figma.com"),
  app_("VLC","video","vlc","#ff8800","videolan.org"),
  app_("Outlook","mail","outlook","#0a64d6","outlook.com"),
  app_("OneNote","file","onenote","#7719aa",""),
  app_("Word","file","winword","#2b579a",""),
  app_("Excel","file","excel","#217346",""),
  app_("PowerPoint","file","powerpnt","#d24726",""),
  app_("Photoshop","image","photoshop","#31a8ff","adobe.com"),
  app_("DaVinci Resolve","video","resolve","#d0d0d0","blackmagicdesign.com"),
  app_("Blender","app","blender","#ea7600","blender.org"),
  app_("WinRAR","folder","winrar","#8a2be2","win-rar.com"),
  app_("7-Zip","folder","7zfm","#1c6dd0","7-zip.org"),
  app_("Paint","brush","mspaint","","")]},
{g:"Geliştirme",items:[
  app_("VS Code","code","code","#007acc","code.visualstudio.com"),
  app_("Visual Studio","code","devenv","#7b3fb8","visualstudio.microsoft.com"),
  app_("Android Studio","phone","studio64","#3ddc84","developer.android.com"),
  app_("PyCharm","code","pycharm64","#21d789","jetbrains.com"),
  app_("IntelliJ","code","idea64","#fe315d","jetbrains.com"),
  app_("WebStorm","code","webstorm64","#07c3f2","jetbrains.com"),
  app_("Sublime","code","sublime_text","#ff9800","sublimetext.com"),
  app_("Notepad++","code","notepad++","#90e59a","notepad-plus-plus.org"),
  app_("GitHub Desktop","code","githubdesktop","#8b949e","desktop.github.com"),
  app_("Postman","send","postman","#ff6c37","postman.com"),
  app_("Terminal","terminal","wt","","")]},
{g:"Oyun",items:[
  app_("Steam","gamepad","steam:","#66c0f4","steampowered.com"),
  app_("Epic Games","gamepad","com.epicgames.launcher:","#888888","epicgames.com"),
  app_("Battle.net","gamepad","battlenet:","#00aeff","battle.net"),
  app_("EA App","gamepad","origin2:","#ff4747","ea.com"),
  app_("Ubisoft Connect","gamepad","uplay:","#0070ff","ubisoft.com"),
  app_("Xbox","gamepad","xbox:","#107c10","xbox.com"),
  app_("GOG Galaxy","gamepad","goggalaxy:","#a855f7","gog.com")]},
{g:"Siteler",items:[
  site("YouTube","video","#ff0033","https://youtube.com","youtube.com"),
  site("YouTube Music","music","#ff0033","https://music.youtube.com","music.youtube.com"),
  site("YouTube Studio","video","#ff0033","https://studio.youtube.com","youtube.com"),
  site("Spotify Web","music","#1db954","https://open.spotify.com","spotify.com"),
  site("Apple Music","music","#fa243c","https://music.apple.com","apple.com"),
  site("SoundCloud","music","#ff5500","https://soundcloud.com","soundcloud.com"),
  site("Deezer","music","#a238ff","https://deezer.com","deezer.com"),
  site("Netflix","video","#e50914","https://netflix.com","netflix.com"),
  site("Prime Video","video","#00a8e1","https://primevideo.com","primevideo.com"),
  site("Disney+","video","#113ccf","https://disneyplus.com","disneyplus.com"),
  site("HBO Max","video","#7b2ff7","https://max.com","max.com"),
  site("Exxen","video","#f5c518","https://exxen.com","exxen.com"),
  site("BluTV","video","#1e88e5","https://blutv.com","blutv.com"),
  site("Gain","video","#ff3b3b","https://gain.tv","gain.tv"),
  site("Crunchyroll","video","#f47521","https://crunchyroll.com","crunchyroll.com"),
  site("Twitch","video","#9146ff","https://twitch.tv","twitch.tv"),
  site("Kick","video","#53fc18","https://kick.com","kick.com"),
  site("Instagram","camera","#e1306c","https://instagram.com","instagram.com"),
  site("X","send","#888888","https://x.com","x.com"),
  site("Facebook","users","#1877f2","https://facebook.com","facebook.com"),
  site("Threads","chat","#888888","https://threads.net","threads.net"),
  site("Reddit","chat","#ff4500","https://reddit.com","reddit.com"),
  site("TikTok","video","#fe2c55","https://tiktok.com","tiktok.com"),
  site("LinkedIn","user","#0a66c2","https://linkedin.com","linkedin.com"),
  site("Pinterest","pin","#e60023","https://pinterest.com","pinterest.com"),
  site("Snapchat","camera","#fffc00","https://snapchat.com","snapchat.com"),
  site("Medium","book","#888888","https://medium.com","medium.com"),
  site("WhatsApp Web","chat","#25d366","https://web.whatsapp.com","whatsapp.com"),
  site("Telegram Web","send","#2aabee","https://web.telegram.org","telegram.org"),
  site("Messenger","chat","#0084ff","https://messenger.com","messenger.com"),
  site("Discord Web","chat","#5865f2","https://discord.com/app","discord.com"),
  site("Gmail","mail","#ea4335","https://mail.google.com","mail.google.com"),
  site("Google","search","#4285f4","https://google.com","google.com"),
  site("Drive","cloud","#1fa463","https://drive.google.com","drive.google.com"),
  site("Docs","file","#4285f4","https://docs.google.com","docs.google.com"),
  site("Meet","video","#00897b","https://meet.google.com","meet.google.com"),
  site("Haritalar","pin","#34a853","https://maps.google.com","maps.google.com"),
  site("Çeviri","globe","#4285f4","https://translate.google.com","translate.google.com"),
  site("Takvim","calendar","#4285f4","https://calendar.google.com","calendar.google.com"),
  site("Fotoğraflar","image","#fbbc04","https://photos.google.com","photos.google.com"),
  site("Bing","search","#008373","https://bing.com","bing.com"),
  site("DuckDuckGo","search","#de5833","https://duckduckgo.com","duckduckgo.com"),
  site("GitHub","code","#8b949e","https://github.com","github.com"),
  site("Stack Overflow","code","#f48024","https://stackoverflow.com","stackoverflow.com"),
  site("Hugging Face","star","#ffcc4d","https://huggingface.co","huggingface.co"),
  site("Canva","brush","#00c4cc","https://canva.com","canva.com"),
  site("Figma Web","brush","#a259ff","https://figma.com","figma.com"),
  site("Notion Web","file","#888888","https://notion.so","notion.so"),
  site("Trello","file","#0079bf","https://trello.com","trello.com"),
  site("Wikipedia","book","#888888","https://tr.wikipedia.org","wikipedia.org"),
  site("TDK","book","#c8102e","https://sozluk.gov.tr","sozluk.gov.tr"),
  site("e-Devlet","user","#d6001c","https://turkiye.gov.tr","turkiye.gov.tr"),
  site("Trendyol","cart","#f27a1a","https://trendyol.com","trendyol.com"),
  site("Hepsiburada","cart","#ff6000","https://hepsiburada.com","hepsiburada.com"),
  site("Amazon","cart","#ff9900","https://amazon.com.tr","amazon.com.tr"),
  site("N11","cart","#7b3fe4","https://n11.com","n11.com"),
  site("Sahibinden","tag","#ffe800","https://sahibinden.com","sahibinden.com"),
  site("Yemeksepeti","cart","#fa0050","https://yemeksepeti.com","yemeksepeti.com"),
  site("Getir","cart","#5d3ebc","https://getir.com","getir.com"),
  site("Ekşi Sözlük","chat","#81c14b","https://eksisozluk.com","eksisozluk.com"),
  site("Speedtest","pulse","#4a90e2","https://speedtest.net","speedtest.net")]},
{g:"Yapay zeka",items:[
  site("ChatGPT","chat","#10a37f","https://chatgpt.com","chatgpt.com"),
  site("Claude Web","star","#d97757","https://claude.ai","claude.ai"),
  site("Gemini","star","#8ab4f8","https://gemini.google.com","gemini.google.com"),
  site("Copilot","star","#2f80ed","https://copilot.microsoft.com","copilot.microsoft.com"),
  site("Perplexity","search","#20b8cd","https://perplexity.ai","perplexity.ai"),
  site("Grok","bolt","#888888","https://grok.com","grok.com"),
  site("DeepSeek","search","#4d6bfe","https://chat.deepseek.com","deepseek.com")]},
{g:"Windows",items:[
  app_("Not Defteri","file","notepad","",""),
  app_("Hesap Mak.","app","calc","",""),
  app_("Dosyalar","folder","explorer","",""),
  app_("Ayarlar","settings","ms-settings:","",""),
  app_("Görev Yön.","pulse","taskmgr","",""),
  app_("Komut İstemi","terminal","cmd","",""),
  app_("PowerShell","terminal","powershell","",""),
  app_("Ekran Alıntısı","snip","ms-screenclip:","",""),
  app_("Denetim Masası","settings","control","",""),
  app_("Aygıt Yöneticisi","settings","devmgmt.msc","",""),
  app_("Kayıt Defteri","wrench","regedit","",""),
  app_("Hizmetler","settings","services.msc","",""),
  app_("Kaynak İzleyici","pulse","resmon","",""),
  app_("Sistem Bilgisi","bulb","msinfo32","",""),
  app_("Uzak Masaüstü","desktop","mstsc","",""),
  app_("Disk Temizleme","trash","cleanmgr","",""),
  app_("Mağaza","cart","ms-windows-store:","",""),
  app_("Fotoğraflar","image","ms-photos:","",""),
  app_("Saat","clock","ms-clock:","",""),
  app_("İndirilenler","download","shell:Downloads","",""),
  app_("Belgeler","file","shell:Personal","",""),
  app_("Resimler","image","shell:My Pictures","",""),
  app_("Geri Dönüşüm","trash","shell:RecycleBinFolder","",""),
  app_("Ses ayarları","volup","ms-settings:sound","",""),
  app_("Wi-Fi ayarları","wifi","ms-settings:network-wifi","",""),
  app_("Bluetooth","wifi","ms-settings:bluetooth","",""),
  app_("Ekran ayarları","desktop","ms-settings:display","",""),
  app_("Windows Update","reload","ms-settings:windowsupdate","","")]},
{g:"Kısayollar · Pencere ve masaüstü",items:[
  keys("Masaüstü","desktop","win+d"),
  keys("Pencere Değiş","switch","alt+tab"),
  keys("Görev Görünümü","taskview","win+tab"),
  keys("Büyüt","maximize","win+up"),
  keys("Küçült","minimize","win+down"),
  keys("Sola Yasla","snapl","win+left"),
  keys("Sağa Yasla","snapr","win+right"),
  keys("Pencereyi Kapat","closewin","alt+f4"),
  keys("Tümünü Küçült","minall","win+m"),
  keys("Yeni Masaüstü","newdesk","win+ctrl+d"),
  keys("Sonraki Masaüstü","nextdesk","win+ctrl+right"),
  keys("Önceki Masaüstü","prevdesk","win+ctrl+left"),
  keys("Kilitle","lock","win+l"),
  keys("Ekranı Yansıt","project","win+p")]},
{g:"Kısayollar · Düzenleme",items:[
  keys("Kopyala","copy","ctrl+c"),
  keys("Yapıştır","paste","ctrl+v"),
  keys("Kes","cut","ctrl+x"),
  keys("Geri Al","undo","ctrl+z"),
  keys("Yinele","redo","ctrl+y"),
  keys("Tümünü Seç","selectall","ctrl+a"),
  keys("Kaydet","save","ctrl+s"),
  keys("Bul","find","ctrl+f"),
  keys("Yazdır","print","ctrl+p"),
  keys("Yeni","newfile","ctrl+n"),
  keys("Aç","folder","ctrl+o"),
  keys("Sil","trash","delete"),
  keys("Enter","enter","enter"),
  keys("Esc","esc","esc")]},
{g:"Kısayollar · Tarayıcı",items:[
  keys("Yeni Sekme","newtab","ctrl+t"),
  keys("Sekmeyi Kapat","closetab","ctrl+w"),
  keys("Kapatılanı Aç","reopentab","ctrl+shift+t"),
  keys("Sonraki Sekme","nexttab","ctrl+tab"),
  keys("Önceki Sekme","prevtab","ctrl+shift+tab"),
  keys("Gizli Pencere","incognito","ctrl+shift+n"),
  keys("Yenile","reload","f5"),
  keys("Zorla Yenile","hardreload","ctrl+f5"),
  keys("Tam Ekran","fullscreen","f11"),
  keys("Yakınlaştır","zoomin","ctrl+="),
  keys("Uzaklaştır","zoomout","ctrl+-"),
  keys("Zoom Sıfırla","zoomreset","ctrl+0"),
  keys("Geri","back","alt+left"),
  keys("İleri","forward","alt+right"),
  keys("Adres Çubuğu","addrbar","ctrl+l")]},
{g:"Kısayollar · Windows araçları",items:[
  keys("Ekran Alıntısı","snip","win+shift+s"),
  keys("Ekran Görüntüsü","camera","printscreen"),
  keys("Pano Geçmişi","clipboard","win+v"),
  keys("Emoji","smile","win+."),
  keys("Arama","search","win+s"),
  keys("Çalıştır","run","win+r"),
  keys("Dosya Gezgini","folder","win+e"),
  keys("Ayarlar","settings","win+i"),
  keys("Bildirimler","bell","win+n"),
  keys("Oyun Çubuğu","gamepad","win+g"),
  keys("Ekran Kaydı","record","win+alt+r"),
  keys("Görev Yön.","pulse","ctrl+shift+esc"),
  keys("Büyüteç","zoomin","win+=")]},
{g:"Müzik ve ses",items:[
  media("Oynat/Duraklat","playpause","toggle"),
  media("Oynat","play","play"),
  media("Duraklat","pause","pause"),
  media("Sonraki","next","next"),
  media("Önceki","prev","prev"),
  media("Ses +","volup","vup"),
  media("Ses -","voldown","vdown"),
  media("Sessiz","mute","mute")]}
];
const GT=[['all','Tümü'],['Tarayıcılar','Tarayıcılar'],['Uygulamalar','Uygulamalar'],['Geliştirme','Geliştirme'],['Oyun','Oyun'],['Siteler','Siteler'],['Yapay zeka','Yapay zeka'],['Windows','Windows'],['k','Kısayollar'],['Müzik ve ses','Müzik ve ses']];
const logoOf=p=>p.type!=='open'?'':(/^[a-z0-9_]+$/i.test(p.target||'')?'exe:'+p.target.toLowerCase():(p.fav||''));
function tileLogo(p){const a=logoOf(p),b=p.fav;if(!a&&!b)return icHtml(p.icon);
 return `<img src="/api/logo/${a||b}" data-alt="${a&&b&&a!==b?b:''}" onerror="logoErr(this)" alt=""><span style="display:none">${icHtml(p.icon)}</span>`}
function logoErr(im){const a=im.dataset.alt;if(a){im.dataset.alt='';im.src='/api/logo/'+a}else{im.style.display='none';im.nextElementSibling.style.display=''}}
function openGal(){$('gal').style.display='flex';$('gq').value='';renderGal();$('gq').focus()}
function closeGal(){$('gal').style.display='none'}
const inTab=g=>gtab==='all'||(gtab==='k'?g.startsWith('Kısayollar'):g===gtab);
function renderGal(){const q=$('gq').value.trim().toLowerCase();
 $('gtabs').innerHTML=GT.map(([k,n])=>`<button class="chip${k===gtab?' on':''}" data-k="${k}">${n}</button>`).join('');
 $('gtabs').querySelectorAll('.chip').forEach(b=>b.onclick=()=>{gtab=b.dataset.k;renderGal()});
 const html=PRESETS.map((g,gi)=>{if(!inTab(g.g))return '';const it=g.items.map((p,pi)=>[p,pi]).filter(([p])=>!q||(p.label+' '+t(p.label)).toLowerCase().includes(q));
  return it.length?`<h3>${g.g}</h3><div class="pg">${it.map(([p,pi])=>`<div class="pk" data-g="${gi}" data-p="${pi}" style="${p.color?'--c:'+p.color:''}">${tileLogo(p)}<em>${esc(p.label)}</em>${B.some(b=>b.label==p.label)?'<i class="ck">✓</i>':''}</div>`).join('')}</div>`:''}).join('');
 $('gbody').innerHTML=html||'<p class="s">Sonuç yok</p>';
 $('gbody').querySelectorAll('.pk').forEach(e=>e.onclick=()=>addPreset(PRESETS[e.dataset.g].items[e.dataset.p]))}
async function addPreset(p){const k={label:p.label,icon:p.icon,type:p.type};
 if(p.color)k.color=p.color;if(p.target)k.target=p.target;if(p.keys)k.keys=[...p.keys];if(p.cmd)k.cmd=p.cmd;
 for(const d of [logoOf(p),p.fav].filter((x,i,a)=>x&&a.indexOf(x)===i)){try{const r=await fetch('/api/favicon?d='+encodeURIComponent(d),{method:'POST'});if(r.ok){k.img=await r.text();break}}catch(e){}}
 B.push(k);sel=B.length-1;save();draw();form();renderGal()}
function addBlank(){B.push({label:'Yeni',icon:'ic:star',type:'open',target:''});sel=B.length-1;save();draw();form();closeGal()}
$('gq').oninput=renderGal;$('gal').onclick=e=>{if(e.target.id=='gal')closeGal()};addEventListener('keydown',e=>{if(e.key==='Escape')closeGal()});
/* ---- ikon seçici ---- */
const ICKEYS=["playpause", "play", "pause", "next", "prev", "volup", "voldown", "mute", "desktop", "switch", "taskview", "maximize", "minimize", "snapl", "snapr", "closewin", "minall", "newdesk", "nextdesk", "prevdesk", "lock", "project", "copy", "paste", "cut", "undo", "redo", "selectall", "save", "find", "search", "print", "newfile", "file", "folder", "trash", "enter", "esc", "newtab", "closetab", "reopentab", "nexttab", "prevtab", "incognito", "reload", "hardreload", "fullscreen", "zoomin", "zoomout", "zoomreset", "back", "forward", "addrbar", "snip", "camera", "clipboard", "smile", "run", "terminal", "settings", "bell", "gamepad", "record", "pulse", "app", "globe", "mail", "home", "music", "video", "link", "keyboard", "chat", "phone", "code", "cloud", "cart", "calendar", "clock", "pin", "bookmark", "sun", "moon", "power", "rocket", "wrench", "bulb", "heart", "user", "users", "image", "mic", "headphones", "wifi", "star", "bolt", "book", "brush", "send", "tag", "ban", "download", "gem"];
function openIcp(){$('icgrid').innerHTML=ICKEYS.map(k=>`<button onclick="pickIc('${k}')" title="${k}"><svg class="ic"><use href="#ic-${k}"/></svg></button>`).join('');$('icp').style.display='flex'}
function closeIcp(){$('icp').style.display='none'}
function pickIc(k){const b=B[sel];b.label=$('f_label').value;b.color=$('f_color').value;b.icon='ic:'+k;save();draw();form();closeIcp()}
$('icp').onclick=e=>{if(e.target.id==='icp')closeIcp()};addEventListener('keydown',e=>{if(e.key==='Escape')closeIcp()});
/* ---- bağlantı: IP gizle/göster, USB ---- */
const EYE_ON='<svg viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF='<svg viewBox="0 0 24 24"><path d="M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4M6.5 6.6C3.6 8.6 2 12 2 12s3.6 7 10 7c1.5 0 2.9-.3 4.1-.9"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';
const mask=s=>s.replace(/\d/g,'•');
function renderIp(){if(!ips.length)return;const a=ips[0]+':5000';$('addr').textContent=ipHidden?mask(a):a;$('eye').innerHTML=ipHidden?EYE_OFF:EYE_ON;
 $('iplist').innerHTML=ips.map((ip,i)=>`<div class="iprow"><code>${ipHidden?mask(ip+':5000'):ip+':5000'}</code>${i==0?'<span class="tag">Önerilen</span>':''}<button class="btn g sm" onclick="cp('${ip}:5000')">Kopyala</button></div>`).join('')}
function toggleIp(){ipHidden=!ipHidden;localStorage.setItem('ipHidden',ipHidden?'1':'0');renderIp()}
function cp(v){navigator.clipboard.writeText(v).then(()=>toast('Kopyalandı ✓')).catch(()=>{})}
async function loadInfo(){try{const i=await (await fetch('/api/info')).json();ips=i.ips&&i.ips.length?i.ips:[i.ip];renderIp()}catch(e){}}
async function usbPoll(){try{const u=await (await fetch('/api/usb')).json();let m,c='';
 if(!u.adb)m='USB: adb bulunamadı';else if(!u.devices.length)m='USB: cihaz yok';else if(u.devices.some(d=>d.ok)){m='USB: bağlı ✓';c='#6fe3a1'}else m='USB: telefonda izin ver';
 ['usb','usb2'].forEach(id=>{const e=$(id);e.textContent=t(m);e.style.color=c;if(!u.adb)e.title=t('Android SDK platform-tools (adb) bulunamadı. Android Studio kurulu olmalı.')})}catch(x){}}
$('s_lang').addEventListener('change',()=>setTimeout(()=>location.reload(),700));
(async function watch(){let ws=-1,wb=-1;for(;;){try{const c=new AbortController(),to=setTimeout(()=>c.abort(),30000);
 const d=await (await fetch('/api/wait?v='+(ws+wb),{cache:'no-store',signal:c.signal})).json();clearTimeout(to);
 if(ws!==-1&&Date.now()-lastPush>1500){const a=document.activeElement;
  if(d.s!==ws&&!(a&&a.type==='range'))loadSet();
  if(d.b!==wb&&$('gal').style.display!=='flex')load()}
 ws=d.s;wb=d.b}catch(e){await new Promise(r=>setTimeout(r,1500))}}})();
/* ---- başlangıç ---- */
enhance(document);tab(localStorage.getItem('tab')||'btns');loadInfo();usbPoll();setInterval(usbPoll,4000);load();loadSet();
fetch('/api/logoprefetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify([...new Set(PRESETS.flatMap(g=>g.items.flatMap(p=>[logoOf(p),p.fav])).filter(Boolean))])});
</script></body></html>"""


# ---------- Açık uygulamalar (alt-tab gibi ama sadece logo) ----------
WINC = {"src": {}, "ids": set(), "desc": {}}
WINDIR = os.path.join(DATA, "wincache")

if os.name == "nt":
    import ctypes
    from ctypes import wintypes as wt
    _u, _g, _k, _dw = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32, ctypes.windll.dwmapi
    _H = wt.HWND
    for _f, _a, _r in (
        ("IsWindowVisible", [_H], wt.BOOL), ("IsIconic", [_H], wt.BOOL), ("IsWindow", [_H], wt.BOOL),
        ("GetWindowTextLengthW", [_H], ctypes.c_int), ("GetClassNameW", [_H, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("GetWindow", [_H, wt.UINT], _H), ("GetWindowLongW", [_H, ctypes.c_int], wt.LONG),
        ("GetWindowThreadProcessId", [_H, ctypes.POINTER(wt.DWORD)], wt.DWORD), ("ShowWindow", [_H, ctypes.c_int], wt.BOOL),
        ("SetForegroundWindow", [_H], wt.BOOL), ("GetForegroundWindow", [], _H),
        ("SendMessageTimeoutW", [_H, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t)], wt.LPARAM),
        ("GetIconInfo", [wt.HICON, ctypes.c_void_p], wt.BOOL), ("DestroyIcon", [wt.HICON], wt.BOOL),
        ("PrivateExtractIconsW", [wt.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(wt.HICON),
                                  ctypes.POINTER(wt.UINT), wt.UINT, wt.UINT], wt.UINT)):
        _fn = getattr(_u, _f); _fn.argtypes = _a; _fn.restype = _r
    _dw.DwmGetWindowAttribute.argtypes = [_H, wt.DWORD, ctypes.c_void_p, wt.DWORD]
    _k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]; _k.OpenProcess.restype = wt.HANDLE
    _k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    _k.CloseHandle.argtypes = [wt.HANDLE]
    _g.GetObjectW.argtypes = [wt.HGDIOBJ, ctypes.c_int, ctypes.c_void_p]
    _g.CreateCompatibleDC.argtypes = [wt.HDC]; _g.CreateCompatibleDC.restype = wt.HDC
    _g.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    _g.DeleteObject.argtypes = [wt.HGDIOBJ]; _g.DeleteDC.argtypes = [wt.HDC]
    _ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    _u.EnumWindows.argtypes = [_ENUM, wt.LPARAM]

    class _ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD), ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]

    class _BITMAP(ctypes.Structure):
        _fields_ = [("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG), ("bmWidthBytes", wt.LONG),
                    ("bmPlanes", wt.WORD), ("bmBitsPixel", wt.WORD), ("bmBits", wt.LPVOID)]

    class _BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    def _bits(hdc, hbm, w, h):
        bi = _BIH(ctypes.sizeof(_BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)   # üstten alta, 32 bit
        buf = ctypes.create_string_buffer(w * h * 4)
        _g.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bi), 0)
        return buf.raw

    def hicon_png(hicon):
        from PIL import Image
        import io
        ii = _ICONINFO()
        if not _u.GetIconInfo(hicon, ctypes.byref(ii)):
            return None
        hdc = _g.CreateCompatibleDC(None)
        try:
            bm = _BITMAP()
            _g.GetObjectW(ii.hbmColor, ctypes.sizeof(bm), ctypes.byref(bm))
            w, h = bm.bmWidth, bm.bmHeight
            if not w or not h:
                return None
            img = Image.frombuffer("RGBA", (w, h), _bits(hdc, ii.hbmColor, w, h), "raw", "BGRA", 0, 1)
            if not any(img.getchannel("A").getdata()):   # alfa kanalı yoksa maskeden üret
                mask = Image.frombuffer("RGBA", (w, h), _bits(hdc, ii.hbmMask, w, h), "raw", "BGRA", 0, 1).convert("L")
                img.putalpha(mask.point(lambda v: 255 - v))
            img.thumbnail((128, 128))
            out = io.BytesIO(); img.save(out, "PNG")
            return out.getvalue()
        finally:
            _g.DeleteObject(ii.hbmColor); _g.DeleteObject(ii.hbmMask); _g.DeleteDC(hdc)

    def _exe_path(pid):
        hp = _k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not hp:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024); n = wt.DWORD(1024)
            ok = _k.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(n))
            return buf.value if ok else ""
        finally:
            _k.CloseHandle(hp)

    def file_desc(path):
        if path in WINC["desc"]:
            return WINC["desc"][path]
        d = ""
        try:
            v = ctypes.windll.version
            sz = v.GetFileVersionInfoSizeW(path, None)
            buf = ctypes.create_string_buffer(sz)
            lp, ln = ctypes.c_void_p(), wt.UINT()
            if sz and v.GetFileVersionInfoW(path, 0, sz, buf) and v.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(lp), ctypes.byref(ln)) and ln.value >= 4:
                w = ctypes.cast(lp, ctypes.POINTER(ctypes.c_ushort))
                sub = "\\StringFileInfo\\%04x%04x\\FileDescription" % (w[0], w[1])
                if v.VerQueryValueW(buf, sub, ctypes.byref(lp), ctypes.byref(ln)) and ln.value:
                    d = ctypes.wstring_at(lp.value).strip()
        except Exception:
            pass
        WINC["desc"][path] = d
        return d

    def list_windows():
        import hashlib
        out, seen, ids = [], set(), set()
        fg = _u.GetForegroundWindow() or 0
        fpid = wt.DWORD(0)
        if fg:
            _u.GetWindowThreadProcessId(fg, ctypes.byref(fpid))
        me = os.getpid()

        def cb(h, _):
            try:
                if not _u.IsWindowVisible(h) or _u.GetWindowTextLengthW(h) == 0:
                    return True
                ex = _u.GetWindowLongW(h, -20)   # GWL_EXSTYLE
                app = ex & 0x40000               # WS_EX_APPWINDOW
                if not app and (ex & 0x80 or ex & 0x08000000 or _u.GetWindow(h, 4)):   # araç penceresi / odaklanmayan / sahipli
                    return True
                cloaked = ctypes.c_int(0)
                _dw.DwmGetWindowAttribute(h, 14, ctypes.byref(cloaked), 4)   # DWMWA_CLOAKED: askıdaki UWP vb.
                if cloaked.value:
                    return True
                cn = ctypes.create_unicode_buffer(80); _u.GetClassNameW(h, cn, 80)
                if cn.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                    return True
                pid = wt.DWORD(0); _u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                if pid.value == me:   # Phone Deck'in kendi penceresi
                    return True
                path = _exe_path(pid.value)
                tl = ctypes.create_unicode_buffer(256)
                ctypes.windll.user32.GetWindowTextW(ctypes.c_void_p(h), tl, 256)
                title = tl.value
                uwp = path.lower().endswith("applicationframehost.exe")
                ident = (title if (uwp or not path) else path).lower()
                key = hashlib.sha1(ident.encode("utf-8", "ignore")).hexdigest()[:12]
                if key in seen:   # her uygulama tek logo (z-sırasında en üstteki pencere)
                    return True
                seen.add(key)
                name = title if (uwp or not path) else (file_desc(path) or os.path.splitext(os.path.basename(path))[0].title())
                WINC["src"][key] = (path, int(h))
                ids.add(int(h))
                out.append({"id": int(h), "key": key, "name": name, "active": bool(fpid.value) and pid.value == fpid.value})
            except Exception:
                pass
            return True

        _u.EnumWindows(_ENUM(cb), 0)
        WINC["ids"] = ids
        return out

    def exe_icon_png(path):
        hi, iid = wt.HICON(), wt.UINT()
        if _u.PrivateExtractIconsW(path, 0, 128, 128, ctypes.byref(hi), ctypes.byref(iid), 1, 0) and hi.value:
            try:
                return hicon_png(hi)
            finally:
                _u.DestroyIcon(hi)
        return None

    def icon_png(key):
        fp = os.path.join(WINDIR, key + ".png")
        if os.path.exists(fp):
            return open(fp, "rb").read()
        path, h = WINC["src"].get(key, ("", 0))
        data = None
        if path and not path.lower().endswith("applicationframehost.exe"):
            hi, iid = wt.HICON(), wt.UINT()
            if _u.PrivateExtractIconsW(path, 0, 128, 128, ctypes.byref(hi), ctypes.byref(iid), 1, 0) and hi.value:
                try:
                    data = hicon_png(hi)
                finally:
                    _u.DestroyIcon(hi)
        if data is None and h:   # exe'den alınamadıysa (UWP / yönetici uygulaması) pencerenin kendi simgesi
            res = ctypes.c_size_t()
            for which in (1, 2, 0):   # ICON_BIG, ICON_SMALL2, ICON_SMALL
                _u.SendMessageTimeoutW(h, 0x7F, which, 0, 2, 150, ctypes.byref(res))   # WM_GETICON
                if res.value:
                    data = hicon_png(wt.HICON(res.value))
                    break
        if data:
            os.makedirs(WINDIR, exist_ok=True)
            open(fp, "wb").write(data)
        return data

    def focus_window(hid):
        if hid not in WINC["ids"] or not _u.IsWindow(hid):
            return False
        if _u.IsIconic(hid):
            _u.ShowWindow(hid, 9)   # SW_RESTORE
        _u.keybd_event(0x12, 0, 0, 0); _u.keybd_event(0x12, 0, 2, 0)   # Alt'a bas-bırak: Windows ön plana geçmeye izin verir
        _u.SetForegroundWindow(hid)
        return True
else:
    def list_windows(): return []
    def icon_png(key): return None
    def exe_icon_png(path): return None
    def focus_window(hid): return False


_wc = {"t": 0.0, "d": []}
_wl = threading.Lock()


@app.get("/api/windows")
def api_windows():
    with _wl:   # birden fazla cihaz/yoklama aynı listeyi paylaşır
        if time.time() - _wc["t"] > 0.8:
            try:
                _wc["d"] = list_windows()
            except Exception:
                _wc["d"] = []
            _wc["t"] = time.time()
        return jsonify(_wc["d"])


@app.get("/api/winicon/<key>")
def api_winicon(key):
    if not re.fullmatch(r"[0-9a-f]{12}", key):
        return "", 404
    try:
        d = icon_png(key)
    except Exception:
        d = None
    if not d:
        return "", 404
    return Response(d, mimetype="image/png", headers={"Cache-Control": "max-age=3600"})


@app.post("/api/focus/<int:hid>")
def api_focus(hid):
    try:
        _wc["t"] = 0.0   # geçişten sonra liste taze alınsın
        return "ok" if focus_window(hid) else ("yok", 404)
    except Exception:
        return "hata", 500


# ---------- USB: adb reverse ile telefonun 127.0.0.1:5000 adresi PC'ye bağlanır ----------
USB = {"adb": None, "devices": []}


def find_adb():
    c = []
    for e in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(e):
            c.append(os.path.join(os.environ[e], "platform-tools", "adb.exe"))
    if os.environ.get("LOCALAPPDATA"):
        c.append(os.path.join(os.environ["LOCALAPPDATA"], "Android", "Sdk", "platform-tools", "adb.exe"))
    c.append(shutil.which("adb") or "")
    return next((p for p in c if p and os.path.exists(p)), None)


def usb_loop():
    flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW

    def run(*a):
        return subprocess.run([USB["adb"], *a], capture_output=True, text=True, timeout=10, creationflags=flags).stdout

    while True:
        try:
            if not USB["adb"]:
                USB["adb"] = find_adb()
                if not USB["adb"]:
                    time.sleep(20)
                    continue
            devs = []
            for ln in run("devices").splitlines()[1:]:
                p = ln.split()
                if len(p) < 2:
                    continue
                d = {"serial": p[0], "state": p[1], "ok": False}
                if p[1] == "device":
                    lst = run("-s", p[0], "reverse", "--list")
                    if "tcp:5000" not in lst:
                        run("-s", p[0], "reverse", "tcp:5000", "tcp:5000")
                        lst = run("-s", p[0], "reverse", "--list")
                    d["ok"] = "tcp:5000" in lst
                devs.append(d)
            USB["devices"] = devs
        except Exception:
            pass
        time.sleep(4)


@app.get("/api/usb")
def api_usb():
    return jsonify({"adb": bool(USB["adb"]), "devices": USB["devices"]})


@app.post("/api/opendata")
def api_opendata():
    if not local():
        return "yasak", 403
    try:
        os.startfile(DATA)
    except Exception:
        pass
    return "ok"


def res(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def set_win_icon(path):
    """Pencere ve görev çubuğu simgesi (pywebview Windows'ta bunu kendisi yapmıyor)."""
    try:
        import ctypes
        from ctypes import c_void_p, c_uint
        u = ctypes.windll.user32
        u.FindWindowW.restype = c_void_p
        u.LoadImageW.restype = c_void_p
        u.SendMessageW.argtypes = [c_void_p, c_uint, c_void_p, c_void_p]
        h = None
        for _ in range(60):
            h = u.FindWindowW(None, "Phone Deck")
            if h:
                break
            time.sleep(0.1)
        if not h:
            return
        u.SendMessageW(h, 0x80, 0, u.LoadImageW(None, path, 1, 16, 16, 0x10))   # küçük simge
        u.SendMessageW(h, 0x80, 1, u.LoadImageW(None, path, 1, 48, 48, 0x10))   # görev çubuğu simgesi
    except Exception:
        pass


def ui_tr():
    l = get_set().get("lang", "auto")
    if l in ("tr", "en"):
        return l == "tr"
    try:
        import ctypes
        return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x1F
    except Exception:
        return False


def make_tray(show):
    try:
        import pystray
        from PIL import Image
        tr = ui_tr()
        icon = pystray.Icon("deck", Image.open(res("TelefonDeck.ico")), "Phone Deck", pystray.Menu(
            pystray.MenuItem("Aç" if tr else "Open", lambda: show(), default=True),
            pystray.MenuItem("Çıkış" if tr else "Quit", lambda: os._exit(0))))
        icon.run_detached()
        return icon
    except Exception:
        return None


def serve():
    app.run(host="0.0.0.0", port=5000, threaded=True)


if __name__ == "__main__":
    import threading, time, urllib.request
    probe = socket.socket()
    try:
        probe.bind(("0.0.0.0", 5000)); probe.close()
    except OSError:
        try:   # zaten açıksa (tepsideyse) var olan pencereyi öne getir
            urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:5000/api/show", data=b"", method="POST"), timeout=2)
            sys.exit(0)
        except Exception:
            pass
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk(); r.withdraw()
        messagebox.showerror("Phone Deck", "5000 portu kullanımda.\nPhone Deck zaten açık olabilir.")
        sys.exit(1)
    if os.name == "nt":
        try:   # BELOW_NORMAL: oyun ve diğer programlar CPU'da öncelikli olur
            _k.GetCurrentProcess.restype = wt.HANDLE; _k.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
            _k.SetPriorityClass(_k.GetCurrentProcess(), 0x4000)
        except Exception:
            pass
    threading.Thread(target=serve, daemon=True).start()
    threading.Thread(target=usb_loop, daemon=True).start()
    for _ in range(60):   # sunucunun ayağa kalkmasını bekle
        try:
            urllib.request.urlopen("http://127.0.0.1:5000/api/info", timeout=0.3); break
        except Exception:
            time.sleep(0.1)
    try:
        import webview   # düzenleme ekranı tarayıcıda değil, kendi uygulama penceresinde açılır
        win = webview.create_window("Phone Deck", "http://127.0.0.1:5000/admin", width=1180, height=760,
                                    min_size=(900, 580), background_color="#0b0d12")
        tray = make_tray(lambda: show_win())
        st = {"hidden": False}

        def hide_win():
            if tray:
                st["hidden"] = True
                win.hide()
                win.load_url("about:blank")   # gizliyken düzenleme sayfası çalışmasın: bellek ve yoklama tasarrufu
            else:
                win.minimize()

        def show_win():
            if st["hidden"]:
                st["hidden"] = False
                win.load_url("http://127.0.0.1:5000/admin")
            win.show()
        SHOW["fn"] = show_win

        def on_closing():
            # X = kapatma değil: tepsiye (tepsi yoksa görev çubuğuna) küçült. Gerçek çıkış: tepsi menüsü ya da "Programı kapat"
            threading.Timer(0.05, hide_win).start()
            return False
        win.events.closing += on_closing
        webview.start(lambda: set_win_icon(res("TelefonDeck.ico")))
        os._exit(0)
    except Exception:
        pass
    # yedek: pywebview kurulu değilse eski küçük pencere + tarayıcı
    import tkinter as tk
    ip = lan_ip()
    w = tk.Tk()
    w.title("Phone Deck"); w.geometry("340x230"); w.configure(bg="#0b0d12")
    w.protocol("WM_DELETE_WINDOW", lambda: os._exit(0))
    tk.Label(w, text="Phone Deck çalışıyor ✔", fg="#7c9cff", bg="#0b0d12", font=("Segoe UI", 13, "bold")).pack(pady=(22, 6))
    tk.Label(w, text="Telefondaki uygulamaya bu adresi gir:", fg="#9aa3bb", bg="#0b0d12").pack()
    e = tk.Entry(w, justify="center", font=("Consolas", 15), width=20)
    e.insert(0, f"{ip}:5000"); e.config(state="readonly"); e.pack(pady=10)
    tk.Button(w, text="⚙  Atamaları düzenle", font=("Segoe UI", 11, "bold"), command=lambda: webbrowser.open("http://localhost:5000/admin")).pack(pady=8)
    w.mainloop()
