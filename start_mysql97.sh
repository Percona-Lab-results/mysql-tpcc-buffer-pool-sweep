#!/bin/bash
set -euo pipefail

# Ensure data dir has correct ownership (MySQL 9.x Oracle image uses UID 27)
mkdir -p /data/mysql-9.7
chown -R 27:27 /data/mysql-9.7

docker run -d \
  --name mysql97 \
  --restart unless-stopped \
  -e MYSQL_ROOT_PASSWORD=rootpassword \
  -e MYSQL_ROOT_HOST=% \
  -e MYSQL_DATABASE=mydb \
  -e MYSQL_USER=myuser \
  -e MYSQL_PASSWORD=mypassword \
  -v /data/mysql-9.7:/var/lib/mysql \
  -v "$(dirname "$0")/mysql97.cnf":/etc/my.cnf:ro \
  --network host \
  mysql:9.7.0-lts

# Wait for MySQL to accept connections. MySQL 9.x removed
# mysql_native_password — clients (HammerDB included) must authenticate over
# TLS using caching_sha2_password. Use the auto-generated /var/lib/mysql/ca.pem
# for one-way verification from the host.
echo "Waiting for MySQL to become ready..."
for i in {1..60}; do
    if docker exec mysql97 mysqladmin ping -uroot -prootpassword --silent 2>/dev/null; then
        break
    fi
    sleep 2
done

echo "MySQL 9.x ready (caching_sha2_password over TLS only)."
