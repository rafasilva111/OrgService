"""Shared, app-agnostic building blocks.

This package is intentionally NOT a Django app registered in INSTALLED_APPS:
it contains only abstract models and pure helpers (no concrete tables),
so it cannot create circular dependencies between domain apps.
"""
