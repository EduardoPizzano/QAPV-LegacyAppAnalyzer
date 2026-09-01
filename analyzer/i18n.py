"""Translation helper for analyzer/ modules.

analyzer/ is used both by the Flask web app (app.py) and by the standalone
CLI (main.py), which never pushes a Flask application context. flask_babel's
gettext() requires one, so this wraps it and falls back to the original
(Spanish) text whenever no app context is active -- the CLI keeps working
exactly as before, and generated text (e.g. SecurityFlag descriptions) is
baked in whatever language is active in the web session at analysis time.
"""

from flask import has_app_context


def _(text: str) -> str:
    if has_app_context():
        from flask_babel import gettext
        return gettext(text)
    return text
