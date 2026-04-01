FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install .

RUN adduser --disabled-password --gecos "" dicomhawk && \
    chown -R dicomhawk:dicomhawk /app

USER dicomhawk

CMD ["dicomhawk", "serve"]
