"""Allow ``python -m acoustic_orchestrator``.

On Windows with Smart App Control the unsigned console-script launcher
(``acoustic-orchestrator.exe``) is blocked; running the module through the
signed ``python.exe`` avoids it.
"""

from acoustic_orchestrator.cli import app

if __name__ == "__main__":
    app(prog_name="acoustic-orchestrator")
