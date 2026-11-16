FROM node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS node
FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl libstdc++6 libatomic1 util-linux \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /opt/npm
ENV ENGINE_SOURCE_COMMIT=63cf40a044ad47739b84eceaf6fb2e298694ffe3
RUN curl --fail --location --proto '=https' --tlsv1.2 \
      https://codeload.github.com/wangthewhale/nuluno-engine-community/tar.gz/63cf40a044ad47739b84eceaf6fb2e298694ffe3 \
      --output /tmp/engine.tar.gz \
    && echo 'ff3b3da5355f4216c83da5a3acbb28cef347dd8883e505703ff48288f929c941  /tmp/engine.tar.gz' | sha256sum --check --status \
    && mkdir /opt/engine && tar -xzf /tmp/engine.tar.gz --strip-components=1 -C /opt/engine \
    && rm /tmp/engine.tar.gz \
    && python -m pip install --no-cache-dir /opt/engine \
    && cd /opt/engine/deploy/mcp-gateway \
    && node /opt/npm/bin/npm-cli.js ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && groupadd --gid 10001 engine && useradd --uid 10001 --gid 10001 --create-home engine \
    && mkdir /state /opt/pilot && chown engine:engine /state
COPY pilot.py start.sh /opt/pilot/
RUN chmod 755 /opt/pilot/start.sh
ENV ENGINE_STATE_DIR=/state ENGINE_BIND_HOST=0.0.0.0 PORT=10000 PYTHONUNBUFFERED=1
WORKDIR /opt/pilot
EXPOSE 10000
VOLUME ["/state"]
ENTRYPOINT ["/opt/pilot/start.sh"]
