# Vercel serverless entry point for the Flask app.
# Vercel's @vercel/python builder imports `app` from this module and runs it
# as a WSGI application.
import os
import sys

# Make the project root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app as application  # noqa: E402

# Vercel's Python runtime expects a WSGI callable named `app`.
app = application
