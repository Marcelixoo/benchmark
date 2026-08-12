# Only S1's two nodes need this — repository-s3 isn't bundled in the official
# opensearchproject/opensearch image, and remote-backed storage needs it on every node
# that carries remote_store node attributes. The entrypoint wrapper seeds the MinIO
# access/secret key into the OpenSearch keystore (S3 client credentials cannot be set
# as a plain opensearch.yml/env setting) before handing off to the real entrypoint.
#
# The plugin zip is fetched on the host (scripts/opensearch or manually, see README)
# rather than inside `opensearch-plugin install <name>` during the build: this
# network's outbound TLS is intercepted in a way that the container's JDK truststore
# doesn't trust (host curl succeeds, container curl/opensearch-plugin fail cert
# validation), so an in-build fetch fails. Installing from a local file sidesteps the
# broken in-container fetch without disabling certificate verification anywhere.
ARG OPENSEARCH_VERSION=3.7.0
FROM opensearchproject/opensearch:${OPENSEARCH_VERSION}
ARG OPENSEARCH_VERSION
COPY repository-s3-${OPENSEARCH_VERSION}.zip /tmp/repository-s3.zip
RUN /usr/share/opensearch/bin/opensearch-plugin install --batch file:///tmp/repository-s3.zip
USER root
COPY os-entrypoint.sh /usr/share/opensearch/os-entrypoint.sh
RUN chmod +x /usr/share/opensearch/os-entrypoint.sh
USER opensearch
ENTRYPOINT ["/usr/share/opensearch/os-entrypoint.sh"]
CMD ["opensearch"]
