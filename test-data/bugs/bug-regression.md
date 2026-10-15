# average() stopped returning 0.0 for an empty list

## Component

sandbox/demo-app/calc.py

## What worked before

In version 2.3, `average([])` returned `0.0`. That behaviour has been in place since the
calculator was written, and SPEC.md still requires it.

## What happens now

After the upgrade to 2.4, `average([])` raises `ZeroDivisionError: division by zero`.

## Request

Please restore the previous behaviour.
