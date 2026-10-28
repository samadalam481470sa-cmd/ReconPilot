FROM python:3.12-slim
WORKDIR /opt/reconpilot
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV RECONPILOT_DB=/data/reconpilot.db HOST=0.0.0.0 PORT=8000
RUN mkdir -p /data
EXPOSE 8000
CMD ["python", "-m", "app"]
