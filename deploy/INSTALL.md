# djinni-market loader — one-time server install

Target: the box's `bi_cache` Postgres cluster, localhost-only, port 5433. No network/firewall
change, no Power BI, no gateway — those parts of `DJINNI_MARKET_PLAN.md` are explicitly out of
scope per the 2026-10-05 owner decision. This installs only the loader job.

Run every numbered step as root (or with sudo) on the server, in order.

## 1. System user + directories

```sh
useradd --system --home-dir /opt/djinni-market --shell /usr/sbin/nologin djinni
install -d -o djinni -g djinni -m 750 /opt/djinni-market
install -d -o djinni -g djinni -m 750 /var/lib/djinni-market
install -d -o root -g root -m 755 /etc/djinni
```

## 2. Code

```sh
sudo -u djinni git clone --branch main --single-branch \
    https://github.com/solohubsi5-bit/djinni-market.git /opt/djinni-market/src
cd /opt/djinni-market/src
sudo -u djinni python3 -m venv /opt/djinni-market/.venv
sudo -u djinni /opt/djinni-market/.venv/bin/pip install -r requirements.txt
```

(Adjust `WorkingDirectory=`/`ExecStart=` in `deploy/djinni-load.service` if the checkout
path differs from `/opt/djinni-market` — the unit as committed expects the package
importable from `/opt/djinni-market`, i.e. either clone straight into
`/opt/djinni-market` or symlink `/opt/djinni-market/djinni_market ->
/opt/djinni-market/src/djinni_market`.)

## 3. Database + roles (once, as the postgres superuser)

```sh
psql "host=localhost port=5433 dbname=postgres" -f /opt/djinni-market/src/db/00_bootstrap_superuser.sql
```

Set passwords without ever echoing them to a terminal or a shell history file:

```sh
LOADER_PW=$(openssl rand -base64 24)
RO_PW=$(openssl rand -base64 24)
psql "host=localhost port=5433 dbname=djinni_market" \
    -v loaderpw="'$LOADER_PW'" -v ropw="'$RO_PW'" \
    -c "ALTER ROLE djinni_loader PASSWORD :loaderpw;" \
    -c "ALTER ROLE djinni_ro PASSWORD :ropw;"
```

Write the loader's env file (root-owned, mode 600 — `install -m 600` so it's never
world-readable even for the instant between creation and chmod):

```sh
install -m 600 -o root -g root /dev/null /etc/djinni/load.env
printf 'DJINNI_PG_DSN=host=localhost port=5433 dbname=djinni_market user=djinni_loader password=%s\n' \
    "$LOADER_PW" > /etc/djinni/load.env
unset LOADER_PW RO_PW
```

Keep `$RO_PW` for whoever sets up read access later (not part of this loader install —
there is no `djinni_ro` consumer yet per the 2026-10-05 scope cut).

## 4. Apply migrations (as superuser, owns-everything objects via `SET ROLE djinni_owner`)

```sh
DJINNI_PG_DSN="host=localhost port=5433 dbname=djinni_market" \
    /opt/djinni-market/.venv/bin/python /opt/djinni-market/src/db/apply_migrations.py
```

Expect: `applied: 0001_schema.sql, 0002_views.sql`.

## 5. Install the unit + timer

```sh
cp /opt/djinni-market/src/deploy/djinni-load.service /opt/djinni-market/src/deploy/djinni-load.timer \
    /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now djinni-load.timer
```

## 6. First manual run (loads whatever the `data` branch currently holds, plus the one-time
   legacy/mapping reference tables)

```sh
sudo -u djinni /opt/djinni-market/.venv/bin/python -m djinni_market.load \
    --data-dir /var/lib/djinni-market/data-clone \
    --legacy-parquet /opt/djinni-market/src/legacy/salaries.parquet \
    --mapping-csv /opt/djinni-market/src/mapping/category_role_mapping.csv
```

(Run it via `systemctl start djinni-load.service` instead if you'd rather see it go through
the unit — same env file either way. The `--legacy-parquet`/`--mapping-csv` flags only need
to be passed once, or again after the mapping CSV changes upstream; the daily timer omits them.)

## 7. Verify

```sh
psql "host=localhost port=5433 dbname=djinni_market" -c \
    "SELECT scrape_date, status, rows_by_table, loaded_at FROM meta.load_log ORDER BY scrape_date;"
psql "host=localhost port=5433 dbname=djinni_market" -c \
    "SELECT scrape_date, count(*) FROM raw.snapshot GROUP BY scrape_date ORDER BY 1;"
psql "host=localhost port=5433 dbname=djinni_market" -c \
    "SELECT count(*) FROM raw.legacy_salaries;"
```

Rows per snapshot date should roughly match the `ok` column in the `data` branch's
`runs.csv` for that date (±expandable-slice variance).

## Rollback

```sh
systemctl disable --now djinni-load.timer djinni-load.service
rm /etc/systemd/system/djinni-load.service /etc/systemd/system/djinni-load.timer
systemctl daemon-reload
psql "host=localhost port=5433 dbname=postgres" -c "DROP DATABASE djinni_market;"
psql "host=localhost port=5433 dbname=postgres" -c \
    "DROP ROLE djinni_loader; DROP ROLE djinni_ro; DROP ROLE djinni_owner;"
userdel djinni
rm -rf /opt/djinni-market /var/lib/djinni-market /etc/djinni
```
