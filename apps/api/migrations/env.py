from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.config import settings
from app.models import Base

if context.config.config_file_name:
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)

url = context.config.attributes.get("database_url") or settings.database_url
connectable = create_engine(url)

with connectable.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()
