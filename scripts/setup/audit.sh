#!/usr/bin/env bash
# Comprehensive audit of the EC2 environment for premium_diff_bot.
# Read-only. Safe to run any time.
set -uo pipefail

DB_SECRET=/home/ubuntu/premium_diff_bot/.db_secret
ENV_FILE=/home/ubuntu/premium_diff_bot/.env

hr() { printf '\n===== %s =====\n' "$1"; }

hr "OS / KERNEL"
. /etc/os-release && echo "$PRETTY_NAME (kernel $(uname -r))"
echo "Memory:"; free -h | sed 's/^/  /'
echo "Disk /:"; df -hT / | sed 's/^/  /'
echo "Time: $(date -u) | TZ=$(timedatectl show -p Timezone --value)"

hr "SERVICES"
for svc in redis-server postgresql nginx ufw fail2ban chrony unattended-upgrades; do
  printf '  %-22s %s (enabled=%s)\n' "$svc" "$(systemctl is-active $svc 2>/dev/null)" "$(systemctl is-enabled $svc 2>/dev/null)"
done

hr "PORTS LISTENING (loopback + public)"
ss -ltn | sed 's/^/  /'

hr "REDIS — config + persistence + memory"
sudo -u redis redis-cli -s /var/run/redis/redis.sock CONFIG GET port
sudo -u redis redis-cli -s /var/run/redis/redis.sock CONFIG GET unixsocket
sudo -u redis redis-cli -s /var/run/redis/redis.sock CONFIG GET maxmemory
sudo -u redis redis-cli -s /var/run/redis/redis.sock CONFIG GET maxmemory-policy
sudo -u redis redis-cli -s /var/run/redis/redis.sock CONFIG GET appendonly
sudo -u redis redis-cli -s /var/run/redis/redis.sock CONFIG GET save
sudo -u redis redis-cli -s /var/run/redis/redis.sock INFO memory | grep -E 'used_memory_human|used_memory_peak_human|maxmemory_human|maxmemory_policy'
sudo -u redis redis-cli -s /var/run/redis/redis.sock INFO persistence | grep -E 'aof_enabled|rdb_last_save_time|loading'
echo "Socket perms: $(ls -l /var/run/redis/redis.sock)"

hr "POSTGRES — settings"
sudo -u postgres psql -c '\du'
sudo -u postgres psql -c '\l+ premium_diff_bot'
sudo -u postgres psql -c "SELECT name, setting FROM pg_settings WHERE name IN ('listen_addresses','password_encryption','ssl','TimeZone','log_timezone','server_encoding','client_encoding','data_directory','max_connections','shared_buffers','work_mem','wal_level') ORDER BY name;"

hr "POSTGRES — pg_hba"
sudo grep -Ev '^[[:space:]]*(#|$)' /etc/postgresql/16/main/pg_hba.conf | sed 's/^/  /'

hr "POSTGRES — trader connectivity + privileges"
. "$DB_SECRET"
PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -U trader -d premium_diff_bot -c "SELECT current_user, current_database(), current_schema(), version();"
PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -U trader -d premium_diff_bot -c "SELECT has_schema_privilege('trader','public','CREATE') AS create_in_public, has_schema_privilege('trader','public','USAGE') AS use_public, has_database_privilege('trader','premium_diff_bot','CREATE') AS create_in_db, has_database_privilege('trader','premium_diff_bot','CONNECT') AS connect_db;"

hr "POSTGRES — trader DDL test (create/insert/select/drop)"
PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -U trader -d premium_diff_bot <<'SQL'
CREATE TABLE _smoke (id INT);
INSERT INTO _smoke VALUES (1),(2);
SELECT count(*) AS rows FROM _smoke;
DROP TABLE _smoke;
SQL

hr ".env keys (values masked)"
if [ -f "$ENV_FILE" ]; then
  while IFS='=' read -r k v; do
    [ -z "$k" ] || [ "${k:0:1}" = "#" ] && continue
    if [ ${#v} -le 8 ]; then masked='***'; else masked="${v:0:4}...${v: -4}"; fi
    printf '  %-25s %s\n' "$k" "$masked"
  done < "$ENV_FILE"
else
  echo "  .env missing!"
fi
echo "  perms: $(ls -l $ENV_FILE)"

hr "FIREWALL"
sudo ufw status verbose 2>&1 | sed 's/^/  /'

hr "TIME SYNC"
chronyc tracking | head -5 | sed 's/^/  /'

hr "PYTHON VENV"
/home/ubuntu/premium_diff_bot/.venv/bin/python --version
/home/ubuntu/premium_diff_bot/.venv/bin/pip list --format=columns 2>/dev/null | grep -iE '^(upstox-python-sdk|fastapi|uvicorn|redis|asyncpg|playwright|cryptography|pyotp|apscheduler|sqlalchemy|alembic|pydantic|httpx|loguru)\s' | sort | sed 's/^/  /'

hr "DONE"
