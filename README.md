# ChronoAthlos — servicio de videos

Servicio independiente para alojar y entregar videos de llegada. Destino:
**https://videos.chronoathlos.com.py**. Compatible con la app v2, protocolo 3.

La PC del operador genera los clips; este servidor los recibe por HTTPS,
conserva los enlaces del corredor y gestiona los correos mediante Brevo.

- [Instalación Docker, configuración y actualizaciones](LEEME.md)
- [Variables de configuración sin secretos](.env.example)
- [Comprobaciones realizadas y pendientes](VERIFICACIONES.txt)

Rama de entrega: `produccion`. Las actualizaciones se despliegan explícitamente
después de las pruebas. No incluir credenciales, datos de carreras ni grabaciones.
El repositorio no está conectado todavía al servidor del cliente.
