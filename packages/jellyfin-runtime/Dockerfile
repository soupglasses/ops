# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

FROM jellyfin/jellyfin:12.4@sha256:fea863d0cd9ec445985d9c75fc6ed64e38a1d2766cf0c250a33d553f4fc1b771
LABEL org.opencontainers.image.source="https://github.com/soupglasses/ops"
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=plugins plugins/ /opt/jellyfin/plugins/
COPY --from=plugins licenses/ /usr/share/licenses/jellyfin-plugins/
COPY runtime.py health.py /usr/local/lib/jellyfin/
ENV JELLYFIN_DATA_DIR=/config/server \
    JELLYFIN_CONFIG_DIR=/config/server/config \
    JELLYFIN_LOG_DIR=/config/server/log \
    JELLYFIN_CACHE_DIR=/cache
ENTRYPOINT ["python3", "/usr/local/lib/jellyfin/runtime.py", "/jellyfin/jellyfin"]
