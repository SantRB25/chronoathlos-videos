# Servidor de videos de llegada — ChronoAthlos
#
# Imagen chica a propósito: el servidor no procesa video, solo recibe archivos,
# los guarda y los sirve. Todo el trabajo pesado pasa en la máquina de la meta.
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir flask==3.1.* waitress==3.0.* requests==2.*

COPY servidor.py correos.py ./
COPY plantillas/ plantillas/

# Los videos viven en el volumen, no en la imagen: sin esto, una actualización
# del contenedor se lleva las carreras de seis meses.
ENV CHRONO_DATOS=/datos PORT=8090
VOLUME /datos
EXPOSE 8090

# Sin CHRONO_TOKEN_SUBIDA nadie puede subir nada; se pasa como variable de entorno.
CMD ["python", "servidor.py"]
