"""Entry-point shim.

The application package lives in diytracker/; this module keeps the
long-standing entry point `app:app` working unchanged
(deploy/diytracker.service: ExecStart ... gunicorn -c gunicorn_conf.py app:app).
"""

from diytracker.app import app

if __name__ == "__main__":
    app.run(debug=True, port=5001)
