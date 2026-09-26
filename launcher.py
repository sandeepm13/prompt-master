"""Entry point used by build-exe.bat (PyInstaller needs a plain script, not `-m`)."""
import sys

from master_prompt.__main__ import main

sys.exit(main())
