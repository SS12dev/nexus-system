# The workbench's only way out: an HTTP(S) proxy that lets through the hosts in allowlist and
# nothing else. Compose gives it two networks: workbench (internal) and egress (internet).
FROM alpine:3.22
RUN apk add --no-cache tinyproxy
COPY tinyproxy.conf allowlist /etc/tinyproxy/
USER tinyproxy
EXPOSE 8888
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
