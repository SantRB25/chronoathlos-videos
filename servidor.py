# -*- coding: utf-8 -*-
"""El servidor que recibe los videos de llegada y se los muestra a cada corredor.

Vive en el VPS. Hace tres cosas y nada más:

1. **Recibe** los clips que le manda la máquina de la meta, por HTTPS con un token
2. **Muestra** a cada corredor su llegada, en una página con un enlace impredecible
3. **Borra** lo que pasó los seis meses

## Por qué HTTPS y no SSH

La otra opción era subir por SFTP. Se descartó: obligaría a dejar una llave del
servidor en la notebook que anda por los predios de las carreras. Si esa notebook
se pierde, con SSH se pierde el servidor entero; con un token de subida se pierde
la capacidad de subir videos, y se revoca cambiando una línea.

## El enlace

    https://videos.chronoathlos.com.py/corrida-2026/a7f3k9m2x1qp/

El token va **en la ruta**, no como `?token=`: así no se filtra por el encabezado
Referer cuando el corredor comparte la página, y el enlace se ve limpio.

Son 12 caracteres de `secrets.token_urlsafe`, unos 72 bits. Adivinar uno a ciegas
no es una posibilidad realista, y no hay índice que listar: pedir la carpeta del
evento sin token devuelve 404 como cualquier otra cosa que no existe.

    python3 servidor.py                 # arranca en el 8090
    python3 servidor.py --puerto 9000
"""
import argparse
import datetime as dt
import hashlib
import tempfile
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import unicodedata

from flask import (Flask, abort, jsonify, render_template, request,
                   send_from_directory)

import correos

RAIZ = os.path.dirname(os.path.abspath(__file__))
DATOS = os.environ.get("CHRONO_DATOS", os.path.join(RAIZ, "datos"))
BASE = os.path.join(DATOS, "indice.db")
MESES_QUE_SE_GUARDA = 6
LARGO_TOKEN = 12
TOPE_SUBIDA_MB = 60          # un clip pesa ~9; el resto es margen

app = Flask(__name__, template_folder=os.path.join(RAIZ, "plantillas"))
app.config["MAX_CONTENT_LENGTH"] = TOPE_SUBIDA_MB * 1024 * 1024

_candado = threading.Lock()


# ── base ────────────────────────────────────────────────────────────────

def conectar():
    conn = sqlite3.connect(BASE, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def preparar():
    os.makedirs(DATOS, exist_ok=True)
    conn = conectar()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS videos (
            evento    TEXT NOT NULL,
            dorsal    TEXT NOT NULL,
            token     TEXT NOT NULL UNIQUE,
            nombre    TEXT,
            tiempo    TEXT,
            posicion  TEXT,
            categoria TEXT,
            titulo    TEXT,
            subido    TEXT,
            visitas   INTEGER DEFAULT 0,
            PRIMARY KEY (evento, dorsal)
        );
        CREATE INDEX IF NOT EXISTS ix_token  ON videos(token);
        CREATE INDEX IF NOT EXISTS ix_subido ON videos(subido);
    """)
    columnas = {r[1] for r in conn.execute("PRAGMA table_info(videos)")}
    for columna in ("firma", "revision", "distancia", "posicion_tipo"):
        if columna not in columnas:
            conn.execute("ALTER TABLE videos ADD COLUMN " + columna + " TEXT")
    correos.preparar(conn)
    conn.commit()
    conn.close()


# ── nombres ─────────────────────────────────────────────────────────────

def apodo(texto):
    """«Corrida Gym Gaviões 2026» -> «corrida-gym-gavioes-2026».

    Se usa como nombre de carpeta y como parte de la URL, así que no puede
    llevar acentos, espacios ni nada que se escape del directorio.
    """
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = texto.encode("ascii", "ignore").decode("ascii").lower()
    texto = re.sub(r"[^a-z0-9]+", "-", texto).strip("-")
    return texto[:60] or "evento"


def carpeta_de(evento, token):
    """La carpeta del video, verificando que no se salga de `datos/`.

    Los dos valores vienen de afuera —uno de la URL, otro del formulario— así que
    no alcanza con limpiarlos: se comprueba que la ruta final caiga adentro.
    """
    ruta = os.path.abspath(os.path.join(DATOS, apodo(evento), token))
    if not ruta.startswith(os.path.abspath(DATOS) + os.sep):
        abort(404)
    return ruta


def token_valido(token):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{8,64}", token or ""))


# ── subida ──────────────────────────────────────────────────────────────

def autorizado():
    """Compara el token de subida sin filtrar por dónde deja de coincidir."""
    esperado = os.environ.get("CHRONO_TOKEN_SUBIDA", "")
    if not esperado:
        return False
    return hmac.compare_digest(request.headers.get("X-Token", ""), esperado)


@app.post("/api/subir")
def subir():
    """Recibe el clip de un corredor. Se puede repetir sin efectos raros.

    Si el dorsal ya estaba subido conserva **su mismo token**: el enlace que
    quedó en un correo enviado no puede cambiar porque el operador reintentó.
    """
    if not autorizado():
        return jsonify({"error": "token de subida inválido"}), 403

    evento = request.form.get("evento", "").strip()
    dorsal = request.form.get("dorsal", "").strip()
    if not evento or not dorsal:
        return jsonify({"error": "faltan evento o dorsal"}), 400
    video = request.files.get("video")
    if not video:
        return jsonify({"error": "falta el archivo de video"}), 400

    with _candado:
        conn = conectar()
        try:
            fila = conn.execute("SELECT * FROM videos WHERE evento=? AND dorsal=?",
                                (apodo(evento), dorsal)).fetchone()
            token = fila["token"] if fila else secrets.token_urlsafe(LARGO_TOKEN)[:LARGO_TOKEN]
            carpeta = carpeta_de(evento, token)
            os.makedirs(carpeta, exist_ok=True)
            # La fila apunta a una versión completa. Una subida interrumpida nunca
            # reemplaza parcialmente el MP4 publicado ni mezcla foto y video.
            with tempfile.TemporaryDirectory(prefix=".recibiendo-", dir=carpeta) as tmp:
                video.save(os.path.join(tmp, "video.mp4"))
                foto = request.files.get("foto")
                if foto:
                    foto.save(os.path.join(tmp, "foto.jpg"))
                if not os.path.getsize(os.path.join(tmp, "video.mp4")):
                    return jsonify(error="video vacío"), 400
                firma = firma_archivos(tmp)
                esperada = request.form.get("firma")
                if esperada and not hmac.compare_digest(esperada, firma):
                    return jsonify(error="contenido incompleto"), 422
                if not fila or fila["firma"] != firma:
                    revision = "v-" + secrets.token_hex(12)
                    destino = os.path.join(carpeta, revision)
                    os.mkdir(destino)
                    for archivo in os.listdir(tmp):
                        os.replace(os.path.join(tmp, archivo), os.path.join(destino, archivo))
                    conn.execute("""
                        INSERT INTO videos (evento,dorsal,token,nombre,tiempo,posicion,
                                            categoria,titulo,subido,firma,revision)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(evento,dorsal) DO UPDATE SET
                          nombre=COALESCE(NULLIF(excluded.nombre,''),videos.nombre),
                          tiempo=COALESCE(NULLIF(excluded.tiempo,''),videos.tiempo),
                          posicion=COALESCE(NULLIF(excluded.posicion,''),videos.posicion),
                          categoria=COALESCE(NULLIF(excluded.categoria,''),videos.categoria),
                          titulo=COALESCE(NULLIF(excluded.titulo,''),videos.titulo),
                          subido=excluded.subido, firma=excluded.firma, revision=excluded.revision
                        """, (apodo(evento), dorsal, token,
                        request.form.get("nombre", ""), request.form.get("tiempo", ""),
                        request.form.get("posicion", ""), request.form.get("categoria", ""),
                        request.form.get("titulo", evento), dt.datetime.now().isoformat(timespec="seconds"),
                        firma, revision))
                    conn.commit()
                conn.execute("UPDATE videos SET distancia=?,posicion_tipo=?,posicion=?,categoria=? WHERE evento=? AND dorsal=?",
                    (request.form.get('distancia',''),request.form.get('posicion_tipo',''),request.form.get('posicion',''),
                     request.form.get('categoria',''),apodo(evento),dorsal))
                conn.commit()
        finally:
            conn.close()
    return jsonify(ok=True, token=token, firma=firma,
                   url="/%s/%s/" % (apodo(evento), token))


def firma_archivos(carpeta):
    h = hashlib.sha256()
    for clave, archivo in (("video", "video.mp4"), ("miniatura", "foto.jpg")):
        h.update(clave.encode("ascii"))
        ruta = os.path.join(carpeta, archivo)
        if os.path.isfile(ruta):
            with open(ruta, "rb") as f:
                for bloque in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(bloque)
    return h.hexdigest()


def carpeta_version(fila):
    carpeta = carpeta_de(fila["evento"], fila["token"])
    return os.path.join(carpeta, fila["revision"]) if fila["revision"] else carpeta


@app.get("/api/estado")
def estado():
    """Qué hay subido de un evento. El operador lo usa para saltar lo ya hecho."""
    if not autorizado():
        return jsonify({"error": "token de subida inválido"}), 403
    evento = apodo(request.args.get("evento", ""))
    with _candado:
        conn = conectar()
        try:
            filas = conn.execute("SELECT * FROM videos WHERE evento=?", (evento,)).fetchall()
            videos = {}
            for fila in filas:
                carpeta = carpeta_version(fila)
                if not os.path.isfile(os.path.join(carpeta, "video.mp4")):
                    continue
                firma = fila["firma"] or firma_archivos(carpeta)
                if not fila["firma"]:
                    conn.execute("UPDATE videos SET firma=? WHERE evento=? AND dorsal=?",
                                 (firma, evento, fila["dorsal"]))
                videos[fila["dorsal"]] = {k: fila[k] for k in ("dorsal", "token", "subido", "visitas")}
                videos[fila["dorsal"]].update(firma=firma, url="/%s/%s/" % (evento, fila["token"]))
            conn.commit()
        finally:
            conn.close()
    return jsonify(evento=evento, videos=videos, protocolo=3)


@app.post('/api/metadatos')
def actualizar_metadatos():
    """Actualizar la clasificación sin retransmitir el video, sobre una firma conocida."""
    if not autorizado():return jsonify(error='token inválido'),403
    d=request.get_json(silent=True) or {}
    with _candado:
        conn=conectar()
        try:
            fila=conn.execute('SELECT firma FROM videos WHERE evento=? AND dorsal=?',
                (apodo(d.get('evento')),str(d.get('dorsal')))).fetchone()
            if not fila or not d.get('firma') or fila['firma']!=d['firma']:
                return jsonify(error='El video remoto no coincide con esta versión.'),409
            columnas=('nombre','tiempo','posicion','categoria','distancia','posicion_tipo','titulo')
            conn.execute('UPDATE videos SET '+','.join(k+'=?' for k in columnas)+' WHERE evento=? AND dorsal=?',
                tuple('' if d.get(k) is None else str(d[k]) for k in columnas)+(apodo(d['evento']),str(d['dorsal'])))
            conn.commit()
        finally:conn.close()
    return jsonify(ok=True)


# ── lo que ve el corredor ───────────────────────────────────────────────

def buscar(evento, token):
    conn = conectar()
    fila = conn.execute("SELECT * FROM videos WHERE evento=? AND token=?",
                        (apodo(evento), token)).fetchone()
    conn.close()
    return fila


@app.get("/<evento>/<token>/")
def pagina(evento, token):
    if not token_valido(token):
        abort(404)
    fila = buscar(evento, token)
    carpeta = carpeta_version(fila) if fila else carpeta_de(evento, token)
    if not fila or not os.path.exists(os.path.join(carpeta, "video.mp4")):
        abort(404)

    conn = conectar()
    conn.execute("UPDATE videos SET visitas=visitas+1 WHERE token=?", (token,))
    conn.commit()
    conn.close()

    return render_template("video.html", v=dict(fila), evento=apodo(evento), token=token,
                           hay_foto=os.path.exists(os.path.join(carpeta, "foto.jpg")))


@app.get("/<evento>/<token>/<archivo>")
def archivo(evento, token, archivo):
    """El video y la foto. `conditional` habilita el salto por Range, que es lo
    que le permite al celular adelantar sin bajar el clip entero."""
    if not token_valido(token) or archivo not in ("video.mp4", "foto.jpg"):
        abort(404)
    if not buscar(evento, token):
        abort(404)
    descarga = request.args.get("bajar") == "1"
    fila = buscar(evento, token)
    nombre = "llegada-%s-%s.mp4" % (apodo(evento), fila["dorsal"])
    return send_from_directory(carpeta_version(fila), archivo,
                               conditional=True, as_attachment=descarga,
                               download_name=nombre if descarga else None)


# ── los correos ─────────────────────────────────────────────────────────
#
# Salen de acá y no de la máquina de la meta: el servidor tiene IP fija —lo que
# el relay SMTP de Brevo exige—, está siempre encendido, y solo puede mandar
# enlaces a videos que ya tiene guardados. Ver correos.py.

def _base_url():
    """La dirección pública, para armar los enlaces de los correos."""
    return os.environ.get("CHRONO_URL_PUBLICA") or request.host_url.rstrip("/")


@app.post("/api/correos")
def encolar_correos():
    """Recibe el mapa dorsal -> email y arranca el envío."""
    if not autorizado():
        return jsonify({"error": "token de subida inválido"}), 403
    datos = request.get_json(silent=True) or {}
    evento = apodo(datos.get("evento", ""))
    if not evento:
        return jsonify({"error": "falta el evento"}), 400

    problema = correos.falta_configurar(correos.configuracion())
    if problema:
        return jsonify({"error": problema}), 400

    with _candado:
        conn = conectar()
        try:
            destinatarios = dict(datos.get('destinatarios') or {})
            desactualizados = []
            if 'firmas' in datos:
                firmas = datos.get('firmas') or {}
                for dorsal in list(destinatarios):
                    fila = conn.execute('SELECT firma FROM videos WHERE evento=? AND dorsal=?', (evento,str(dorsal))).fetchone()
                    if not fila or not firmas.get(dorsal) or fila['firma'] != firmas[dorsal]:
                        desactualizados.append(str(dorsal));del destinatarios[dorsal]
                if desactualizados:
                    return jsonify(error='Hay videos pendientes de actualizar.',desactualizados=desactualizados),409
            resumen = correos.encolar(conn, evento, destinatarios)
        finally:
            conn.close()

    if datos.get("solo_encolar"):
        return jsonify(resumen)

    arranque = correos.enviar(
        conectar, _base_url(), evento,
        reenviar=bool(datos.get("reenviar")),
        solo=set(str(d) for d in datos["dorsales"]) if datos.get("dorsales") else None,
        prueba=datos.get("probar") or None)
    if "error" in arranque:
        return jsonify(dict(resumen, error=arranque["error"])), 409
    return jsonify(dict(resumen, enviando=True))


def _webhook_autorizado(token_en_ruta=""):
    """Deja pasar al webhook de Brevo, venga como venga la credencial.

    Se aceptan varias vías a propósito. La documentación de Brevo describe el
    modo «Basic» —credenciales en la URL, que viajan como `Authorization:
    Basic`— pero **no dice en qué cabecera manda el token** en el modo Token.
    Descubrirlo en producción, el día que rebote un correo, es tarde.

    De todas, la recomendada es **Basic**: es la única que Brevo documenta con
    precisión, y la credencial viaja en una cabecera en vez de en la URL. Eso
    importa: las rutas quedan escritas en los registros de acceso del proxy, y
    un token en la ruta es un secreto en texto plano en cada línea del log.
    """
    import base64

    esperado = os.environ.get("CHRONO_TOKEN_BREVO", "")
    usuario = os.environ.get("CHRONO_WEBHOOK_USUARIO", "")
    clave = os.environ.get("CHRONO_WEBHOOK_CLAVE", "")
    if not esperado and not clave:
        return False                     # sin nada configurado, no entra nadie

    def igual(a, b):
        return bool(a) and bool(b) and hmac.compare_digest(str(a), str(b))

    cabecera = request.headers.get("Authorization", "")
    if clave and cabecera.startswith("Basic "):
        try:
            par = base64.b64decode(cabecera[6:]).decode("utf-8", "replace")
        except (ValueError, TypeError):
            par = ""
        suyo_usuario, _, suya_clave = par.partition(":")
        if igual(suyo_usuario, usuario) and igual(suya_clave, clave):
            return True

    if esperado:
        if cabecera.startswith("Bearer ") and igual(cabecera[7:].strip(), esperado):
            return True
        # el modo Token de Brevo no documenta su cabecera: se miran las usuales
        for nombre in ("X-Auth-Token", "X-Webhook-Token", "X-Token", "Token",
                       "X-Sib-Token", "Authorization"):
            if igual(request.headers.get(nombre, "").strip(), esperado):
                return True
        if igual(token_en_ruta, esperado):
            return True                  # compatibilidad: el token en la ruta

    return False


@app.post("/api/brevo", defaults={"token": ""})
@app.post("/api/brevo/<token>")
def webhook_brevo(token):
    """Lo que Brevo cuenta de cada correo: entregado, abierto, rebotado, spam.

    Sin esto, el panel muestra en verde trescientos correos que pueden haber
    ido todos a spam: el relay devuelve 250 y nosotros no nos enteramos de
    nada más. Este es el único lugar donde se sabe qué pasó de verdad.

    Un intento sin credencial devuelve 404 y no 403: no se confirma que la
    ruta exista.
    """
    if not _webhook_autorizado(token):
        abort(404)

    datos = request.get_json(silent=True) or {}
    conn = conectar()
    try:
        return jsonify(correos.anotar_evento(conn, datos))
    finally:
        conn.close()


@app.get("/api/correos")
def estado_correos():
    if not autorizado():
        return jsonify({"error": "token de subida inválido"}), 403
    conn = conectar()
    try:
        return jsonify(correos.estado(conn, apodo(request.args.get("evento", ""))))
    finally:
        conn.close()


@app.post("/api/borrar-evento")
def borrar_evento():
    """Borra un evento entero: sus archivos, sus videos y su historial de correos.

    Existe para sacar las pruebas antes de la carrera, sin pedirle nada a quien
    administra el servidor. Hay que escribir el nombre dos veces porque no tiene
    vuelta atrás: el volumen no guarda copias de lo que se borra. Los correos ya
    enviados no se pueden retirar; lo que se borra es el historial que evita
    reenviarlos, así que un evento borrado y vuelto a subir los manda de nuevo.
    """
    if not autorizado():
        return jsonify({"error": "token de subida inválido"}), 403
    datos = request.get_json(silent=True) or {}
    if not str(datos.get("evento") or "").strip():
        return jsonify({"error": "falta el evento"}), 400
    evento = apodo(datos.get("evento"))
    if apodo(datos.get("confirmar")) != evento:
        return jsonify({"error": "repetí el nombre del evento en «confirmar» para borrarlo"}), 409
    conn = conectar()
    try:
        # Primero los archivos y después el índice, igual que la limpieza diaria:
        # un corte en el medio deja huérfano un archivo, no un enlace sin video.
        for fila in conn.execute("SELECT token FROM videos WHERE evento = ?", (evento,)).fetchall():
            shutil.rmtree(carpeta_de(evento, fila["token"]), ignore_errors=True)
        videos = conn.execute("DELETE FROM videos WHERE evento = ?", (evento,)).rowcount
        correos_borrados = conn.execute("DELETE FROM correos WHERE evento = ?", (evento,)).rowcount
        conn.commit()
    finally:
        conn.close()
    shutil.rmtree(os.path.join(DATOS, evento), ignore_errors=True)
    print("borrado el evento %s: %d videos, %d correos" % (evento, videos, correos_borrados),
          flush=True)
    return jsonify({"ok": True, "evento": evento, "videos": videos,
                    "correos": correos_borrados})


@app.get("/salud")
def salud():
    conn = conectar()
    n = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    conn.close()
    libre = shutil.disk_usage(DATOS).free / 1e9
    falta = correos.falta_configurar(correos.configuracion())
    # La versión la fija quien despliega; sin ella no se puede saber qué código corre.
    version = os.environ.get("CHRONO_VERSION", "") or "sin informar"
    return jsonify({"ok": True, "version": version, "videos": n,
                    "espacio_libre_gb": round(libre, 1),
                    "correos": "listo" if not falta else falta})


@app.errorhandler(404)
def no_esta(_):
    return render_template("no-esta.html"), 404


# ── limpieza ────────────────────────────────────────────────────────────

def limpiar(meses=MESES_QUE_SE_GUARDA):
    """Borra los videos que pasaron el plazo. Primero el archivo, después el
    registro: si se corta en el medio, la próxima pasada lo termina."""
    corte = (dt.datetime.now() - dt.timedelta(days=30 * meses)).isoformat()
    conn = conectar()
    viejos = conn.execute("SELECT evento, token FROM videos WHERE subido < ?",
                          (corte,)).fetchall()
    for v in viejos:
        carpeta = os.path.join(DATOS, v["evento"], v["token"])
        shutil.rmtree(carpeta, ignore_errors=True)
    conn.execute("DELETE FROM videos WHERE subido < ?", (corte,))
    conn.commit()
    for evento in os.listdir(DATOS):
        ruta = os.path.join(DATOS, evento)
        if os.path.isdir(ruta) and not os.listdir(ruta):
            os.rmdir(ruta)
    conn.close()
    return len(viejos)


def limpieza_diaria():
    """Un hilo que limpia una vez por día. Es `daemon`: no traba el apagado."""
    def tarea():
        while True:
            try:
                borrados = limpiar()
                if borrados:
                    print("Limpieza: %d videos vencidos" % borrados, flush=True)
            except Exception as e:                                 # noqa: BLE001
                print("Limpieza falló: %s" % e, flush=True)
            threading.Event().wait(24 * 3600)
    threading.Thread(target=tarea, daemon=True).start()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--puerto", type=int, default=int(os.environ.get("PORT", 8090)))
    p.add_argument("--limpiar", action="store_true", help="borrar los vencidos y salir")
    args = p.parse_args()

    preparar()
    if args.limpiar:
        print("%d videos borrados" % limpiar())
        return 0

    if not os.environ.get("CHRONO_TOKEN_SUBIDA"):
        print("FALTA CHRONO_TOKEN_SUBIDA: nadie va a poder subir nada.", flush=True)
    falta = correos.falta_configurar(correos.configuracion())
    if falta:
        print("Correos sin configurar: %s" % falta, flush=True)

    limpieza_diaria()
    from waitress import serve
    print("Videos de llegada en el puerto %d — datos en %s" % (args.puerto, DATOS),
          flush=True)
    serve(app, host="0.0.0.0", port=args.puerto, threads=8)
    return 0


if __name__ == "__main__":
    sys.exit(main())
