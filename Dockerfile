FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV OBLIGATE_HOST=0.0.0.0 PORT=8000 OBLIGATE_DB=/data/obligate.db
RUN mkdir -p /data
EXPOSE 8000
CMD ["python", "-m", "obligate.server"]
