FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY . .
# ponytail: dev deps baked in; split a slim prod stage when deploying (M1 is dev-only).
RUN pip install --no-cache-dir -e ".[dev]"
RUN useradd -m app
USER app
EXPOSE 8000
