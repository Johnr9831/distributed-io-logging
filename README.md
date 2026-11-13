# I/O Performance — Individual vs. Batched `fsync()`

**Proyecto experimental de Sistemas Operativos:** *Experimental Evaluation of Individual vs. Batched I/O Operations in a Distributed Logging System*.

## 1. Qué construimos

```text
             VM Linux                           Raspberry Pi
        ┌─────────────────┐                ┌───────────────────────┐
        │ Contenedor      │                │ Servidor Python       │
        │ client.py       │ ── TCP logs ──>│ server.py             │
        │ (1 KiB/registro)│                │      │                │
        │                 │ <── TCP ACK ───│ write() → fsync()     │
        │ timestamps ACK  │                │ monitor.py → CSV      │
        └─────────────────┘                └───────────────────────┘
```

- **Individual:** `recibir → escribir 1 registro → os.fsync() → ACK`.
- **Batch:** `recibir → acumular hasta 50 registros O 50 ms desde el primero → escribir lote → os.fsync() → ACK de cada registro del lote`.
- **Semántica de ACK:** el servidor confirma un registro **solo después** de que `os.fsync()` termina sin error. Esto **no equivale** a una garantía universal frente a cortes de energía en cualquier hardware.
- **Experimentos previstos:** 100, 300 y 600 registros/s; registros de **1 024 bytes**; **60 s** por ejecución; **5 repeticiones**; **2 métodos** = **30 corridas**. Los parámetros son un plan inicial: validar con una prueba piloto antes de medir.
- **Una conexión TCP por ejecución.** El servidor atiende secuencialmente las corridas y crea un archivo `.bin` diferente por `run_id`. No se comparan corridas paralelas.

## 2. Reparto de trabajo para dos personas

| Persona | Componente | Archivos que mantiene | Entregable verificable |
|---|---|---|---|
| **1 — Raspberry Pi / I/O** | Instalar la Pi, implementar y ajustar las dos estrategias de escritura, respetar ACK después de `fsync`, monitorizar recursos, verificar datos | `server.py`, `monitor.py`, `verify.py`, `requirements-server.txt` | Capturas de servidor, archivos `.bin`, `.server.json`, `.monitor.csv` y resultado de `verify.py`; medición de dispositivo de almacenamiento |
| **2 — VM / Docker / resultados** | Preparar contenedor, generar tasa estable, medir envío→ACK, automatizar 30 corridas, calcular estadísticas y elaborar gráficos | `client.py`, `run_experiments.py`, `analyze.py`, `Dockerfile`, `requirements-analysis.txt` | Imagen Docker, CSV de latencias, JSON de cliente, `manifest.csv`, `aggregate.csv` y gráficos |
| **Ambas** | Acordar protocolo TCP, pruebas integradas Pi ↔ VM, controles experimentales, revisión de errores, informe final | `protocol.py`, `tests/`, este `README.md` | Prueba completa reproducible; interpretación de throughput, p50, p95, CPU y disco |

**Regla de integración:** `protocol.py` es el contrato entre ambos: encabezado JSON con longitud, registros binarios de tamaño fijo con ID de 8 bytes y ACK binarios de 8 bytes. Acordar cualquier modificación de este archivo antes de hacerla. La persona 1 no debe depender de la implementación interna del cliente y viceversa.

### Flujo sugerido de Git

```bash
git init
git add .
git commit -m "Initial experimental logging system"
git branch persona1-pi
git branch persona2-vm
```

Cada integrante trabaja en su rama (pueden crear las ramas desde GitHub). Integrar mediante pull requests y ejecutar `python -m unittest discover -s tests -v` después de cada fusión. No suban al repositorio `data/`, `results/` ni `.venv/` (ignorados en `.gitignore`).

## 3. Archivos incluidos

```text
io_logging_project/
├── README.md                 # Guía para ambos integrantes
├── protocol.py               # Mensajes TCP compartidos
├── server.py                 # Persona 1, ejecuta en Raspberry Pi
├── monitor.py                # Persona 1, CPU/RAM/escrituras disco por segundo
├── verify.py                 # Persona 1, comprueba IDs y contenido guardado
├── requirements-server.txt   # psutil para monitorización
├── client.py                 # Persona 2, emisor y mediciones de latencia
├── run_experiments.py        # Persona 2, piloto y matriz de corridas
├── analyze.py                # Persona 2, tablas y gráficos
├── requirements-analysis.txt # matplotlib para gráficos
├── Dockerfile                # Persona 2, contenedor del cliente
├── .gitignore
└── tests/
    ├── test_protocol.py
    └── test_integration.py   # Prueba extremo a extremo local (ambos modos)
```

## 4. Preparar la Raspberry Pi — persona 1

Se recomienda Python 3.10+ y red LAN estable. En la Pi, copiar o clonar el mismo proyecto y ejecutar desde su carpeta:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip sysstat
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-server.txt
python server.py --bind 0.0.0.0 --port 5000 --data-dir data
```

El servidor permanece escuchando y atiende todas las corridas sucesivas. En otra terminal puede usar:

```bash
hostname -I
lsblk -o NAME,TYPE,SIZE,MOUNTPOINT
# Si el almacenamiento principal corresponde, por ejemplo, a mmcblk0:
iostat -dx 1 mmcblk0
```

Opcional: cerrar el servidor y reiniciarlo con `--disk-device mmcblk0` **solo si** `mmcblk0` aparece en `psutil.disk_io_counters(perdisk=True)`; así el CSV de monitor usa ese dispositivo en lugar de todos los discos juntos. Para ver los nombres disponibles:

```bash
python -c 'import psutil; print(list(psutil.disk_io_counters(perdisk=True)))'
```

Permitir el puerto TCP 5000 únicamente dentro de la red de laboratorio. Si hay `ufw` activo, configurar una regla apropiada para la red de la VM; no exponer el servicio a Internet. **No ejecutar como `sudo`** salvo que exista una razón específica.

## 5. Preparar VM Linux + Docker — persona 2

Desde la carpeta del proyecto en la VM:

```bash
docker build -t io-log-client .
mkdir -p results
```

Reemplazar `192.168.1.50` por la **IP real de la Raspberry Pi**:

```bash
docker run --rm \
  -v "$PWD/results:/app/results" \
  io-log-client client.py \
  --host 192.168.1.50 \
  --mode individual \
  --rate 100 \
  --duration 10 \
  --run-id prueba_ind_001 \
  --output-dir /app/results
```

Probar también batching con otro identificador (cada `run-id` debe ser único):

```bash
docker run --rm \
  -v "$PWD/results:/app/results" \
  io-log-client client.py \
  --host 192.168.1.50 \
  --mode batch \
  --rate 100 \
  --duration 10 \
  --run-id prueba_batch_001 \
  --output-dir /app/results
```

Si Docker en la VM no puede llegar a la IP de la Pi, verificar primero la conectividad desde la VM (`ping` / `nc -vz 192.168.1.50 5000`) y después la red Docker. No necesitas `--network host` si la red bridge de Docker tiene ruta hacia la Pi.

### Prueba piloto automatizada (dos corridas de 5 s)

```bash
docker run --rm \
  -v "$PWD/results:/app/results" \
  io-log-client run_experiments.py \
  --host 192.168.1.50 \
  --pilot \
  --output-dir /app/results
```

**Antes de la campaña final:** confirmar que ambos modos completan, que se reciben todos los ACK, que `verify.py` reporta `OK`, que `monitor.py` genera CSV y que la Pi no queda saturada de forma que impida comparar resultados. Los 100/300/600 registros/s son propuestas, no tasas garantizadas; si las cambian, documentar por qué.

### Campaña completa: 30 ejecuciones

```bash
docker run --rm \
  -v "$PWD/results:/app/results" \
  io-log-client run_experiments.py \
  --host 192.168.1.50 \
  --rates 100,300,600 \
  --duration 60 \
  --repetitions 5 \
  --pause 5 \
  --batch-size 50 \
  --batch-timeout-ms 50 \
  --output-dir /app/results
```

**Tiempo de carga activa:** 30 × 60 s = 30 minutos; se suman pausas, sincronizaciones y drenaje de ACK pendientes. El cliente envía sin esperar el ACK anterior, para que la tasa de carga no quede artificialmente limitada a un único registro en vuelo. El throughput reportado como `throughput_window_logs_s` cuenta **ACK recibidos dentro de la ventana activa** dividido entre los segundos configurados; el drenaje posterior se registra por separado.

## 6. Verificación, resultados y gráficos

En la Pi, después de cada ejecución, buscar archivos como:

```text
data/prueba_ind_001.bin          # Datos escritos realmente
data/prueba_ind_001.server.json  # Contadores: recibidos, ACK, fsync
data/prueba_ind_001.monitor.csv  # CPU/RAM/disco a intervalos de ~1 s
```

Para verificar contenido completo determinista (no solo el número de registros):

```bash
python verify.py --run-id prueba_ind_001 --data-dir data
```

En la VM, el cliente crea:

```text
results/prueba_ind_001.client.json  # Resumen throughput y percentiles
results/prueba_ind_001.latency.csv  # Tiempo envío→ACK por registro
results/manifest.csv                # Registro de todas las ejecuciones del runner
```

Copiar los archivos de metadatos y monitorización de la Pi a la VM (ajustar usuario/ruta):

```bash
scp -r usuario@192.168.1.50:/ruta/al/proyecto/data ./pi_data
```

Para evitar transferir los archivos `.bin` grandes puede copiar únicamente `*.server.json` y `*.monitor.csv`, pero los `.bin` deben conservarse para auditar la integridad. Ejecutar el análisis en la **VM o equipo anfitrión**, con Python disponible:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-analysis.txt
python analyze.py --results-dir results --server-data-dir pi_data
```

Se generan:

- `results/run_table.csv`: tabla por ejecución, incluidas las incompletas.
- `results/aggregate.csv`: promedios y desviaciones estándar por carga y estrategia, solo corridas completas.
- `results/throughput.png` y `results/latency_p95.png`: comparaciones principales.
- `results/server_cpu.png` y `results/disk_writes.png`: si se copiaron las mediciones de la Pi.

**Interpretación:** latencia p95 = percentil 95 de la latencia envío→ACK de cada corrida; en `aggregate.csv` se promedian los p95 de las repeticiones, lo cual **no** es el p95 global combinado. La CPU del servidor puede alcanzar más del 100 % si se computa sobre múltiples núcleos (métrica de proceso en `psutil`). Los contadores de disco pueden incluir E/S de otros procesos; documentar la carga de fondo.

## 7. Comprobar que funciona sin una Raspberry Pi (solo desarrollo)

En un equipo con Python 3 y `psutil` instalado:

```bash
pip install -r requirements-server.txt
python -m unittest discover -s tests -v
```

Las pruebas inician un servidor local, envían registros de ambos modos y verifican los archivos recibidos. Esto **no sustituye** la ejecución obligatoria con Pi + VM; sirve para integrar código antes de disponer del hardware.

## 8. Preguntas y criterios experimentales importantes

1. **¿Qué demuestra el estudio?** Si batching cambia throughput y latencia respecto de sincronizar cada registro en ese hardware, con esas cargas y el mismo protocolo.
2. **¿Qué no demuestra?** Que batching siempre es mejor, que `fsync()` garantiza persistencia ante cualquier fallo físico o que los resultados se generalizan a SSD NVMe o a todas las Pi.
3. **Controlar variables:** mismo Pi, sistema de archivos, disco, red, record size, semillas, temperatura y procesos de fondo; alternar orden individual/batch.
4. **Congestión y backlog:** distinguir tasa programada de envíos, tasa lograda de envío, ACK dentro de los 60 s y ACK recibidos durante el drenaje.
5. **Resultados incompletos:** si faltan ACK o falla el servidor, la corrida queda como incompleta y **no** se mezcla en el promedio; investigarla y repetirla cuando proceda.
6. **Medición del tiempo:** la latencia se mide íntegramente en el cliente con reloj monotónico, sin necesitar sincronización de relojes entre la VM y la Pi.
7. **Referencia del trabajo:** *CJFS: Concurrent Journaling for Better Scalability*, USENIX FAST 2023. Su optimización de journaling es un antecedente de sobrecarga de sincronización; **no es** una replicación exacta de la comparación de `os.fsync()` individual vs batching a nivel de aplicación.

## 9. Plan de integración mínimo

- **Etapa A:** persona 1 ejecuta `server.py`; persona 2 valida conexión y un ACK (método individual).
- **Etapa B:** persona 1 activa batching; persona 2 compara el número de llamadas `fsync_calls` para los mismos registros.
- **Etapa C:** ambas comprueban `verify.py`, monitorización y corridas piloto.
- **Etapa D:** persona 2 lanza 30 corridas; persona 1 supervisa temperatura, almacenamiento, red y archivos por corrida.
- **Etapa E:** ambas revisan `aggregate.csv`, gráficos, resultados fallidos y redactan metodología, limitaciones y conclusiones.

**Prioridad:** que el protocolo, los ACK y la verificación de almacenamiento sean correctos **antes** de comparar las cifras de rendimiento.
