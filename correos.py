# -*- coding: utf-8 -*-
"""Manda los correos desde el servidor, no desde la máquina de la meta.

## Por qué acá y no allá

La primera versión mandaba los correos desde la notebook del operador. Tres
razones para haberlo movido, y la del medio es la que de verdad importa:

1. **La IP.** El relay SMTP de Brevo filtra por IP autorizada, y la notebook está
   en una red distinta en cada carrera. El servidor tiene IP fija: se autoriza
   una vez y listo
2. **Un correo no puede apuntar a un video que no existe.** Acá solo se manda lo
   que el servidor ya tiene guardado; desde la notebook se podía mandar el correo
   antes de que la subida terminara
3. **Si la notebook se apaga, el envío sigue.** Un lote de trescientos con la
   conexión de un predio tarda, y el operador quiere irse a su casa

De paso, la clave de Brevo deja de viajar en una notebook que anda por los
eventos: vive en las variables de entorno del servidor.

## La regla que no se toca

Un correo enviado no se vuelve a mandar salvo que se pida expresamente. Mandarle
a trescientas personas el mismo correo dos veces es peor que no mandarlo.
"""
import datetime as dt
import json
import os
import re
import smtplib
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import requests

API_BREVO = "https://api.brevo.com/v3/smtp/email"
SMTP_BREVO = ("smtp-relay.brevo.com", 587)
ESPERA = 30
CORREO_VALIDO = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def por_smtp(clave):
    """`xsmtpsib-…` es del relay SMTP; `xkeysib-…` es de la API HTTP."""
    return str(clave or "").startswith("xsmtpsib-")


def configuracion():
    """Lo que el servidor sabe de Brevo, todo por variables de entorno."""
    return {
        "clave": os.environ.get("BREVO_CLAVE", ""),
        "usuario_smtp": os.environ.get("BREVO_USUARIO_SMTP", ""),
        "remitente": os.environ.get("CHRONO_REMITENTE", ""),
        "remitente_nombre": os.environ.get("CHRONO_REMITENTE_NOMBRE", "ChronoAthlos"),
        "responder_a": os.environ.get("CHRONO_RESPONDER_A", ""),
        # Sin espaciado por defecto. El cupo de Brevo es **diario**, no de
        # velocidad: mandar los trescientos despacio consume lo mismo que
        # mandarlos rápido, y de paso tardaría más de una hora. El 429 se
        # maneja donde corresponde, reintentando. Se puede espaciar igual si
        # algún día hace falta.
        "por_hora": int(os.environ.get("CHRONO_CORREOS_POR_HORA", "0")),
    }


def falta_configurar(cfg):
    if not cfg["clave"]:
        return "Falta BREVO_CLAVE en el servidor."
    if not cfg["remitente"]:
        return "Falta CHRONO_REMITENTE en el servidor."
    if por_smtp(cfg["clave"]) and not cfg["usuario_smtp"]:
        return "Con clave SMTP hace falta también BREVO_USUARIO_SMTP."
    return None


# ── el mensaje ──────────────────────────────────────────────────────────

def armar(v, enlace, cfg):
    """El HTML del correo. Tablas y estilos en línea: es lo único que
    interpretan igual Gmail, Outlook y los clientes de celular."""
    nombre = (v.get("nombre") or "").split()[0] if v.get("nombre") else ""
    saludo = "Hola %s," % nombre if nombre else "Hola,"
    tiempo, dorsal = v.get("tiempo") or "", v.get("dorsal") or ""
    evento = v.get("titulo") or "la carrera"

    celda_tiempo = ""
    if tiempo:
        celda_tiempo = ("""<td align="center" style="padding:14px 8px">
            <div style="font-size:11px;letter-spacing:.08em;color:#6b7683;text-transform:uppercase">Tiempo</div>
            <div style="font-size:20px;font-weight:bold;padding-top:3px">%s</div>
          </td>""" % tiempo)

    html = """\
<!doctype html><html><body style="margin:0;padding:0;background:#f4f6f8">
<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="background:#f4f6f8">
<tr><td align="center" style="padding:24px 12px">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0"
         style="max-width:480px;background:#ffffff;border-radius:12px;overflow:hidden;
                font-family:Helvetica,Arial,sans-serif;color:#1a1d21">
    <tr><td style="padding:28px 28px 8px">
      <p style="margin:0 0 14px;font-size:16px;line-height:1.5">%(saludo)s</p>
      <p style="margin:0;font-size:16px;line-height:1.5">
        Ya está tu video de llegada en <strong>%(evento)s</strong>.</p>
    </td></tr>
    <tr><td style="padding:18px 28px 0">
      <a href="%(enlace)s" style="text-decoration:none;display:block">
        <img src="%(enlace)sfoto.jpg" width="424" alt="Tu llegada a la meta"
             style="width:100%%;max-width:424px;display:block;border-radius:8px;border:0">
      </a>
    </td></tr>
    <tr><td style="padding:20px 28px 0">
      <table role="presentation" width="100%%" cellpadding="0" cellspacing="0"
             style="border:1px solid #e3e7ec;border-radius:8px"><tr>
          <td align="center" style="padding:14px 8px;border-right:1px solid #e3e7ec">
            <div style="font-size:11px;letter-spacing:.08em;color:#6b7683;text-transform:uppercase">Dorsal</div>
            <div style="font-size:20px;font-weight:bold;padding-top:3px">%(dorsal)s</div>
          </td>%(celda_tiempo)s</tr></table>
    </td></tr>
    <tr><td align="center" style="padding:22px 28px 6px">
      <a href="%(enlace)s"
         style="display:inline-block;background:#ffb020;color:#17120a;text-decoration:none;
                padding:14px 34px;border-radius:9px;font-weight:bold;font-size:16px">
        Ver mi llegada</a>
    </td></tr>
    <tr><td style="padding:16px 28px 28px">
      <p style="margin:0;font-size:12px;line-height:1.6;color:#6b7683;text-align:center">
        El video queda disponible seis meses. Si el botón no funciona, copiá este enlace:<br>
        <span style="color:#8b97a6;word-break:break-all">%(enlace)s</span></p>
      <p style="margin:16px 0 0;font-size:12px;color:#8b97a6;text-align:center">
        Cronometraje y video: <strong style="color:#6b7683">%(remitente)s</strong></p>
    </td></tr>
  </table>
</td></tr></table></body></html>""" % {
        "saludo": saludo, "evento": evento, "enlace": enlace, "dorsal": dorsal,
        "celda_tiempo": celda_tiempo, "remitente": cfg["remitente_nombre"]}

    texto = ("%s\n\nYa está tu video de llegada en %s.\n\nDorsal %s%s\n\n"
             "Miralo acá: %s\n\nEl video queda disponible seis meses.\n%s\n"
             % (saludo, evento, dorsal, ("  ·  Tiempo " + tiempo) if tiempo else "",
                enlace, cfg["remitente_nombre"]))
    return html, texto


# ── el transporte ───────────────────────────────────────────────────────

class Relay:
    """La conexión SMTP, abierta una vez para todo el lote.

    Es la misma lección que la subida de los clips: abrir la conexión por cada
    mensaje cuesta más que el envío. Acá hay handshake TLS **y** autenticación,
    y serían trescientas veces. Se reconecta sola si el servidor corta.
    """

    def __init__(self, usuario, clave):
        self.usuario, self.clave = usuario, clave
        self.conexion = None

    def mandar(self, mensaje):
        for intento in (1, 2):
            try:
                if self.conexion is None:
                    maquina = smtplib.SMTP(*SMTP_BREVO, timeout=ESPERA)
                    maquina.starttls()
                    maquina.login(self.usuario, self.clave)
                    self.conexion = maquina
                self.conexion.send_message(mensaje)
                return mensaje["Message-ID"], None
            except smtplib.SMTPAuthenticationError as e:
                return None, "el usuario o la clave SMTP no sirven (%s)" % e.smtp_code
            except smtplib.SMTPRecipientsRefused:
                return None, "la dirección fue rechazada"
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                    OSError) as e:
                self.conexion = None
                if intento == 2:
                    return None, "no se pudo conectar al SMTP: %s" % e
            except smtplib.SMTPException as e:
                return None, str(e)
        return None, "no se pudo enviar"

    def cerrar(self):
        if self.conexion is not None:
            try:
                self.conexion.quit()
            except smtplib.SMTPException:
                pass
            self.conexion = None


def por_api(clave, destino, nombre, asunto, html, texto, cfg, etiqueta=""):
    cuerpo = {
        "sender": {"name": cfg["remitente_nombre"], "email": cfg["remitente"]},
        "to": [{"email": destino, "name": nombre or destino}],
        "subject": asunto, "htmlContent": html, "textContent": texto,
        "tags": [etiqueta] if etiqueta else ["video-llegada"],
    }
    if cfg.get("responder_a"):
        cuerpo["replyTo"] = {"email": cfg["responder_a"]}

    for intento in (1, 2, 3):
        try:
            r = requests.post(API_BREVO, timeout=ESPERA, json=cuerpo, headers={
                "api-key": clave, "content-type": "application/json",
                "accept": "application/json"})
        except requests.RequestException as e:
            if intento == 3:
                return None, str(e)
            time.sleep(3 * intento)
            continue
        if r.status_code in (200, 201, 202):
            return r.json().get("messageId", "enviado"), None
        if r.status_code == 401:
            return None, "la clave de Brevo no sirve"
        if r.status_code == 400:
            return None, "rechazado: %s" % r.text[:160]    # la dirección o el remitente
        # 429: se va demasiado rápido. Es el único caso donde frenar sirve.
        if r.status_code == 429 and intento < 3:
            time.sleep(20 * intento)
            continue
        return None, "Brevo respondió %d: %s" % (r.status_code, r.text[:120])
    return None, "no se pudo enviar"


def mensaje_smtp(destino, nombre, asunto, html, texto, cfg, etiqueta=""):
    m = EmailMessage()
    m["Subject"] = asunto
    if etiqueta:
        # Brevo devuelve esto tal cual en el webhook: es lo que permite saber
        # de qué corredor hablaba un rebote.
        m["X-Mailin-custom"] = etiqueta
    m["From"] = formataddr((cfg["remitente_nombre"], cfg["remitente"]))
    m["To"] = formataddr((nombre or "", destino))
    if cfg.get("responder_a"):
        m["Reply-To"] = cfg["responder_a"]
    m["Message-ID"] = make_msgid(domain=cfg["remitente"].split("@")[-1])
    m.set_content(texto)                       # el plano primero, el HTML después
    m.add_alternative(html, subtype="html")
    return m


# ── la cola ─────────────────────────────────────────────────────────────

TRABAJO = {"corriendo": False, "evento": "", "hechos": 0, "total": 0,
           "enviados": 0, "fallados": 0, "detalle": "", "error": None,
           "termino": None}
_candado = threading.Lock()


# Lo que Brevo puede contar de un correo, de peor a mejor. El orden importa:
# un «abierto» que llega después de un «entregado» avanza, pero un «entregado»
# que llega tarde no puede pisar a un «rebotado».
DESENLACES = ["enviado", "entregado", "abierto", "clicado"]
PROBLEMAS = {"hard_bounce": "rebotó", "soft_bounce": "rebotó (temporal)",
             "blocked": "bloqueado", "spam": "lo marcaron como spam",
             "invalid_email": "dirección inválida",
             "deferred": "demorado", "error": "error"}
LOGROS = {"delivered": "entregado", "opened": "abierto",
          "unique_opened": "abierto", "click": "clicado"}


def preparar(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS correos (
            evento   TEXT NOT NULL,
            dorsal   TEXT NOT NULL,
            email    TEXT,
            estado   TEXT DEFAULT 'pendiente',
            intentos INTEGER DEFAULT 0,
            mensaje  TEXT,
            detalle  TEXT,
            momento  TEXT,
            PRIMARY KEY (evento, dorsal)
        )""")
    # Qué pasó del otro lado, que es distinto de qué pasó al mandarlo
    columnas = {f[1] for f in conn.execute("PRAGMA table_info(correos)")}
    for nombre in ("desenlace", "desenlace_motivo", "desenlace_momento"):
        if nombre not in columnas:
            conn.execute("ALTER TABLE correos ADD COLUMN %s TEXT" % nombre)
    conn.commit()


def anotar_evento(conn, datos):
    """Guarda lo que Brevo cuenta de un correo ya enviado.

    Brevo devuelve en `tag` lo que le mandamos en `X-Mailin-custom`, que es
    «evento|dorsal». Con eso se ubica la fila sin depender del Message-ID, que
    el relay reescribe.

    Un evento nunca retrocede: si ya sabemos que rebotó, un «entregado» que
    llega tarde no lo borra.
    """
    # Brevo devuelve la cabecera `X-Mailin-custom` en un campo con ese mismo
    # nombre, **no** en `tag` — que llega vacío. Se comprobó con el payload
    # real; la documentación no lo aclara. Se miran las otras igual por si
    # cambian, y `tags` puede venir como lista en los envíos por API.
    etiqueta = ""
    for clave in ("X-Mailin-custom", "x-mailin-custom", "tag", "tags"):
        valor = datos.get(clave)
        if isinstance(valor, list):
            valor = valor[0] if valor else ""
        if valor:
            etiqueta = str(valor)
            break
    evento, _, dorsal = etiqueta.partition("|")
    email = (datos.get("email") or "").strip()

    fila = None
    if evento and dorsal:
        fila = conn.execute("SELECT * FROM correos WHERE evento=? AND dorsal=?",
                            (evento, dorsal)).fetchone()
    if fila is None and email and not (evento and dorsal):
        # Sin etiqueta utilizable, el último correo mandado a esa dirección.
        # Una etiqueta explícita de otro evento nunca puede caer en este fallback.
        fila = conn.execute("SELECT * FROM correos WHERE email=? "
                            "ORDER BY momento DESC LIMIT 1", (email,)).fetchone()
    if fila is None:
        # Sin esto, un webhook que llega pero no se puede atribuir es
        # indistinguible de uno que nunca llegó: los dos dejan el desenlace
        # vacío. El payload en el registro es lo único que dice cuál de los dos.
        print("webhook sin dueño: %s" % json.dumps(datos, ensure_ascii=False)[:600],
              flush=True)
        return {"ok": False, "motivo": "no encontré a quién corresponde",
                "campos": sorted(datos.keys())}

    tipo = str(datos.get("event") or "").lower()
    if tipo in PROBLEMAS:
        nuevo, motivo = "problema", PROBLEMAS[tipo]
        detalle = datos.get("reason") or datos.get("error") or ""
        if detalle:
            motivo += ": " + str(detalle)[:120]
    elif tipo in LOGROS:
        nuevo, motivo = LOGROS[tipo], ""
        anterior = fila["desenlace"]
        if anterior == "problema":
            return {"ok": True, "ignorado": "ya sabíamos que falló"}
        if anterior in DESENLACES and DESENLACES.index(anterior) >= DESENLACES.index(nuevo):
            return {"ok": True, "ignorado": "no agrega nada"}
    else:
        return {"ok": True, "ignorado": "evento que no nos dice nada: %s" % tipo}

    conn.execute("""UPDATE correos SET desenlace=?, desenlace_motivo=?,
                    desenlace_momento=? WHERE evento=? AND dorsal=?""",
                 (nuevo, motivo, dt.datetime.now().isoformat(timespec="seconds"),
                  fila["evento"], fila["dorsal"]))
    conn.commit()
    return {"ok": True, "dorsal": fila["dorsal"], "desenlace": nuevo}


def encolar(conn, evento, destinatarios):
    """Guarda a quién hay que escribirle. No manda nada todavía.

    Solo entran los dorsales que **ya tienen su video acá**: así es imposible
    mandar un enlace a un video que no terminó de subir.
    """
    subidos = {f["dorsal"] for f in conn.execute(
        "SELECT dorsal FROM videos WHERE evento=?", (evento,))}
    puestos, sin_video, invalidos = 0, [], []
    for dorsal, email in destinatarios.items():
        dorsal, email = str(dorsal), (email or "").strip()
        if not email:
            continue
        if dorsal not in subidos:
            sin_video.append(dorsal)
            continue
        if not CORREO_VALIDO.match(email):
            invalidos.append({"dorsal": dorsal, "email": email})
            continue
        conn.execute("""INSERT INTO correos (evento,dorsal,email) VALUES (?,?,?)
                        ON CONFLICT(evento,dorsal) DO UPDATE SET email=excluded.email""",
                     (evento, dorsal, email))
        puestos += 1
    conn.commit()
    return {"encolados": puestos, "sin_video": sin_video, "invalidos": invalidos}


def _pendientes(conn, evento, reenviar, solo):
    estados = ("'pendiente','error','enviado'" if reenviar else "'pendiente','error'")
    filas = conn.execute(
        "SELECT * FROM correos WHERE evento=? AND estado IN (%s)" % estados,
        (evento,)).fetchall()
    if solo:
        filas = [f for f in filas if f["dorsal"] in solo]
    return sorted(filas, key=lambda f: int(f["dorsal"]) if f["dorsal"].isdigit() else 0)


def enviar(abrir_conn, base_url, evento, reenviar=False, solo=None, prueba=None):
    """Manda los pendientes. Corre en un hilo; el avance queda en TRABAJO."""
    cfg = configuracion()
    problema = falta_configurar(cfg)
    if problema:
        return {"error": problema}

    with _candado:
        if TRABAJO["corriendo"]:
            return {"error": "Ya hay un envío en curso (%s)." % TRABAJO["evento"]}
        TRABAJO.update(corriendo=True, evento=evento, hechos=0, total=0, enviados=0,
                       fallados=0, detalle="", error=None, termino=None)

    def tarea():
        conn = abrir_conn()
        relay = Relay(cfg["usuario_smtp"], cfg["clave"]) if por_smtp(cfg["clave"]) else None
        espera = 3600.0 / cfg["por_hora"] if cfg["por_hora"] else 0
        try:
            filas = _pendientes(conn, evento, reenviar, solo)
            TRABAJO["total"] = len(filas)
            for i, fila in enumerate(filas):
                v = conn.execute("SELECT * FROM videos WHERE evento=? AND dorsal=?",
                                 (evento, fila["dorsal"])).fetchone()
                if not v:
                    continue
                enlace = "%s/%s/%s/" % (base_url.rstrip("/"), evento, v["token"])
                html, texto = armar(dict(v), enlace, cfg)
                asunto = "Tu video de llegada — %s" % (v["titulo"] or evento)
                destino = prueba or fila["email"]

                etiqueta = "%s|%s" % (evento, fila["dorsal"])
                if relay:
                    mid, motivo = relay.mandar(
                        mensaje_smtp(destino, v["nombre"], asunto, html, texto,
                                     cfg, etiqueta))
                else:
                    mid, motivo = por_api(cfg["clave"], destino, v["nombre"],
                                          asunto, html, texto, cfg, etiqueta)

                conn.execute("""UPDATE correos SET estado=?, intentos=intentos+1,
                                mensaje=?, detalle=?, momento=?
                                WHERE evento=? AND dorsal=?""",
                             ("enviado" if mid else "error", mid, motivo,
                              dt.datetime.now().isoformat(timespec="seconds"),
                              evento, fila["dorsal"]))
                conn.commit()

                TRABAJO["hechos"] = i + 1
                TRABAJO["enviados"] += 1 if mid else 0
                TRABAJO["fallados"] += 0 if mid else 1
                TRABAJO["detalle"] = "#%s %s" % (fila["dorsal"], motivo or "ok")

                # Un problema de credencial o de permiso no se arregla insistiendo:
                # los trescientos van a fallar igual.
                if motivo and any(t in motivo for t in
                                  ("no sirve", "no sirven", "Unauthorized", "5.7.1")):
                    TRABAJO["error"] = motivo
                    break
                if espera and i + 1 < len(filas):
                    time.sleep(espera)
        except Exception as e:                                 # noqa: BLE001
            TRABAJO["error"] = str(e)
        finally:
            if relay:
                relay.cerrar()
            conn.close()
            TRABAJO["corriendo"] = False
            TRABAJO["termino"] = dt.datetime.now().isoformat(timespec="seconds")

    threading.Thread(target=tarea, daemon=True).start()
    return {"ok": True}


def estado(conn, evento):
    """Cómo viene el envío. Incluye el detalle por dorsal, que es lo que el
    panel del operador muestra en su tabla: trescientas filas no son nada."""
    filas = [dict(f) for f in conn.execute(
        "SELECT dorsal, email, estado, detalle, momento, desenlace, "
        "desenlace_motivo FROM correos WHERE evento=?", (evento,))]
    cuenta = {}
    for f in filas:
        cuenta[f["estado"]] = cuenta.get(f["estado"], 0) + 1
    # Lo que Brevo contó después: entregado, abierto, rebotado…
    desenlaces = {}
    for f in filas:
        if f.get("desenlace"):
            desenlaces[f["desenlace"]] = desenlaces.get(f["desenlace"], 0) + 1
    problemas = [f for f in filas if f.get("desenlace") == "problema"]
    # TRABAJO vive en memoria y se pierde si el contenedor se reinicia: un
    # redespliegue a mitad de un lote dejaba el panel diciendo «0 enviados»
    # aunque los correos hubieran salido. La verdad está en la base, así que
    # los totales salen de ahí y TRABAJO solo aporta el avance en vivo.
    trabajo = dict(TRABAJO)
    if not trabajo["corriendo"] and trabajo["evento"] != evento:
        trabajo.update(enviados=cuenta.get("enviado", 0),
                       fallados=cuenta.get("error", 0),
                       total=sum(cuenta.values()), hechos=sum(cuenta.values()),
                       detalle="", evento=evento)

    return {"cuenta": cuenta, "desenlaces": desenlaces,
            "por_dorsal": {f["dorsal"]: f for f in filas},
            "fallados": ([f for f in filas if f["estado"] == "error"] + problemas)[:20],
            "trabajo": trabajo}
