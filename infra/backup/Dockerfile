# The backup service: nightly pg_dump of Nexus's databases plus a copy of the MinIO files.
# pg_dump must be at least as new as the server, so this tracks the postgres image's major (17).
FROM postgres:17.11-alpine
COPY backup.sh /usr/local/bin/nexus-backup
RUN chmod +x /usr/local/bin/nexus-backup
ENTRYPOINT []
CMD ["nexus-backup", "loop"]
