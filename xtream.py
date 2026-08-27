"""
Cliente Xtream Codes (stdlib pura, sin dependencias).

Reimplementa el protocolo que usan MaxPlayer / DonIPTV contra el panel
'Xtream-Masters OTT'. Sirve para autenticar, listar categorias/canales/VOD/series,
obtener EPG y construir las URLs de reproduccion.

Sobre lo tolerante que hay que ser con estos paneles:

  No siguen HTTP. El panel de este proyecto contesta HTTP 512 llevando en el
  cuerpo la respuesta buena en JSON, devuelve 404 con el cuerpo vacio cuando el
  usuario no existe, suelta texto plano ("Invalid Authorization or URL / 404
  Error.") con un 200 cuando la contrasena no vale, y manda `auth` unas veces
  como numero y otras como la cadena "0". Un cliente que se crea el codigo de
  estado se queda sin lista y sin saber por que. Aqui se mira el cuerpo
  SIEMPRE, y se traduce cada caso a una frase que diga que ha pasado.
"""
import urllib.request
import urllib.parse
import urllib.error
import gzip
import json
import ssl
import base64
import threading
import zlib

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

# Cloudflare (que es quien hay delante de estos paneles) responde 403 "error
# code: 1010" a las peticiones sin User-Agent, asi que nunca se manda vacio.
UA_POR_DEFECTO = "VLC/3.0.20 LibVLC/3.0.20"


class XtreamError(Exception):
    pass


class XtreamClient:
    def __init__(self, server, username, password,
                 user_agent=UA_POR_DEFECTO, output="ts", timeout=25):
        self.server = server.rstrip("/")
        self.username = username
        self.password = password
        self.user_agent = user_agent or UA_POR_DEFECTO
        self.output = output          # ts | m3u8
        self.timeout = timeout
        self.info = None              # respuesta de autenticacion

    # ---- HTTP ----------------------------------------------------------
    @staticmethod
    def _cuerpo(resp, raw):
        """Descomprime el cuerpo segun Content-Encoding."""
        enc = (resp.headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            try:
                return gzip.decompress(raw)
            except (OSError, EOFError, zlib.error):
                return raw            # anunciado pero no comprimido: pasa
        if "deflate" in enc:
            try:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except zlib.error:
                return raw
        return raw

    def _open(self, url):
        # Se pide gzip: la lista de canales son ~550 kB de JSON muy repetitivo
        # que comprimido baja a ~42 kB (13 veces menos). El panel lo soporta.
        req = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=_SSL) as r:
                return self._cuerpo(r, r.read())
        except urllib.error.HTTPError as e:
            # AQUI esta el motivo de que no cargara nada: este panel manda el
            # JSON bueno con codigos como 512, y descartarlo por el codigo era
            # tirar justo la respuesta.
            try:
                raw = self._cuerpo(e, e.read())
            except Exception:
                raw = b""
            if raw.strip():
                return raw
            if e.code == 404:
                raise XtreamError(
                    "el servidor no reconoce esta cuenta (HTTP 404 sin respuesta): "
                    "comprueba usuario y contrasena")
            raise XtreamError(f"el servidor respondio HTTP {e.code} sin contenido")
        except urllib.error.URLError as e:
            raise XtreamError(f"no se pudo conectar: {e.reason}")
        except Exception as e:
            raise XtreamError(f"{type(e).__name__}: {e}")

    def _api(self, action=None, **params):
        q = {"username": self.username, "password": self.password}
        if action:
            q["action"] = action
        q.update({k: v for k, v in params.items() if v is not None})
        url = f"{self.server}/player_api.php?" + urllib.parse.urlencode(q)
        raw = self._open(url)
        if not raw.strip():
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            texto = raw[:200].decode("utf-8", "replace").strip()
            # El panel contesta en texto plano cuando rechaza la cuenta, y con
            # un 200 por delante. Sin esto el usuario veia el texto crudo y no
            # habia forma de saber que lo que pasaba era que no le dejaban.
            bajo = texto.lower()
            if "invalid" in bajo or "auth" in bajo or "not found" in bajo:
                raise XtreamError(f"el servidor rechaza la cuenta: {texto}")
            raise XtreamError(f"respuesta no-JSON de {action or 'login'}: {texto}")

    # ---- auth ----------------------------------------------------------
    def login(self):
        self.info = self._api()
        ui = self.info.get("user_info", {}) if isinstance(self.info, dict) else {}
        if not ui:
            raise XtreamError("el servidor no ha devuelto datos de la cuenta")
        # `auth` llega como 1, "1", 0 o "0" segun el panel y el dia. Comparar
        # con el entero 0, como se hacia antes, dejaba pasar el "0" en cadena:
        # se daba por buena una autenticacion fallida y el fallo aparecia mas
        # tarde y disfrazado, como una lista de canales vacia.
        if str(ui.get("auth", "")).strip().lower() in ("0", "", "false", "none"):
            raise XtreamError("usuario o contrasena incorrectos")
        estado = str(ui.get("status") or "Active").strip()
        if estado.lower() != "active":
            raise XtreamError(f"la cuenta no esta activa (el panel dice: {estado})")
        return self.info

    # ---- catalogos -----------------------------------------------------
    def live_categories(self):
        return self._api("get_live_categories") or []

    def live_streams(self, category_id=None):
        return self._api("get_live_streams", category_id=category_id) or []

    def catalog(self):
        """Autentica y trae categorias y canales, las tres cosas a la vez.

        Antes eran tres viajes seguidos, y cada uno pagaba su apreton de manos
        TLS (~300 ms contra este servidor) ademas del tiempo que tarde el panel
        en generar la respuesta. Lanzadas a la vez, el total pasa de ser la
        suma de las tres a ser la mas lenta de las tres.

        Devuelve (categorias, canales).
        """
        tareas = {}
        for nombre, fn in (("login", self.login),
                           ("categorias", self.live_categories),
                           ("canales", self.live_streams)):
            caja = {}

            def corre(fn=fn, caja=caja):
                try:
                    caja["v"] = fn()
                except Exception as e:              # se decide al recogerlas
                    caja["e"] = e

            hilo = threading.Thread(target=corre, daemon=True)
            hilo.start()
            tareas[nombre] = (hilo, caja)

        for hilo, _ in tareas.values():
            hilo.join(self.timeout + 5)

        # El login manda: si la cuenta no vale, los otros dos fallos son
        # consecuencia y su mensaje solo despistaria.
        for nombre in ("login", "categorias", "canales"):
            _, caja = tareas[nombre]
            if "e" in caja:
                raise caja["e"]
            if "v" not in caja:
                raise XtreamError(f"el servidor no respondio a tiempo ({nombre})")
        return tareas["categorias"][1]["v"], tareas["canales"][1]["v"]

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
