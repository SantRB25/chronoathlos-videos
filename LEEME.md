# Servicio de videos de ChronoAthlos — entrega 2026-10-08

Instalar exclusivamente este servicio en `https://videos.chronoathlos.com.py`.
Es independiente de la plataforma de inscripciones/resultados. La PC del operador
recorta el video; este servicio recibe, almacena, muestra y envía los correos.
Código Python 3.12 + Flask/Waitress, protocolo de la app v2: **3**.

## 1. Instalación inicial

### Si usan EasyPanel

1. Crear un servicio App exclusivo `chronoathlos-videos`. Usar este paquete como
   fuente o un repositorio privado independiente. Constructor: Dockerfile en raíz.
2. Puerto interno 8090. Montar un volumen persistente exclusivo en `/datos`.
3. Copiar las variables de `.env.example` al panel y completar los secretos.
4. Configurar el dominio `videos.chronoathlos.com.py`, destino puerto 8090,
   HTTPS con certificado automático. La IP DNS debe ser la de este servidor.
5. Permitir 60 MB por solicitud y al menos 180 segundos para recibir cada subida
   en las capas de proxy que utilicen. Aplicarlo a este servicio; no hace falta
   modificar la plataforma existente. Si Cloudflare está delante, verificar
   también su límite; para la primera instalación se recomienda DNS-only.
6. Una sola instancia del servicio: su índice SQLite y sus colas no están
   diseñados para múltiples réplicas compartiendo `/datos`.

### Si usan Docker Compose

Desde una carpeta permanente, por ejemplo `/opt/chronoathlos-videos`:

```sh
cp .env.example .env
chmod 600 .env
# Editar .env y completar las variables; no ejecutar con secretos vacíos.
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
# Ejecutar dos veces: un valor para CHRONO_TOKEN_SUBIDA y otro para CHRONO_WEBHOOK_CLAVE.
docker compose up -d --build
docker compose ps
```

El token de subida se comunica a Santiago por un canal privado. Se configura en
la app de Windows, Ajustes → Servidor de videos / Token de subida. No incluirlo en
Git, mensajes públicos o este paquete. El servidor debe permitir conexiones
salientes a Brevo por puerto 587 (SMTP) o 443 (API, si se usa esa credencial).

El contenedor publica 8090 solo en localhost. El proxy HTTPS existente debe
reenviar este dominio a `http://127.0.0.1:8090`. Si 8090 está ocupado, establecer
`CHRONO_PUERTO_HOST` con un puerto libre y ajustar el proxy. El ejemplo nginx es
solo el bloque location, para integrar en su virtual host HTTPS. Si su proxy está
en otro contenedor, configurar red Docker compartida y destino interno; no usar
el localhost de otro contenedor como si fuera el del host.

No ejecutar `docker compose down -v`: elimina el almacenamiento de las carreras.
Mantener el nombre del volumen `chronoathlos_videos_datos` entre actualizaciones.

## 2. Correos

- Autenticar `chronoathlos.com.py` en Brevo y agregar/verificar el remitente
  `videos@chronoathlos.com.py`.
- `BREVO_CLAVE`: una clave SMTP de Brevo dedicada a este servicio; para SMTP
  completar también `BREVO_USUARIO_SMTP` con el usuario indicado por Brevo.
  Santiago proporcionará las credenciales por un canal privado. No copiar la
  credencial general de otros sistemas al repositorio.
- `CHRONO_REMITENTE=videos@chronoathlos.com.py`.
- `CHRONO_URL_PUBLICA=https://videos.chronoathlos.com.py` (sin /v2).
- `CHRONO_WEBHOOK_USUARIO=chronoathlos-videos` y clave independiente del token de subida.
- En Brevo configurar webhook transaccional a
  `https://videos.chronoathlos.com.py/api/brevo`, autenticación Basic con ese
  usuario/clave. Eventos: delivered, opened/unique_opened, click, hard_bounce,
  soft_bounce, blocked, spam, invalid_email.
- Confirmar que el cupo disponible alcanza para los participantes de la carrera.
  El espaciado no aumenta el cupo diario.

Los correos salen después de publicar el video; las correcciones conservan el
mismo enlace y no reenvían el correo ya enviado. Un webhook correcto permite
conocer entrega/rebote; salud «correos listo» solo confirma variables presentes,
no autentica el dominio ni comprueba la entrega.

## 3. Comprobaciones antes de usar la app

```sh
curl --fail https://videos.chronoathlos.com.py/salud
# Usar un entorno con CHRONO_TOKEN_SUBIDA, sin ponerlo en la línea de comandos:
python3 verificar_instalacion.py
```

El verificador utiliza librería estándar; comprueba salud HTTPS, autenticación y
protocolo 3. No escribe datos ni envía correos. Después, con Santiago:

1. Configurar URL y token nuevos en la app v2 y subir un clip de prueba.
2. Abrir página, miniatura, reproducir y descargar; comprobar la reproducción móvil.
3. Probar cinco subidas simultáneas de aproximadamente 14 MB: que ninguna corte por tiempo.
4. Enviar solo a los dos correos de prueba autorizados y comprobar recepción/webhook.
5. Corregir un tiempo: mismo enlace, video actualizado y ningún correo duplicado.
6. Actualizar/recrear el contenedor y comprobar que ese video sigue disponible.

El servicio retiene las carreras seis meses y las limpia diariamente. Dimensionar
con los archivos reales: 336 clips de 14 MB son alrededor de 4,7 GB por carrera,
más miniaturas, versiones reemplazadas y respaldos. Vigilar `/salud` y el disco.
No importar los datos de las pruebas de FOCUS como carrera real.

## 4. Actualizaciones sin intervención recurrente del desarrollador

Propuesta: repositorio **privado independiente** de FOCUS, solo con estos archivos.
El servidor obtiene permiso de lectura mediante deploy key o integración del
panel. No necesitamos acceso al repositorio de la plataforma ni SSH del servidor.

- Rama `produccion`: solo versiones probadas. Desactivar despliegue automático de
  cada push; usar un despliegue explícito después de las pruebas.
- El desarrollador configura una vez un enlace de despliegue que solo actualice
  este servicio. Si EasyPanel ofrece Deployment URL, usar ese enlace, sin un token
  administrativo general. Guardarlo como secreto `VIDEOS_DEPLOY_URL` del entorno
  GitHub `produccion`; nunca en el código.
- El workflow incluido prueba y construye en push/PR. La acción manual
  «Verificar y desplegar videos», ejecutada en `produccion` con `desplegar=true`,
  repite las pruebas y solicita el despliegue por POST.
- El servidor debe leer el mismo repositorio y rama probados. No mover la rama
  mientras se realiza el despliegue. Si el panel permite fijar commit o imagen,
  preferir ese mecanismo para asegurar exactamente la versión elegida.
- El workflow admite un enlace HTTPS de despliegue por POST; adaptar el mecanismo
  si su panel exige otro método. No está conectado ni probado con su servidor aún.
- Confirmar en el panel commit/imagen y finalización; `/salud` confirma que el
  servicio responde, pero no identifica la versión instalada.
- Durante una carrera, programar cambios fuera de grabación/procesamiento y envío.
  Antes de actualizar, pausar la automatización y esperar que terminen subidas y
  correos activos. Los videos ya publicados permanecen en el volumen.

Si no hay enlace de despliegue, alternativas: mecanismo de CI con permiso limitado
al servicio o imagen versionada en un registry con descarga autorizada. Git por
sí solo no despliega: falta ese vínculo inicial al servidor. No instalar polling
ni actualizaciones ciegas del sistema completo.

## 5. Respaldo y vuelta atrás

Respaldar `/datos` completo, incluido `indice.db` (enlaces e historial de correos).
Hacer el respaldo con el servicio detenido y sin trabajos activos, o usar un
respaldo consistente de SQLite y archivos. No copiar solamente los MP4.

Antes de actualizar, guardar commit/imagen anterior y respaldo del volumen.
Para volver atrás, seleccionar esa versión y redeployar conservando `/datos`.
Si una actualización futura cambia el esquema de forma incompatible, requerirá
procedimiento propio; no restaurar un índice antiguo sobre videos nuevos sin revisar.

## 6. Qué necesitamos al terminar

- Confirmación de HTTPS y volumen persistente.
- Token de subida por canal privado, sin credenciales administrativas del servidor.
- Método de actualizaciones y enlace limitado a este servicio, si está disponible.
- Prueba conjunta de subida, correo, corrección y persistencia antes de usarlo en carrera.

## 7. Servidor confirmado: Docker — mecanismo recomendado

El desarrollador confirmó Docker. La instalación simple del apartado 1 sirve.
Para actualizaciones autónomas, proponemos **Git privado + SSH con comando forzado**;
no hace falta acceso a la consola ni instalar un panel.

Configuración inicial a cargo del administrador:

1. Crear el repositorio privado independiente con estos archivos; rama `produccion`.
   Dar al servidor una deploy key de **solo lectura** para ese repositorio.
2. Clonar en `/opt/chronoathlos-videos/codigo`. Mantener `compose.yaml` y `.env`
   fuera del clon, en `/opt/chronoathlos-videos/`, propiedad root. El Compose de
   esta entrega admite `CHRONO_CODIGO` para construir desde el clon. Para la primera instalación en esta estructura:
   `CHRONO_CODIGO=/opt/chronoathlos-videos/codigo docker compose up -d --build`
   desde `/opt/chronoathlos-videos`, con `.env` completado.
3. Instalar `actualizar-docker.sh` como `/usr/local/sbin/chronoathlos-videos-actualizar`,
   propietario root:root y modo 0755. Revisarlo antes; requiere Git, Docker Compose
   y `flock`. No dar escritura sobre este archivo a la cuenta de despliegue.
4. Crear una cuenta SSH dedicada sin permisos generales de Docker y con sudo
   permitido **solo** sobre ese script, sin argumentos. Restringir su clave pública
   a un comando forzado y sin forwarding/TTY. Ejemplo de línea en authorized_keys:

   `restrict,command="sudo -n /usr/local/sbin/chronoathlos-videos-actualizar" ssh-ed25519 CLAVE_PUBLICA_DE_DESPLIEGUE`

   La clave privada de despliegue queda en los secretos de GitHub; enviar al
   administrador únicamente su clave pública. Esta es distinta de la deploy key
   usada por el servidor para leer Git. El administrador ajustará su política SSH
   y sudo para que esta cuenta no tenga otros métodos de acceso ni privilegios.
5. Cargar en el entorno GitHub `produccion`: `VIDEOS_SSH_HOST`, `VIDEOS_SSH_USER`,
   `VIDEOS_SSH_KEY` y `VIDEOS_SSH_KNOWN_HOSTS`. Confirmar la huella del servidor con
   el administrador; no aceptar una huella obtenida sin verificar su identidad.
6. Ejecutar manualmente el workflow «Desplegar Docker por SSH limitado» desde
   `produccion` después de pausar tareas. Este método reemplaza al webhook del
   apartado 4; usar uno solo. El script solo actualiza el servicio `videos` y
   conserva su volumen. No usar el usuario dentro del grupo docker.

El script está preparado y validado sintácticamente, pero su instalación SSH/sudo
requiere la configuración y prueba del administrador. No se ha instalado en el
servidor del cliente. No mover `produccion` durante el despliegue; confirmar el
commit que imprime el script. Para una versión futura con migraciones incompatibles,
preparar instrucciones particulares antes de ejecutarla.
