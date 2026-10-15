# average() crashes on an empty list

## Component

sandbox/demo-app/calc.py

## Steps to reproduce

1. Import the calculator and call `average([])`.
2. Observe the exception.

## Expected

SPEC.md says `average([])` returns `0.0`.

## Actual

`ZeroDivisionError: division by zero`, raised from `average()`.
