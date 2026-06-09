FROM node:24-alpine AS miniapp
WORKDIR /miniapp
COPY miniapp/package.json miniapp/package-lock.json* ./
RUN npm install
COPY miniapp ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY allys ./allys
COPY --from=miniapp /miniapp/dist ./miniapp_dist
EXPOSE 8000
CMD ["uvicorn", "allys.main:app", "--host", "0.0.0.0", "--port", "8000"]
