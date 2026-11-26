# OntoShip role registry: the machine source of the role lists, in the init
# bundle.
#
# Data only, no logic. Consumers parse this file as text
# (tests/test_role_registry.py) and keep their own carriers in two-way parity:
# test tuples, the init skill's check line, the reference sections.
# Composition: the core bootstraps the agent config; the review panel is a
# subset of the core. Adding a role means adding it here; the parity test
# names every missed carrier.
# Pack roles (reviewer-<kontur>-<aspect>) join the panel dynamically from
# installed-pack manifests and are not listed here; the validation pool lives
# in the registry's [validation] section, not in this file.

CORE_ROLES = ("reviewer-spec", "reviewer-tests", "reviewer-code", "reviewer-kb", "critic", "curator")
PANEL_ROLES = ("reviewer-spec", "reviewer-tests", "reviewer-code", "reviewer-kb")
