#!/bin/bash
set -e

if [ ! -f /usr/share/opensearch/config/opensearch.keystore ]; then
  /usr/share/opensearch/bin/opensearch-keystore create
fi
echo "$MINIO_ROOT_USER" | /usr/share/opensearch/bin/opensearch-keystore add --stdin --force s3.client.default.access_key
echo "$MINIO_ROOT_PASSWORD" | /usr/share/opensearch/bin/opensearch-keystore add --stdin --force s3.client.default.secret_key

exec /usr/share/opensearch/opensearch-docker-entrypoint.sh "$@"
