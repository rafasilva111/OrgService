"""Shared, app-agnostic building blocks.

This package *is* a Django app registered in ``INSTALLED_APPS``
(``config/settings/base.py``). It defines only abstract models
(``TimeStampedModel``, ``ActiveModel``, ``SoftDeleteModel``) and pure helpers,
so it creates no tables of its own.
"""
