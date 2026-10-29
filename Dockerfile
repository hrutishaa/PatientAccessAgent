FROM python:3.12-slim
WORKDIR /srv
RUN useradd -m app
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .
USER app
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
