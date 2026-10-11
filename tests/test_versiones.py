import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import servidor


class Versiones(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(servidor, 'DATOS', self.tmp.name),
                  patch.object(servidor, 'BASE', str(Path(self.tmp.name) / 'indice.db')),
                  patch.dict(os.environ, CHRONO_TOKEN_SUBIDA='prueba')):
            p.start()
            self.addCleanup(p.stop)
        servidor.preparar()
        self.client = servidor.app.test_client()

    def subir(self, video=b'video1', firma=None):
        datos = dict(evento='Evento', dorsal='1', video=(io.BytesIO(video), 'video.mp4'),
                     foto=(io.BytesIO(b'foto'), 'foto.jpg'))
        if firma:
            datos['firma'] = firma
        return self.client.post('/api/subir', data=datos, headers={'X-Token': 'prueba'})

    def estado(self):
        return self.client.get('/api/estado?evento=Evento', headers={'X-Token': 'prueba'}).json

    def test_pagina_y_correo_ocultan_fracciones_sin_cambiar_el_tiempo_guardado(self):
        import correos
        subido=self.subir().json
        r=self.client.post('/api/metadatos',json=dict(evento='Evento',dorsal='1',
            firma=subido['firma'],tiempo='00:18:13.095',nombre='Prueba'),
            headers={'X-Token':'prueba'})
        self.assertEqual(r.status_code,200)
        pagina=self.client.get(subido['url']).get_data(as_text=True)
        self.assertIn('00:18:13',pagina)
        self.assertNotIn('00:18:13.095',pagina)
        conn=servidor.conectar()
        try: fila=dict(conn.execute('SELECT * FROM videos WHERE dorsal=?',('1',)).fetchone())
        finally: conn.close()
        self.assertEqual(fila['tiempo'],'00:18:13.095')
        html,texto=correos.armar(fila,'https://ejemplo.test/video',{'remitente_nombre':'Prueba'})
        for contenido in (html,texto):
            self.assertIn('00:18:13',contenido)
            self.assertNotIn('00:18:13.095',contenido)
        self.assertEqual(fila['tiempo'],'00:18:13.095')

    def test_version_mismo_enlace_reintento_y_contenido_incompleto(self):
        primera = self.subir().json
        self.assertEqual(primera['firma'], hashlib.sha256(b'videovideo1miniaturafoto').hexdigest())
        self.assertEqual(self.estado()['videos']['1']['firma'], primera['firma'])
        self.assertEqual(self.subir().json, primera)
        self.assertEqual(self.subir(b'video2', 'incorrecta').status_code, 422)
        self.assertEqual(self.client.get(primera['url'] + 'video.mp4').data, b'video1')
        segunda = self.subir(b'video2').json
        self.assertEqual(segunda['url'], primera['url'])
        self.assertNotEqual(segunda['firma'], primera['firma'])
        self.assertEqual(self.client.get(primera['url'] + 'video.mp4').data, b'video2')
        self.assertEqual(self.client.get(primera['url'] + 'foto.jpg').data, b'foto')

    def test_migra_archivos_anteriores_sin_reenviarlos(self):
        self.subir()
        c = servidor.conectar()
        fila = c.execute('SELECT * FROM videos').fetchone()
        carpeta = Path(servidor.carpeta_version(fila))
        for f in carpeta.iterdir():
            f.replace(carpeta.parent / f.name)
        c.execute('UPDATE videos SET firma=NULL, revision=NULL')
        c.commit()
        c.close()
        self.assertTrue(self.estado()['videos']['1']['firma'])

    def test_interrupcion_antes_del_commit_conserva_version_publicada(self):
        primero = self.subir().json
        with patch.object(servidor.os, 'replace', side_effect=OSError('disco')):
            self.assertEqual(self.subir(b'nuevo').status_code, 500)
        self.assertEqual(self.client.get(primero['url'] + 'video.mp4').data, b'video1')
        self.assertEqual(self.estado()['videos']['1']['firma'], primero['firma'])

    def test_metadatos_condicionales_conservan_video_y_enlace(self):
        primero=self.subir().json
        headers={'X-Token':'prueba'}
        datos=dict(evento='Evento',dorsal='1',firma=primero['firma'],posicion=2,
                   distancia='5K',posicion_tipo='General 5K · Masculino',categoria='General Masculino')
        self.assertEqual(self.client.post('/api/metadatos',json=datos,headers=headers).status_code,200)
        self.assertEqual(self.client.get(primero['url']+'video.mp4').data,b'video1')
        pagina=self.client.get(primero['url']).get_data(as_text=True)
        self.assertIn('General 5K',pagina)
        self.assertEqual(self.estado()['protocolo'],3)
        self.assertEqual(self.client.post('/api/metadatos',json=dict(datos,firma='vieja'),headers=headers).status_code,409)
        self.assertEqual(self.client.post('/api/metadatos',json=datos).status_code,403)

    def test_correo_rechaza_firma_desactualizada(self):
        self.subir()
        with patch.object(servidor.correos,'falta_configurar',return_value=None), \
             patch.object(servidor.correos,'enviar') as enviar:
            r=self.client.post('/api/correos',headers={'X-Token':'prueba'},json={
                'evento':'Evento','destinatarios':{'1':'p@example.test'},'firmas':{'1':'vieja'}})
            self.assertEqual(r.status_code,409)
            enviar.assert_not_called()

    def test_webhook_otra_instancia_no_usa_email_para_cambiar_el_evento(self):
        c=servidor.conectar()
        c.execute("INSERT INTO correos(evento,dorsal,email,estado) VALUES ('actual','1','p@example.test','enviado')")
        c.commit()
        resultado=servidor.correos.anotar_evento(c,{'event':'delivered','email':'p@example.test','X-Mailin-custom':'otra-instancia|1'})
        self.assertFalse(resultado['ok'])
        self.assertIsNone(c.execute("SELECT desenlace FROM correos").fetchone()[0])
        correcto=servidor.correos.anotar_evento(c,{'event':'delivered','email':'p@example.test','X-Mailin-custom':'actual|1'})
        self.assertTrue(correcto['ok'])
        self.assertEqual(c.execute("SELECT desenlace FROM correos").fetchone()[0],'entregado')
        c.close()

    def test_salud_informa_la_version_desplegada(self):
        commit='867a48908a5b914101dcd9dc2b306aa20f50c5fc'
        with patch.dict(os.environ, CHRONO_VERSION=commit):
            self.assertEqual(self.client.get('/salud').json['version'],commit)
        with patch.dict(os.environ, CHRONO_VERSION=''):
            self.assertEqual(self.client.get('/salud').json['version'],'sin informar')

    def test_borrar_evento_pide_el_nombre_dos_veces_y_limpia_archivos_e_indice(self):
        url = self.subir().json['url']
        c = servidor.conectar()
        c.execute("INSERT INTO correos(evento,dorsal,email,estado) VALUES ('evento','1','p@example.test','enviado')")
        c.commit()
        c.close()
        pedir = lambda **cuerpo: self.client.post('/api/borrar-evento', json=cuerpo,
                                                  headers={'X-Token': 'prueba'})
        self.assertEqual(self.client.post('/api/borrar-evento',
                                          json={'evento': 'Evento', 'confirmar': 'Evento'}).status_code, 403)
        self.assertEqual(pedir(evento='Evento', confirmar='otra cosa').status_code, 409)
        self.assertEqual(pedir(evento='', confirmar='').status_code, 400)
        self.assertTrue(self.estado()['videos']['1']['firma'], 'un pedido rechazado no borra nada')
        hecho = pedir(evento='Evento', confirmar='Evento')
        self.assertEqual(hecho.status_code, 200)
        self.assertEqual((hecho.json['videos'], hecho.json['correos']), (1, 1))
        self.assertEqual(self.estado()['videos'], {})
        self.assertEqual(self.client.get(url + 'video.mp4').status_code, 404)
        self.assertFalse((Path(self.tmp.name) / 'evento').exists())
        c = servidor.conectar()
        self.assertEqual(c.execute('SELECT COUNT(*) FROM correos').fetchone()[0], 0)
        c.close()
        self.assertEqual(pedir(evento='Evento', confirmar='Evento').json['videos'], 0,
                         'borrar dos veces no es un error')

if __name__ == '__main__':
    unittest.main()
