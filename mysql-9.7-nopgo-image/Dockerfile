# MySQL 9.7.0 "no-PGO" build from Percona's custom-build tarball.
# The Percona Testing tarball at
#   downloads.percona.com/.../TESTING/issue-CUSTOM111/
#     mysql-9.7.0-linux-x86_64.tar.gz
# is an Oracle-layout tarball (bin/ share/ lib/ …) compiled without
# profile-guided optimisation — used to measure the PGO uplift vs. the
# stock mysql:9.7.0-lts image built from the shipped RPMs.
#
# Entrypoint + gosu are copied from mysql:lts so the resulting image
# accepts the same MYSQL_ROOT_PASSWORD / MYSQL_DATABASE / MYSQL_USER /
# MYSQL_PASSWORD env vars as every other image in this harness.

FROM oraclelinux:9-slim

RUN set -eux; \
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

# Unpack the tarball to /usr/local/mysql. The tarball's top-level dir is
# mysql-9.7.0-linux-x86_64/; --strip-components=1 flattens it.
COPY mysql-9.7.0-linux-x86_64.tar.gz /tmp/mysql.tar.gz
RUN set -eux; \
    mkdir -p /usr/local/mysql; \
    tar -xzf /tmp/mysql.tar.gz -C /usr/local/mysql --strip-components=1; \
    rm -f /tmp/mysql.tar.gz; \
    # Symlink binaries onto PATH so `mysql`, `mysqld`, `mysqladmin`, … are
    # resolvable the same way Oracle's RPMs arrange them.
    for b in /usr/local/mysql/bin/*; do \
        ln -sf "$b" "/usr/sbin/$(basename "$b")"; \
    done; \
    # The entrypoint expects these dirs on the runtime fs.
    mkdir -p /var/run/mysqld /var/lib/mysql /var/lib/mysql-files; \
    chown -R mysql:mysql /var/run/mysqld /var/lib/mysql /var/lib/mysql-files /usr/local/mysql; \
    chmod 1777 /var/run/mysqld

# Reuse the mysql:lts entrypoint + gosu binary for env-var compatibility.
COPY entrypoint/docker-entrypoint.sh /usr/local/bin/
COPY entrypoint/gosu /usr/local/bin/gosu
RUN chmod +x /usr/local/bin/gosu

ENV PATH=/usr/local/mysql/bin:$PATH
VOLUME /var/lib/mysql
ENTRYPOINT ["docker-entrypoint.sh"]
EXPOSE 3306 33060 33061
CMD ["mysqld"]
