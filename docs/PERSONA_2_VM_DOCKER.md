# Persona 2 — Cliente Linux/Docker, experimentos y análisis

## Tu responsabilidad

Construir y validar el **emisor de registros** dentro de Docker; automatizar las cargas y producir estadísticas y figuras reproducibles.

**Tus archivos:** `client.py`, `run_experiments.py`, `analyze.py`, `Dockerfile`, `requirements-analysis.txt`. `protocol.py` es compartido con la persona 1.

## Pasos de trabajo

1. Instalar/preparar Docker en la VM Linux.
2. Desde el proyecto: `docker build -t io-log-client .`.
3. Pedir a la persona 1 la IP de la Pi y comprobar conectividad al puerto 5000.
4. Enviar primero 10 s a 100 registros/s en modo `individual`, y después repetir en `batch` con **otro `run_id`**.
5. Revisar que el emisor no espere el ACK para enviar el próximo registro (recepción de ACK en un hilo separado).
6. Guardar `results/<run_id>.client.json` y `.latency.csv` por cada ejecución.
7. Ejecutar el piloto `run_experiments.py --pilot`, verificar que el servidor genera archivos y que se reciben los ACK.
8. Confirmar con la persona 1 que las corridas tienen registros íntegros y monitorización correcta.
9. Lanzar la matriz completa (2 estrategias × 3 cargas × 5 repeticiones = 30 corridas) únicamente cuando ambos lo aprueben como válido.
10. Copiar los metadatos/monitorización de la Pi, correr `analyze.py` y documentar throughput, latencia p50/p95, variabilidad, CPU y disco.

## Criterios de aceptación

- El cliente envía exactamente registros de **1 024 bytes** (ID de 8 bytes + contenido reproducible).
- Puede programar 100/300/600 registros/s y la duración mediante argumentos CLI.
- Mide tiempos con reloj monotónico del **cliente**, desde envío hasta ACK.
- Registra ACK dentro de la ventana de carga y ACK tardíos durante drenaje por separado.
- Repite cada escenario cinco veces y alterna el orden entre los dos métodos.
- Genera `manifest.csv`, `run_table.csv`, `aggregate.csv` y gráficos.
- Excluye los experimentos fallidos de los promedios y conserva el detalle de las fallas.

## Comandos fundamentales

```bash
docker build -t io-log-client .
mkdir -p results
# Sustituir IP_DE_PI por la IP real:
docker run --rm -v "$PWD/results:/app/results" io-log-client \
  run_experiments.py --host IP_DE_PI --pilot --output-dir /app/results
# Campaña completa:
docker run --rm -v "$PWD/results:/app/results" io-log-client \
  run_experiments.py --host IP_DE_PI --rates 100,300,600 \
  --duration 60 --repetitions 5 --pause 5 --output-dir /app/results
# Después de copiar de la Pi los *.server.json y *.monitor.csv:
pip install -r requirements-analysis.txt
python analyze.py --results-dir results --server-data-dir pi_data
```

## Qué debes compartir para integrar

- Versión del cliente y los parámetros de las cargas.
- CSV de latencias y resumen por escenario.
- Comparaciones de throughput y p95 acompañadas de desviación estándar.
- Registros de ejecuciones fallidas o ACK sin respuesta.

**Importante:** 600 registros/s es una tasa solicitada, no necesariamente alcanzada. Reportar la tasa real y los ACK recibidos en los 60 segundos, además de los ACK tardíos.
