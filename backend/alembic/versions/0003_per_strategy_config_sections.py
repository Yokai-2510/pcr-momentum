"""Rename per-index config sections to per-strategy instrument sections.

Step 3 / Phase A: config_settings rows move from the legacy `index:{idx}`
naming (implicitly owned by the default strategy) to the explicit
`instrument:{strategy_id}:{instrument}` naming so multiple strategies can
carry independent instrument configs.

Revision ID: 0003_per_strategy_config_sections
Revises: 0002_seed
"""

from alembic import op

revision = "0003_per_strategy_config_sections"
down_revision = "0002_seed"
branch_labels = None
depends_on = None

_RENAMES = [
    ("index:nifty50", "instrument:bid_ask_imbalance_v1:nifty50"),
    ("index:banknifty", "instrument:bid_ask_imbalance_v1:banknifty"),
]


def upgrade() -> None:
    for old, new in _RENAMES:
        op.execute(
            f"UPDATE config_settings SET key = '{new}' WHERE key = '{old}'"
        )


def downgrade() -> None:
    for old, new in _RENAMES:
        op.execute(
            f"UPDATE config_settings SET key = '{old}' WHERE key = '{new}'"
        )
