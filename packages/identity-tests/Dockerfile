# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
FROM golang:1.27.4-alpine3.23@sha256:f70850ef6ce341e8b6427f677049145d63c0b7ce002d4d7e3d464828b1b96dcd AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY integration ./integration
RUN CGO_ENABLED=0 go test -c -trimpath -ldflags="-s -w" -buildvcs=false -p=2 -tags=system -o /out/integration ./integration

FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS tests
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
