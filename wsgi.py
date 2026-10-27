"""WSGI entrypoint for PythonAnywhere or any HTTPS WSGI host."""
from briefbot.wsgi import create_application

application = create_application()
