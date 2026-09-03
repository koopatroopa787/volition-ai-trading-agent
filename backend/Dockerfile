FROM golang:1.24-alpine AS alpaca-cli
RUN CGO_ENABLED=0 go install github.com/alpacahq/cli/cmd/alpaca@v0.0.14

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENV VOLITION_PROJECT_ROOT=/app

WORKDIR /app/backend
COPY --from=alpaca-cli /go/bin/alpaca /usr/local/bin/alpaca
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
