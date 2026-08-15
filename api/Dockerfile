FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    TZ=Asia/Tokyo

WORKDIR /app

# psycopg は binary wheel を使うため libpq のビルドは不要
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# PDF出力（F-09）用。Chromiumと、それを動かすOS依存パッケージを一緒に入れる。
# --with-deps がないと python:3.12-slim（Debian）側にheadless Chromiumが
# 起動できない（フォント・共有ライブラリ不足）。
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
