FROM alpine:3.22
RUN apk add --no-cache git openssh-client ca-certificates
COPY sync.sh /usr/local/bin/sync.sh
RUN chmod +x /usr/local/bin/sync.sh
ENTRYPOINT ["/usr/local/bin/sync.sh"]
