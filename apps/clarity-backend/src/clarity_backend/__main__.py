"""Allow ``python -m clarity_backend``.

On Windows with Smart App Control the unsigned console-script launcher
(``clarity-backend.exe``) is blocked; running the module through the signed
``python.exe`` avoids it.
"""

from clarity_backend.cli import main

if __name__ == "__main__":
    main()
