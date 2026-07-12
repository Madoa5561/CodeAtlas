# ADR-0015: SQLAlchemy, Alembic, and SQLite

- Status: Accepted
- Date: 2026-07-12

## Context

M3 needs relational constraints, explicit transactions, reversible schema evolution, and a local OSS deployment without a separate database service.

## Decision

Use SQLAlchemy 2.0.37 as the adapter ORM/toolkit, Alembic 1.14.1 for explicit migrations, and SQLite as the M3 database. SQLAlchemy and Alembic are MIT licensed and support Python 3.12. Foreign keys, WAL, `synchronous=FULL`, busy timeout, and `check_same_thread=False` are configured by the store. Synchronous DB operations cross an `asyncio.to_thread` boundary.

Application startup checks the Alembic revision but never runs an upgrade automatically. Operators use `codeatlas-admin db upgrade`. SQLite batch migration mode is enabled for future constraint changes.

## Consequences

SQLite has one writer and is appropriate for a single-node M3 deployment. Repository leases and short transactions reduce contention, but this is not a distributed queue. PostgreSQL is not implemented.
