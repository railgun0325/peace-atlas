"""The v2 workbench is the default app. Legacy routes are archived, not mounted."""
from .workbench.server import create_app

app = create_app()
