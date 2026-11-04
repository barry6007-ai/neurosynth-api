import json
import argparse
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

def ensure_sslmode_required(db_url: str) -> str:
    """
    Ensure ?sslmode=require is present in the connection URL.
    """
    parsed = urlparse(db_url)
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if q.get("sslmode") is None:
        q["sslmode"] = "require"
    new_query = urlencode(q)
    return urlunparse(parsed._replace(query=new_query))

def run(conn, sql, name, summary, key):
    """
    Execute a SQL statement and print pass/fail.
    On failure, issue a ROLLBACK so subsequent statements can proceed.
    Returns (ok: bool, rows: list|None).
    """
    try:
        res = conn.execute(text(sql))
        rows = []
        try:
            rows = res.fetchall()
        except Exception:
            # Statement did not return rows (e.g., CREATE EXTENSION)
            rows = []
        print(f"✅ {name}: OK")
        if rows:
            print("   ↳ Result:", rows[0])
        summary[key] = {"ok": True, "result": rows[:1]}
        return True, rows
    except SQLAlchemyError as e:
        print(f"❌ {name}: FAILED")
        print(f"   ↳ Error: {e}")
        summary[key] = {"ok": False, "error": str(e)}
        try:
            # Clear aborted transaction state
            conn.exec_driver_sql("ROLLBACK")
        except Exception:
            pass
        return False, None

def ensure_extension(conn, extname: str, summary, key_prefix: str):
    """
    Try to enable a PostgreSQL extension in the *current database*.
    Then verify presence via pg_extension.
    """
    run(conn, f"CREATE EXTENSION IF NOT EXISTS {extname};",
        f"enable {extname}", summary, f"{key_prefix}.enable")

    # Verify it's installed
    ok, rows = run(conn,
        f"SELECT extname, extversion, extnamespace::regnamespace "
        f"FROM pg_extension WHERE extname = '{extname}';",
        f"{extname} extension installed", summary, f"{key_prefix}.installed")

    return ok

def check_tsvector(conn, summary):
    print("\n=== Check tsvector (Full-Text Search) ===")
    run(conn,
        "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tsvector') AS has_tsvector;",
        "tsvector type exists", summary, "tsvector.type_exists")
    run(conn,
        "SELECT to_tsvector('english', 'hello world') AS tv;",
        "to_tsvector works", summary, "tsvector.to_tsvector")

def check_pgvector(conn, summary):
    print("\n=== Check pgvector (Vector similarity) ===")
    ensure_extension(conn, "vector", summary, "pgvector")

    # Check that 'vector' type exists (namespace+type)
    run(conn, """
        SELECT n.nspname, t.typname
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'vector';
    """, "vector type present", summary, "pgvector.type_present")

    # Functional test: create temp table and use <-> operator
    run(conn, """
        CREATE TEMP TABLE _vec_test (v vector(3));
        INSERT INTO _vec_test (v) VALUES ('[1,2,3]'), ('[3,2,1]');
        SELECT v <-> '[0,0,0]'::vector AS l2 FROM _vec_test ORDER BY 1 LIMIT 1;
    """, "vector type & <-> distance operator", summary, "pgvector.distance_op")

def check_postgis(conn, summary):
    print("\n=== Check PostGIS (Spatial) ===")
    ensure_extension(conn, "postgis", summary, "postgis")

    # Version function (indicates PostGIS is operational)
    run(conn,
        "SELECT PostGIS_Full_Version() LIMIT 1;",
        "PostGIS_Full_Version() works", summary, "postgis.version_fn")

    # Basic geometry operation
    run(conn,
        "SELECT ST_AsText(ST_Buffer(ST_GeomFromText('POINT(0 0)', 4326), 1.0)) LIMIT 1;",
        "Geometry operations (ST_Buffer)", summary, "postgis.geometry_ops")

def main():
    parser = argparse.ArgumentParser(description="PostgreSQL feature self-check (tsvector, pgvector, PostGIS)")
    parser.add_argument("--url", required=True, help="Postgres connection URL")
    args = parser.parse_args()

    db_url = ensure_sslmode_required(args.url)
    summary = {}

    # Use AUTOCOMMIT so each statement is its own transaction
    engine = create_engine(db_url, pool_pre_ping=True)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        print("=== Environment / sanity checks ===")
        run(conn, "SHOW server_version;", "server_version", summary, "env.server_version")
        run(conn, "SELECT current_database();", "current database", summary, "env.current_database")

        print("\n=== Ensure/verify extensions & run feature tests ===")
        check_tsvector(conn, summary)
        check_pgvector(conn, summary)
        check_postgis(conn, summary)

    print("\n=== Summary (JSON) ===")
    print(json.dumps(summary, indent=2, default=str))

if __name__ == "__main__":
    main()

