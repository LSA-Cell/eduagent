# syntax=docker/dockerfile:1
# Official MinIO images are no longer publicly pullable. Build the existing
# server and bucket initializer from pinned upstream release sources instead.
FROM golang:1.24.8-alpine3.22 AS server-build
ARG GOPROXY=https://proxy.golang.org,direct
ADD --checksum=sha256:45521908307306e925c98d629e1c17d78c8b72b6ee242b1bfb1409f7d8ee5841 https://codeload.github.com/minio/minio/tar.gz/9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a /tmp/source.tar.gz
WORKDIR /src
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src \
    && CGO_ENABLED=0 go build -p 1 -trimpath -ldflags="-s -w" -o /out/minio .

FROM golang:1.24.8-alpine3.22 AS client-build
ARG GOPROXY=https://proxy.golang.org,direct
ADD --checksum=sha256:95cd293c7119f16921a6dc515a1fb74a2227f19fd994b9c8b770a154e802ac44 https://codeload.github.com/minio/mc/tar.gz/7394ce0dd2a80935aded936b09fa12cbb3cb8096 /tmp/source.tar.gz
WORKDIR /src
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src \
    && CGO_ENABLED=0 go build -p 1 -trimpath -ldflags="-s -w" -o /out/mc .

FROM alpine:3.22 AS runtime
# curl is required by the existing server healthcheck; sh by createbuckets.
RUN apk add --no-cache ca-certificates curl

FROM runtime AS client
COPY --from=client-build /out/mc /usr/local/bin/mc
COPY --from=client-build /src/LICENSE /licenses/LICENSE
ENTRYPOINT ["mc"]

FROM runtime AS server
COPY --from=server-build /out/minio /usr/local/bin/minio
COPY --from=server-build /src/LICENSE /licenses/LICENSE
ENTRYPOINT ["minio"]
