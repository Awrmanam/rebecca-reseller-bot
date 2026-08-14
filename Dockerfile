FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY app app
RUN pip install --no-cache-dir .
RUN mkdir -p /data && chown -R nobody:nogroup /data
USER nobody
EXPOSE 8080
CMD ["python","-m","app.main"]
