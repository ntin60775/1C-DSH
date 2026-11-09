# OntoShip role registry: the machine source of the role lists, in the init
# bundle next to gen_agents.py (plan-gate decisions of docs/plans/role-registry.md,
# accepted by the operator 2026-11-01). Precedent: the installer array SKILLS
# (scripts/install.sh) is the machine source of the bundle list.
#
# Data only, no logic. Consumers parse this file as text — the anchor test
# tests/test_role_registry.py — and keep their own carriers in two-way parity:
# test tuples, the init skill's check line, the reference sections.
# Composition: the core bootstraps the agent config; the review panel is a
# subset of the core. Adding a role means adding it here; the parity test
# names every missed carrier.

CORE_ROLES = ("reviewer-spec", "reviewer-tests", "reviewer-code", "reviewer-kb", "critic", "curator")
PANEL_ROLES = ("reviewer-spec", "reviewer-tests", "reviewer-code", "reviewer-kb")
