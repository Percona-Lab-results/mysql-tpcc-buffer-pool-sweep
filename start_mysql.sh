#!/bin/bash
set -euo pipefail

docker run -d \
  --name mysql \
  --restart unless-stopped \
  -e MYSQL_ROOT_PASSWORD=rootpassword \
  -e MYSQL_DATABASE=mydb \
  -e MYSQL_USER=myuser \
  -e MYSQL_PASSWORD=mypassword \
  -v /data/mysql-8.4:/var/lib/mysql \
  -v "$(dirname "$0")/mysql.cnf":/etc/mysql/conf.d/mysql.cnf:ro \
  --network host \
  mysql:8.4.8

# Wait for MySQL to accept connections, then convert users to mysql_native_password
# so clients without TLS (e.g. HammerDB 5.0 defaults) can authenticate.
echo "Waiting for MySQL to become ready..."
for i in {1..60}; do
    if docker exec mysql mysqladmin ping -uroot -prootpassword --silent 2>/dev/null; then
        break
    fi
    sleep 2
done

docker exec mysql mysql -uroot -prootpassword <<'SQL'
ALTER USER 'root'@'%'         IDENTIFIED WITH mysql_native_password BY 'rootpassword';
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'rootpassword';
ALTER USER 'myuser'@'%'       IDENTIFIED WITH mysql_native_password BY 'mypassword';
FLUSH PRIVILEGES;
SQL

echo "MySQL ready — users switched to mysql_native_password."
