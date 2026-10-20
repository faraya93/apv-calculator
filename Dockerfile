# Imagen base oficial de Python, variante slim (sin extras innecesarios).
FROM python:3.13-slim

WORKDIR /app

# Primero solo requirements: Docker cachea esta capa y no reinstala
# dependencias salvo que requirements.txt cambie.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Después, el código de la app.
COPY . .

EXPOSE 8501

# 0.0.0.0 = la app escucha fuera del contenedor (tu navegador la puede abrir).
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
