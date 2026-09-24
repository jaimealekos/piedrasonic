# Pruebas

Aquí vive el **canal de televisión falso**: un servidor que sirve un `.ts` en
directo por HTTP, **se cae cuando se le manda** y, si se le pide por `url_rota`,
contesta un 404 como el panel de verdad cuando el canal ya no está. Con él se
prueba lo único de este programa que no se puede probar a mano de forma
razonable: qué pasa cuando el servidor corta a mitad de una grabación, o cuando
un canal sencillamente no va. Las dos cosas ocurren solas y cuando les da la
gana.

No sustituye a probar contra el servidor de verdad —esa sigue siendo la norma
de la casa—, pero un corte a voluntad no hay otra manera de conseguirlo.

## Qué hay

| Fichero | Qué es |
|---|---|
| `canal_falso.py` | El servidor. Se puede usar suelto (`python canal_falso.py`) para mirar con VLC a mano, o importar desde una prueba. |
| `graba_con_cortes.py` | La prueba gorda: mete el `VlcPlayer` de verdad en una ventana fuera de pantalla, graba, provoca los cortes y juzga lo que queda en el disco. |
| `controles.py` | Cuándo deben verse las barras del reproductor y cuándo no. Rápida, y no necesita ni servidor ni vídeo. |
| `arranque_lento.py` | Que un canal que tarda en arrancar no se dé por caído, y que uno que se cae de verdad sí. |
| `canal_roto.py` | Que un canal que no va se diga y se deje de intentar, en vez de reintentarlo cada cinco segundos para siempre. |
| `donde_muere.py` | **No es una prueba, es un diagnóstico.** Dice en cuál de los tres saltos —panel, 302, origen— ha muerto la cosa, contra la cuenta de verdad. Con `--vigila` se queda sondeando y deja un CSV. |
| `listas_guardadas.py` | Que se guarden las diez últimas listas, que roten sin pisarse y que recuperar una vieja ponga **sus** canales. Llama a `_guarda_cache()`, no imita la rotación: la primera versión la imitaba y pasó en verde con la función sin escribir. |
| `cartel_carga.py` | Que el cartel «Pidiendo los canales…» se quite al terminar la descarga, incluso con la vista en «Favoritos». |
| `interfaz.py` | El panel de ajustes, el modo edición de categorías y el arranque. Con un panel Xtream falso: ni servidor de verdad ni ffmpeg. |

El vídeo de pruebas (`fuente.ts`, `fuente-otra.ts`) **no se versiona**: son 30
MB y se fabrican solos con ffmpeg la primera vez. Lo que se graba va a
`grabado/`, que tampoco se versiona.

## Cómo se corre

Hace falta **ffmpeg y ffprobe en el PATH** —para fabricar el vídeo de pruebas y
para mirar el resultado por dentro— y el VLC de 64 bits en `vendor\vlc`, el
mismo que usa el empaquetado.

```bash
python pruebas\graba_con_cortes.py
```

Tarda un par de minutos: los cortes son de verdad y el reproductor se toma sus
cinco segundos entre reintentos, como en la vida real. Para correr uno solo:

```bash
python pruebas\graba_con_cortes.py cortes
```

Las otras van aparte:

```bash
python pruebas\controles.py
```

```bash
python pruebas\arranque_lento.py
```

```bash
python pruebas\canal_roto.py
```

```bash
python pruebas\listas_guardadas.py
```

```bash
python pruebas\interfaz.py
```

## Qué comprueba, y por qué eso

| Escenario | Qué pasa | Qué tiene que salir |
|---|---|---|
| `cortes` | el servidor cuelga dos veces | **un** fichero, con dos huecos hacia delante |
| `atasco` | el flujo se muere callado: el socket sigue vivo pero solo llega relleno | **un** fichero, con un hueco |
| `formato` | el canal vuelve con otros códecs | **dos** ficheros, y el segundo limpio |
| `singrabar` | lo mismo pero sin grabar | ningún fichero, y que reconecte igual |

Y en los cuatro, dos cosas que no se ven mirando el vídeo:

- **Nunca dos conexiones abiertas a la vez.** Estas cuentas suelen permitir una
  sola, así que solaparlas te tiraría la imagen. Es lo que obliga a cerrar la
  entrada con `set_media(None)` en vez de dejarla morir sola.
- **Los sellos de tiempo no van hacia atrás.** Un salto hacia atrás es
  exactamente el fallo que tenía pegar los trozos a posteriori: el
  demultiplexor lo lee como fin de fichero y el reproductor se para ahí.

Lo que se considera bueno está en `ESPERADO`, y las quejas del decodificador
tienen un tope de 50 —una grabación sana da 2 ó 4— salvo el primer fichero de
`formato`, que tiene derecho a acabar sucio: son los segundos que se tarda en
notar que el canal ha cambiado de formato, y de lo que se trata es de que ese
daño quede acotado en vez de comerse el resto de la grabación.

## El canal que no va

`canal_roto.py` mira lo que sustituyó al bucle de reintentos infinito, que
contra un canal que no existe eran unas setecientas peticiones por hora con la
cuenta del usuario. Cuatro cosas:

1. **se deja de intentar** —tres intentos al abrir, seis tras una caída— ;
2. **se dice el error**, preguntándoselo al servidor: «el servidor dice que ese
   canal no existe (404)»;
3. **hay botón para reintentar a mano**;
4. y la que da sentido a las otras: **rendido no se manda ni una petición más**.

Las esperas de la caída se acortan a propósito dentro de la prueba
(`ESPERAS_CORTAS`): lo que se comprueba es la política, no cuántos segundos dura
cada espera; con los valores de verdad tardaría cuatro minutos. Y el tope de
peticiones es 8 y no 3 porque **VLC pide dos veces por intento** (medido: 7 en
total, contando la sonda que pregunta el motivo).

## Dónde muere una conexión

`donde_muere.py` no prueba el programa: mira la red. Existe porque pedir un
canal son **tres saltos** (panel → 302 → origen) y el que falla casi siempre es
el tercero, que es el que nadie mira. Sin esto, «no se ve nada» es
indistinguible entre cuenta caducada, canal que ya no existe, servidor caído y
filtro del operador.

```bash
python pruebas\donde_muere.py
```

Descubre los orígenes él solo leyendo los 302 —cambian, y hay al menos siete—, y
sondea cada uno junto a **una IP contigua**, que es la comparación que decide: si
la contigua va y el origen no, el camino hasta esa red está bien. Ojo con leerlo
de más: las contiguas resultaron ser **más servidores del mismo servicio**, no
terceros neutrales.

Y como el filtro va y viene, hay modo de vigilancia, que es lo que hay que
lanzar **cuando falle de verdad**:

```bash
python pruebas\donde_muere.py --vigila 120
```

Sondea cada origen en el 443 y en el 22, más una contigua y dos controles, y va
escribiendo `ruta.csv`. Lo que se busca ahí: **si el 443 cae mientras el 22
aguanta**, el filtro va contra IP:puerto y mover el servicio de puerto puede
bastar. Esa medición es la que falta para cerrar el asunto.

## Los controles del reproductor

`controles.py` mira dos cosas distintas, porque dos fallos distintos dejaban al
usuario sin controles y los dos acababan igual: reiniciando el programa.

**La decisión.** `_want()` dice si en ese momento tiene que haber barras. Ahí
estaba el primer fallo: entrar a pantalla completa es un doble clic en el
vídeo, y el **primer** clic de ese doble llegaba como clic sencillo, que es el
que alterna los controles. Se apagaban justo antes de entrar.

**Y si las ventanas siguen vivas.** Poner o quitar la pantalla completa recrea
la ventana nativa del root, y Windows destruye las que son propiedad suya —las
barras lo son—. Se quedaban con un handle muerto y no volvían a aparecer nunca.
Esto **no se ve mirando la decisión**: `_want()` seguía diciendo que sí, y la
prueba pasaba en verde con el fallo delante. Por eso ahora se le pregunta a
Windows, poniendo la ventana a pantalla completa de verdad un par de segundos,
que es la única forma de provocarlo.

## Trampas del propio banco de pruebas

Dos cosas que costaron un rato y no conviene volver a descubrir:

- **El servidor va dentro del mismo proceso que la prueba.** Con el servidor en
  un subproceso, VLC daba «connection refused» de forma errática contra un
  servidor que estaba perfectamente escuchando y al que `curl` sí entraba.
- **Un paso del guion por latido, y el latido cada 250 ms.** Con latidos de segundos, «corta» y
  «vuelve» caían en el mismo y se ejecutaban seguidos: el corte no llegaba a
  ocurrir y la prueba salía en verde con un corte de menos.

Y una del servidor: se sirve alineado a paquete TS (188 bytes), porque un
servidor de verdad no te entrega media cabecera y VLC se lía si empieza a
mitad —se veía como un error de descompresión que no venía a cuento—.
