# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
FROM golang:1.27.3-alpine3.23@sha256:7fd63dd7c37f5ddaef15bd8fd3f3e4f90f0d50cd9cae255c7cf73887a494d8df AS generate
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
RUN apk add --no-cache bash && \
    go install golang.org/x/tools/cmd/goimports@v0.52.0 && \
    go install sigs.k8s.io/controller-tools/cmd/controller-gen@v0.20.1 && \
    go install github.com/crossplane/crossplane-tools/cmd/angryjet@v0.0.0-20260719180100-659f1dc036c5 && \
    go install github.com/google/yamlfmt/cmd/yamlfmt@v0.21.0
COPY . .
RUN bash generate.sh

FROM generate AS build
RUN go test -buildvcs=false -p=2 . ./cmd/provider && \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -buildvcs=false -p=2 -o /out/provider ./cmd/provider

FROM scratch AS generated
COPY --from=generate /src/generated/crds /generated/crds
COPY --from=generate /src/config/schema.json /generated/schema.json

FROM build AS test-build
RUN CGO_ENABLED=0 go test -c -tags=integration -buildvcs=false -p=2 -o /out/tests .

FROM alpine:3.23@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS tests
RUN apk add --no-cache ca-certificates
COPY --from=test-build /out/tests /tests
ENTRYPOINT ["/tests"]

FROM alpine:3.23@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS runtime
LABEL org.opencontainers.image.source="https://github.com/soupglasses/ops"
RUN apk add --no-cache ca-certificates
COPY --from=build /out/provider /provider
COPY LICENSE /licenses/provider-jellyfin-MIT
COPY --from=generate /go/pkg/mod/github.com/soupglasses/terraform-provider-jellyfin@v0.0.0-20261018003529-65dbe330d086/LICENSE /licenses/terraform-provider-jellyfin-MPL
USER 65532:65532
ENTRYPOINT ["/provider"]
