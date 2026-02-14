"""create metric table

Revision ID: b85762c8e1b1
Revises: 
Create Date: 2023-01-27 10:28:05.188252

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b85762c8e1b1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Creates the metrics table in the database that will store the results of each metric on each log file.
    id(int): A unique ID for each column. Acts as the primary key for this table. Autoincrements.
    file_hash(bytes): The MD5 hash of the log file the metric was checked against.
    metric_hash(bytes): The MD5 hash of the group file the metric is in.
    file_name(str): The filename of the log file.
    group(str): The group the metric is in.
    metric(str): The metric's name.
    value(str): The value the metric returned as a string.
    stoplight(int): The severity of the metric as represented by a stoplight. 0 = green, 1 = yellow, 2 = red, -1 = not implemented.
    metric_timestamp(datetime.datetime): The time the metric was added to the database. Automatically added by the database.
    """
    op.create_table(
        "metrics",
        sa.Column(
            "id", sa.Integer, primary_key=True, autoincrement=True, nullable=False
        ),
        sa.Column("file_hash", sa.LargeBinary(16), nullable=False),
        sa.Column("metric_hash", sa.LargeBinary(16), nullable=False),
        sa.Column("file_name", sa.Unicode(1024), nullable=False),
        sa.Column("group", sa.Unicode(1024), nullable=False),
        sa.Column("metric", sa.Unicode(1024), nullable=False),
        sa.Column("value", sa.Unicode(1024), nullable=False),
        sa.Column("stoplight", sa.SmallInteger, nullable=False),
        sa.Column(
            "metric_timestamp",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    """Drops the metrics table."""
    op.drop_table("metrics")
