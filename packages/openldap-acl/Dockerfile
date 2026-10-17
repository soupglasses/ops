# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
FROM docker.io/nfrastack/openldap:2.6-8.1.1@sha256:5e5c8fdd1d7e3a9623500839699b258d0569bf965528b85c682ec5d0902f5d6c
LABEL org.opencontainers.image.source="https://github.com/soupglasses/ops"
USER root
RUN apk add --no-cache python3
COPY reconcile.py serve.sh /usr/local/lib/openldap-acl/
USER 389:389
ENTRYPOINT ["python3", "/usr/local/lib/openldap-acl/reconcile.py"]
