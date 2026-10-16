# run_formatter() breaks on file names with spaces

## Steps to reproduce

1. Create a file called `my notes.py`.
2. Call `run_formatter("my notes.py")`.

## Actual

The formatter reports `my: No such file` and `notes.py: No such file`. The function builds the
shell command with an f-string and runs it with `subprocess.run(cmd, shell=True)`, so the name
is split on the space.

## Expected

The file is formatted. Passing an argument list without `shell=True`, or quoting with
`shlex.quote`, would fix it.
