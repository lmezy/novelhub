import importlib.util
from pathlib import Path


VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load_migrations():
    migrations = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        spec = importlib.util.spec_from_file_location(
            f"migration_{path.stem}",
            path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        migrations.append((path.stem, module))
    return migrations


def test_all_migration_revisions_fit_alembic_version_column():
    migrations = _load_migrations()
    assert migrations

    for filename, module in migrations:
        assert len(module.revision) <= 32, (
            f"{filename}: revision {module.revision!r} exceeds varchar(32)"
        )


def test_migration_chain_is_linear_and_ends_at_head():
    migrations = _load_migrations()
    revisions = {module.revision for _, module in migrations}
    assert len(revisions) == len(migrations)

    by_down_revision = {
        module.down_revision: module for _, module in migrations
    }
    assert by_down_revision[None] is not None

    heads = [
        module.revision
        for _, module in migrations
        if module.revision not in by_down_revision
    ]
    assert heads == ["0022_book_display_cover"]

    visited = []
    node = by_down_revision[None]
    while node is not None:
        visited.append(node.revision)
        node = by_down_revision.get(node.revision)
    assert len(visited) == len(migrations)
