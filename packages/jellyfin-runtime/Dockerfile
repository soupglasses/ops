# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

FROM jellyfin/jellyfin:12.2@sha256:357724bf0ae27a672c7cbaa899db2d9abeb13dbd8657ccce750258a4c059d037
LABEL org.opencontainers.image.source="https://github.com/soupglasses/ops"
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates restic \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ldap-plugin . /opt/jellyfin/plugins/LDAP-Auth_24.0.0.0/
COPY --from=ldap-license /LICENSE /usr/share/licenses/jellyfin-ldap/LICENSE
COPY runtime.py configuration.py media_restore.py health.py /usr/local/lib/jellyfin/
ENV JELLYFIN_DATA_DIR=/config/server \
    JELLYFIN_CONFIG_DIR=/config/server/config \
    JELLYFIN_LOG_DIR=/config/server/log \
    JELLYFIN_CACHE_DIR=/cache
ENTRYPOINT ["python3", "/usr/local/lib/jellyfin/runtime.py", "serve", "--", "/jellyfin/jellyfin"]
