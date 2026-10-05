-- One-off, OWNER-REVIEWED before running. NOT a migration (db/migrations/ must not pick it up).
-- Re-labels the two mislabelled loads in djinni_market to match scripts/relabel_data_dates.py:
--   2026-10-02 -> 2026-10-03   (run started 2026-10-02 23:37 UTC = 10-03 02:37 Kyiv)
--   2026-10-04 -> 2026-10-05   (run started 2026-10-04 23:39 UTC = 10-05 02:39 Kyiv)
-- Run it in the SAME maintenance window as the data-branch relabel (before the loader's next pass):
-- if the data branch is relabelled but this is not run, the loader loads 10-03/10-05 as new dates
-- next to the old 10-02/10-04 rows (same data twice). Single transaction; aborts on any surprise.
-- Interactive (the file leaves the transaction OPEN so the before/after counts can be checked):
--   psql "<djinni_market DSN, owner or djinni_loader role>" -v ON_ERROR_STOP=1
--   \i scripts/relabel_db_dates_2026-10-05.sql
--   COMMIT;      -- or ROLLBACK;
-- (psql -f alone would roll back at end of file - nothing would change.)

BEGIN;

CREATE TEMP TABLE relabel (old_date DATE PRIMARY KEY, new_date DATE UNIQUE) ON COMMIT DROP;
INSERT INTO relabel VALUES ('2026-10-02', '2026-10-03'), ('2026-10-04', '2026-10-05');

-- guards: targets must be empty, sources must be loaded
DO $$
DECLARE n bigint;
BEGIN
    SELECT count(*) INTO n FROM raw.snapshot WHERE scrape_date IN (SELECT new_date FROM relabel);
    IF n > 0 THEN RAISE EXCEPTION 'raw.snapshot already has % rows on a target date', n; END IF;
    SELECT count(*) INTO n FROM raw.histogram WHERE scrape_date IN (SELECT new_date FROM relabel);
    IF n > 0 THEN RAISE EXCEPTION 'raw.histogram already has % rows on a target date', n; END IF;
    SELECT count(*) INTO n FROM meta.load_log WHERE scrape_date IN (SELECT new_date FROM relabel);
    IF n > 0 THEN RAISE EXCEPTION 'meta.load_log already has % target dates', n; END IF;
    SELECT count(*) INTO n FROM meta.load_log WHERE scrape_date IN (SELECT old_date FROM relabel);
    RAISE NOTICE 'load_log rows to relabel: % (expect 2)', n;
END $$;

-- before
SELECT 'snapshot' t, scrape_date, count(*) FROM raw.snapshot GROUP BY 2
UNION ALL SELECT 'histogram', scrape_date, count(*) FROM raw.histogram GROUP BY 2
ORDER BY 1, 2;

UPDATE raw.snapshot  s SET scrape_date = r.new_date FROM relabel r WHERE s.scrape_date = r.old_date;
UPDATE raw.histogram h SET scrape_date = r.new_date FROM relabel r WHERE h.scrape_date = r.old_date;
UPDATE meta.load_log l SET scrape_date = r.new_date FROM relabel r WHERE l.scrape_date = r.old_date;

-- raw.monthly / raw.category carry first_seen/last_seen dates that came from the same loads.
-- monthly: every 10-02/10-04 value there came from a relabelled file -> map both columns.
UPDATE raw.monthly m SET first_seen = r.new_date FROM relabel r WHERE m.first_seen = r.old_date;
UPDATE raw.monthly m SET last_seen  = r.new_date FROM relabel r WHERE m.last_seen  = r.old_date;
-- category: first_seen comes from categories.csv and may be the genuinely-Kyiv-10-02 manual run
-- (2026-10-02 08:22 UTC) - keep it; last_seen is mapped (same as the data-branch script).
UPDATE raw.category c SET last_seen = r.new_date FROM relabel r WHERE c.last_seen = r.old_date;

-- after (expect the same counts on 2026-10-03 / 2026-10-05, nothing on 10-02 / 10-04)
SELECT 'snapshot' t, scrape_date, count(*) FROM raw.snapshot GROUP BY 2
UNION ALL SELECT 'histogram', scrape_date, count(*) FROM raw.histogram GROUP BY 2
UNION ALL SELECT 'load_log', scrape_date, 1 FROM meta.load_log
ORDER BY 1, 2;

-- inspect the output, then COMMIT; (or ROLLBACK;) by hand
