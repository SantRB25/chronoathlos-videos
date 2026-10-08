"""Verificación HTTPS de solo lectura. Token únicamente por variable de entorno."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def obtener(url, token=None):
    req = urllib.request.Request(url, headers={'X-Token': token} if token else {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    base = os.environ.get('CHRONO_URL_PUBLICA', 'https://videos.chronoathlos.com.py').rstrip('/')
    if urllib.parse.urlsplit(base).scheme != 'https':
        raise ValueError('Usar URL publica HTTPS')
    token = os.environ.get('CHRONO_TOKEN_SUBIDA', '')
    if not token:
        raise ValueError('Definir CHRONO_TOKEN_SUBIDA en el entorno, no en argumentos')
    salud = obtener(base + '/salud')
    if salud.get('ok') is not True:
        raise ValueError('Salud no confirmada')
    print('Salud HTTPS: OK. Disco libre: %s GB. Correos: %s' % (
        salud.get('espacio_libre_gb'), salud.get('correos')))
    try:
        obtener(base + '/api/estado?evento=verificacion-instalacion')
    except urllib.error.HTTPError as e:
        if e.code != 403:
            raise ValueError('Respuesta de autenticacion inesperada') from None
    else:
        raise ValueError('La API acepta acceso sin token')
    estado = obtener(base + '/api/estado?evento=verificacion-instalacion', token)
    if estado.get('protocolo') != 3:
        raise ValueError('Se necesita protocolo 3 para la app v2')
    print('Autenticacion y protocolo 3: OK. No se subieron archivos ni enviaron correos.')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        # No exponer URLs de excepciones que pudieran incluir credenciales.
        print('Verificacion fallida: ' + (str(e) if isinstance(e, ValueError) else type(e).__name__), file=sys.stderr)
        sys.exit(1)
