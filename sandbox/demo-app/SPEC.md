# demo-app — documented requirements

This file is the record of what the sandbox application is required to do. Triage
treats behaviour that violates a requirement here as a `bug`; a request for behaviour
that is not written here is a `feature-as-bug`.

## calc.average(values)

- Returns the arithmetic mean of `values`.
- Returns `0.0` when `values` is empty.

## storage.save_records(path, records)

- Writes `records` to `path` as JSON.
- An existing file at `path` is replaced only after the new content has been written
  successfully. A failed write must never destroy the records already stored there.
