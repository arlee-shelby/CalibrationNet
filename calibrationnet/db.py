"""Engine and session setup, separate from the schema. The connection URL comes
from the DATABASE_URL environment variable (or a .env file), ex:

    DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/calibrationnet

Usage:

    from calibrationnet.db import get_session

    with get_session() as session:
        run = session.get(Run, 1)
        ...
        session.commit()
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()


@lru_cache(maxsize=1)
def get_engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Put it in your environment or a .env file.")
    return create_engine(url)


def get_session() -> Session:
    factory = sessionmaker(bind=get_engine())
    return factory()
