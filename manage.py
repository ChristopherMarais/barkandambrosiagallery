#!/usr/bin/env python
import os
import sys
from pathlib import Path

def main():
    # Force the current directory into Python's path so it finds 'beetlesgallery'
    sys.path.append(str(Path(__file__).resolve().parent))

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'beetlesgallery.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()
