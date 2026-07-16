"""Admin tooling logic layer.

Shared by the manage.py CLI wrappers and the Textual TUI
(diytracker.admin.tui). Modules here never print, prompt, or sys.exit:
they raise AdminError on user-facing failures and return plain
dataclasses (never live ORM objects), so both frontends can render
results however they like. Callers provide the Flask app context.
"""
