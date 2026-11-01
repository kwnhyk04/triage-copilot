# Production's egress proxy (infra/squid/squid.conf, mounted at /etc/squid/squid.conf): Ubuntu's
# squid from noble-security, and socat for the alert mail relay (smtp-relay in the prod compose
# file), nothing else. Ubuntu drops superseded builds from its archive, so the newest build is
# installed, not an exact version, and the build fails on a squid older than SQUID_MIN. Ubuntu's
# own squid.conf is removed, so the image never runs without TC's.
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
ARG SQUID_MIN=6.14-0ubuntu0.24.04.4
RUN apt-get update \
  && apt-get install -y --no-install-recommends -t noble-security squid socat \
  && rm -rf /var/lib/apt/lists/* /etc/squid/squid.conf \
  && version="$(dpkg-query -W -f='${Version}' squid)" \
  && echo "squid ${version} (minimum ${SQUID_MIN})" \
  && dpkg --compare-versions "${version}" ge "${SQUID_MIN}"
USER proxy
ENTRYPOINT ["squid", "-N", "-f", "/etc/squid/squid.conf"]
