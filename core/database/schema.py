"""
Module Name: schema.py
Author: AuralArchivist
Created: July 6, 2026
Last Modified: October 9, 2026
Description:
    SQLAlchemy Core table definitions. DDL is owned by migrations.py.

Location:
    /core/database/schema.py
"""

from sqlalchemy import (
    MetaData, Table, Column,
    Integer, Text, Float, Boolean, TIMESTAMP,
)

metadata = MetaData()

books = Table(
    "books", metadata,
    Column("id",                 Integer, primary_key=True, autoincrement=True),
    Column("title",              Text,    nullable=False),
    Column("author",             Text),
    Column("series",             Text),
    Column("sequence",           Text),
    Column("narrator",           Text),
    Column("runtime",            Integer),
    Column("release_date",       Text),
    Column("language",           Text),
    Column("publisher",          Text),
    Column("overall_rating",     Text),
    Column("rating",             Float),
    Column("num_ratings",        Integer, default=0),
    Column("asin",               Text),
    Column("summary",            Text),
    Column("cover_image",        Text),
    Column("source",             Text),
    Column("library_status",     Text),
    Column("metadata_source",    Text),
    Column("monitored",          Boolean, default=True),
    Column("file_path",          Text),
    Column("created_at",         TIMESTAMP),
    Column("updated_at",         TIMESTAMP),
    Column("series_asin",        Text),
    Column("file_size",          Integer),
    Column("file_format",        Text),
    Column("file_quality",       Text),
    Column("imported_to_library", Boolean, default=False),
    Column("import_date",        Integer),
    Column("naming_template",    Text),
)

authors = Table(
    "authors", metadata,
    Column("id",                 Integer, primary_key=True, autoincrement=True),
    Column("name",               Text,    nullable=False, unique=True),
    Column("audible_author_id",  Text),
    Column("author_image_url",   Text),
    Column("author_bio",         Text),
    Column("author_page_url",    Text),
    Column("total_books_count",  Integer, default=0),
    Column("audible_books_count", Integer, default=0),
    Column("last_fetched_at",    TIMESTAMP),
    Column("created_at",         TIMESTAMP),
    Column("updated_at",         TIMESTAMP),
)

author_name_overrides = Table(
    "author_name_overrides", metadata,
    Column("id",                    Integer, primary_key=True, autoincrement=True),
    Column("source_author_name",    Text,    nullable=False),
    Column("preferred_author_name", Text,    nullable=False),
    Column("asin",                  Text,    default=""),
    Column("notes",                 Text),
    Column("created_at",            TIMESTAMP),
    Column("updated_at",            TIMESTAMP),
)

audible_library = Table(
    "audible_library", metadata,
    Column("asin",               Text, primary_key=True),
    Column("title",              Text),
    Column("author",             Text),
    Column("authors",            Text),
    Column("narrator",           Text),
    Column("narrators",          Text),
    Column("series_title",       Text),
    Column("series_sequence",    Text),
    Column("publisher",          Text),
    Column("publication_date",   Text),
    Column("release_date",       Text),
    Column("duration_minutes",   Integer),
    Column("runtime_length_min", Integer),
    Column("summary",            Text),
    Column("description",        Text),
    Column("genres",             Text),
    Column("tags",               Text),
    Column("language",           Text),
    Column("format",             Text),
    Column("rating",             Float),
    Column("num_ratings",        Integer),
    Column("purchase_date",      Text),
    Column("listened",           Boolean, default=False),
    Column("progress",           Float,    default=0),
    Column("favorite",           Boolean, default=False),
    Column("cover_image_url",    Text),
    Column("local_cover_path",   Text),
    Column("file_path",          Text),
    Column("file_size",          Integer),
    Column("added_date",         Text),
    Column("last_updated",       Text),
    Column("metadata_source",    Text),
    Column("sync_status",        Text),
)

series_metadata = Table(
    "series_metadata", metadata,
    Column("series_asin",  Text, primary_key=True),
    Column("series_title", Text, nullable=False),
    Column("series_url",   Text),
    Column("sku",          Text),
    Column("sku_lite",     Text),
    Column("total_books",  Integer, default=0),
    Column("description",  Text),
    Column("cover_url",    Text),
    Column("last_synced",  TIMESTAMP),
    Column("created_at",   TIMESTAMP),
    Column("updated_at",   TIMESTAMP),
)

series_books = Table(
    "series_books", metadata,
    Column("id",               Integer, primary_key=True, autoincrement=True),
    Column("series_asin",      Text,    nullable=False),
    Column("book_asin",        Text,    nullable=False),
    Column("book_title",       Text),
    Column("sequence",         Text),
    Column("sort_order",       Text),
    Column("relationship_type", Text,   default="child"),
    Column("in_library",       Boolean, default=False),
    Column("last_checked",     TIMESTAMP),
    Column("author",           Text),
    Column("narrator",         Text),
    Column("publisher",        Text),
    Column("release_date",     Text),
    Column("runtime",          Integer, default=0),
    Column("rating",           Text),
    Column("num_ratings",      Integer, default=0),
    Column("summary",          Text),
    Column("cover_image",      Text),
    Column("language",         Text,    default="English"),
)

download_queue = Table(
    "download_queue", metadata,
    Column("id",                   Integer, primary_key=True, autoincrement=True),
    Column("book_title",           Text,    nullable=False),
    Column("book_author",          Text),
    Column("book_asin",            Text),
    Column("search_result_id",     Integer),
    Column("download_url",         Text),
    Column("download_client",      Text),
    Column("download_client_id",   Text),
    Column("status",               Text,    default="queued"),
    Column("quality_score",        Float),
    Column("match_score",          Float),
    Column("file_format",          Text),
    Column("file_size",            Integer),
    Column("download_progress",    Float,    default=0.0),
    Column("eta_seconds",          Integer),
    Column("error_message",        Text),
    Column("last_error",           Text),
    Column("retry_count",          Integer, default=0),
    Column("max_retries",          Integer, default=3),
    Column("seeding_ratio",        Float,    default=0.0),
    Column("seeding_time_seconds", Integer, default=0),
    Column("queued_at",            TIMESTAMP),
    Column("started_at",           TIMESTAMP),
    Column("completed_at",         TIMESTAMP),
    Column("created_at",           TIMESTAMP),
    Column("updated_at",           TIMESTAMP),
    Column("seeding_enabled",      Boolean, default=False),
    Column("delete_source",        Boolean, default=False),
    Column("temp_file_path",       Text),
    Column("converted_file_path",  Text),
    Column("original_file_path",   Text),
    Column("priority",             Integer, default=5),
    Column("next_retry_at",        Text),
    Column("download_type",        Text,    default="torrent"),
    Column("final_file_path",      Text),
    Column("voucher_file_path",    Text),
    Column("indexer",              Text),
    Column("info_hash",            Text),
)

title_visibility = Table(
    "title_visibility", metadata,
    Column("asin",            Text, primary_key=True),
    Column("title",           Text),
    Column("author",          Text),
    Column("series_asin",     Text),
    Column("series_title",    Text),
    Column("source_context",  Text),
    Column("hidden_at",       TIMESTAMP),
)

indexer_status = Table(
    "indexer_status", metadata,
    Column("id",                    Integer, primary_key=True, autoincrement=True),
    Column("indexer_name",          Text,    nullable=False, unique=True),
    Column("indexer_type",          Text),
    Column("base_url",              Text),
    Column("api_key",               Text),
    Column("enabled",               Boolean, default=True),
    Column("last_check",            TIMESTAMP),
    Column("last_success",          TIMESTAMP),
    Column("last_failure",          TIMESTAMP),
    Column("total_searches",        Integer, default=0),
    Column("successful_searches",   Integer, default=0),
    Column("failed_searches",       Integer, default=0),
    Column("average_response_time", Float),
    Column("health_status",         Text,    default="unknown"),
    Column("error_message",         Text),
    Column("priority",              Integer, default=0),
    Column("created_at",            TIMESTAMP),
    Column("updated_at",            TIMESTAMP),
)

search_preferences = Table(
    "search_preferences", metadata,
    Column("id",               Integer, primary_key=True, autoincrement=True),
    Column("preference_key",   Text,    nullable=False, unique=True),
    Column("preference_value", Text),
    Column("value_type",       Text,    default="string"),
    Column("category",         Text,    default="general"),
    Column("description",      Text),
    Column("created_at",       TIMESTAMP),
    Column("updated_at",       TIMESTAMP),
)


hardcover_link = Table(
    "hardcover_link", metadata,
    Column("id",                    Integer, primary_key=True, autoincrement=True),
    Column("book_id",                Integer, nullable=False, unique=True),
    Column("hardcover_book_id",      Integer, nullable=False),
    Column("hardcover_edition_id",   Integer),
    Column("asin",                   Text),
    Column("match_confidence",       Text,    default="unverified"),
    Column("matched_at",             TIMESTAMP),
    Column("created_at",             TIMESTAMP),
    Column("updated_at",             TIMESTAMP),
)

hardcover_shelf_state = Table(
    "hardcover_shelf_state", metadata,
    Column("id",                    Integer, primary_key=True, autoincrement=True),
    Column("book_id",                Integer, nullable=False, unique=True),
    Column("hardcover_user_book_id",  Integer),
    Column("hardcover_status_id",    Integer),
    Column("hardcover_owned",        Boolean, default=False),
    Column("last_pulled_at",         TIMESTAMP),
    Column("created_at",             TIMESTAMP),
    Column("updated_at",             TIMESTAMP),
)

hardcover_push_queue = Table(
    "hardcover_push_queue", metadata,
    Column("id",               Integer, primary_key=True, autoincrement=True),
    Column("book_id",           Integer, nullable=False),
    Column("action",            Text,    nullable=False),
    Column("payload",           Text),
    Column("status",            Text,    default="pending"),
    Column("retry_count",       Integer, default=0),
    Column("max_retries",       Integer, default=5),
    Column("last_error",        Text),
    Column("created_at",        TIMESTAMP),
    Column("updated_at",        TIMESTAMP),
)

hardcover_trending_cache = Table(
    "hardcover_trending_cache", metadata,
    Column("id",                 Integer, primary_key=True, autoincrement=True),
    Column("window",              Text,    nullable=False),
    Column("rank",                 Integer, nullable=False),
    Column("hardcover_book_id",    Integer, nullable=False),
    Column("title",                Text),
    Column("author",               Text),
    Column("cover_url",            Text),
    Column("stat_label",           Text),
    Column("rating",               Float),
    Column("ratings_count",        Integer),
    Column("asin",                 Text),
    Column("narrator",             Text),
    Column("audio_seconds",        Integer),
    Column("refreshed_at",         TIMESTAMP),
)

hardcover_new_releases_cache = Table(
    "hardcover_new_releases_cache", metadata,
    Column("id",                 Integer, primary_key=True, autoincrement=True),
    Column("hardcover_book_id",    Integer, nullable=False),
    Column("title",                Text),
    Column("author",               Text),
    Column("cover_url",            Text),
    Column("release_date",         Text),
    Column("asin",                 Text),
    Column("narrator",             Text),
    Column("audio_seconds",        Integer),
    Column("refreshed_at",         TIMESTAMP),
)

