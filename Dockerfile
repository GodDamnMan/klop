FROM python:3.12-slim
WORKDIR /app
COPY server.py healthcheck.py ./
COPY static ./static
RUN useradd --create-home app && mkdir /data && chown app:app /data
USER app
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "healthcheck.py"]
CMD ["python", "server.py"]
