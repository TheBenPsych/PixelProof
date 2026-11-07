import sys

from .cli import main

if __name__ == "__main__":   # needed so the picture helpers (prep.py) don't start a second app on Windows
    sys.exit(main())
