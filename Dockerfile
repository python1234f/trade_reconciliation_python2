FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EVENTS_FILE=/app/data/trade-events-sample.jsonl \
    CLEARING_FILE=/app/data/clearing-positions-sample.csv \
    BREAK_EXPLAINER=offline

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir '.[dev,llm]'

EXPOSE 8000
CMD ["uvicorn", "trade_reconciliation.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
