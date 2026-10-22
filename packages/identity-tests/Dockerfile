# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
FROM golang:1.27.3-alpine3.23@sha256:7fd63dd7c37f5ddaef15bd8fd3f3e4f90f0d50cd9cae255c7cf73887a494d8df AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY integration ./integration
RUN CGO_ENABLED=0 go test -c -trimpath -ldflags="-s -w" -buildvcs=false -p=2 -tags=system -o /out/integration ./integration

FROM alpine:3.23@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS tests
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
