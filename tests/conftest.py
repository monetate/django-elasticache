"""
Shared pytest configuration for the django-elasticache test suite.

Configure Django settings at import time, before any test module is collected, so the backends import cleanly.
This replaces nose's ``setup_package`` package-level fixture.
"""
from django.conf import global_settings, settings

if not settings.configured:
    settings.configure()
# Tests patch ``django.conf.settings`` with the ``global_settings`` module, so its ``configured`` flag must read True.
global_settings.configured = True
