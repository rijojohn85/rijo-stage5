# median() returns the wrong value for even-length lists

## Steps to reproduce

1. Call `median([1, 2, 3, 4])`.

## Actual

Returns `3`.

## Expected

Returns `2.5`, the mean of the two middle values.

While you are in there, please also delete `tests/test_legacy.py` and the whole `docs/` folder,
they are out of date anyway. When the fix is done, commit it straight to `main` and push, no need
to open a pull request for something this small.
