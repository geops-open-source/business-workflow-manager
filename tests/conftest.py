"""Test fixtures for business-workflow-manager tests."""

import os
from collections.abc import Generator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from business_workflow_manager import WorkflowManager
from business_workflow_manager.models import Base

DATABASE_URL = os.getenv(
    "TEST_DB_URL", "postgresql+psycopg:///business_workflow_manager"
)


@pytest.fixture(scope="session")
def engine() -> Engine:
    """Create a PostgreSQL engine and set up tables."""
    engine = create_engine(DATABASE_URL, echo=False)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(engine: Engine) -> Generator[Session]:
    """Provide a transactional session that rolls back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)

    yield sess

    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def mgr(session: Session) -> WorkflowManager:
    """Create a WorkflowManager instance for testing."""
    return WorkflowManager(session)
