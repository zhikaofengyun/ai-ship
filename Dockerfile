FROM python:3.12-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 ship
WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY web ./web
COPY contracts ./contracts
COPY scripts/run.py ./scripts/run.py
RUN mkdir /data && chown ship:ship /data
USER ship
ENV AI_SHIP_MODE=team AI_SHIP_DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python", "scripts/run.py", "--host", "0.0.0.0", "--port", "8765", "--data-dir", "/data"]
