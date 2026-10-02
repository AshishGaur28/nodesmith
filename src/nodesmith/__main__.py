"""``python -m nodesmith`` runs the command line."""

import sys

from .cli.main import main

sys.exit(main())
