"""Bot Insights report engine.

Pure-data prepare functions live in `contexts/`, presentation lives in
`templates/`. Charts and formatters are exposed to Jinja as globals.
"""

from __future__ import annotations

__all__ = ["render"]
