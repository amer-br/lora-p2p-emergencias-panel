# Panel del campus — Red LoRa de emergencias

Panel web (Django + PostgreSQL, en Docker) para el responsable de protección civil del campus. El panel es **un nodo más de la red LoRa P2P**: escucha las tramas que circulan, consolida incidentes, acuses y el censo de evacuación, y permite responder desde una computadora.

| Pantalla | Qué hace |
|----------|----------|
| **Tablero** | Mapa del campus con incidentes activos (por prioridad), recursos y última posición de cada nodo; estado de la radio, cifras del censo y nodos recientes. Se actualiza cada 5 s. |
| **Incidentes** | Lista filtrable por estado y categoría. El detalle muestra los datos de la trama, la línea de tiempo de acuses y las acciones del responsable. |
| **Acciones sobre un incidente** | *Brigada en camino* (ACK_EN_CAMINO), *Brigada en el sitio* (ACK_LLEGUE) y *Marcar como resuelto* (ACK_RESUELTO, solo después de llegar, CU-09). Cada una transmite un acuse de 20 B a la red. Notas internas que no se transmiten. |
| **Emitir aviso** (CU-18) | Difunde a toda la red una alerta del puesto de mando (incendio, médica, estructural o SOS) con texto de hasta 32 bytes y, opcionalmente, una ubicación en el mapa. En la app aparece como «Aviso del puesto de mando». |
| **Censo** (CU-19) | Personas a salvo frente a nodos escuchados, cada Sender_ID una vez; botón para iniciar un censo nuevo al comenzar una evacuación. |
| **Reporte** (CU-20) | Métricas del simulacro en un periodo: incidentes recibidos, con respuesta, resueltos y sin atención; mediana, P95 y máximo del tiempo hasta el primer acuse, la llegada y la resolución; avisos, acuses del panel, personas a salvo y tramas válidas o descartadas. Se imprime o se descarga en CSV (una fila por incidente). |

## Arquitectura

```
Teléfonos (app Flutter) ─┐
                          ├─ red LoRa ── simulador de radio (WebSocket :8080)
Postman / curl ──────────┘                    │
                                               │ ws://host.docker.internal:8080/ws
                       ┌──────────────── Docker ────────────────┐
                       │  radio  → escuchar_radio (worker)        │
                       │  web    → Django + gunicorn  :8000       │
                       │  db     → PostgreSQL 16                  │
                       └──────────────────────────────────────────┘
```

- `emergencias/protocolo.py`: codec binario (alerta 56 B, acuse 20 B, Safety Check 17 B, CRC-16 con TTL en 0). Produce exactamente los mismos bytes que `Aplicacion/lib/protocol/tramas.dart`; las pruebas lo verifican con una trama generada por la app.
- `emergencias/servicios.py`: procesador de tramas entrantes (deduplicación por Msg_ID, acuses que llegan antes que su alerta, estado Emitido → En asistencia → Resuelto de la SRS) y las acciones del responsable.
- `emergencias/teselas.py` y `static/emergencias/mapa_campus.js`: el mapa replica el radar de la app (`lib/ui/radar_screen.dart`): fondo verde, cuadrícula de referencia de ~110 m que se ve donde no hay teselas, contorno del campus y los mismos colores e íconos de Material. Las teselas de OpenStreetMap se guardan en caché la primera vez, igual que hace flutter_map en el teléfono; solo se aceptan teselas del área del campus.
- `emergencias/management/commands/escuchar_radio.py`: mantiene la conexión con la radio, guarda lo que llega y transmite la cola de tramas salientes. Si se cae la conexión reintenta, y lo pendiente se envía al reconectar.

## Ejecutar

1. Arranca el simulador de radio (desde `Aplicacion/`):
   ```
   dart run tool/simulador_radio.dart
   ```
2. Prepara la configuración y levanta los contenedores (desde `PanelCampus/`):
   ```
   copy .env.example .env      # cambia DJANGO_SECRET_KEY y RESPONSABLE_PASSWORD
   docker compose up --build
   ```
3. Abre http://localhost:8000 y entra con `RESPONSABLE_USUARIO` / `RESPONSABLE_PASSWORD` del `.env`.

Para generar tráfico, usa la app con `--dart-define=SIMULADOR=ws://localhost:8080/ws` o la colección de Postman `Aplicacion/tool/simulador_radio.postman_collection.json`.

Comandos útiles:

```
docker compose logs -f radio                          # ver lo que escucha y transmite el panel
docker compose down                                   # detener (los datos quedan en el volumen)
docker compose down -v                                # detener y borrar la base de datos
```

## Pruebas

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python manage.py test emergencias
```

Sin `POSTGRES_HOST`, Django usa SQLite, así que las pruebas no necesitan Docker.

## Limitaciones

- **Acuses entre la app y el panel.** La app procesa los acuses (CU-08 a CU-11) con la misma trama de 20 B y las mismas reglas de consolidación, así que el estado de un incidente coincide en el tablero y en los teléfonos. En el historial de la app, los acuses del panel aparecen como «Nodo 0000CA0A» (su `PANEL_SENDER_ID`).
- **Falta el firmware de la pasarela.** El puente de radio (`puente_radio/`) ya conecta el panel con un T-Beam por USB, pero el firmware del T-Beam en modo pasarela aún no existe; mientras tanto, el panel se prueba con el simulador. El contrato que debe cumplir el firmware está en `puente_radio/README.md`.
- **Primera descarga del mapa.** Al arrancar, el panel descarga en segundo plano las 298 teselas del campus (zoom 15 a 19, ~2 MB) y las guarda en el volumen `teselas`; desde entonces el mapa funciona sin internet. Si arranca sin conexión, el mapa muestra la cuadrícula de referencia (como la app) y se puede completar después con `docker compose exec web python manage.py precargar_teselas`. Leaflet y los íconos vienen incluidos en el proyecto, así que no dependen de ningún CDN.
- **Sin HTTPS.** Está pensado para la red local del campus. Si se publica fuera, hay que ponerlo detrás de un proxy con TLS y activar `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` y HSTS.
