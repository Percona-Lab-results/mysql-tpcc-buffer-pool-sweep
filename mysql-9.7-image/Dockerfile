FROM oraclelinux:9-slim

RUN set -eux; \
    # runtime deps for the mysql-community-server RPMs
    microdnf install -y \
        findutils \
        ncurses \
        openssl \
        shadow-utils \
        libaio \
        numactl-libs \
        tzdata \
        which \
        xz \
        perl \
        libtirpc \
        net-tools \
        procps-ng \
        systemd-libs; \
    microdnf clean all

RUN groupadd -g 27 -o mysql && \
    useradd -M -N -g mysql -o -r -d /var/lib/mysql -s /bin/false -c "MySQL Server" -u 27 mysql

# Install the locally-downloaded MySQL 9.7.0 RPMs in one rpm invocation so
# deps between them are resolved in a single transaction. microdnf in the
# oraclelinux:9-slim image doesn't accept local rpm paths directly.
COPY rpms/ /tmp/rpms/
RUN set -eux; \
    # --nodeps: server RPM requires `systemd` (the unit); we only need the
    # shared libraries, which are already pulled in by systemd-libs.
    # --noscripts: skip systemd service enablement that fails in-build.
    rpm -ivh --nodeps --noscripts \
        /tmp/rpms/mysql-community-common-9.7.0-1.el9.x86_64.rpm \
        /tmp/rpms/mysql-community-client-plugins-9.7.0-1.el9.x86_64.rpm \
        /tmp/rpms/mysql-community-libs-9.7.0-1.el9.x86_64.rpm \
        /tmp/rpms/mysql-community-icu-data-files-9.7.0-1.el9.x86_64.rpm \
        /tmp/rpms/mysql-community-client-9.7.0-1.el9.x86_64.rpm \
        /tmp/rpms/mysql-community-server-9.7.0-1.el9.x86_64.rpm; \
    rm -rf /tmp/rpms; \
    # Writable /var/run/mysqld for the non-root mysql user
    mkdir -p /var/run/mysqld /var/lib/mysql /var/lib/mysql-files; \
    chown -R mysql:mysql /var/run/mysqld /var/lib/mysql /var/lib/mysql-files; \
    chmod 1777 /var/run/mysqld

VOLUME /var/lib/mysql

COPY entrypoint/docker-entrypoint.sh /usr/local/bin/
# gosu is statically linked; copy it over so the entrypoint's
# `exec gosu mysql "$@"` step works (image base has no gosu package).
COPY entrypoint/gosu /usr/local/bin/gosu
RUN chmod +x /usr/local/bin/gosu
ENTRYPOINT ["docker-entrypoint.sh"]

EXPOSE 3306 33060 33061
CMD ["mysqld"]
