"""数据库版本绑定表 database_bindings（ADR-014）

Revision ID: b4e8a2c7d1f9
Revises: f0cfad1d4c42
Create Date: 2026-09-15

兼容性（ADR-014）：全部既有库（含 data/ashare_lab.db）迁移后初始状态=未绑定
（本表为空表）——实验默认被 require_database_binding 拒绝，豁免路径保底；
全新库由首次播种/写行情落绑定。SQLite 走 batch 惯例，PostgreSQL 同迁移可用。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4e8a2c7d1f9'
down_revision: Union[str, None] = 'f0cfad1d4c42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('database_bindings',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('seed', sa.Integer(), nullable=True),
    sa.Column('generator_version', sa.String(length=64), nullable=False),
    sa.Column('code_commit', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('database_bindings')
