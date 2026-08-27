FROM python:3.12-slim

# WeasyPrint 69's native libraries -- the minimal verified set (Pango for
# text layout, Harfbuzz for shaping, DejaVu so fonts always exist). Modern
# WeasyPrint needs NO cairo/gdk-pixbuf (pydyf replaced them) and no
# compilers (every dependency ships a Linux wheel).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so rebuilds reuse the layer cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn uvicorn

# The app code (venv/.git/tests/tools are excluded via .dockerignore).
COPY . .

EXPOSE 8000

CMD ["gunicorn", "fastapi_app:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000"]
