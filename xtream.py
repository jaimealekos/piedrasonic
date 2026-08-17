"""
Cliente Xtream Codes (stdlib pura, sin dependencias).

Reimplementa el protocolo que usan MaxPlayer / DonIPTV contra el panel
'Xtream-Masters OTT'. Sirve para autenticar, listar categorias/canales/VOD/series,
obtener EPG y construir las URLs de reproduccion.
"""
import urllib.request
import urllib.parse
import json
import ssl
import base64

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


class XtreamError(Exception):
    pass


class XtreamClient:
    def __init__(self, server, username, password,
                 user_agent="VLC/3.0.20 LibVLC/3.0.20", output="ts", timeout=25):
        self.server = server.rstrip("/")
        self.username = username
        self.password = password
        self.user_agent = user_agent
        self.output = output          # ts | m3u8
        self.timeout = timeout
        self.info = None              # respuesta de autenticacion

    # ---- HTTP ----------------------------------------------------------
    def _open(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=_SSL) as r:
                return r.read()
        except Exception as e:
            raise XtreamError(f"{e} :: {url}")

    def _api(self, action=None, **params):
        q = {"username": self.username, "password": self.password}
        if action:
            q["action"] = action
        q.update({k: v for k, v in params.items() if v is not None})
        url = f"{self.server}/player_api.php?" + urllib.parse.urlencode(q)
        raw = self._open(url)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise XtreamError(f"Respuesta no-JSON de {action}: {raw[:200]!r}")

    # ---- auth ----------------------------------------------------------
    def login(self):
        self.info = self._api()
        ui = (self.info or {}).get("user_info", {})
        if not ui or ui.get("auth") == 0 or ui.get("status") not in (None, "Active"):
            raise XtreamError(f"Autenticacion fallida: {ui}")
        return self.info

    # ---- catalogos -----------------------------------------------------
    def live_categories(self):
        return self._api("get_live_categories") or []

    def live_streams(self, category_id=None):
        return self._api("get_live_streams", category_id=category_id) or []

    # ---- EPG -----------------------------------------------------------
    def short_epg(self, stream_id, limit=6):
        data = self._api("get_short_epg", stream_id=stream_id, limit=limit) or {}
        out = []
        for e in data.get("epg_listings", []):
            out.append({
                "title": _b64(e.get("title", "")),
                "desc": _b64(e.get("description", "")),
                "start": e.get("start", ""),
                "end": e.get("end", ""),
            })
        return out

    # ---- URLs de reproduccion -----------------------------------------
    def live_url(self, stream_id, ext=None):
        ext = ext or self.output
        return f"{self.server}/live/{self.username}/{self.password}/{stream_id}.{ext}"

    def timeshift_url(self, stream_id, start, duration_min, ext=None):
        """Catch-up en formato ruta (el mas soportado por Xtream).
        start = 'YYYY-MM-DD:HH-MM' (hora local del servidor)."""
        ext = ext or self.output
        return (f"{self.server}/timeshift/{self.username}/{self.password}/"
                f"{int(duration_min)}/{start}/{stream_id}.{ext}")

    def catchup_url(self, stream_id, start, duration_min):
        # forma alternativa via php
        return (f"{self.server}/streaming/timeshift.php?username={self.username}"
                f"&password={self.password}&stream={stream_id}"
                f"&start={start}&duration={duration_min}")

    # ---- export M3U ----------------------------------------------------
    def build_m3u(self, streams, categories_map):
        lines = ["#EXTM3U"]
        for s in streams:
            sid = s.get("stream_id")
            name = s.get("name", str(sid))
            logo = s.get("stream_icon", "") or ""
            grp = categories_map.get(str(s.get("category_id")), "")
            epg = s.get("epg_channel_id", "") or ""
            lines.append(
                f'#EXTINF:-1 tvg-id="{epg}" tvg-logo="{logo}" group-title="{grp}",{name}')
            lines.append(self.live_url(sid))
        return "\n".join(lines) + "\n"


def _b64(s):
    if not s:
        return ""
    try:
        return base64.b64decode(s).decode("utf-8", "ignore")
    except Exception:
        return s
