# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
FROM golang:1.27.3-alpine3.23@sha256:5d5113301ffcac84e2bfb20c42648803fecaea6789c89bb9b66884e18cbb30a7 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY integration ./integration
RUN CGO_ENABLED=0 go test -c -trimpath -ldflags="-s -w" -buildvcs=false -p=2 -tags=system -o /out/integration ./integration

FROM alpine:3.24@sha256:3f4d6b32e26703c6082839234f43769d5046b3ac7c910033911d1e71d8cd630a AS tests
LABEL org.opencontainers.image.source="https://github.com/soupglasses/ops"
RUN apk add --no-cache openldap openldap-back-mdb openldap-overlay-ppolicy openldap-clients python3 py3-pip && \
    apk add --no-cache --virtual .build python3-dev openldap-dev gcc musl-dev && \
    pip install --no-cache-dir --break-system-packages 'canaille[ldap,oidc]==0.3.6' && apk del .build
COPY --from=build /out/integration /tests/integration
COPY tests/verify_canaille.py /work/tests/verify_canaille.py
COPY canaille-schema/oauth2-openldap.ldif /fixtures/oauth2-openldap.ldif
ENV LDAP_MODULE_PATH=/usr/lib/openldap CANAILLE_SCHEMA_LDIF=/fixtures/oauth2-openldap.ldif CANAILLE_TEST_PYTHON=/usr/bin/python3
WORKDIR /work/integration
ENTRYPOINT ["/tests/integration"]
