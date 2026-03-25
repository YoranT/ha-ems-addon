ARG BUILD_FROM
FROM $BUILD_FROM

# Install system packages
RUN apk add --no-cache \
    python3 \
    py3-pip \
    nginx \
    curl \
    bash \
    jq

# Install Python packages
RUN pip3 install --break-system-packages --no-cache-dir \
    aiohttp \
    aiofiles \
    pyyaml \
    paho-mqtt \
    pymodbus==3.6.9 \
    websockets

# Copy all files
COPY rootfs /

# Make scripts executable
RUN chmod a+x /usr/bin/run.sh

CMD [ "/usr/bin/run.sh" ]
