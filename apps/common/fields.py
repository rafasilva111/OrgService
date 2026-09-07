"""Minimal PostgreSQL ``ltree`` field.

Django does not ship an ``ltree`` field (it was never merged into the contrib
module), so we provide a small, dependency-free one:

  * maps to the native ``ltree`` type (indexable with GiST/GIN);
  * stores paths as ``"a.b.c"`` strings / lists of labels;
  * exposes ltree lookups:
      ``path__descendants="a.b"``   → rows whose path is strictly under "a.b"   (SQL ``<@``)
      ``path__ancestors="a.b.c"``   → rows that are ancestors of "a.b.c"        (SQL ``@>``)
      ``path__nlevel=3``            → rows at exactly depth 3                   (``nlevel()``)

Requires PostgreSQL with the ``ltree`` extension (see migrations:
``CreateExtension("ltree")``).
"""

from django.db import models
from django.db.models import Lookup


class AncestorLookup(Lookup):
    """``path @> value`` — rows that are ancestors of ``value`` (incl. itself)."""

    lookup_name = "ancestors"

    def as_sql(self, compiler, connection):
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return f"{lhs} @> {rhs}", [*lhs_params, *rhs_params]


class DescendantLookup(Lookup):
    """``path <@ value`` — rows whose path is below ``value`` (incl. itself)."""

    lookup_name = "descendants"

    def as_sql(self, compiler, connection):
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return f"{lhs} <@ {rhs}", [*lhs_params, *rhs_params]


class NlevelLookup(Lookup):
    """``nlevel(path) = value`` — rows at a given depth."""

    lookup_name = "nlevel"

    def as_sql(self, compiler, connection):
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return f"nlevel({lhs}) = {rhs}", [*lhs_params, *rhs_params]


class LTreeField(models.Field):
    description = "PostgreSQL ltree materialized path"

    def db_type(self, connection):
        return "ltree"

    def from_db_value(self, value, expression, connection):
        return value  # plain string is enough for the skeleton

    def to_python(self, value):
        if value is None:
            return value
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            return ".".join(value)
        raise ValueError(f"Invalid ltree value: {value!r}")

    def get_prep_value(self, value):
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return ".".join(value)
        return str(value)


LTreeField.register_lookup(AncestorLookup)
LTreeField.register_lookup(DescendantLookup)
LTreeField.register_lookup(NlevelLookup)
