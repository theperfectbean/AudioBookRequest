from sqlalchemy import create_engine, event
from sqlmodel import Session, text
import structlog

from app.internal.env_settings import Settings

logger = structlog.stdlib.get_logger()

db = Settings().db
if db.use_postgres:
    engine = create_engine(
        f"postgresql://{db.postgres_user}:{db.postgres_password}@{db.postgres_host}:{db.postgres_port}/{db.postgres_db}?sslmode={db.postgres_ssl_mode}",
        pool_size=db.pool_size,
        max_overflow=db.max_overflow,
        pool_timeout=db.pool_timeout,
        pool_pre_ping=db.pool_pre_ping,
    )
else:
    sqlite_path = Settings().get_sqlite_path()
    # SQLite doesn't support connection pooling same way, but we configure defaults
    engine = create_engine(
        f"sqlite+pysqlite:///{sqlite_path}",
        connect_args={"check_same_thread": False},
        pool_size=db.pool_size,
        max_overflow=db.max_overflow,
        pool_pre_ping=db.pool_pre_ping,
    )

# Log pool configuration at startup
logger.info(
    "Database connection pool configured",
    database_type="PostgreSQL" if db.use_postgres else "SQLite",
    pool_size=db.pool_size,
    max_overflow=db.max_overflow,
    pool_timeout=db.pool_timeout,
    pool_pre_ping=db.pool_pre_ping,
)

# Add event listener for connection pool exhaustion warnings
@event.listens_for(engine, "connect")
def receive_connect(dbapi_conn, connection_record):
    """Log when a new connection is established"""
    if Settings().app.debug:
        logger.debug("Database connection established")


def get_session():
    with Session(engine) as session:
        if not Settings().db.use_postgres:
            session.execute(text("PRAGMA foreign_keys=ON"))
        yield session
