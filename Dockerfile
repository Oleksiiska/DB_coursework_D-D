# Образ застосунку: той самий образ для всіх процесів — API, завантажувача
# CSV, генератора, краулера, процесу запису й перевірки сховища. Процес
# обирається аргументом запуску:  docker run <образ> storage-check
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Залежності — окремим шаром: зміна коду не перевстановлює драйвери
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY workers ./workers

# Непривілейований користувач. Каталог журналів у Compose монтується
# з хоста, тож журнали лишаються після видалення контейнера.
RUN useradd --uid 1000 --create-home app \
    && mkdir -p /app/logs \
    && chown app:app /app/logs
USER app

ENV LOG_DIR=/app/logs

# Порт процесу API (у Compose відкрито лише на 127.0.0.1)
EXPOSE 8000

ENTRYPOINT ["python", "-m", "workers.launcher"]
CMD ["storage-check"]
