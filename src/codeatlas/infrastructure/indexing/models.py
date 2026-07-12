"""SQLAlchemy persistence models; these are not domain models."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the M3 SQLite schema."""


class RepositoryRow(Base):
    __tablename__ = "repositories"
    repository_id: Mapped[str] = mapped_column(String(37), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class SnapshotRow(Base):
    __tablename__ = "repository_snapshots"
    snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.repository_id", ondelete="RESTRICT"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("index_generations.generation_id", ondelete="CASCADE"), unique=True
    )
    parser_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    parser_metadata_json: Mapped[str] = mapped_column(Text, nullable=False)


class GenerationRow(Base):
    __tablename__ = "index_generations"
    __table_args__ = (
        UniqueConstraint("repository_id", "generation_number"),
        UniqueConstraint("repository_id", "commit_sha", "generation_number"),
        CheckConstraint(
            "status IN ('pending','running','validating','active','failed','superseded')"
        ),
        Index(
            "uq_active_generation_per_repository",
            "repository_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
        ),
    )
    generation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.repository_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    owner_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(String(256))
    statistics_json: Mapped[str | None] = mapped_column(Text)


class IndexedFileRow(Base):
    __tablename__ = "indexed_files"
    __table_args__ = (UniqueConstraint("generation_id", "path"),)
    row_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    logical_file_id: Mapped[str] = mapped_column(String(37), nullable=False, index=True)
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("index_generations.generation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str | None] = mapped_column(String(32), index=True)
    encoding: Mapped[str | None] = mapped_column(String(32))
    security_status: Mapped[str] = mapped_column(String(16), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(64))
    parser_id: Mapped[str | None] = mapped_column(String(128))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    grammar_version: Mapped[str | None] = mapped_column(String(32))
    syntax_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class FileVersionRow(Base):
    __tablename__ = "file_versions"
    file_row_id: Mapped[int] = mapped_column(
        ForeignKey("indexed_files.row_id", ondelete="CASCADE"), primary_key=True
    )
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    git_object_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_utf8: Mapped[bytes | None] = mapped_column(LargeBinary)


class SymbolRow(Base):
    __tablename__ = "symbols"
    __table_args__ = (UniqueConstraint("generation_id", "symbol_id"),)
    row_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol_id: Mapped[str] = mapped_column(String(39), nullable=False, index=True)
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("index_generations.generation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_row_id: Mapped[int] = mapped_column(
        ForeignKey("indexed_files.row_id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    qualified_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    start_byte: Mapped[int] = mapped_column(Integer, nullable=False)
    end_byte: Mapped[int] = mapped_column(Integer, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    start_column: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_column: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_symbol_id: Mapped[str | None] = mapped_column(String(39), index=True)
    exported: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_documentation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    parser_id: Mapped[str] = mapped_column(String(128), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    grammar_version: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    derivation_kind: Mapped[str] = mapped_column(String(32), nullable=False)


class RelationMixin:
    relation_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("index_generations.generation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_row_id: Mapped[int] = mapped_column(
        ForeignKey("indexed_files.row_id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_symbol_id: Mapped[str | None] = mapped_column(String(39), index=True)
    raw_target: Mapped[str] = mapped_column(Text, nullable=False)
    start_byte: Mapped[int] = mapped_column(Integer, nullable=False)
    end_byte: Mapped[int] = mapped_column(Integer, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    start_column: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_column: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_status: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    resolver_metadata: Mapped[str | None] = mapped_column(Text)


class ImportRelationRow(RelationMixin, Base):
    __tablename__ = "import_relations"


class ExportRelationRow(RelationMixin, Base):
    __tablename__ = "export_relations"


class ReferenceRelationRow(RelationMixin, Base):
    __tablename__ = "reference_relations"
    relation_kind: Mapped[str] = mapped_column(String(32), nullable=False)


class IndexJobRow(Base):
    __tablename__ = "index_jobs"
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.repository_id", ondelete="RESTRICT"), primary_key=True
    )
    owner_token: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_expires_at: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    heartbeat_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ValidationResultRow(Base):
    __tablename__ = "validation_results"
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("index_generations.generation_id", ondelete="CASCADE"), primary_key=True
    )
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    details_json: Mapped[str] = mapped_column(Text, nullable=False)
    validated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class SchemaMetadataRow(Base):
    __tablename__ = "schema_metadata"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(128), nullable=False)
