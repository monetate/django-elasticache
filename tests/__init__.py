"""
Package-level setup for the django-elasticache test suite.

Configure Django settings before tests run.
"""
from django.conf import global_settings, settings


def setup_package():
    if not settings.configured:
        settings.configure()
    global_settings.configured = True
