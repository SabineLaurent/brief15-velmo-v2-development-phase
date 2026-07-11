"""Table `memory_facts` — mémoire long terme factuelle.

Revision ID: 0002_memory_facts
Revises: 0001_initial
Create Date: 2026-07-11
"""

from __future__ import annotations

from alembic import op

from velmo.db import Base

revision = "0002_memory_facts"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `create_all` est idempotent (checkfirst) : sur une base déjà migrée par
    # 0001, seule la table `memory_facts` (ajoutée depuis à `Base`) est créée.
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    op.drop_table("memory_facts")
