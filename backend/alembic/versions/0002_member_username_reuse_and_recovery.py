"""Support deleted-member username reuse, member-scoped revocations, and persistence recovery journal.

Revision ID: 0002_member_username_reuse
Revises: 0001_labops_v2_hardened
Create Date: 2026-11-20 08:30:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_member_username_reuse"
down_revision = "0001_labops_v2_hardened"
branch_labels = None
depends_on = None

_MEMBER_COLUMNS = (
    "id, username, email, full_name, role, status, provisioning_state, "
    "assigned_server, workspace_name, workspace_dir, ssh_port, storage_quota_gb, "
    "cpu_limit_cores, ram_limit_gb, vram_quota_gb, gpu_permissions_json, "
    "archived_at, archive_manifest_json, deleted_at, version, payload_json, "
    "created_at, updated_at"
)


def _sqlite_has_inline_username_unique(bind) -> bool:
    idx_rows = bind.exec_driver_sql("PRAGMA index_list('members')").fetchall()
    for row in idx_rows:
        idx_name = str(row[1])
        is_unique = bool(row[2])
        if is_unique and idx_name not in ("ix_members_username", "uq_members_active_username"):
            info_rows = bind.exec_driver_sql(f'PRAGMA index_info("{idx_name}")').fetchall()
            cols = [str(ir[2]) for ir in info_rows]
            if cols == ["username"]:
                return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "members" in tables:
        if dialect == "sqlite":
            op.execute(sa.text("DROP INDEX IF EXISTS ix_members_username"))
            op.execute(sa.text("DROP INDEX IF EXISTS uq_members_active_username"))
            if _sqlite_has_inline_username_unique(bind):
                op.execute(sa.text("DROP TABLE IF EXISTS _members_mig_0002"))
                op.create_table(
                    "_members_mig_0002",
                    sa.Column("id", sa.String(length=64), primary_key=True),
                    sa.Column("username", sa.String(length=64), nullable=False),
                    sa.Column("email", sa.String(length=255), nullable=False, server_default=""),
                    sa.Column("full_name", sa.String(length=160), nullable=False, server_default=""),
                    sa.Column("role", sa.String(length=32), nullable=False, server_default="RESEARCHER"),
                    sa.Column("status", sa.String(length=48), nullable=False, server_default="ACTIVE"),
                    sa.Column("provisioning_state", sa.String(length=64), nullable=False, server_default="PENDING"),
                    sa.Column("assigned_server", sa.String(length=64), nullable=False, server_default="workstation"),
                    sa.Column("workspace_name", sa.String(length=128), nullable=False, server_default=""),
                    sa.Column("workspace_dir", sa.String(length=255), nullable=True),
                    sa.Column("ssh_port", sa.Integer(), nullable=True),
                    sa.Column("storage_quota_gb", sa.Integer(), nullable=False, server_default="100"),
                    sa.Column("cpu_limit_cores", sa.Integer(), nullable=False, server_default="8"),
                    sa.Column("ram_limit_gb", sa.Integer(), nullable=False, server_default="16"),
                    sa.Column("vram_quota_gb", sa.Integer(), nullable=False, server_default="12"),
                    sa.Column("gpu_permissions_json", sa.JSON(), nullable=False),
                    sa.Column("archived_at", sa.String(length=64), nullable=True),
                    sa.Column("archive_manifest_json", sa.JSON(), nullable=True),
                    sa.Column("deleted_at", sa.String(length=64), nullable=True),
                    sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
                    sa.Column("payload_json", sa.JSON(), nullable=False),
                    sa.Column("created_at", sa.String(length=64), nullable=False, server_default=""),
                    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
                )
                op.execute(sa.text(f"INSERT INTO _members_mig_0002 ({_MEMBER_COLUMNS}) SELECT {_MEMBER_COLUMNS} FROM members"))
                op.drop_table("members")
                op.rename_table("_members_mig_0002", "members")
                op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_members_status ON members (status)"))
            op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_members_username ON members (username)"))
            op.execute(
                sa.text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_members_active_username "
                    "ON members (username) WHERE status != 'DELETED' OR deleted_at IS NULL"
                )
            )
        else:
            for uc in insp.get_unique_constraints("members"):
                if list(uc.get("column_names") or []) == ["username"] and uc.get("name"):
                    op.drop_constraint(uc["name"], "members", type_="unique")
            for idx in insp.get_indexes("members"):
                if list(idx.get("column_names") or []) == ["username"] and idx.get("unique") and idx.get("name"):
                    op.drop_index(idx["name"], table_name="members")
            op.create_index("ix_members_username", "members", ["username"], unique=False)
            op.create_index(
                "uq_members_active_username",
                "members",
                ["username"],
                unique=True,
                postgresql_where=sa.text("status != 'DELETED' OR deleted_at IS NULL"),
            )

    if "revoked_tokens" in tables:
        cols = {c["name"] for c in insp.get_columns("revoked_tokens")}
        idxs = {i["name"] for i in insp.get_indexes("revoked_tokens")}
        if "member_id" not in cols:
            op.add_column("revoked_tokens", sa.Column("member_id", sa.String(length=64), nullable=True))
        if "ix_revoked_tokens_member_id" not in idxs:
            op.create_index("ix_revoked_tokens_member_id", "revoked_tokens", ["member_id"])
        if "permanent" not in cols:
            default_false = sa.text("0") if dialect == "sqlite" else sa.text("false")
            op.add_column(
                "revoked_tokens",
                sa.Column("permanent", sa.Boolean(), nullable=False, server_default=default_false),
            )

    if "persistence_recovery_records" not in tables:
        op.create_table(
            "persistence_recovery_records",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("entity_type", sa.String(length=48), nullable=False),
            sa.Column("entity_id", sa.String(length=64), nullable=False),
            sa.Column("username", sa.String(length=64), nullable=True),
            sa.Column("operation", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING_RECOVERY"),
            sa.Column("error_detail", sa.Text(), nullable=True),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_persistence_recovery_records_entity_id", "persistence_recovery_records", ["entity_id"])
        op.create_index("ix_persistence_recovery_records_username", "persistence_recovery_records", ["username"])
        op.create_index("ix_persistence_recovery_records_status", "persistence_recovery_records", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "persistence_recovery_records" in tables:
        op.drop_index("ix_persistence_recovery_records_status", table_name="persistence_recovery_records")
        op.drop_index("ix_persistence_recovery_records_username", table_name="persistence_recovery_records")
        op.drop_index("ix_persistence_recovery_records_entity_id", table_name="persistence_recovery_records")
        op.drop_table("persistence_recovery_records")

    if "revoked_tokens" in tables:
        idxs = {i["name"] for i in insp.get_indexes("revoked_tokens")}
        cols = {c["name"] for c in insp.get_columns("revoked_tokens")}
        if "ix_revoked_tokens_member_id" in idxs:
            op.drop_index("ix_revoked_tokens_member_id", table_name="revoked_tokens")
        with op.batch_alter_table("revoked_tokens") as batch_op:
            if "permanent" in cols:
                batch_op.drop_column("permanent")
            if "member_id" in cols:
                batch_op.drop_column("member_id")

    if "members" in tables:
        if dialect == "sqlite":
            op.execute(sa.text("DROP INDEX IF EXISTS uq_members_active_username"))
            op.execute(sa.text("DROP INDEX IF EXISTS ix_members_username"))
            op.create_index("ix_members_username", "members", ["username"], unique=True)
        else:
            op.drop_index("uq_members_active_username", table_name="members")
            op.drop_index("ix_members_username", table_name="members")
            op.create_index("ix_members_username", "members", ["username"], unique=True)
