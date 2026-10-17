# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
FROM golang:1.27.2-alpine3.23@sha256:9e45f0eb4a63ed37ad6e604950407558b7c738e34e015ae77a5d4ad369cde079 AS generate
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
RUN apk add --no-cache bash && \
    go install golang.org/x/tools/cmd/goimports@v0.52.0 && \
    go install sigs.k8s.io/controller-tools/cmd/controller-gen@v0.20.1 && \
    go install github.com/crossplane/crossplane-tools/cmd/angryjet@v0.0.0-20260719180100-659f1dc036c5 && \
    go install github.com/google/yamlfmt/cmd/yamlfmt@v0.21.0 && go clean -cache
COPY . .
RUN ./generate.sh

FROM generate AS build
RUN CGO_ENABLED=0 go test -vet=off -buildvcs=false -p=2 ./cmd/provider && \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -buildvcs=false -p=2 -o /out/provider ./cmd/provider && \
    CGO_ENABLED=0 go test -c -vet=off -trimpath -ldflags="-s -w" -buildvcs=false -p=2 -tags=system -o /out/integration ./integration && \
    go clean -cache

FROM scratch AS generated
COPY --from=generate /src/generated /generated

FROM alpine:3.23@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS runtime
LABEL org.opencontainers.image.source="https://github.com/soupglasses/ops"
RUN apk add --no-cache ca-certificates
COPY --from=build /out/provider /provider
COPY LICENSE /licenses/provider-ldap-MIT
USER 65532:65532
ENTRYPOINT ["/provider"]

FROM alpine:3.23@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS tests
LABEL org.opencontainers.image.source="https://github.com/soupglasses/ops"
RUN apk add --no-cache openldap openldap-back-mdb openldap-clients python3 py3-pip && \
    apk add --no-cache --virtual .build python3-dev openldap-dev gcc musl-dev && \
    pip install --no-cache-dir --break-system-packages 'canaille[ldap,oidc]==0.3.6' && apk del .build
COPY --from=build /out/integration /tests/integration
COPY tests/verify_canaille.py /work/tests/verify_canaille.py
COPY canaille-schema/oauth2-openldap.ldif /fixtures/oauth2-openldap.ldif
ENV LDAP_MODULE_PATH=/usr/lib/openldap CANAILLE_SCHEMA_LDIF=/fixtures/oauth2-openldap.ldif CANAILLE_TEST_PYTHON=/usr/bin/python3
WORKDIR /work/integration
ENTRYPOINT ["/tests/integration"]
