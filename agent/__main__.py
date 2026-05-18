"""Allow running as `python -m agent`."""
import sys
from .main import main
sys.exit(main())
