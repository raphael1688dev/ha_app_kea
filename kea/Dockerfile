# https://developers.home-assistant.io/docs/apps/configuration#app-dockerfile
FROM ghcr.io/home-assistant/base:3.24

RUN apk add --no-cache \
        bash \
        kea-dhcp4 \
        kea-dhcp6 \
        py3-pip \
        python3

# The Web UI dependencies go into a separate venv so they cannot interfere with the system Python packages.
# All versions are pinned in requirements.txt (kept current by Renovate).
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]

# BUILD_VERSION / BUILD_ARCH are provided by the Supervisor (or the HA builder action).
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}" \
    org.opencontainers.image.title="Home Assistant App: ISC Kea DHCP Server" \
    org.opencontainers.image.description="ISC Kea dual-stack DHCP server (DHCPv4 + optional DHCPv6) with an Ingress Web UI." \
    org.opencontainers.image.source="https://github.com/raphael1688dev/ha_app_kea" \
    org.opencontainers.image.licenses="MIT"
