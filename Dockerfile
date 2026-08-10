# ---- builder: install the package and its deps into an isolated prefix ----
FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml .
COPY src/ src/

# --prefix isolates the install so the runtime stage copies only the result, not the build toolchain.
RUN pip install --no-cache-dir --prefix=/install .

# ---- runtime: minimal image, unprivileged user, no build toolchain ----
FROM python:3.12-slim AS runtime

# System user with no login shell and no home to write to.
RUN groupadd --system dicomhawk \
    && useradd --system --gid dicomhawk --no-create-home --shell /usr/sbin/nologin dicomhawk

# libmagic1: python-magic links against it; yara-python bundles its own libyara.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# Writable runtime paths owned by the unprivileged user (traces, DB, logs live on mounted volumes).
RUN mkdir -p /opt/dicomhawk/storage /opt/dicomhawk/state /var/log/dicomhawk \
    && chown -R dicomhawk:dicomhawk /opt/dicomhawk /var/log/dicomhawk

USER dicomhawk
WORKDIR /opt/dicomhawk

CMD ["dicomhawk", "serve"]
