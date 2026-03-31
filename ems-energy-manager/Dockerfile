ARG BUILD_FROM
FROM $BUILD_FROM

# System packages
RUN apk add --no-cache \
    python3 \
    py3-pip \
    nginx \
    curl \
    bash \
    jq

# Python packages — no MQTT, real device drivers
RUN pip3 install --break-system-packages --no-cache-dir \
    aiohttp==3.9.5 \
    aiofiles==23.2.1 \
    pyyaml \
    solaredge-modbus==0.8.0 \
    pymodbus==3.6.9

# Copy files
COPY rootfs /

RUN chmod a+x /usr/bin/run.sh

CMD [ "/usr/bin/run.sh" ]
