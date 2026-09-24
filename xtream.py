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
import codecs
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

# A donde redirige Cloudflare cuando corta el video. Ese dominio resuelve a
# 127.0.0.1, o sea que el reproductor acaba conectandose a si mismo y lo unico
# que se ve es un error de conexion que no dice nada de la causa real.
CF_ABUSO = "cloudflare-terms-of-service-abuse"

# Formato de directo por defecto. HLS y no TS: el TS crudo es justo lo que los
# CDN reconocen y cortan; el .m3u8 se sirve desde el origen y pasa.
SALIDA_POR_DEFECTO = "m3u8"


class XtreamError(Exception):
    pass


class _NoSigasRedirecciones(urllib.request.HTTPRedirectHandler):
    """Deja que las redirecciones lleguen como HTTPError, para poder leerlas."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _TrozosJSON:
    """Va sacando objetos completos de un array JSON que llega a pedazos.

    El panel manda la lista con `Transfer-Encoding: chunked` y la va soltando
    segun la genera, asi que no hay que esperar al ultimo byte para tener
    canales que pintar: en cuanto se ha cerrado una llave a nivel raiz, ese
    canal esta entero y es utilizable.

    Solo cuenta llaves, con la unica sutileza que hace falta para no contarlas
    mal: las que van dentro de una cadena no cuentan, y una comilla escapada no
    cierra la cadena. Lo que entrega es texto crudo; parsearlo es de quien
    llama, que ademas puede descartar lo que no le encaje.
    """

    def __init__(self):
        self._buf = ""
        self._i = 0                  # por donde va el escaneo dentro de _buf
        self._ini = None             # inicio del objeto que esta a medias
        self._prof = 0
        self._cadena = False
        self._escapa = False

    def come(self, texto):
        self._buf += texto
        buf = self._buf
        n = len(buf)
        i = self._i
        objetos = []
        while i < n:
            ch = buf[i]
            if self._cadena:
                if self._escapa:
                    self._escapa = False
                elif ch == "\\":
                    self._escapa = True
                elif ch == '"':
                    self._cadena = False
            elif ch == '"':
                self._cadena = True
            elif ch == "{":
                if self._prof == 0:
                    self._ini = i
                self._prof += 1
            elif ch == "}":
                if self._prof > 0:
                    self._prof -= 1
                    if self._prof == 0 and self._ini is not None:
                        objetos.append(buf[self._ini:i + 1])
                        self._ini = None
            i += 1
        # Tirar lo ya consumido: sin esto el buffer crece hasta los 425 kB de
        # la lista entera y cada trozo nuevo se escanea sobre una cadena mas
        # larga. Se conserva solo el objeto que haya quedado a medias.
        if self._ini is None:
            self._buf = ""
            self._i = 0
        else:
            self._buf = buf[self._ini:]
            self._i = n - self._ini
            self._ini = 0
        return objetos


class XtreamClient:
    def __init__(self, server, username, password,
                 user_agent=UA_POR_DEFECTO, output=SALIDA_POR_DEFECTO, timeout=25):
        self.server = server.rstrip("/")
        self.username = username
        self.password = password
        self.user_agent = user_agent or UA_POR_DEFECTO
        self.output = output          # ts | m3u8
        self.timeout = timeout
        self.info = None              # respuesta de autenticacion

    # ---- HTTP ----------------------------------------------------------
    @staticmethod
    def _descomprime(raw, enc):
        """Descomprime un cuerpo ya entero segun su Content-Encoding."""
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

    @staticmethod
    def _cuerpo(resp, raw):
        return XtreamClient._descomprime(
            raw, (resp.headers.get("Content-Encoding") or "").lower())

    def _url(self, action=None, **params):
        q = {"username": self.username, "password": self.password}
        if action:
            q["action"] = action
        q.update({k: v for k, v in params.items() if v is not None})
        return f"{self.server}/player_api.php?" + urllib.parse.urlencode(q)

    def _abrir(self, url):
        """Abre la URL y devuelve (respuesta sin leer, encoding, codigo).

        Se separa de `_open` porque la lista de canales se lee a trozos, no de
        una pieza. La tolerancia es la misma y por el mismo motivo: este panel
        manda el JSON bueno con codigos como 512, asi que un HTTPError no se
        descarta, se lee igual —su cuerpo es justo la respuesta que hace falta.
        """
        # Se pide gzip: la lista de canales son ~550 kB de JSON muy repetitivo
        # que comprimido baja a ~42 kB (13 veces menos). El panel lo soporta.
        req = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
        })
        try:
            r = urllib.request.urlopen(req, timeout=self.timeout, context=_SSL)
        except urllib.error.HTTPError as e:
            r = e
        except urllib.error.URLError as e:
            raise XtreamError(f"no se pudo conectar: {e.reason}")
        except Exception as e:
            raise XtreamError(f"{type(e).__name__}: {e}")
        codigo = getattr(r, "code", None) or getattr(r, "status", None) or 200
        return r, (r.headers.get("Content-Encoding") or "").lower(), codigo

    @staticmethod
    def _sin_cuerpo(codigo):
        """El error que toca cuando la respuesta ha venido vacia."""
        if codigo == 404:
            return XtreamError(
                "el servidor no reconoce esta cuenta (HTTP 404 sin respuesta): "
                "comprueba usuario y contrasena")
        return XtreamError(f"el servidor respondio HTTP {codigo} sin contenido")

    def _open(self, url):
        r, enc, codigo = self._abrir(url)
        try:
            raw = self._descomprime(r.read(), enc)
        except XtreamError:
            raise
        except Exception as e:
            raise XtreamError(f"se corto la respuesta: {type(e).__name__}: {e}")
        finally:
            try:
                r.close()
            except Exception:
                pass
        if raw.strip():
            return raw
        if not 200 <= codigo < 300:
            raise self._sin_cuerpo(codigo)
        return raw

    @staticmethod
    def _traduce(texto, action):
        """Convierte el cuerpo de la respuesta en datos, o en una queja clara."""
        if not texto.strip():
            return []
        try:
            return json.loads(texto)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            trozo = texto[:200].strip()
            # El panel contesta en texto plano cuando rechaza la cuenta, y con
            # un 200 por delante. Sin esto el usuario veia el texto crudo y no
            # habia forma de saber que lo que pasaba era que no le dejaban.
            bajo = trozo.lower()
            if "invalid" in bajo or "auth" in bajo or "not found" in bajo:
                raise XtreamError(f"el servidor rechaza la cuenta: {trozo}")
            raise XtreamError(f"respuesta no-JSON de {action or 'login'}: {trozo}")

    def _api(self, action=None, **params):
        raw = self._open(self._url(action, **params))
        return self._traduce(raw.decode("utf-8", "replace"), action)

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

    def live_streams_goteo(self, category_id=None, on_lote=None, corta=None):
        """Como `live_streams`, pero entregando los canales segun van llegando.

        Medido contra el panel de este proyecto, pidiendo la categoria grande
        (1382 canales): el primer byte llega a los 7,4 s y el ultimo a los
        27,7, en dos rafagas —unos 500 canales en la primera—. Esperando al
        final se tiran veinte segundos en los que ya habia media categoria
        encima de la mesa.

        `on_lote` recibe listas de canales conforme se completan. Corre en el
        hilo que llama, que nunca es el de la interfaz.

        Lo que se DEVUELVE es la lista buena: al terminar se parsea el cuerpo
        entero de una pieza, con json de verdad. El goteo es un adelanto para
        poder pintar; si se hubiera dejado algo por el camino, el resultado
        final lo trae igual. Cuesta 12 ms sobre 1737 canales, o sea nada.

        `corta` es una funcion sin argumentos: si devuelve cierto, se
        abandona la descarga. Sirve para no seguir bajando la lista de una
        cuenta que el usuario acaba de cambiar.
        """
        r, enc, codigo = self._abrir(self._url("get_live_streams",
                                               category_id=category_id))
        if "gzip" in enc:
            desc = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif "deflate" in enc:
            desc = zlib.decompressobj(-zlib.MAX_WBITS)
        else:
            desc = None
        # Decodificador incremental y no bytes.decode() por trozo: un acento
        # partido entre dos pedazos del cable saldria como un interrogante en
        # el nombre del canal.
        aletras = codecs.getincrementaldecoder("utf-8")("replace")
        trozos = _TrozosJSON()
        partes = []
        try:
            while True:
                if corta is not None and corta():
                    raise XtreamError("descarga cancelada")
                try:
                    # read1 y no read: `read(n)` se queda esperando a juntar
                    # los n bytes, asi que una rafaga corta del servidor no se
                    # ve hasta que llega la siguiente —justo lo contrario de
                    # lo que se busca aqui—. read1 devuelve lo que ya hay.
                    pedazo = (r.read1(16384) if hasattr(r, "read1")
                              else r.read(4096))
                except Exception as e:
                    raise XtreamError(
                        f"se corto la respuesta: {type(e).__name__}: {e}")
                if not pedazo:
                    break
                if desc is not None:
                    try:
                        pedazo = desc.decompress(pedazo)
                    except zlib.error:
                        # Anunciado como comprimido y no lo estaba: se deja de
                        # descomprimir y se sigue en claro, como hace _open.
                        desc = None
                texto = aletras.decode(pedazo)
                if not texto:
                    continue
                partes.append(texto)
                if on_lote is None:
                    continue
                lote = []
                for crudo in trozos.come(texto):
                    try:
                        d = json.loads(crudo)
                    except ValueError:
                        continue      # el parseo final de verdad lo traera
                    if isinstance(d, dict) and d.get("stream_id") is not None:
                        lote.append(d)
                if lote:
                    on_lote(lote)
        finally:
            try:
                r.close()
            except Exception:
                pass
        partes.append(aletras.decode(b"", True))
        entero = "".join(partes)
        if not entero.strip() and not 200 <= codigo < 300:
            raise self._sin_cuerpo(codigo)
        return self._traduce(entero, "get_live_streams") or []

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

    # ---- que formato de directo sirve de verdad este servidor ----------
    def _sonda_directo(self, url):
        """Abre una URL de directo y mira si sale video, sin bajarse el canal.

        Las redirecciones se siguen a mano a proposito: hay que VER a donde
        manda el servidor. Cuando Cloudflare corta el video —su contrato no
        permite repartir television por su CDN— responde 302 hacia
        www.cloudflare-terms-of-service-abuse.com, que resuelve a 127.0.0.1.
        Siguiendola sin mirar, el fallo llega disfrazado de "conexion
        rechazada" y parece cosa de la red del usuario o del reproductor.

        Devuelve (sirve, explicacion).
        """
        sin_saltos = urllib.request.build_opener(_NoSigasRedirecciones)
        for _ in range(5):
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                r = sin_saltos.open(req, timeout=min(self.timeout, 12))
                cabeza = r.read(1024)
                r.close()
            except urllib.error.HTTPError as e:
                destino = e.headers.get("Location") if e.headers else None
                if e.code in (301, 302, 303, 307, 308) and destino:
                    if CF_ABUSO in destino:
                        return False, ("el CDN (Cloudflare) esta bloqueando el video "
                                       "en este formato")
                    url = urllib.parse.urljoin(url, destino)
                    continue
                return False, f"el servidor respondio HTTP {e.code}"
            except Exception as e:
                return False, f"{type(e).__name__}: {str(e)[:60]}"
            if cabeza.startswith(b"#EXTM3U"):
                return True, "lista HLS"
            if cabeza[:1] == b"\x47":
                return True, "flujo MPEG-TS"
            return False, "la respuesta no es ni HLS ni MPEG-TS"
        return False, "demasiadas redirecciones"

    def formato_que_funciona(self, stream_id, candidatos=None):
        """Cual de los formatos de directo entrega video en este servidor.

        Existe porque un panel puede tener la lista perfectamente accesible y
        el video cortado: son caminos distintos, y el segundo puede estar
        bloqueado por el CDN que hay delante. Se prueba primero el configurado,
        para no cambiar nada cuando ya va bien.

        Devuelve (extension, explicacion) o (None, explicacion del ultimo).
        """
        orden = list(candidatos or ([self.output] if self.output else []))
        for ext in ("m3u8", "ts"):
            if ext not in orden:
                orden.append(ext)
        motivo = "sin probar"
        for ext in orden:
            sirve, motivo = self._sonda_directo(self.live_url(stream_id, ext))
            if sirve:
                return ext, motivo
        return None, motivo

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
