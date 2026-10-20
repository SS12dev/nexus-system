# The backup service: nightly pg_dump of Jarvis's databases plus a copy of the MinIO files.
# pg_dump must be at least as new as the server, so this tracks the postgres image's major (17).
FROM postgres:17-alpine
COPY backup.sh /usr/local/bin/jarvis-backup
RUN chmod +x /usr/local/bin/jarvis-backup
ENTRYPOINT []
CMD ["jarvis-backup", "loop"]
