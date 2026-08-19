"""Sanity test for Rivian integration setup."""

from custom_components.rivian.const import DOMAIN


async def test_domain_const():
    """Verify integration domain constant is correct."""
    assert DOMAIN == "rivian"
