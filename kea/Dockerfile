# https://developers.home-assistant.io/docs/apps/configuration#app-dockerfile
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.24
FROM ${BUILD_FROM}

RUN apk add --no-cache \
        bash \
        kea-dhcp4 \
        kea-dhcp6 \
        py3-pip \
        python3

# Web UI 相依套件裝在獨立 venv，避免與系統 Python 套件互相影響
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir \
        fastapi==0.142.2 \
        uvicorn==0.54.0

COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]

LABEL \
    org.opencontainers.image.title="Home Assistant App: ISC Kea DHCP Server" \
    org.opencontainers.image.description="ISC Kea dual-stack DHCP server (DHCPv4 + optional DHCPv6) with an Ingress Web UI." \
    org.opencontainers.image.source="https://github.com/raphael1688dev/ha_app_kea"
