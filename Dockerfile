FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias primero (mejor cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar codigo
COPY wallapop_monitor.py .

# Crear volumen para persistir historial de chollos vistos
VOLUME ["/app/data"]
ENV HISTORIAL_PATH=/app/data/chollos_vistos.json

# Zona horaria Espana
ENV TZ=Europe/Madrid

# Ejecutar sin buffer para ver logs en tiempo real
ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "wallapop_monitor.py"]
