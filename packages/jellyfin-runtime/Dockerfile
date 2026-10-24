# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

FROM jellyfin/jellyfin:12.3@sha256:2038fe038ab3876a80f6e7b7e0aef6607b032c7dc0c9b3ad3296d0b2eba3bbb5
LABEL org.opencontainers.image.source="https://github.com/soupglasses/ops"
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ARG LDAP_PLUGIN_VERSION
COPY --from=ldap-plugin . /opt/jellyfin/plugins/LDAP-Auth_${LDAP_PLUGIN_VERSION}/
COPY --from=ldap-license /LICENSE /usr/share/licenses/jellyfin-ldap/LICENSE
COPY runtime.py health.py /usr/local/lib/jellyfin/
ENV JELLYFIN_DATA_DIR=/config/server \
    JELLYFIN_CONFIG_DIR=/config/server/config \
    JELLYFIN_LOG_DIR=/config/server/log \
    JELLYFIN_CACHE_DIR=/cache
ENTRYPOINT ["python3", "/usr/local/lib/jellyfin/runtime.py", "/jellyfin/jellyfin"]
