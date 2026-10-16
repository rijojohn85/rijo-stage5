# `mytool tools list` shows tools that were already deleted

## Steps to reproduce

1. Run `mytool tools add lint` and then `mytool tools remove lint`.
2. Run `mytool tools list`.

## Actual

`lint` is still listed, with permissions shown as `rw-`.

## Expected

Deleted tools are not listed. The registry in `app/registry.py` seems to keep removed entries
and only marks them inactive, but `list_tools()` does not filter on that flag.
