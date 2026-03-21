FROM python:3.11-slim AS base
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY payment-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS final
COPY deployment-services/shared ./shared
COPY payment-service/api ./api
COPY payment-service/core ./core
COPY payment-service/manage.py ./manage.py

ENV PYTHONPATH=/app
EXPOSE 8004
CMD ["python", "manage.py", "runserver", "0.0.0.0:8004"]
