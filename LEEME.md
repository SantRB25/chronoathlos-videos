# Servicio de videos de ChronoAthlos — entrega 2026-10-08

Instalar exclusivamente este servicio en `https://videos.chronoathlos.com.py`.
Es independiente de la plataforma de inscripciones/resultados. La PC del operador
recorta el video; este servicio recibe, almacena, muestra y envía los correos.
Código Python 3.12 + Flask/Waitress, protocolo de la app v2: **3**.

## 1. Instalación inicial

### Si usan EasyPanel

1. Crear un servicio App exclusivo `chronoathlos-videos`. Usar este paquete como
   fuente, o el repositorio de entrega del apartado 4. Constructor: Dockerfile en raíz.
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

La cuenta de Brevo es de FOCUS: la autenticación del dominio
`chronoathlos.com.py`, el remitente `videos@chronoathlos.com.py` y la configuración
del webhook las hacemos nosotros. De tu lado solo hay que completar estas variables
en el `.env`.

- `BREVO_CLAVE`: una clave SMTP de Brevo dedicada a este servicio; para SMTP
  completar también `BREVO_USUARIO_SMTP` con el usuario indicado por Brevo.
  Santiago proporcionará las dos por un canal privado. No copiar la credencial
  general de otros sistemas al repositorio.
- `CHRONO_REMITENTE=videos@chronoathlos.com.py`.
- `CHRONO_URL_PUBLICA=https://videos.chronoathlos.com.py` (sin /v2).
- `CHRONO_WEBHOOK_USUARIO=chronoathlos-videos` y clave independiente del token de subida.
- Nosotros apuntamos el webhook transaccional de Brevo a
  `https://videos.chronoathlos.com.py/api/brevo` con autenticación Basic y esos
  mismos usuario y clave, para los eventos delivered, opened/unique_opened, click,
  hard_bounce, soft_bounce, blocked, spam e invalid_email. Para eso **necesitamos
  que nos pases la `CHRONO_WEBHOOK_CLAVE` que pusiste**, igual que el token de
  subida: si no coincide, Brevo no puede avisar entregas ni rebotes.
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

El verificador utiliza librería estándar; comprueba salud HTTPS, autenticación,
protocolo 3 y muestra la versión instalada que informa `/salud`. No escribe datos
ni envía correos. Después, con Santiago:

1. Configurar URL y token nuevos en la app v2 y subir un clip de prueba.
2. Abrir página, miniatura, reproducir y descargar; comprobar la reproducción móvil.
3. Probar cinco subidas simultáneas de aproximadamente 14 MB: que ninguna corte por tiempo.
4. Enviar solo a los dos correos de prueba autorizados y comprobar recepción/webhook.
5. Corregir un tiempo: mismo enlace, video actualizado y ningún correo duplicado.
6. Actualizar/recrear el contenedor y comprobar que ese video sigue disponible.

**Borrar las pruebas antes de la carrera**: `POST /api/borrar-evento` con la cabecera
`X-Token` y `{"evento": "...", "confirmar": "..."}` elimina un evento completo: sus
archivos, sus enlaces y su historial de correos. Lo usamos para que las pruebas de
FOCUS no queden mezcladas con la carrera real, sin pedirte nada. Hay que escribir el
nombre dos veces porque no tiene vuelta atrás —el volumen no guarda copias— y cada
borrado queda registrado en el log del contenedor. Los correos ya enviados no se
pueden retirar: lo que se borra es el historial que evita reenviarlos, así que un
evento borrado y vuelto a subir los manda de nuevo.

El servicio retiene las carreras seis meses y las limpia diariamente. Dimensionar
con los archivos reales: 336 clips de 14 MB son alrededor de 4,7 GB por carrera,
más miniaturas, versiones reemplazadas y respaldos. Vigilar `/salud` y el disco.
No importar los datos de las pruebas de FOCUS como carrera real.

## 4. Actualizaciones sin intervención recurrente del desarrollador

El código vive en un repositorio propio de este servicio, ya publicado:
**https://github.com/SantRB25/chronoathlos-videos**, rama `produccion`. Es de
lectura pública: el servidor lo clona sin credenciales ni invitación, y solo
nosotros escribimos en él. No necesitamos acceso al repositorio de la plataforma
ni una consola libre en el servidor.

- Rama `produccion`: solo versiones probadas. Sin despliegue automático en cada
  push; la actualización se solicita siempre de forma explícita.
- Un único mecanismo, detallado en el apartado 7: una cuenta SSH dedicada cuya
  clave solo puede ejecutar el script de actualización de este servicio.
- El workflow «Verificar el servicio de videos» prueba y construye en cada push y
  pull request. El workflow «Desplegar Docker por SSH limitado» se ejecuta a mano
  desde `produccion`, repite las pruebas y recién entonces pide la actualización.
- El servidor debe quedar en el mismo commit probado. No mover `produccion`
  mientras se está desplegando.
- `/salud` informa el campo `version` con el commit instalado: así se confirma qué
  código quedó corriendo, sin entrar al servidor. El propio script compara ese
  valor con el commit solicitado y avisa si no coinciden.
- Durante una carrera, programar los cambios fuera de la grabación, el procesamiento
  y el envío. Antes de actualizar, pausar la automatización y esperar que terminen
  las subidas y los correos activos. Los videos publicados permanecen en el volumen.

Git por sí solo no actualiza Docker: hace falta ese vínculo con el servidor, que el
administrador configura una sola vez. No instalar polling ni actualizaciones ciegas
del sistema completo.

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
- Token de subida y clave del webhook por canal privado, sin credenciales
  administrativas del servidor.
- Cuenta SSH de despliegue limitada a este servicio y huella del servidor (apartado 7).
- Prueba conjunta de subida, correo, corrección y persistencia antes de usarlo en carrera.

## 7. Servidor confirmado: Docker — configuración del mecanismo

El desarrollador confirmó Docker. La instalación simple del apartado 1 alcanza para
empezar. Para las actualizaciones usamos **Git + SSH con comando forzado**: no hace
falta instalar un panel ni darnos una consola.

Configuración inicial a cargo del administrador:

1. Clonar `https://github.com/SantRB25/chronoathlos-videos`, rama `produccion`, en
   `/opt/chronoathlos-videos/codigo`. El repositorio es público: no hace falta
   deploy key ni token de lectura. Mantener `compose.yaml` y `.env` fuera del clon,
   en `/opt/chronoathlos-videos/`, propiedad root. El Compose de esta entrega admite
   `CHRONO_CODIGO` para construir desde el clon. Para la primera instalación en esta
   estructura:
   `CHRONO_CODIGO=/opt/chronoathlos-videos/codigo docker compose up -d --build`
   desde `/opt/chronoathlos-videos`, con `.env` completado.
2. Instalar `actualizar-docker.sh` como `/usr/local/sbin/chronoathlos-videos-actualizar`,
   propietario root:root y modo 0755. Revisarlo antes; requiere Git, Docker Compose
   y `flock`. No dar escritura sobre este archivo a la cuenta de despliegue.
3. Crear una cuenta SSH dedicada sin permisos generales de Docker y con sudo
   permitido **solo** sobre ese script, sin argumentos. Restringir su clave pública
   a un comando forzado y sin forwarding/TTY. Ejemplo de línea en authorized_keys:

   `restrict,command="sudo -n /usr/local/sbin/chronoathlos-videos-actualizar" ssh-ed25519 CLAVE_PUBLICA_DE_DESPLIEGUE`

   La clave privada queda en los secretos de GitHub; al administrador le enviamos
   únicamente la clave pública. El administrador ajustará su política de SSH y sudo
   para que esta cuenta no tenga otros métodos de acceso ni privilegios.
4. Pasarnos la huella del servidor: la línea completa que devuelve, **en el propio
   servidor**, `ssh-keyscan -t ed25519 <host-ssh>` (agregar `-p <puerto>` si SSH no
   escucha en el 22). Con eso GitHub reconoce al servidor al conectarse y corta si
   alguien responde en su lugar. Esa línea va al secreto `VIDEOS_SSH_KNOWN_HOSTS`,
   junto con `VIDEOS_SSH_HOST`, `VIDEOS_SSH_USER` y `VIDEOS_SSH_KEY` en el entorno
   GitHub `produccion`. La huella se confirma con el administrador por un canal
   donde conste su identidad; una obtenida por nuestra cuenta desde internet no
   sirve de control.
5. Ejecutar a mano el workflow «Desplegar Docker por SSH limitado» desde
   `produccion`, después de pausar las tareas. El script solo actualiza el servicio
   `videos` y conserva su volumen. No usar un usuario dentro del grupo docker.

El script está preparado y validado sintácticamente, pero su instalación SSH/sudo
requiere la configuración y la prueba del administrador. No se ha instalado en el
servidor del cliente. No mover `produccion` durante el despliegue; confirmar el
commit que imprime el script y el que informa `/salud`. Para una versión futura con
migraciones incompatibles, preparamos instrucciones particulares antes de ejecutarla.
