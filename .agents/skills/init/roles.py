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
# installed-pack manifests and are not listed here. The delivery matrix below
# carries the core routes and the validation pool: init reconciles a
# consumer's registry with it, so the shipped mechanics equal the project's.

CORE_ROLES = ("reviewer-spec", "reviewer-tests", "reviewer-code", "reviewer-kb", "critic", "curator")
PANEL_ROLES = ("reviewer-spec", "reviewer-tests", "reviewer-code", "reviewer-kb")

# The delivery matrix: the standard layout init ships to a consumer and
# keeps equal to the project registry on every run. `sync_config.py` writes
# it; the table in init/SKILL.md mirrors ROLE_ROUTES; the pool block mirrors
# POOL_ROUTES. Rows are (role, route, level); routes are `<provider>/<model>`.
ROLE_ROUTES = (
    ("reviewer-spec", "qwen-token-plan-max/qwen4-max", "medium"),
    ("reviewer-tests", "qwen-token-plan-max/qwen4-max", "medium"),
    ("reviewer-code", "qwen-token-plan-max/qwen4-max", "medium"),
    ("reviewer-kb", "qwen-token-plan-max/qwen4-max", "medium"),
    ("critic", "qwen-token-plan-max/qwen4-max", "xhigh"),
    ("curator", "qwen-token-plan/qwen3.8-flash", "medium"),
)

# The validation pool is part of the matrix: three distinct models by the
# model part, rows are (route, level).
POOL_ROUTES = (
    ("qwen-token-plan-deepseek/deepseek-v4.1-flash-1017", "max"),
    ("qwen-token-plan/qwen3.8-flash", "xhigh"),
    ("qwen-token-plan-glm/glm-5.3", "max"),
)
