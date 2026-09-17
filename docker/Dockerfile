FROM ubuntu:26.04

WORKDIR /app

COPY --chmod=755 docker/docker-build.sh ./
COPY docker/patches/ /opt/lamp/patches/
COPY docker/git-hooks/ /opt/lamp/git-hooks/

RUN --mount=type=cache,id=lamp-apt,target=/var/cache/apt,sharing=locked \
    LAMP_IMAGE_BUILD=1 /app/docker-build.sh

# Required by existing projects, in addition to the archived LAMP setup.
RUN apt-get update && apt-get install -y --no-install-recommends pandoc xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

# Some projects pin dependencies to Python 3.12 rather than Ubuntu's system Python.
RUN cd /tmp \
    && curl --fail --show-error --location --retry 3 --connect-timeout 30 --max-time 300 \
        https://www.python.org/ftp/python/3.12.14/Python-3.12.14.tar.xz -o Python-3.12.14.tar.xz \
    && printf '%s\n' '5c8462af5790baf43a321a1559dbe0db06d1be4300fb85fb53c40060668e548a  Python-3.12.14.tar.xz' | sha256sum --check \
    && tar -xJf Python-3.12.14.tar.xz \
    && cd Python-3.12.14 \
    && ./configure --prefix=/opt/python3.12 --without-static-libpython --disable-test-modules --with-ensurepip=install \
    && make -j4 && make altinstall \
    && ln -s /opt/python3.12/bin/python3.12 /usr/local/bin/python3.12 \
    && python3.12 -c 'import ssl, hashlib, sqlite3, bz2, lzma, ctypes, readline, venv' \
    && cd /tmp && rm -rf /tmp/Python-3.12.14 /tmp/Python-3.12.14.tar.xz

RUN apt-get update && apt-get install -y --no-install-recommends openvpn wireguard-tools iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY --chmod=755 docker/docker-entrypoint.sh docker/docker-start.sh ./
COPY docker/scripts/ /opt/lamp/
# Shipped for `docker run ... init`, which copies the host cli and compose file out of a pulled image.
COPY lamp ./lamp
COPY docker/docker-compose.yml ./docker/docker-compose.yml

ENV PATH="/root/.local/bin:/root/.cargo/bin:/root/.opencode/bin:/root/.syncdb/vendor/bin:/root/.git-filter-repo:${PATH}:/opt/lamp/node-tools" \
    LANG=en_US.UTF-8 \
    TZ=Europe/Berlin \
    COMPOSER_ALLOW_SUPERUSER=1

# Disabled together with the CUDA installation in docker-build.sh.
# ENV PATH="/usr/local/cuda/bin:${PATH}" NVCC_CCBIN=/usr/bin/g++-14

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["/app/docker-start.sh"]
