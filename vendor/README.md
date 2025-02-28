# Vendored dependencies

DISCLAIMER: These dependencies are included here as-is. They are only here because Flet is having trouble loading them.
`wsgiref` is part of the Python standard library yet Flet is having trouble loading it. This is a workaround.

If at all possible this workaround should be removed in the future.

## Libraries

- [wsgiref](https://docs.python.org/3/library/wsgiref.html) - WSGI (Web Server Gateway Interface) server reference implementation.
  - This code was pulled from the Cpython source code [here](https://github.com/python/cpython/tree/main/Lib/wsgiref) 