FROM python:3.12-slim
WORKDIR /app
COPY server.py .
COPY static ./static
RUN useradd --create-home app && mkdir /data && chown app:app /data
USER app
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "server.py"]
