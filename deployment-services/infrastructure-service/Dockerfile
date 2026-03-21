FROM python:3.11-slim AS base
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget unzip libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Terraform
RUN wget -q https://releases.hashicorp.com/terraform/1.7.0/terraform_1.7.0_linux_amd64.zip && \
    unzip terraform_1.7.0_linux_amd64.zip && \
    mv terraform /usr/local/bin/ && \
    rm terraform_1.7.0_linux_amd64.zip

COPY deployment-services/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS final
COPY deployment-services/shared ./shared
COPY deployment-services/infrastructure-service/api ./api
COPY deployment-services/infrastructure-service/core ./core
COPY deployment-services/infrastructure-service/infra ./infra
COPY deployment-services/infrastructure-service/manage.py \
     deployment-services/infrastructure-service/__init__.py \
     ./

ENV PYTHONPATH=/app
EXPOSE 8003
CMD ["python", "manage.py", "runserver", "0.0.0.0:8003"]
