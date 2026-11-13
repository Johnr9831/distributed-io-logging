# Persona 1 — Servidor, Raspberry Pi y almacenamiento

## Tu responsabilidad

Construir y validar el **receptor** de registros. Garantizar que el ACK solo se envía después de un `os.fsync()` exitoso y medir los recursos utilizados por la Raspberry Pi.

**Tus archivos:** `server.py`, `monitor.py`, `verify.py`, `requirements-server.txt`. `protocol.py` es compartido y su formato se debe coordinar con la persona 2.

## Pasos de trabajo

1. Preparar la Pi: `sudo apt install python3-venv sysstat` y `pip install -r requirements-server.txt` dentro de un entorno virtual.
2. Ejecutar `python server.py --bind 0.0.0.0 --port 5000 --data-dir data`.
3. Compartir la IP de la Pi con la persona 2 (`hostname -I`).
4. Asegurarte de que el método `individual` realiza una sincronización por registro; el modo `batch` realiza una por grupo (hasta 50 registros o 50 ms).
5. Verificar que los ACK se envían únicamente después de la sincronización y que un error no se interpreta como éxito.
6. Comprobar `data/<run_id>.server.json`: `records_received`, `records_acked`, `fsync_calls`, `status`.
7. Ejecutar `python verify.py --run-id <run_id> --data-dir data` para comprobar cada byte almacenado frente a los datos esperados.
8. Revisar `data/<run_id>.monitor.csv` y, opcionalmente, `iostat -dx 1 <dispositivo>`.
9. Registrar las condiciones: modelo de Pi, sistema operativo, dispositivo microSD/SSD, sistema de archivos, conexión de red, temperatura, servicios en segundo plano.
10. Entregar a la persona 2 los JSON y CSV de la Pi para el análisis (conservar los `.bin` originales para verificación).

## Criterios de aceptación

- El servidor atiende **las dos estrategias** sin cambiar el código entre corridas.
- El modo individual cumple `fsync_calls == records_acked` cuando la corrida es completa.
- En batching, normalmente `fsync_calls < records_acked` si se acumulan registros.
- `verify.py` devuelve `status: OK` y número correcto de registros.
- Existen CSV por corrida con CPU, memoria y actividad de disco.
- Se documenta si el dispositivo de almacenamiento es microSD, SSD u otro.

## Comandos fundamentales

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-server.txt
python server.py --bind 0.0.0.0 --port 5000 --data-dir data
# En otra terminal:
lsblk -o NAME,TYPE,SIZE,MOUNTPOINT
iostat -dx 1
# Después de recibir una corrida:
python verify.py --run-id prueba_ind_001 --data-dir data
```

## Qué debes compartir para integrar

- IP y puerto de la Pi, estado del servidor.
- Evidencia de un registro con ACK en individual y en batch.
- Conteos exactos de sincronizaciones de ambos modos.
- Archivos de datos y mediciones para revisar consistencia.

**No hagas la campaña completa sin ejecutar antes un piloto con la persona 2.**
