FROM alpine:3.20

RUN apk add --no-cache \
        bash \
        jq \
        kea-dhcp4 \
        kea-dhcp6

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
