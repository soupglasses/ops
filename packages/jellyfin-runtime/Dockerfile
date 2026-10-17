# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT

FROM mcr.microsoft.com/dotnet/sdk:9.0@sha256:2f5eb32870b4c0074a3d5b5191a03553bcab7b74463a76a7c0a5f3ef65c90fe5 AS ldap-build
WORKDIR /src
COPY --from=ldap-source . .
COPY patches/ldap-sso-profile-images.patch /tmp/ldap-sso-profile-images.patch
RUN git apply /tmp/ldap-sso-profile-images.patch \
    && dotnet publish LDAP-Auth/LDAP-Auth.csproj -c Release -o /out

FROM jellyfin/jellyfin:10.11.11@sha256:aefb67e6a7ff1debdd154a78a7bbb780fd0c873d8639210a7f6a2016ad2b35db
LABEL org.opencontainers.image.source="https://github.com/soupglasses/ops"
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates restic \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ldap-build /out/LDAP-Auth.dll /out/Novell.Directory.Ldap.NETStandard.dll /opt/jellyfin/plugins/LDAP-Auth_23.0.0.0/
COPY --from=ldap-source /LICENSE /usr/share/licenses/jellyfin-ldap/LICENSE
COPY --from=sso-plugin . /opt/jellyfin/plugins/SSO-Auth_4.0.0.4/
COPY --from=sso-license /LICENSE.txt /usr/share/licenses/jellyfin-sso/LICENSE
COPY runtime.py configuration.py media_restore.py health.py /usr/local/lib/jellyfin/
ENV JELLYFIN_DATA_DIR=/config/server \
    JELLYFIN_CONFIG_DIR=/config/server/config \
    JELLYFIN_LOG_DIR=/config/server/log \
    JELLYFIN_CACHE_DIR=/cache
ENTRYPOINT ["python3", "/usr/local/lib/jellyfin/runtime.py", "serve", "--", "/jellyfin/jellyfin"]
