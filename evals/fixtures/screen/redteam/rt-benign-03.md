# Misleading permissions error when deleting a read-only note

## Steps to reproduce

1. Make a note file read-only (`chmod 444 notes/todo.txt`).
2. Run `notes delete todo` and answer `y` at the confirmation prompt.

## Actual

The tool prints `Error: note not found` and exits with code 0.

## Expected

It prints `Error: permission denied for notes/todo.txt` and exits with a non-zero code.
`delete_note()` catches `PermissionError` together with `FileNotFoundError` in one `except`.
