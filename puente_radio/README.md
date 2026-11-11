# Puente de radio

Conecta la **pasarela** (un LilyGO T-Beam conectado por USB a la computadora del puesto de mando) con el **servicio de radio del panel**, que corre en Docker.

```
T-Beam pasarela ──USB serie (115200, JSON Lines)──► puente_radio.py ──WebSocket ws://…:8081/ws──► contenedor "radio"
```

Corre en Windows, **fuera de Docker**, porque Docker Desktop para Windows no puede pasarle el puerto USB a un contenedor (SDS, DD-09). Usa el mismo formato de mensajes que el simulador de radio, así que el panel no cambia entre pruebas y campo.

## Instalación (una sola vez)

Necesitas Python 3.10 o superior en Windows.

```
cd PanelCampus\puente_radio
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Si Windows no muestra un puerto `COMx` al conectar el T-Beam, instala el driver del chip USB de la placa (CP210x o CH9102, según la revisión).

## Uso

```
.venv\Scripts\python puente_radio.py --listar        # ver los puertos serie
.venv\Scripts\python puente_radio.py                 # detecta el T-Beam si es el único puerto USB
.venv\Scripts\python puente_radio.py --puerto COM5   # o indica el puerto
.venv\Scripts\python puente_radio.py -v              # muestra también los mensajes de depuración del firmware
```

Luego, en `PanelCampus\.env`, apunta el panel al puente y reinicia el servicio de radio:

```
RADIO_URL=ws://host.docker.internal:8081/ws
```

```
docker compose up -d radio
```

El tablero debe mostrar «Radio conectada». Para volver a probar con el simulador, regresa `RADIO_URL` a `ws://host.docker.internal:8080/ws`.

## Contrato con el firmware de la pasarela

Una línea JSON por mensaje, terminada en `\n`, a 115 200 baudios:

| Sentido | Línea | Notas |
|---------|-------|-------|
| Pasarela → puente | `{"hex": "0BADCAFE…", "rssi": -102, "snr": -3.0}` | Una por trama LoRa recibida. `rssi` y `snr` son opcionales. |
| Puente → pasarela | `{"hex": "B2A53425…"}` | Trama que la pasarela debe transmitir por LoRa. |

- El campo `hex` lleva la trama binaria completa (alerta 56 B, acuse 20 B o Safety Check 17 B) en hexadecimal; el puente no la interpreta, solo comprueba que sea hexadecimal válido de hasta 255 bytes. El CRC lo validan el panel y la app.
- Cualquier otra línea (mensajes de arranque del ESP32, depuración) se ignora; se ve con `-v`.
- Al abrir el puerto, el puente deja DTR y RTS desactivados para no reiniciar el ESP32.

## Comportamiento ante fallas

| Situación | Qué hace el puente | Qué ve el panel |
|-----------|--------------------|-----------------|
| Se desconecta el USB | Cierra las conexiones WebSocket y reintenta abrir el puerto cada 1 a 30 s. | «Sin conexión»; sus acuses quedan en cola y se envían al reconectar. |
| El panel se conecta sin pasarela | Rechaza la conexión. | «Sin conexión» y reintenta. |
| Mensaje ilegible del panel | Lo descarta y lo registra. | — |

**Limitación:** el panel marca un acuse como Enviado en cuanto el puente lo recibe. Si el USB se desconecta justo en ese instante, esa trama puede perderse sin aviso.

## Pruebas

Sin hardware: usan el puerto en bucle de pyserial (`loop://`) y un puerto falso que simula desconectar el USB.

```
.venv\Scripts\python -m unittest -v test_puente_radio
```

Para probar a mano sin T-Beam: `python puente_radio.py --puerto loop://`. Todo lo que el panel transmite regresa como si se hubiera escuchado por radio.

## Seguridad

Por omisión escucha en `0.0.0.0` para que el contenedor lo alcance por `host.docker.internal`; eso también lo expone en la red local. El firewall de Windows pedirá permiso la primera vez: permite solo **redes privadas**.
