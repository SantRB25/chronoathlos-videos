#!/bin/sh
# Instalar fuera del repositorio, root:root 0755. Configuracion fija del servicio.
set -eu
case "${SSH_ORIGINAL_COMMAND:-desplegar}" in
  desplegar) ;;
  *) echo 'Solo se permite desplegar el servicio de videos.' >&2; exit 1 ;;
esac
base=/opt/chronoathlos-videos
codigo="$base/codigo"
# compose.yaml y .env permanecen fuera del codigo y son administrados por el servidor.
exec 9>"$base/actualizacion.lock"
flock -n 9 || { echo 'Ya hay una actualizacion activa.' >&2; exit 1; }
[ "$(git -C "$codigo" symbolic-ref --short HEAD)" = produccion ] || { echo "El clon debe estar en la rama produccion." >&2; exit 1; }
git -C "$codigo" pull --ff-only origin produccion
CHRONO_VERSION=$(git -C "$codigo" rev-parse HEAD)
export CHRONO_VERSION
CHRONO_CODIGO="$codigo"
export CHRONO_CODIGO
cd "$base"
docker compose build videos
docker compose up -d --no-deps videos
# La construccion fallida no sustituye el contenedor que estaba funcionando.
# No se borra ni recrea el volumen.
printf 'Despliegue solicitado para commit %s\n' "$CHRONO_VERSION"
docker compose ps videos
# Confirma dentro del servidor que el contenedor en pie es el commit pedido.
sleep 5
instalado=$(docker compose exec -T videos python -c \
  "import json,urllib.request;print(json.load(urllib.request.urlopen('http://127.0.0.1:8090/salud'))['version'])" \
  2>/dev/null) || instalado=desconocida
printf 'Version que informa /salud: %s\n' "$instalado"
[ "$instalado" = "$CHRONO_VERSION" ] || echo 'La version informada no coincide con el commit pedido; revisar antes de usar el servicio.' >&2
