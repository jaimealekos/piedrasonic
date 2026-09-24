"""
Reproductor VLC embebido con controles en capas flotantes TRANSLÚCIDAS.
El vídeo ocupa toda la superficie; las barras de control son ventanas sin
borde con canal alfa, PROPIEDAD (owner) de la ventana principal y SIN
-topmost: al ser propiedad del root van siempre por encima de él (y del
vídeo nativo de VLC), pero por debajo de cualquier otra aplicación que nos
tape, así que Windows las recorta solo — nada de regiones ni sondeos de
foco. Minimizar el root las esconde también solo (regla de owned windows).

Claves de Windows/Tk aprendidas:
  * -alpha se fija AL CREAR la ventana.
  * Se oculta moviéndola fuera de pantalla (withdraw/deiconify no re-mapea
    ventanas overrideredirect de forma fiable).
  * El dueño se fija con GWLP_HWNDPARENT; una ventana owned se dibuja sobre
    su dueño sin necesidad de -topmost (también sobre el hijo de VLC).
  * SetWindowRgn sobre ventanas con -alpha (layered) es terreno minado: se
    probó para recortar las barras y provocaba cuelgues y redibujos rotos.

Requiere: python-vlc + VLC instalado (libvlc.dll).
"""
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
import tkinter as tk
import customtkinter as ctk
from theme import C, font
import registro

import struct

CF_ABUSO = "cloudflare-terms-of-service-abuse"
VLC_HINT = ""     # explicacion legible si no se pudo localizar VLC


def _dll_bits(path):
    """32 o 64 segun la cabecera PE de la DLL, o None si no se puede leer."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0x3C)
            pe = struct.unpack("<I", fh.read(4))[0]
            fh.seek(pe + 4)
            machine = struct.unpack("<H", fh.read(2))[0]
    except (OSError, struct.error):
        return None
    return {0x014C: 32, 0x8664: 64, 0xAA64: 64}.get(machine)


def _use_vlc(d):
    """Deja el entorno listo para que `import vlc` cargue el VLC de `d`.

    Son las tres cosas que hay que hacer SIEMPRE, venga la carpeta de donde
    venga: ruta absoluta a la DLL (si no, el hook de ctypes de PyInstaller la
    busca dentro del bundle), carpeta de plugins, y add_dll_directory para que
    libvlccore.dll —que vive al lado— tambien se encuentre.
    """
    os.environ["PYTHON_VLC_LIB_PATH"] = os.path.join(d, "libvlc.dll")
    plugins = os.path.join(d, "plugins")
    if os.path.isdir(plugins):
        os.environ.setdefault("PYTHON_VLC_MODULE_PATH", plugins)
        os.environ.setdefault("VLC_PLUGIN_PATH", plugins)
    add = getattr(os, "add_dll_directory", None)       # Python 3.8+
    if add is not None:
        try:
            add(d)
        except OSError:
            pass
    return d


def _prepare_vlc():
    """Localiza VLC y prepara el entorno ANTES de importar `vlc`.

    Congelado con PyInstaller esto es obligatorio, por dos motivos que se suman:

    1. El hook de ctypes de PyInstaller intercepta `ctypes.CDLL` y, ante un
       nombre relativo como 'libvlc.dll' o '.\\libvlc.dll' (que es justo lo que
       usa el parche interno de python-vlc), busca dentro del bundle en vez de
       en el sistema. Al no encontrarlo lanza el famoso
       "Failed to load dynlib/dll ... most likely this dynlib/dll was not found
       when the application was frozen". Con una ruta ABSOLUTA no interfiere.

    2. Desde Python 3.8, Windows ya no resuelve las DLL dependientes por el PATH
       ni por el directorio actual. Aunque libvlc.dll cargue, `libvlccore.dll`
       —que vive a su lado— no se encontraria. De ahi el `add_dll_directory`.

    Devuelve el directorio de VLC, o None si no hay instalacion.
    """
    lib = os.environ.get("PYTHON_VLC_LIB_PATH")
    if lib and os.path.isfile(lib):
        # Ojo: tambien por aqui hay que pasar por _use_vlc. Antes esta rama
        # devolvia la carpeta y ya, sin add_dll_directory ni ruta de plugins,
        # con lo que la unica salida de emergencia que ofrece el mensaje de
        # error ("define PYTHON_VLC_LIB_PATH") no llegaba a funcionar.
        return _use_vlc(os.path.dirname(lib))

    candidates = []
    # Si el .exe trae VLC empaquetado, tiene prioridad sobre lo que haya
    # instalado en la maquina: es la unica forma de no depender de la version ni
    # de la arquitectura del PC ajeno.
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.append(os.path.join(base, "vlc"))
    else:
        # Ejecutando desde el codigo se mira donde mira el `.spec` para
        # compilar, y en el mismo orden: PIEDRASONIC_VLC_DIR y `vendor\vlc`.
        # Sin esto, en una maquina con VLC de 32 bits instalado el programa se
        # negaba a arrancar aunque tuviera al lado el de 64 que el propio
        # repositorio usa para construirse.
        candidates.append(os.environ.get("PIEDRASONIC_VLC_DIR"))
        candidates.append(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "vendor", "vlc"))
    if sys.platform.startswith("win"):
        try:
            import winreg
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for sub in (r"SOFTWARE\VideoLAN\VLC",
                            r"SOFTWARE\WOW6432Node\VideoLAN\VLC"):
                    try:
                        with winreg.OpenKey(root, sub) as k:
                            d, _ = winreg.QueryValueEx(k, "InstallDir")
                            if d:
                                candidates.append(d)
                    except OSError:
                        pass
        except ImportError:
            pass
        for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(var)
            if base:
                candidates.append(os.path.join(base, "VideoLAN", "VLC"))

    wrong_arch = []
    for d in candidates:
        if not d:                      # PIEDRASONIC_VLC_DIR sin definir
            continue
        dll = os.path.join(d, "libvlc.dll")
        if not os.path.isfile(dll):
            continue
        # Comprobacion de arquitectura ANTES de intentar cargarla. Es la causa
        # numero uno del fallo en un PC ajeno: videolan.org ha servido durante
        # anos el instalador de 32 bits por defecto, asi que mucha gente "tiene
        # VLC instalado" pero es x86 y un .exe de 64 bits no puede cargar esa
        # DLL jamas. Sin esta comprobacion el usuario solo ve un error de
        # ctypes que no dice nada.
        bits = _dll_bits(dll)
        if bits and bits != (8 * struct.calcsize("P")):
            wrong_arch.append((d, bits))
            continue
        return _use_vlc(d)

    global VLC_HINT
    mine = 8 * struct.calcsize("P")
    if wrong_arch:
        d, bits = wrong_arch[0]
        VLC_HINT = (
            f"VLC encontrado en {d} es de {bits} bits y esta aplicacion es de "
            f"{mine} bits: son incompatibles.\n\n"
            f"Instala VLC de {mine} bits desde videolan.org (en la pagina de "
            f"descarga, elige explicitamente la version de {mine} bits).")
    else:
        VLC_HINT = (
            "No se ha encontrado ninguna instalacion de VLC.\n\n"
            "Instala VLC desde videolan.org, o define la variable de entorno "
            "PYTHON_VLC_LIB_PATH con la ruta completa a libvlc.dll.")
    return None


VLC_DIR = _prepare_vlc()

try:
    if VLC_DIR is None and sys.platform.startswith("win"):
        raise RuntimeError(VLC_HINT)
    import vlc
    VLC_OK = True
    VLC_ERR = None
except Exception as e:                # pragma: no cover
    VLC_OK = False
    VLC_ERR = e

if sys.platform.startswith("win"):
    import ctypes

    _GA_ROOT = 2
    _GWLP_HWNDPARENT = -8
    _U = ctypes.windll.user32          # objeto cacheado: firmas, una sola vez
    _U.GetAncestor.restype = ctypes.c_void_p
    _U.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    _SETPTR = getattr(_U, "SetWindowLongPtrW", _U.SetWindowLongW)
    _SETPTR.restype = ctypes.c_void_p
    _SETPTR.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    _U.WindowFromPoint.restype = ctypes.c_void_p
    _U.WindowFromPoint.argtypes = [_POINT]


def _desktop_dir():
    """Carpeta real del Escritorio (aguanta OneDrive y Windows en español)."""
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        try:
            class _GUID(ctypes.Structure):
                _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort),
                            ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]
            fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,      # FOLDERID_Desktop
                        (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                             0x9A, 0x87, 0xC6, 0x41))
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(
                    ctypes.byref(fid), 0, None, ctypes.byref(p)) == 0:
                d = p.value
                ctypes.windll.ole32.CoTaskMemFree(p)
                if d and os.path.isdir(d):
                    return d
        except Exception:
            pass
    d = os.path.join(home, "Desktop")
    return d if os.path.isdir(d) else home


OVL = "#0d0d10"
ASPECT_MIN = 16 / 9                # nunca más estrecho que 16:9
ASPECT_MAX = 3.0
ALPHA = 0.78
OFFSCREEN = "1x1+-10000+-10000"


def _fmt(ms):
    if ms is None or ms < 0:
        return "--:--"
    s = int(ms // 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _traduce_http(cod):
    if cod == 404:
        return "El servidor dice que ese canal no existe (404)."
    if cod in (401, 403):
        return ("El servidor deniega el acceso a ese canal (%d): la cuenta "
                "no lo incluye o está en uso en otro sitio." % cod)
    if cod == 429:
        return ("El servidor está limitando las peticiones (429): conviene "
                "esperar un rato antes de volver a intentarlo.")
    # Los 52x son de Cloudflare y dicen algo muy concreto: CLOUDFLARE responde,
    # pero no consigue hablar con el servidor que hay detras. O sea que el
    # problema no esta ni en tu conexion ni en el CDN, sino en el origen del
    # panel. Sin esto se leian como "el servidor falla", que manda a mirar al
    # sitio equivocado. Visto en directo el 5 de septiembre de 2026.
    if cod in (521, 522, 523, 524):
        return ("Cloudflare responde, pero no consigue hablar con el servidor "
                "del panel que hay detrás (error %d). El panel está caído o no "
                "le contesta a Cloudflare; no es cosa de tu conexión." % cod)
    if cod in (525, 526):
        return ("Cloudflare no puede establecer el cifrado con el servidor del "
                "panel (error %d): es un problema del certificado del origen."
                % cod)
    if 500 <= cod < 600:
        return "El servidor falla al servir ese canal (%d)." % cod
    return "El servidor responde %d y no da vídeo." % cod


def _toca(ip, puerto, tmo=5.0):
    """Un toque TCP. Distinguir el silencio del rechazo es media explicación:
    un RST demuestra que la dirección se alcanza; el silencio es la firma de
    que alguien se está comiendo el paquete por el camino."""
    s = socket.socket()
    s.settimeout(tmo)
    try:
        s.connect((ip, puerto))
        return "abre"
    except socket.timeout:
        return "silencio"
    except ConnectionRefusedError:
        return "rechazo"
    except OSError as e:
        return "rechazo" if getattr(e, "winerror", 0) == 10061 else "raro"
    finally:
        try:
            s.close()
        except Exception:
            pass


def _contigua(ip):
    """Una dirección de al lado, para comparar. Ojo al leerlo: en este servicio
    las contiguas resultaron ser MÁS ORÍGENES del mismo panel, no terceros
    neutrales. Sirve igual para lo que se usa —¿llega el camino hasta esa
    red?— pero no dice nada de nadie ajeno."""
    p = ip.split(".")
    try:
        u = int(p[3])
    except (IndexError, ValueError):
        return None
    p[3] = str(u - 1 if u > 1 else u + 1)
    return ".".join(p)


def sonda_motivo(url, user_agent="VLC/3.0", timeout=8.0):
    """Por qué no se ve este canal, mirando los TRES saltos por separado.

    Pedir un canal es panel -> 302 -> origen (ver «El panel no sirve el vídeo»
    en la bitácora), y el que falla casi siempre es el tercero, que es el que
    nadie mira. Un «no se pudo abrir» no distingue entre cuenta caducada, canal
    que ya no existe, servidor caído y paquetes que se pierden por el camino;
    esto sí, y además dice si merece la pena la otra ruta.

    Se llama UNA vez, y solo cuando ya se ha dejado de intentar: la entrada
    está cerrada, así que no hay dos conexiones a la vez.

    NUNCA devuelve la URL ni nada sacado de ella: lleva el usuario y la
    contraseña dentro (norma de la casa). Las frases se construyen a partir del
    código o del tipo de fallo, nunca del texto de la excepción.
    """
    class _NoSigas(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    op = urllib.request.build_opener(_NoSigas, urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})

    # --- salto 1 y 2: el panel, y a dónde manda ---
    destino = None
    try:
        r = op.open(req, timeout=timeout)
        datos = r.read(2048)
        r.close()
        if not datos:
            return "El panel acepta la conexión pero no manda nada."
        # sin redirección: el panel sirve el canal él mismo y va bien
        return ("El panel sí entrega el canal, pero el vídeo no llega a "
                "reproducirse. Puede ser cosa del canal o de este momento.")
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            destino = e.headers.get("Location")
        else:
            return _traduce_http(e.code)
    except socket.timeout:
        return "El panel no contesta a tiempo."
    except urllib.error.URLError as e:
        m = e.reason
        if isinstance(m, (socket.timeout, TimeoutError)):
            return "El panel no contesta a tiempo."
        if isinstance(m, ConnectionRefusedError):
            return "El panel rechaza la conexión."
        if isinstance(m, socket.gaierror):
            return "No se encuentra el panel (¿hay conexión a internet?)."
        return "No se pudo llegar al panel."
    except Exception:
        return "No se pudo llegar al panel."

    if not destino:
        return "El panel redirige a ninguna parte."
    if CF_ABUSO in destino:
        # el corte del CDN disfrazado de redirección: ver el punto 3 de la
        # bitácora. Parece un fallo de red y no lo es
        return ("El CDN ha cortado este canal (redirección de abuso de "
                "Cloudflare). Prueba a cambiar el formato de salida.")

    # --- salto 3: el origen, que es donde muere casi siempre ---
    u = urllib.parse.urlsplit(destino)
    host, puerto = u.hostname, u.port or 443
    if not host:
        return "El panel redirige a una dirección que no se entiende."
    try:
        ips = sorted({x[4][0] for x in socket.getaddrinfo(
            host, puerto, socket.AF_INET, socket.SOCK_STREAM)})
    except Exception:
        return "No se encuentra el servidor de vídeo de este canal."
    if not ips:
        return "No se encuentra el servidor de vídeo de este canal."

    ip = ips[0]
    r1 = _toca(ip, puerto)
    if r1 == "abre":
        return ("El panel y el servidor de vídeo responden los dos, pero el "
                "vídeo no llega a reproducirse. Puede ser cosa del canal.")
    if r1 == "rechazo":
        return ("El servidor de vídeo de este canal rechaza la conexión: se "
                "alcanza, pero ahí no hay nada escuchando.")

    # silencio: es lo interesante. ¿Llega el camino hasta esa red?
    vec = _contigua(ip)
    r2 = _toca(vec, puerto) if vec else "raro"
    if r2 == "silencio":
        return ("El panel responde, pero no se alcanza la red del servidor de "
                "vídeo de este canal: los paquetes se pierden por el camino y "
                "tampoco contesta ninguna dirección de al lado.")
    return ("El panel responde, pero el servidor de vídeo de este canal no se "
            "alcanza desde tu conexión: se pierden los paquetes por el camino, "
            "mientras que una dirección contigua del mismo servidor sí "
            "contesta. Apunta a esa dirección en concreto, no a tu línea.")


def _icon(master, glyph, cmd, size=34, fsize=15):
    return ctk.CTkButton(master, text=glyph, width=size, height=size,
                         corner_radius=size // 2, fg_color="transparent",
                         hover_color=C["surface3"], text_color=C["text"],
                         font=font(fsize), command=cmd)


class _Bar(tk.Toplevel):
    """Ventana translúcida sin borde usada como capa sobre el vídeo. No es
    -topmost: se le pone como dueño el root (ver _own_bars), con lo que va
    encima de él pero debajo de las demás aplicaciones."""
    def __init__(self, master):
        super().__init__(master)
        self.overrideredirect(True)
        try:
            self.attributes("-alpha", ALPHA)
        except Exception:
            pass
        self.configure(bg=OVL)
        self.geometry(OFFSCREEN)
        self.inner = ctk.CTkFrame(self, fg_color=OVL, corner_radius=0)
        self.inner.pack(fill=tk.BOTH, expand=True)

    def place_over(self, x, y, w, h):
        self.geometry(f"{max(w,1)}x{max(h,1)}+{int(x)}+{int(y)}")

    def hide_off(self):
        self.geometry(OFFSCREEN)


class VlcPlayer(ctk.CTkFrame):
    def __init__(self, master, user_agent="VLC/3.0", network_caching=1500,
                 request_fullscreen=None, request_panels=None, on_aspect=None,
                 request_ontop=None, snapshot_dir=None, record_dir=None,
                 volume=90, muted=False):
        super().__init__(master, fg_color=C["video"], corner_radius=0)
        self.user_agent = user_agent
        self.network_caching = network_caching
        self.request_fullscreen = request_fullscreen
        self.request_panels = request_panels
        self.on_aspect = on_aspect
        self.request_ontop = request_ontop
        self.snapshot_dir = snapshot_dir
        self.record_dir = record_dir
        self._url = None               # lo que suena ahora, para poder relanzarlo
        self._url_orig = None          # como lo pidio la aplicacion, sin rodeos
        self._title = ""
        self._rec_on = False
        self._rec_ini = 0.0            # 0 = grabacion armada pero sin empezar
        self._rec_file = None
        self._rec_n = 0                # ficheros de esta grabacion (normalmente uno)
        self._rec_pistas = None        # codecs al empezar, para ver si cambian
        self._want_play = False        # lo que quiere el usuario, no lo que hay
        self._recon_n = 0              # intentos ya fallidos en esta tanda
        self._recon_at = 0.0           # cuando toca el siguiente intento
        self._recon_prob = 0.0         # intento lanzado, esperando a ver si entra
        self._fase = ""                # "abre" (nunca dio imagen) | "cae" | ""
        self._rendido = False          # se dejo de intentar: espera al usuario
        self._sonda = None             # el hilo que le pregunta al servidor
        self._corte = None             # hilo que cierra la entrada anterior (stop() bloquea)
        self._entrada_activa = False   # hay una entrada de VLC en marcha por cerrar
        self._pend_launch = None       # arranque en espera de que termine el corte
        self._t_last = -1              # reloj del canal, para detectar atascos
        self._t_since = 0.0
        self._arrancado = False        # el canal ha llegado a dar imagen
        self._visto = False            # este canal dio imagen alguna vez
        self._msg_txt = ""
        self._msg_after = None
        self._msg_boton = False        # el cartel lleva el boton de reintentar
        self._msg_h = 0                # alto medido del cartel, en pixeles
        self._msg_mid = False          # midiendo: no reentrar
        self._rendido_cab = ""         # primera linea del cartel de rendicion
        self._seeking = False
        self._live = True
        self._overlay_on = True
        self._actions_on = False
        self._focus_ts = 0.0           # última vez que la app ganó el foco
        self._pointer_in = False       # puntero sobre el vídeo o las barras
        self._pos_raton = None         # dónde estaba, para saber si se mueve
        self._raton_after = None
        self._leave_after = None
        self._resize_ts = 0.0          # último Configure (arrastre del borde)
        self._resize_after = None
        self._released = False         # release() ya destruyó las barras
        self._muted = False
        self._vol_prev = int(volume) if int(volume) > 0 else 90
        self._ar = ASPECT_MIN
        self._cfg_after = None
        self._idle_pending = False
        self._fs_mode = False
        self._revealed = True          # en ventana siempre visible
        self._hide_after = None
        self._anim = None
        self._click_after = None       # clic sencillo a la espera de ser doble

        args = ["--no-video-title-show", "--quiet", "--intf", "dummy",
                # Con esto la cadena de salida sobrevive a un relanzamiento del
                # medio: es lo que permite que una grabacion con cortes siga en
                # EL MISMO fichero. Ver la seccion de grabacion.
                "--sout-keep",
                f"--network-caching={network_caching}",
                f"--http-user-agent={user_agent}"]

        # Modo diagnostico: PIEDRASONIC_DEBUG=1 hace que VLC escriba un log
        # detallado junto al ejecutable. Es la unica forma de saber que pasa en
        # un PC ajeno: si el decodificador arranca, que salida de video elige,
        # y si algun modulo falla al cargar. Sin esto, un vídeo en negro no
        # distingue entre "no llega el stream", "no decodifica" y "decodifica
        # pero no pinta".
        if os.environ.get("PIEDRASONIC_DEBUG"):
            base = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                                   else os.path.abspath(__file__))
            self.log_path = os.path.join(base, "piedrasonic-vlc.log")
            args = [a for a in args if a != "--quiet"]
            args += ["--verbose=2", "--file-logging", f"--logfile={self.log_path}",
                     "--log-verbose=2"]
        else:
            self.log_path = None

        self.instance = vlc.Instance(args) if VLC_OK else None
        self.mp = self.instance.media_player_new() if VLC_OK else None

        # ---------- superficie de vídeo ----------
        self.video = tk.Frame(self, bg=C["video"], highlightthickness=0, bd=0)
        self.video.place(x=0, y=0, relwidth=1, relheight=1)
        self.placeholder = ctk.CTkLabel(self.video, text="●  IPTV",
                                        text_color=C["faint"], fg_color=C["video"],
                                        font=font(22, "bold"))
        self.placeholder.place(relx=0.5, rely=0.5, anchor="center")
        self.video.bind("<Button-1>", lambda e: self._on_video_click())
        self.video.bind("<Double-1>", lambda e: self._fs_click())
        self.video.bind("<Motion>", self._on_motion)
        self.video.bind("<Enter>", self._on_enter)
        self.video.bind("<Leave>", self._on_leave)

        # ---------- capas ----------
        self.top = _Bar(self)
        self.bottom = _Bar(self)
        # El aviso de reconexion va en su propia capa y no en un widget dentro
        # del marco del video: ahi quedaria DEBAJO de la superficie nativa de
        # VLC y no se veria (es la misma razon por la que las barras son
        # ventanas aparte).
        self.msg = _Bar(self)
        self.msg_lbl = ctk.CTkLabel(self.msg.inner, text="", text_color=C["text"],
                                    font=font(13, "bold"), wraplength=520)
        self.msg_lbl.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)
        # El boton de reintentar a mano solo aparece cuando se ha dejado de
        # intentar solo. Vive dentro de la capa del aviso porque un boton
        # dentro del marco del video quedaria debajo de la superficie de VLC.
        self.msg_btn = ctk.CTkButton(self.msg.inner, text="Reintentar", width=124,
                                     height=32, font=font(12, "bold"),
                                     fg_color=C["accent"], hover_color=C["accent_hi"],
                                     text_color="#ffffff",
                                     command=self._reintenta_a_mano)

        self.title_lbl = ctk.CTkLabel(self.top.inner, text="", text_color=C["text"],
                                      font=font(14, "bold"), anchor="w")
        self.title_lbl.pack(side=tk.LEFT, padx=(16, 10))
        self.epg_lbl = ctk.CTkLabel(self.top.inner, text="", text_color=C["muted"],
                                    font=font(11), anchor="e")
        self.epg_lbl.pack(side=tk.RIGHT, padx=(10, 16))

        self.actions = ctk.CTkFrame(self.bottom.inner, fg_color="transparent")
        self.bar = ctk.CTkFrame(self.bottom.inner, fg_color="transparent")
        self.bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.btn_play = _icon(self.bar, "▶", self.toggle_pause, 38, 16)
        self.btn_play.pack(side=tk.LEFT, padx=(14, 2), pady=8)
        _icon(self.bar, "⏹", self.stop, 34, 13).pack(side=tk.LEFT, padx=2, pady=8)
        self.time_lbl = ctk.CTkLabel(self.bar, text="--:--", width=52,
                                     text_color=C["text"], font=font(11))
        self.time_lbl.pack(side=tk.LEFT, padx=(8, 4))
        self.seek = ctk.CTkSlider(self.bar, from_=0, to=1000, height=16,
                                  button_color=C["accent"], button_hover_color=C["accent_hi"],
                                  progress_color=C["accent"], fg_color=C["surface3"],
                                  command=self._on_seek_move)
        self.seek.set(1000)
        self.seek.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.seek.bind("<Button-1>", lambda e: setattr(self, "_seeking", True))
        self.seek.bind("<ButtonRelease-1>", self._on_seek_release)
        self.dur_lbl = ctk.CTkLabel(self.bar, text="", width=52,
                                    text_color=C["text"], font=font(11))
        self.dur_lbl.pack(side=tk.LEFT, padx=(4, 8))
        self.live_badge = ctk.CTkLabel(self.bar, text="● DIRECTO",
                                       text_color=C["danger"], font=font(11, "bold"))
        self.live_badge.pack(side=tk.LEFT, padx=(4, 10))
        self.btn_mute = _icon(self.bar, "🔊", self.toggle_mute, 30, 13)
        self.btn_mute.pack(side=tk.LEFT, padx=(2, 2), pady=8)
        self.vol = ctk.CTkSlider(self.bar, from_=0, to=100, width=90, height=16,
                                 button_color=C["text"], button_hover_color="#ffffff",
                                 progress_color=C["muted"], fg_color=C["surface3"],
                                 command=self._on_vol)
        self.vol.set(self._vol_prev)
        self.vol.pack(side=tk.LEFT, padx=(0, 6))
        if muted:
            self.toggle_mute(True)
        self.btn_rec = _icon(self.bar, "●", self.toggle_record, 34, 13)
        self.btn_rec.configure(text_color=C["danger"])
        self.btn_rec.pack(side=tk.LEFT, padx=(4, 0), pady=8)
        self.rec_lbl = ctk.CTkLabel(self.bar, text="", text_color=C["danger"],
                                    font=font(11, "bold"))
        self.btn_snap = _icon(self.bar, "📷", self.snapshot, 34, 13)
        self.btn_snap.pack(side=tk.LEFT, padx=(4, 0), pady=8)
        self.btn_ontop = _icon(self.bar, "📌", self._ontop_click, 34, 13)
        self.btn_ontop.pack(side=tk.LEFT, padx=(4, 0), pady=8)
        self.btn_panels = _icon(self.bar, "◧", self._panels_click, 34, 17)
        self.btn_panels.pack(side=tk.LEFT, padx=(4, 0), pady=8)
        _icon(self.bar, "⛶", self._fs_click, 34, 14).pack(side=tk.LEFT, padx=(4, 14), pady=8)

        self._bind_hwnd()
        self._rootwin = self.winfo_toplevel()
        self._own_bars()
        # reposicionar SÍNCRONO en cada resize/move (root y vídeo) para que las
        # capas no se queden descolgadas al arrastrar el borde de la ventana
        self._rootwin.bind("<Configure>", self._reposition, add="+")
        self.video.bind("<Configure>", self._reposition, add="+")
        self._rootwin.bind("<FocusIn>", self._on_focus_in, add="+")
        # movimiento del ratón sobre vídeo o barras -> revela en pantalla completa
        for w in (self.top, self.top.inner, self.bottom, self.bottom.inner,
                  self.msg, self.msg.inner):
            w.bind("<Motion>", self._on_motion, add="+")
        for w in (self.top, self.bottom, self.msg):
            w.bind("<Enter>", self._on_enter, add="+")
            w.bind("<Leave>", self._on_leave, add="+")
        self.after(120, self._refresh)
        self.after(500, self._tick)
        self.after(600, self._vigila_raton)
        if not VLC_OK:
            self.placeholder.configure(text="VLC no disponible\n" + str(VLC_ERR),
                                       font=font(12))

    # ---- embedding -----------------------------------------------------
    def _bind_hwnd(self):
        if not VLC_OK:
            return
        self.update_idletasks()
        hwnd = self.video.winfo_id()
        if sys.platform.startswith("win"):
            self.mp.set_hwnd(hwnd)
        elif sys.platform == "darwin":
            self.mp.set_nsobject(hwnd)
        else:
            self.mp.set_xwindow(hwnd)
        try:
            self.mp.video_set_mouse_input(False)
            self.mp.video_set_key_input(False)
        except Exception:
            pass

    # ---- posicionamiento de capas -------------------------------------
    def _geom(self):
        try:
            if not self.video.winfo_ismapped():
                return None
            vx = self.video.winfo_rootx()
            vy = self.video.winfo_rooty()
            vw = self.video.winfo_width()
            vh = self.video.winfo_height()
            if vw < 40 or vh < 40:
                return None
            m = 14
            top_h = 44
            bar_h = 58 + (40 if self._actions_on else 0)
            return (vx, vy, vw, vh, m, top_h, bar_h)
        except Exception:
            return None

    def _place(self, prog=1.0):
        g = self._geom()
        if not g:
            return False
        vx, vy, vw, vh, m, top_h, bar_h = g
        # prog<1 => deslizando: arriba baja desde el borde, abajo sube desde abajo
        ot = int((1 - prog) * (top_h + m + 12))
        ob = int((1 - prog) * (bar_h + m + 12))
        self.top.place_over(vx + m, vy + m - ot, vw - 2 * m, top_h)
        self.bottom.place_over(vx + m, vy + vh - bar_h - m + ob, vw - 2 * m, bar_h)
        return True

    def _want(self):
        if not self._want_base():
            return False
        return not (self._fs_mode and not self._revealed)

    def _refresh(self):
        if self._want() and self._place():
            pass
        else:
            self.top.hide_off()
            self.bottom.hide_off()
        self._msg_place()

    def _reposition(self, _e=None):
        # Al cerrar, `release()` destruye las barras y la ventana aún recibe
        # algún Configure mientras se deshace: moverlas entonces es un
        # TclError.
        if self._released:
            return
        # mientras dura el arrastre del borde las barras se quedan puestas; al
        # acabar se vuelve a mirar si el ratón sigue encima
        self._resize_ts = time.time()
        if self._resize_after:
            try:
                self.after_cancel(self._resize_after)
            except Exception:
                pass
        self._resize_after = self.after(900, self._resize_done)
        # inmediato (sigue el arrastre) + pasada after_idle (fija la posición
        # final exacta cuando el layout se ha asentado) => sin lag residual
        self._msg_place()
        if self._want() and self._place():
            if not self._idle_pending:
                self._idle_pending = True
                self.after_idle(self._settle)
            return
        self.top.hide_off()
        self.bottom.hide_off()

    def _settle(self):
        self._idle_pending = False
        if self._want():
            self._place()

    def _resize_done(self):
        # al expirar el margen hay que RE-EVALUAR sí o sí: aunque el ratón no
        # haya cambiado de sitio, la excusa de "se está redimensionando" acaba
        # de caducar y con ella la razón por la que estaban puestas
        self._resize_after = None
        self._pointer_in = self._pointer_over()
        self._refresh()

    def _on_focus_in(self, _e):
        # marca de tiempo: el clic que ACTIVA la ventana no debe además
        # alternar los controles (ver _on_video_click)
        self._focus_ts = time.time()
        self._refresh()

    @staticmethod
    def _hwnd(w):
        """HWND de la ventana de nivel superior que contiene al widget."""
        return _U.GetAncestor(w.winfo_id(), _GA_ROOT)

    def _own_bars(self):
        """Hace a las barras PROPIEDAD del root (GWLP_HWNDPARENT): quedan por
        encima de él y del vídeo sin -topmost, y por debajo de las demás
        aplicaciones, que las recortan/tapan de forma natural."""
        if not sys.platform.startswith("win"):
            try:
                for w in (self.top, self.bottom, self.msg):
                    w.wm_transient(self._rootwin)
            except Exception:
                pass
            return
        try:
            root_h = self._hwnd(self._rootwin)
            for w in (self.top, self.bottom, self.msg):
                _SETPTR(self._hwnd(w), _GWLP_HWNDPARENT, root_h)
                w.lift()
        except Exception:
            pass

    # ---- pantalla completa: auto-ocultar y revelar al mover el ratón ---
    def suelta_barras(self):
        """Desata las barras del root, ANTES de que Tk lo recree.

        Poner o quitar la pantalla completa recrea la ventana nativa del root
        —el HWND de su marco cambia—, y Windows destruye las ventanas que son
        PROPIEDAD de la que se lleva por delante. Las barras se quedaban con un
        handle muerto (medido: `IsWindow` pasaba a False y no volvia a ser
        True), asi que dejaban de aparecer al pasar el raton, y tampoco volvian
        al salir de pantalla completa: habia que reiniciar el programa.

        Sueltas no son propiedad de nadie y sobreviven al cambio; en cuanto el
        root nuevo esta en pie se vuelven a atar con `_own_bars`.
        """
        if not sys.platform.startswith("win"):
            return
        try:
            for w in (self.top, self.bottom, self.msg):
                h = self._hwnd(w)
                if h:
                    _SETPTR(h, _GWLP_HWNDPARENT, None)
        except Exception:
            pass

    def set_fullscreen(self, on):
        # Entrar o salir de pantalla completa reinicia el alternado de los
        # controles. Dentro no pinta nada —quien manda es el auto-ocultado, que
        # los saca al mover el raton— y dejarlo apagado al salir es como se
        # llegaba a la ventana sin controles y sin manera de recuperarlos.
        self._overlay_on = True
        self._click_cancel()             # un clic a medias no cambia de modo
        self._fs_mode = bool(on)
        # El root es otro: hay que volver a atarlas, y decirle al reproductor
        # cual es ahora su ventana de nivel superior.
        self._rootwin = self.winfo_toplevel()
        self._own_bars()
        if on:
            self._revealed = False       # empieza oculto en fullscreen
            self._refresh()
        else:
            self._anim_cancel()
            if self._hide_after:
                try:
                    self.after_cancel(self._hide_after)
                except Exception:
                    pass
                self._hide_after = None
            self._revealed = True        # en ventana siempre visible
            self._refresh()

    def _on_video_click(self):
        """Un clic en el vídeo alterna los controles. Dos, van a pantalla
        completa.

        Por eso el sencillo espera: el primer clic de un doble llega aqui
        igual, y alternaba los controles justo antes de entrar en pantalla
        completa. Dentro no volvian a salir —el alternado manda sobre el
        auto-ocultado, ver `_want_base`— y al salir tampoco, porque el
        interruptor se quedaba apagado: habia que reiniciar el programa.
        """
        if self._fs_mode:
            self._reveal()
            return
        if time.time() - self._focus_ts <= 0.35:
            return             # el clic que trae la ventana al frente no cuenta
        self._click_cancel()
        self._click_after = self.after(300, self._clic_sencillo)

    def _clic_sencillo(self):
        self._click_after = None
        self.toggle_overlay()

    def _click_cancel(self):
        if self._click_after:
            try:
                self.after_cancel(self._click_after)
            except Exception:
                pass
            self._click_after = None

    def _on_motion(self, _e=None):
        if not self._pointer_in:
            self._on_enter()
        if self._fs_mode and self._want_base():
            self._reveal()

    def _on_enter(self, _e=None):
        self._leave_cancel()
        if not self._pointer_in:
            self._pointer_in = True
            self._refresh()

    def _on_leave(self, _e=None):
        # con margen: al cruzar del vídeo a la barra hay un Leave transitorio
        self._leave_cancel()
        self._leave_after = self.after(250, self._leave_check)

    def _leave_cancel(self):
        if self._leave_after:
            try:
                self.after_cancel(self._leave_after)
            except Exception:
                pass
            self._leave_after = None

    def _leave_check(self):
        self._leave_after = None
        dentro = self._pointer_over()
        if dentro != self._pointer_in:
            self._pointer_in = dentro
            self._refresh()

    def _pointer_over(self):
        """True si el puntero está de verdad sobre NUESTRO vídeo o barras (si
        otra ventana tapa ese punto, el punto es suyo y devuelve False)."""
        try:
            px, py = self.winfo_pointerxy()
        except Exception:
            return False
        if not sys.platform.startswith("win"):
            try:
                x, y = self.video.winfo_rootx(), self.video.winfo_rooty()
                return (x <= px < x + self.video.winfo_width()
                        and y <= py < y + self.video.winfo_height())
            except Exception:
                return False
        try:
            under = _U.WindowFromPoint(_POINT(int(px), int(py)))
            if not under:
                return False
            mine = {self._hwnd(w) for w in (self._rootwin, self.top, self.bottom,
                                            self.msg)}
            return _U.GetAncestor(under, _GA_ROOT) in mine
        except Exception:
            return False

    # ---- el ratón, preguntado en vez de esperado -------------------------
    # Sobre el vídeo NO llegan eventos de Tk: la superficie de VLC es una
    # ventana nativa que se queda los Motion y los Enter. En ventana se
    # disimulaba —entrando desde las columnas sí llega un Enter—, pero en
    # pantalla completa no hay ninguna otra ventana de Tk debajo del puntero y
    # las barras no salían NUNCA; y al volver a la ventana tampoco, porque el
    # puntero se quedaba marcado como fuera y ya no había quien lo corrigiera:
    # había que reiniciar el programa. Asi que se pregunta dónde está, cada
    # poco, desde el hilo de Tk.

    RATON = 120         # milisegundos entre preguntas

    def _vigila_raton(self):
        self._raton_after = None
        try:
            self._mira_raton()
        finally:
            self._raton_after = self.after(self.RATON, self._vigila_raton)

    def _mira_raton(self):
        try:
            px, py = self.winfo_pointerxy()
        except Exception:
            return
        movido = (px, py) != self._pos_raton
        self._pos_raton = (px, py)
        dentro = self._pointer_over()
        if dentro != self._pointer_in:
            self._pointer_in = dentro
            self._refresh()
        # En pantalla completa las barras salen al MOVER el ratón, no por
        # estar quieto encima: si no, no se esconderían nunca.
        if movido and dentro and self._fs_mode and self._want_base():
            self._reveal()

    def _want_base(self):
        """ÚNICO sitio donde se decide si debe haber barras (ignorando el
        auto-ocultado de pantalla completa, que va en _want):

          * los controles alternados con un clic mandan sobre todo lo demás;
          * en ventana solo se ven con el ratón encima del reproductor...
          * ...salvo mientras se redimensiona o se mueve la ventana: ahí el
            puntero está en el borde, fuera del vídeo, y ocultarlos sería
            justo lo contrario de lo que uno quiere ver al ajustar el tamaño;
          * minimizada, nunca.
        """
        if not self._overlay_on:
            return False
        if not self._fs_mode and not self._pointer_in and not self._resizing():
            return False
        try:
            return self._rootwin.state() not in ("iconic", "withdrawn")
        except Exception:
            return True

    def _resizing(self):
        return (time.time() - self._resize_ts) < 0.8

    def _reveal(self):
        if self._hide_after:
            try:
                self.after_cancel(self._hide_after)
            except Exception:
                pass
        self._hide_after = self.after(5000, self._unreveal)
        if not self._revealed:
            self._revealed = True
            self._slide_in()
        else:
            self._place(1.0)

    def _unreveal(self):
        self._hide_after = None
        self._revealed = False
        self._refresh()

    def _slide_in(self):
        self._anim_cancel()

        def step(i):
            prog = min(i / 6.0, 1.0)
            if not (self._want() and self._place(prog)):
                return
            if i < 6:
                self._anim = self.after(16, lambda: step(i + 1))
            else:
                self._anim = None
        step(0)

    def _anim_cancel(self):
        if self._anim:
            try:
                self.after_cancel(self._anim)
            except Exception:
                pass
            self._anim = None

    def toggle_overlay(self, show=None):
        self._overlay_on = (not self._overlay_on) if show is None else bool(show)
        self._refresh()

    def enable_actions(self, on):
        self._actions_on = bool(on)
        if on:
            self.actions.pack(side=tk.TOP, fill=tk.X, before=self.bar, pady=(6, 0))
        else:
            self.actions.pack_forget()
        self._refresh()

    # compat
    def hide_controls(self):
        self.toggle_overlay(False)

    def show_controls(self):
        self.toggle_overlay(True)

    # ---- API -----------------------------------------------------------
    def play(self, url, title="", live=True):
        """Pone un canal. Si habia grabacion en marcha se cierra: un fichero
        es un canal, no lo que fuera pasando por la ventana."""
        if self._rec_on:
            self._rec_end()
        self._recon_corta()
        if live:
            # Abrir es la fase «abre»: el vigilante juzga este primer intento
            # igual que a los demas —conectar, arrancar, entrar o no—, pero con
            # paciencia y sin dar la alarma. Antes el primer lanzamiento no lo
            # vigilaba nadie: si VLC se quedaba abriendo para siempre no habia
            # ni cartel ni intento, y si decia Error o Ended una sola vez se
            # entraba de golpe en el bucle de reconexiones.
            self._fase = "abre"
            self._recon_prob = time.monotonic()
        self._visto = False
        self._url_orig = url
        registro.apunta("abre", canal=title, via="directo")
        self._launch(url, title, live)

    def _launch(self, url, title, live, sout=None, resume_ms=0):
        """Arranca el medio de verdad. `sout` es la cadena de salida de VLC
        cuando ademas de verse hay que grabar."""
        if not VLC_OK:
            return
        # Si hay una entrada en marcha, se cierra ANTES y en un hilo (cerrar
        # puede bloquear, ver `_suelta_entrada`). El medio nuevo se
        # pone cuando el corte termina, desde el `_tick`, para no tocar el
        # reproductor a la vez desde dos hilos.
        if self._entrada_activa or self._corte_ocupado():
            if not self._corte_ocupado():
                self._suelta_entrada()
            self._pend_launch = (url, title, live, sout, resume_ms)
            self._want_play = True
            self._url, self._title, self._live = url, title, live
            return
        self._want_play = True
        self._t_last, self._t_since = -1, 0.0
        self._arrancado = False        # todavia no ha dado ni un fotograma
        self._url = url
        self._title = title
        self._live = live
        self.placeholder.place_forget()
        media = self.instance.media_new(url)
        media.add_option(f":http-user-agent={self.user_agent}")
        media.add_option(f":network-caching={self.network_caching}")
        if sout:
            media.add_option(":sout=" + sout)
            # Sin esto VLC manda a la salida TODAS las pistas del canal, y la
            # rama que pinta en pantalla las reproduce todas a la vez: los
            # canales de la TDT llevan tres audios (principal, audiodescrito y
            # otro) y se oian los tres solapados, como un eco. Medido: 3
            # decodificadores y 3 salidas de audio a la vez; con esto, uno.
            # A cambio, en el fichero va el audio que estabas oyendo, no los
            # tres.
            media.add_option(":no-sout-all")
        self.mp.set_media(media)
        self.mp.audio_set_volume(int(self.vol.get()))
        self.mp.play()
        self._entrada_activa = True
        if resume_ms:
            self.after(400, lambda: self._resume_at(resume_ms))
        self.btn_play.configure(text="⏸")
        self.title_lbl.configure(text=title)
        if live:
            self.seek.configure(state="disabled")
            self.live_badge.configure(text="● DIRECTO", text_color=C["danger"])
        else:
            self.seek.configure(state="normal")
            self.live_badge.configure(text="VOD", text_color=C["muted"])
        self._overlay_on = True
        for d in (200, 800, 1600):
            self.after(d, self._refresh)

    def set_epg(self, text):
        self.epg_lbl.configure(text=text)

    def toggle_pause(self):
        if not VLC_OK or self.mp.get_media() is None:
            return
        self.mp.pause()
        self.btn_play.configure(text="⏸" if self.mp.is_playing() else "▶")

    def stop(self):
        if not VLC_OK:
            return
        self._want_play = False
        self._recon_corta()
        if self._rec_on:
            self._rec_end()
        self._suelta_entrada(salvar=False)   # en un hilo: stop() puede bloquear
        self.btn_play.configure(text="▶")
        self.placeholder.configure(text="●  IPTV", font=font(22, "bold"))
        self.placeholder.place(relx=0.5, rely=0.5, anchor="center")

    def _on_vol(self, _v):
        v = int(float(self.vol.get()))
        if v:
            self._vol_prev = v
        self._set_muted(v == 0)
        self._apply_vol()

    def toggle_mute(self, on=None):
        target = (not self._muted) if on is None else bool(on)
        self.vol.set(0 if target else (self._vol_prev or 50))
        self._set_muted(target)
        self._apply_vol()

    def _set_muted(self, on):
        self._muted = bool(on)
        self.btn_mute.configure(text="🔇" if self._muted else "🔊")

    def _apply_vol(self):
        if VLC_OK:
            self.mp.audio_set_volume(int(float(self.vol.get())))

    def get_volume_state(self):
        """(nivel, silenciado) para recordarlo entre sesiones."""
        nivel = self._vol_prev if self._muted else int(float(self.vol.get()))
        return nivel, self._muted

    def _on_seek_move(self, _v):
        if self._seeking:
            self.time_lbl.configure(text=_fmt(self._target_ms()))

    def _target_ms(self):
        dur = self.mp.get_length() if VLC_OK else 0
        return int(dur * (float(self.seek.get()) / 1000.0)) if dur else 0

    def _on_seek_release(self, _e):
        if VLC_OK and not self._live and self.mp.get_length() > 0:
            self.mp.set_position(float(self.seek.get()) / 1000.0)
        self._seeking = False

    def _fs_click(self):
        self._click_cancel()       # era un doble: el sencillo no llega a contar
        if callable(self.request_fullscreen):
            self.request_fullscreen()

    def aspect(self):
        """Proporción (ancho/alto) que debe tener la superficie para que el
        vídeo no salga con franjas arriba y abajo. Nunca baja de 16:9: si el
        canal es más estrecho (4:3, SD anamórfico) las franjas caen a los
        lados, que es justo lo que se busca."""
        return self._ar

    def _read_aspect(self):
        """Proporción del vídeo en curso, o None si todavía no se sabe."""
        if not VLC_OK or self.mp is None:
            return None
        try:
            w, h = self.mp.video_get_size(0)      # (0, 0) hasta que arranca
        except Exception:
            return None
        if not (w and h):
            return None
        return min(max(w / float(h), ASPECT_MIN), ASPECT_MAX)

    def _panels_click(self):
        if callable(self.request_panels):
            self.request_panels()

    def _ontop_click(self):
        if callable(self.request_ontop):
            self.request_ontop()

    def set_ontop(self, on):
        """El root cambia de banda (-topmost): las barras tienen que ir CON él,
        o quedarían por debajo del propio root y desaparecerían."""
        self.btn_ontop.configure(text_color=C["accent"] if on else C["text"])
        for w in (self.top, self.bottom):
            try:
                w.attributes("-topmost", bool(on))
                w.lift()
            except Exception:
                pass
        self._refresh()               # re-fijar -topmost puede mover la geometría

    def snapshot(self):
        """Guarda un PNG del fotograma actual y devuelve la ruta (o None)."""
        if not VLC_OK or self.mp.get_media() is None:
            return None
        d = self.snapshot_dir
        if not (d and os.path.isdir(d)):
            d = _desktop_dir()
        base = re.sub(r"[^\w\- ]", "", self.title_lbl.cget("text")).strip()[:40]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(d, f"{base or 'piedrasonic'} {stamp}.png")
        ok = self.mp.video_take_snapshot(0, path, 0, 0) == 0
        self.btn_snap.configure(text="✓" if ok else "✕")
        self.after(900, lambda: self.btn_snap.configure(text="📷"))
        return path if ok else None

    # ---- grabacion -----------------------------------------------------
    # VLC 3 no deja encender la grabacion sobre algo que ya esta sonando: la
    # cadena de salida se fija al abrir el medio. Asi que grabar es relanzar el
    # mismo canal con un `sout` de dos ramas —una a la pantalla y otra al
    # fichero— y pararlo es relanzarlo sin ella. Se ve un parpadeo de un
    # segundo al empezar y al terminar, y es el precio: la alternativa era
    # abrir una segunda conexion al servidor, y estas cuentas suelen permitir
    # una sola, con lo que grabar te tiraria la imagen.
    # No se recodifica nada: `mux=ts` es el mismo envoltorio con el que viaja
    # el canal, y los codecs se copian tal cual. Va el video y el audio que
    # estas oyendo: ver el `:no-sout-all` de `_launch`.
    #
    # Si el canal se cae y vuelve, la grabacion sigue en EL MISMO fichero. La
    # clave son dos cosas y solo esas: `--sout-keep` en la instancia y **no
    # llamar a `stop()`** al relanzar. Con eso la cadena de salida sobrevive al
    # relanzamiento, el fichero no se cierra y el multiplexor sigue con su
    # linea de tiempo, asi que el corte queda como un hueco hacia delante y no
    # como un empalme roto. Para cerrar la entrada vieja sin llevarse la salida
    # por delante se usa `set_media(None)`: `stop()` si la destruye, y ademas
    # trunca el fichero (medido: 1,55 MB ya grabados desaparecian y quedaba
    # solo el tramo nuevo).

    def toggle_record(self):
        """Empieza o para la grabacion del canal que se esta viendo."""
        if not VLC_OK or self.mp.get_media() is None or not self._url:
            return
        if self._rendido:
            return       # no hay canal: primero el boton de reintentar
        if self._rec_on:
            pos = self._pos_ms()          # antes de soltar la entrada, que luego
            self._rec_end()               # ya no hay a quien preguntarle
            if not self._esta_caido():
                # Cerrar antes de reabrir. Empezar y parar de grabar es
                # relanzar el medio, y relanzarlo sin cerrar dejaba la conexion
                # vieja y la nueva pegadas: con una cuenta de una sola conexion
                # eso es una reconexion fallida y cinco segundos de espera.
                self._suelta_entrada()
                self._launch(self._url, self._title, self._live, resume_ms=pos)
            return       # en medio de un corte, relanzar es cosa del vigilante
        path = self._rec_path()
        if not path:
            self._rec_fail()
            return
        pos = self._pos_ms()
        self._rec_on = True
        self._rec_file = path
        self._rec_n = 1
        self._rec_ini = 0.0
        self._rec_pistas = self._pistas()
        self._rec_ui(True)
        if self._esta_caido():
            # dar a grabar con el canal caido: queda armada y empieza sola en
            # cuanto vuelva (el intento de reconexion ya lleva el sout, y el
            # contador lo arranca `_rec_sigue`)
            self.rec_lbl.configure(text_color=C["muted"])
            return
        self._rec_ini = time.monotonic()
        # cerrar antes de reabrir, y cerrando de verdad: todavia no hay salida
        # que guardar y se relanza sin esperas de por medio
        self._suelta_entrada(salvar=False)
        self._launch(self._url, self._title, self._live, sout=self._sout(),
                     resume_ms=pos)

    def _esta_caido(self):
        """Si ahora mismo no hay imagen y el vigilante esta en ello.

        Relanzar el medio en ese momento es cosa suya y no de quien pulsa
        grabar. No basta con mirar si hay un intento en marcha: al ABRIR un
        canal tambien lo hay, y ahi el canal puede estar ya dando imagen —lo
        que falta es que el vigilante se entere en el siguiente latido—."""
        if not (self._recon_at or self._recon_prob):
            return False
        try:
            return not (self.mp.get_state() == vlc.State.Playing
                        and self.mp.get_time() > 0)
        except Exception:
            return True

    def _sout(self):
        """La cadena de salida de VLC para la grabacion en curso.

        Tiene que salir IDENTICA en cada relanzamiento mientras dure la misma
        grabacion: es por lo que VLC reconoce la salida que guardo y sigue
        escribiendo en el mismo fichero en vez de abrir otro.

        Las barras invertidas de una ruta de Windows se las come el parseador
        de cadenas de VLC; con barras normales el fichero se abre igual."""
        if not (self._rec_on and self._rec_file):
            return None
        dst = self._rec_file.replace("\\", "/")
        return "#duplicate{dst=display,dst=std{access=file,mux=ts,dst='%s'}}" % dst

    def _rec_seg(self):
        """Lo que dura el fichero, cortes incluidos.

        Cuenta el reloj de pared y no solo el rato que hubo senal a proposito:
        el corte esta DENTRO del fichero, como un hueco, asi que un contador
        que se parase diria una duracion que no es la que luego se ve."""
        return (time.monotonic() - self._rec_ini) if self._rec_ini else 0.0

    def _rec_pausa(self):
        """Se corto el flujo. La grabacion no se para —el fichero sigue abierto
        y el hueco se queda dentro—; solo se apaga el rojo del contador."""
        if self._rec_on:
            self.rec_lbl.configure(text_color=C["muted"])

    def _rec_sigue(self):
        """Vuelve la senal. Si la grabacion estaba armada esperandola, aqui es
        donde empieza a contar."""
        if not self._rec_on:
            return
        if not self._rec_ini:
            self._rec_ini = time.monotonic()
        self.rec_lbl.configure(text_color=C["danger"])

    def _pistas(self):
        """Los codecs del canal, para poder ver si cambian al reconectar.

        Devuelve algo como ('h264', 'mpga'), o None si aun no se sabe."""
        try:
            m = self.mp.get_media()
            pistas = m.tracks_get() if m else None
            if not pistas:
                return None
            firma = []
            for t in pistas:
                c = t.codec
                firma.append(bytes([(c >> (8 * i)) & 0xFF for i in range(4)])
                             .decode("latin-1").strip())
            return tuple(sorted(firma))
        except Exception:
            return None

    def _rec_path(self):
        """Ruta del fichero nuevo: nombre del canal y hora de comienzo, que
        no se repite. Devuelve None si ahi no se puede escribir."""
        d = self.record_dir
        if not (d and os.path.isdir(d)):
            d = _desktop_dir()
        base = re.sub(r"[^\w\- ]", "", self._title or "").strip()[:40]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(d, f"{base or 'piedrasonic'} {stamp}.ts")
        try:
            with open(path, "wb"):     # reserva el nombre y prueba la carpeta
                pass                   # (os.access miente en Windows)
        except OSError:
            return None
        return path

    def _rec_nuevo_fichero(self):
        """Abre un fichero nuevo para lo que queda de grabacion.

        Ya no se usa en cada corte —para eso esta la salida que se conserva—
        sino solo cuando el canal vuelve con OTROS codecs. Ahi seguir en el
        mismo fichero no vale: la salida conserva las pistas de antes y mete el
        flujo nuevo en el hueco del viejo, con lo que el segundo tramo sale
        basura (medido: 4.517 quejas del decodificador, frente a 4 de una
        grabacion sana). Mejor cortar por lo sano y decirlo.
        """
        if self._rec_file and os.path.exists(self._rec_file):
            try:
                if os.path.getsize(self._rec_file) == 0:
                    return            # el anterior no escribio nada: se reusa
            except OSError:
                pass
        path = self._rec_path()
        if not path:
            self._rec_end()
            self._rec_fail()
            return
        self._rec_file = path
        self._rec_ini = time.monotonic()
        self._rec_n += 1

    def _rec_end(self):
        """Cierra el estado de grabacion. El fichero lo cierra VLC al parar o
        al cambiar de medio, por eso comprobarlo va con retraso."""
        self._rec_on = False
        self._rec_ini = 0.0
        self._rec_pistas = None
        self._rec_ui(False)
        path, self._rec_file = self._rec_file, None
        solo = self._rec_n <= 1
        self._rec_n = 0
        if path:
            self.after(1500, lambda: self._rec_check(path, solo))

    def _rec_check(self, path, avisa=True):
        """Si el fichero se quedo vacio, la grabacion no llego a arrancar: se
        borra y el boton lo dice. Un .ts de 0 bytes en la carpeta es peor.
        `avisa` es False cuando la grabacion ya dejo otros ficheros buenos: ahi
        uno ultimo vacio no es un fallo que contar."""
        try:
            if os.path.exists(path) and os.path.getsize(path) == 0:
                os.remove(path)
                if avisa:
                    self._rec_fail()
        except OSError:
            pass

    def _rec_fail(self):
        self.btn_rec.configure(text="✕")
        self.after(1200, lambda: self.btn_rec.configure(text="●"))

    def _rec_ui(self, on):
        """El boton hundido en rojo mientras graba, con el tiempo al lado."""
        if on:
            self.btn_rec.configure(fg_color=C["danger"], hover_color=C["danger"],
                                   text_color="#ffffff")
            self.rec_lbl.configure(text=_fmt(0), text_color=C["danger"])
            self.rec_lbl.pack(side=tk.LEFT, padx=(4, 0), before=self.btn_snap)
        else:
            self.btn_rec.configure(fg_color="transparent",
                                   hover_color=C["surface3"],
                                   text_color=C["danger"])
            self.rec_lbl.pack_forget()

    def _pos_ms(self):
        """Donde vamos. En directo no hay a donde volver; en catch-up si."""
        if self._live:
            return 0
        try:
            return max(0, int(self.mp.get_time()))
        except Exception:
            return 0

    def _resume_at(self, ms, tries=0):
        """Devuelve el catch-up al punto en el que estaba tras relanzarlo."""
        if ms <= 0 or tries > 15:
            return
        try:
            if self.mp.get_length() > 0:
                self.mp.set_time(int(ms))
                return
        except Exception:
            return
        self.after(200, lambda: self._resume_at(ms, tries + 1))

    # ---- reconexion ----------------------------------------------------
    # Aqui pasan dos cosas distintas y hasta ahora se trataban igual, que era
    # el fallo:
    #
    #   * ABRIR un canal. Mientras no ha dado ni un fotograma, lo que esta
    #     pasando es que arranca. Hay canales que tardan un buen rato en
    #     conectar, y soltarles a los dos segundos un «no arranca, probando
    #     otra vez en 4 s» es dar la alarma por algo que todavia no ha fallado.
    #     En esta fase se es paciente y CALLADO: el cartel dice «conectando»,
    #     sin cuentas atras y sin hablar de nada perdido.
    #   * Que se CAIGA lo que ya estaba sonando. Eso si es una perdida, y se
    #     reintenta solo, que es de lo que se trata.
    #
    # Y en los dos casos se DEJA de intentar. Un bucle de reintentos cada cinco
    # segundos contra un canal que no va son setecientas peticiones por hora
    # con la cuenta del usuario, y asi es como se acaba baneado. Se intenta un
    # numero contado de veces, con esperas cada vez mas largas, y luego se
    # para: el cartel dice que ha pasado —preguntandoselo al servidor, porque
    # «no se pudo abrir» no es una explicacion— y ofrece un boton para volver a
    # intentarlo a mano.
    #
    # Se mira el estado desde el latido de Tk y no desde los eventos de VLC a
    # proposito: los eventos llegan en un hilo de VLC, y desde ahi ni se tocan
    # widgets ni se puede llamar a `stop()` sin arriesgarse a un abrazo mortal.

    PACIENCIA = 15      # segundos para que un intento de RECONEXION conecte
    # Abriendo se espera mas: reconectando ya se sabe que el servidor iba hace
    # un momento, pero un canal recien pulsado puede tardar de verdad, y es lo
    # que el usuario acaba de pedir. Antes este primer intento no lo vigilaba
    # NADIE: si VLC se quedaba abriendo no habia ni cartel ni limite.
    PACIENCIA_ABRE = 25
    ATASCO = 10         # segundos con el reloj parado para dar el flujo por muerto
    # Arrancar no es lo mismo que atascarse. Hay canales que tardan un buen
    # rato en dar el primer fotograma —La 1 lo hace— y con los diez segundos
    # del atasco se daban por perdidos antes de empezar. Mientras el reloj no
    # se haya movido NUNCA, el canal esta arrancando, no muerto.
    ARRANQUE = 45       # segundos que se le dan a un canal conectado para dar imagen
    CALLADO = 2.5       # antes de esto no se dice nada: casi todos entran ya

    # Esperas entre intentos, en segundos; `ESPERAS[n]` es lo que se espera
    # antes del intento n+1, y cuando se acaba la lista se deja de intentar.
    # Al ABRIR son cortas y pocas: el usuario esta delante y acaba de pulsar,
    # asi que o entra pronto o mas vale decirselo. Tras una CAIDA van creciendo,
    # porque puede no haber nadie mirando y lo que importa es no martillear.
    ESPERAS_ABRE = (2, 3)
    ESPERAS_CAE = (5, 5, 10, 20, 30, 60)

    def _watchdog(self):
        """Vigila el flujo. Solo en directo: en un catch-up que se acaba, que
        se acabe."""
        if not (VLC_OK and self._want_play and self._url and self._live):
            return
        if self._corte_ocupado():
            # El corte anterior sigue en su hilo: stop() de VLC tiene tomado el
            # lock interno de libvlc, y llamar aqui a get_state/get_time (o
            # cualquier funcion de VLC) se quedaria esperando ese lock y colgaria
            # el hilo de Tk, y con el toda la app. No se toca VLC hasta que el
            # corte termine; solo se mantiene el aviso de reintento.
            if self._recon_at and (self._recon_at - time.monotonic()) <= 0:
                self._msg(self._texto_intento())
            return
        if self._rendido:                        # ya no se intenta: manda el usuario
            self._mira_sonda()
            return
        ahora = time.monotonic()
        st = self.mp.get_state()
        if self._recon_at:                       # esperando al siguiente intento
            falta = self._recon_at - ahora
            if falta > 0:
                self._msg(self._texto_espera(falta))
            elif self._corte_ocupado():
                # aun cerrando la entrada anterior: esperar a que
                # termine su hilo antes de relanzar, sin bloquear la interfaz
                self._msg(self._texto_intento())
            else:
                self._reintenta()
            return
        if self._recon_prob:                     # lanzado: ¿ha entrado o no?
            # Si ya esta reproduciendo, es que ha conectado y lo que falta es
            # que arranque: se le da el tiempo de arranque y no el de conectar.
            if st == vlc.State.Playing:
                tope = self.ARRANQUE
            else:
                tope = (self.PACIENCIA_ABRE if self._fase == "abre"
                        else self.PACIENCIA)
            va = ahora - self._recon_prob
            if st == vlc.State.Playing and self.mp.get_time() > 0:
                self._recon_ok()
            elif st in (vlc.State.Error, vlc.State.Ended) or va > tope:
                self._recon_falla()
            elif va > self.CALLADO:
                # tarda: se dice, pero sin alarma. Antes de CALLADO no se dice
                # nada, que la mayoria entra en menos de eso y un cartel que
                # parpadea en cada canal es peor que ninguno.
                self._msg(self._texto_intento())
            return
        if st in (vlc.State.Error, vlc.State.Ended) or self._atascado(st, ahora):
            self._perdida()

    def _atascado(self, st, ahora):
        """Dice que si cuando VLC cree que reproduce pero el reloj del canal
        lleva parado un buen rato: el flujo esta muerto aunque nadie avise."""
        if st != vlc.State.Playing or self._seeking:
            self._t_last, self._t_since = -1, 0.0
            return False
        t = self.mp.get_time()
        if t > 0:
            self._arrancado = True       # ha dado imagen: a partir de aqui, atasco
        if t != self._t_last:
            self._t_last, self._t_since = t, ahora
            return False
        if not self._t_since:
            self._t_since = ahora
            return False
        return ahora - self._t_since > (self.ATASCO if self._arrancado
                                        else self.ARRANQUE)

    def _suelta_entrada(self, salvar=None):
        """Cierra la conexion con el servidor, SIEMPRE EN UN HILO APARTE.

        Cerrar la entrada de VLC es sincrono y puede BLOQUEAR mucho: `stop()`
        espera a que el hilo de entrada de VLC termine, y cuando el servidor
        deja de responder ese hilo se queda atascado en una lectura de red que
        no vuelve. Llamado desde el hilo de Tk —como hacia el vigilante en cada
        reconexion— eso congelaba TODA la app (la pila del hilo principal moria
        dentro de libvlc_media_player_stop). Por eso el corte se hace en un
        hilo demonio: la interfaz sigue viva, y el relanzamiento espera a que
        el corte termine (ver `_corte_ocupado`, `_pend_launch` y la puerta del
        vigilante).

        Grabando se usa `set_media(None)` en vez de `stop()` para no llevarse
        por delante la cadena de salida —y con ella el fichero—."""
        if salvar is None:
            salvar = self._rec_on
        mp = self.mp

        def corta():
            try:
                if salvar:
                    vlc.libvlc_media_player_set_media(mp, None)
                else:
                    mp.stop()
            except Exception:
                pass
            finally:
                self._entrada_activa = False

        self._corte = threading.Thread(target=corta, daemon=True)
        self._corte.start()

    def _corte_ocupado(self):
        """True mientras se esta cerrando la entrada anterior, en su hilo."""
        return self._corte is not None and self._corte.is_alive()

    def _perdida(self):
        """Se ha caido lo que estaba sonando."""
        # `_visto` y no `_arrancado`: el segundo lo reinicia cada `_launch`, y
        # empezar o parar de grabar relanza el medio. Lo que decide si esto es
        # una caida o una apertura es si ESTE canal ha dado imagen alguna vez
        # desde que se pulso, no desde el ultimo relanzamiento.
        self._fase = "cae" if self._visto else "abre"
        self._recon_n = 0
        self._rec_pausa()
        self._suelta_entrada()
        self.btn_play.configure(text="▶")
        self._t_last, self._t_since = -1, 0.0
        registro.apunta("cae" if self._fase == "cae" else "no_abre",
                        canal=self._title,
                        via="directo",
                        resultado="fallo")
        self._programa_intento()

    def _programa_intento(self):
        """Pone hora al siguiente intento, o se rinde si ya no quedan."""
        esperas = self.ESPERAS_ABRE if self._fase == "abre" else self.ESPERAS_CAE
        if self._recon_n >= len(esperas):
            self._rendirse()
            return
        self._recon_at = time.monotonic() + esperas[self._recon_n]

    def _texto_espera(self, falta):
        # Abriendo no hay cuenta atras: la espera es de dos o tres segundos y
        # lo que toca decir es que se sigue conectando, no que falta poco para
        # otro intento.
        if self._fase == "abre":
            return self._texto_intento()
        seg = max(1, int(falta + 0.5))
        if self._recon_n:
            t = "No se pudo reconectar (intento %d). Otro en %d s…" % (self._recon_n, seg)
        else:
            t = "Se ha perdido la conexión. Reconectando en %d s…" % seg
        return t + (" · grabación en pausa" if self._rec_on else "")

    def _texto_intento(self):
        if self._fase == "abre":
            t = "Conectando con el canal…"
            if self._recon_n:
                t += "  (intento %d)" % (self._recon_n + 1)
        else:
            t = "Reconectando…" + (" (intento %d)" % self._recon_n
                                   if self._recon_n > 1 else "")
        return t + (" · grabación en pausa" if self._rec_on else "")

    def _reintenta(self):
        self._recon_at = 0.0
        self._recon_n += 1
        self._recon_prob = time.monotonic()
        url = self._url_orig or self._url
        self._msg(self._texto_intento())
        # grabando, `_sout()` trae la misma cadena de siempre: al ser identica
        # a la anterior, VLC reaprovecha la salida que guardo y el fichero
        # sigue creciendo donde lo dejo
        self._launch(url, self._title, self._live, sout=self._sout())

    def _recon_ok(self):
        volvia = (self._fase == "cae")
        self._fase = ""
        tardo = ((time.monotonic() - self._recon_prob) * 1000
                 if self._recon_prob else None)   # antes de borrarlo, que si no
        self._recon_prob = 0.0                    # el cuaderno se queda sin dato
        self._recon_n = 0
        self._arrancado = True
        self._visto = True
        registro.apunta("suena", canal=self._title,
                        via="directo",
                        resultado="ok",
                        ms=tardo, detalle="intento %d" % (self._recon_n + 1))
        segui = self._rec_on
        cola = ""
        if segui:
            # Si el canal vuelve con otros codecs, seguir en el mismo fichero
            # daria basura: se cierra y se abre otro. Ver `_rec_nuevo_fichero`.
            ahora = self._pistas()
            if ahora and self._rec_pistas and ahora != self._rec_pistas:
                self._rec_nuevo_fichero()
                self._rec_pistas = ahora
                cola = " · el canal cambió de formato: la grabación sigue en otro fichero"
                # hay que relanzar: la salida que hay abierta escribe en el
                # fichero de antes, y la cadena nueva es la que abre el otro
                if self._rec_on:
                    self._launch(self._url, self._title, self._live, sout=self._sout())
            elif ahora and not self._rec_pistas:
                self._rec_pistas = ahora
            if not cola:
                cola = " · sigue grabando en el mismo fichero"
        self._rec_sigue()
        if volvia:
            self._msg("Conexión recuperada" + cola, 2500)
        else:
            self._msg_off()          # abrir un canal que entra no se rotula

    def _recon_falla(self):
        registro.apunta("intento", canal=self._title,
                        via="directo",
                        resultado="fallo",
                        ms=(time.monotonic() - self._recon_prob) * 1000
                        if self._recon_prob else None,
                        detalle="intento %d de la fase %s"
                                % (self._recon_n + 1, self._fase or "?"))
        self._recon_prob = 0.0
        self._suelta_entrada()
        self._programa_intento()

    def _recon_corta(self):
        """Se cambia de canal o se para: se olvida lo que hubiera en marcha."""
        self._fase = ""
        self._rendido = False
        self._sonda = None
        self._recon_n = 0
        self._recon_at = 0.0
        self._recon_prob = 0.0
        self._msg_off()

    # ---- cuando se deja de intentar ------------------------------------

    def _rendirse(self):
        """Se agotaron los intentos. A partir de aqui manda el usuario.

        Es lo contrario de lo que habia: el bucle de reintentos cada cinco
        segundos no acababa nunca, y contra un canal que sencillamente no va
        eso es martillear al servidor con la cuenta del usuario hasta que la
        corta. Aqui se para, se dice por que y se ofrece el boton."""
        self._rendido = True
        self._recon_at = 0.0
        self._recon_prob = 0.0
        cola = ""
        if self._rec_on:
            # cerrar la grabacion ANTES de parar: `stop()` con la salida viva
            # trunca el fichero (ver la seccion de grabacion)
            self._rec_end()
            cola = " La grabación se ha cerrado."
        self._suelta_entrada(salvar=False)
        self.btn_play.configure(text="▶")
        self._rendido_cab = (
            ("No se ha podido abrir el canal." if self._fase == "abre"
             else "Se perdió la conexión y no ha vuelto.")
            + " Se ha dejado de intentar." + cola)
        self._pinta_rendido("Preguntando al servidor qué ha pasado…")
        self._pregunta_al_servidor()

    def _pinta_rendido(self, motivo):
        self._msg(self._rendido_cab + "\n" + motivo, boton=True)

    def _pregunta_al_servidor(self):
        """Le pregunta al servidor por que no da el canal, en un hilo.

        El hilo deja la respuesta en una lista y quien la mira es Tk, desde el
        latido (`_mira_sonda`). Llamar a `after()` desde un hilo que no es el
        de Tk PARECE que funciona y no lo es: en cuanto la ventana no esta
        dentro de su bucle, Tcl revienta con «main thread is not in main
        loop». Es la misma norma que la carga de la lista."""
        self._sonda = []
        url, ua, caja = (self._url_orig or self._url), self.user_agent, self._sonda
        threading.Thread(target=lambda: caja.append(sonda_motivo(url, ua)),
                         daemon=True).start()

    def _mira_sonda(self):
        if self._sonda:
            motivo, self._sonda = self._sonda[0], None
            registro.apunta("rendido", canal=self._title,
                            via="directo",
                            resultado="rendido", detalle=motivo)
            self._pinta_rendido(motivo)

    def _reintenta_a_mano(self):
        """El botón del cartel: vuelve a empezar como si se acabara de pulsar
        el canal. Lo que estaba en marcha ya se cerró al rendirse."""
        if not (VLC_OK and self._url):
            return
        self._sonda = None
        self._rendido = False
        self._recon_n = 0
        self._recon_at = 0.0
        self._fase = "abre" if self._live else ""
        self._recon_prob = time.monotonic() if self._live else 0.0
        # se reintenta con la misma url de siempre
        url = self._url_orig or self._url
        self._msg(self._texto_intento() if self._live else "")
        self._launch(url, self._title, self._live)

    # ---- aviso sobre el video ------------------------------------------
    def _msg(self, texto, ms=0, boton=False):
        """Pone el cartel en el centro del video. `ms` lo quita solo; `boton`
        le añade el de reintentar a mano."""
        if self._msg_after:
            try:
                self.after_cancel(self._msg_after)
            except Exception:
                pass
            self._msg_after = None
        self._msg_txt = texto
        self.msg_lbl.configure(text=texto)
        if bool(boton) != self._msg_boton:
            # Se reordena solo al cambiar, que esto pasa por aqui en cada
            # latido: el boton se empaqueta ANTES para que se quede con su
            # trozo de abajo, y la etiqueta se expande por lo que queda.
            self._msg_boton = bool(boton)
            self.msg_lbl.pack_forget()
            self.msg_btn.pack_forget()
            if self._msg_boton:
                self.msg_btn.pack(side=tk.BOTTOM, pady=(0, 16))
            self.msg_lbl.pack(fill=tk.BOTH, expand=True, padx=14,
                              pady=(16, 12) if self._msg_boton else 10)
        self._msg_place()
        if ms:
            self._msg_after = self.after(ms, self._msg_off)

    def _msg_off(self):
        self._msg_after = None
        self._msg_txt = ""
        if self._msg_boton:
            self._msg_boton = False
            self.msg_btn.pack_forget()
            self.msg_lbl.pack_forget()
            self.msg_lbl.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)
        self.msg.hide_off()

    def _msg_place(self):
        g = self._geom()
        if not (self._msg_txt and g):
            self.msg.hide_off()
            return
        vx, vy, vw, vh, m, top_h, bar_h = g
        w = max(320, min(vw - 2 * m, 560))
        # El alto no se escribe a mano: se le pregunta al contenido, que es la
        # misma leccion que `_ajusta_alto` de settings.py. Con un numero fijo,
        # el cartel de error —tres lineas y un boton— se quedaba recortado.
        # Ojo con el escalado: `wraplength` es de un widget de CustomTkinter y
        # va SIN escalar, mientras que `geometry` de esta capa (un Toplevel de
        # Tk pelado) va en pixeles de pantalla.
        h = 56
        if not self._msg_mid:
            self._msg_mid = True
            try:
                esc = ctk.ScalingTracker.get_widget_scaling(self.msg_lbl)
                self.msg_lbl.configure(wraplength=int(max(200, (w - 40) / esc)))
                self.msg.update_idletasks()
                h = max(56, min(self.msg.inner.winfo_reqheight(), 300))
            except Exception:
                h = 150 if self._msg_boton else 56
            finally:
                self._msg_mid = False
            self._msg_h = h
        h = self._msg_h or h
        self.msg.place_over(vx + (vw - w) // 2, vy + (vh - h) // 2, w, h)

    def set_panels_hidden(self, on):
        """Refleja en el botón si las columnas laterales están ocultas."""
        self.btn_panels.configure(text="▭" if on else "◧")

    def _tick(self):
        if self._pend_launch is not None and not self._corte_ocupado():
            args = self._pend_launch
            self._pend_launch = None
            self._launch(*args)
        try:
            if not self._corte_ocupado():
                ar = self._read_aspect()
                if ar and abs(ar - self._ar) > 0.01:
                    self._ar = ar
                    if callable(self.on_aspect):
                        self.on_aspect()
        except Exception:
            pass
        if self._rec_on:
            self.rec_lbl.configure(text=_fmt(self._rec_seg() * 1000))
        self._watchdog()
        try:
            if VLC_OK and self.mp.get_media() is not None and not self._seeking and not self._corte_ocupado():
                pos = self.mp.get_time()
                dur = self.mp.get_length()
                self.time_lbl.configure(text=_fmt(pos))
                if self._live:
                    self.dur_lbl.configure(text="")
                    self.seek.set(1000)
                else:
                    self.dur_lbl.configure(text=_fmt(dur))
                    if dur > 0:
                        self.seek.set(1000 * pos / dur)
        except Exception:
            pass
        self.after(500, self._tick)

    def release(self):
        self._released = True
        self._want_play = False
        if self._raton_after:
            try:
                self.after_cancel(self._raton_after)
            except Exception:
                pass
            self._raton_after = None
        try:
            for w in (self.top, self.bottom, self.msg):
                w.destroy()
        except Exception:
            pass
        try:
            if VLC_OK:
                self.mp.stop()
                self.mp.release()
                self.instance.release()
        except Exception:
            pass
