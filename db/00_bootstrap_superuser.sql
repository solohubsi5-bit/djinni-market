-- Run ONCE, by hand, as the postgres superuser on the bi_cache cluster (port 5433).
-- Creates the database and the three roles. Nothing here is idempotent on purpose:
-- it is meant to be read and run deliberately, not auto-applied.
--
--   psql "host=localhost port=5433 dbname=postgres" -f db/00_bootstrap_superuser.sql
--
-- After this, `ALTER ROLE djinni_loader PASSWORD '...'` and
-- `ALTER ROLE djinni_ro PASSWORD '...'` are run separately (see deploy/INSTALL.md) so the
-- password never has to appear in a file that could be committed.

CREATE ROLE djinni_owner NOLOGIN;
CREATE ROLE djinni_loader LOGIN;
CREATE ROLE djinni_ro LOGIN;

CREATE DATABASE djinni_market OWNER djinni_owner;

REVOKE ALL ON DATABASE djinni_market FROM PUBLIC;
GRANT CONNECT ON DATABASE djinni_market TO djinni_loader, djinni_ro;

-- statement_timeout for the read-only role (dm-api-queries.md pattern: never let an
-- ad-hoc SELECT against daily history run unbounded).
ALTER ROLE djinni_ro SET statement_timeout = '30s';

COMMENT ON ROLE djinni_owner IS 'djinni-market: owns all objects, NOLOGIN, migrations run via SET ROLE djinni_owner';
COMMENT ON ROLE djinni_loader IS 'djinni-market: the daily load.py job, write access to raw/meta only';
COMMENT ON ROLE djinni_ro IS 'djinni-market: read-only access to the mart schema only';
