# https://developers.home-assistant.io/docs/apps/configuration#app-dockerfile
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.24
FROM ${BUILD_FROM}

RUN apk add --no-cache \
        bash \
        jq \
        kea-dhcp4 \
        kea-dhcp6

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]

LABEL \
    org.opencontainers.image.title="Home Assistant App: ISC Kea DHCP Server" \
    org.opencontainers.image.description="ISC Kea dual-stack DHCP server (DHCPv4 + optional DHCPv6)." \
    org.opencontainers.image.source="https://github.com/raphael1688dev/ha_app_kea"
