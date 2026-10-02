"""Which slices of djinni.co/salaries/ to collect.

Categories and experience levels are discovered from the page every run, so new
Djinni categories are picked up automatically. The deeper levels are fixed here.

Slice tree (each level only expands parents that pass `slices.expandable`):

  L1  category (+ all) x exp (+ any)                          always, ~880 pages
  L2  L1 x english_level in ENGLISH_LEVELS
  L3  L2 x region in REGIONS                                  (region "all" = L2 itself)
  L4  (L2 + L3) x work_format in WORK_FORMATS                 (format "total" = the parent)

English levels on Djinni are exclusive (they sum to the "any" total), so
"upper and above" is the four levels listed. English "any" is covered by L1 only.
Upper bound without pruning: 882 x 4 x 2 x 3 = ~21k; with MIN_CANDIDATES=10 ~9k/day.
"""

BASE_URL = 'https://djinni.co/salaries/'

ENGLISH_LEVELS = ('upper', 'fluent', 'proficient', 'native')
REGIONS = ('UKR',)
WORK_FORMATS = ('office', 'full_remote')

# a slice is split further only if it is big enough to be worth it
MIN_CANDIDATES = 10
MIN_JOBS = 10

# Histogram + 12-month series are stored for L1 only (12 rows/page each would
# otherwise be ~200k rows/day); the headline snapshot is stored for every slice.
DETAIL_LEVELS = (1,)

# politeness
WORKERS = 5
DELAY_S = 0.5          # per worker, between requests (~7 req/s total)
TIMEOUT_S = 30
MAX_RETRIES = 4
BREAKER_CONSECUTIVE = 20   # this many 403/429 in a row -> stop the run, keep what we have

# a run fails (nothing written) if more than this share of pages error out
MAX_ERROR_RATE = 0.02
