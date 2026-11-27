"""Allow ``python -m output_analyzer``.

On Windows with Smart App Control the unsigned console-script launcher
(``output-analyzer.exe``) is blocked; running the module through the signed
``python.exe`` avoids it.
"""

import sys

from output_analyzer.cli import main

if __name__ == "__main__":
    sys.exit(main())
