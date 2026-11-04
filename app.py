# app.py
# 依照 https://github.com/spiketren/ns-nano 規格完整實作 (PostgreSQL 正統方案)

import os
from flask import Flask, jsonify, abort, send_file, request
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

_engine = None

def get_engine():
    """從環境變數 DB_URL (或 DATABASE_URL) 取得 PostgreSQL 連線"""
    global _engine
    if _engine is not None:
        return _engine
    db_url = os.getenv("DB_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("Missing DB_URL (or DATABASE_URL) environment variable.")
    # 將舊版 postgres:// 轉為 postgresql://
    if db_url.startswith("postgres://"):
        db_url = "postgresql://" + db_url[len("postgres://"):]
    _engine = create_engine(
        db_url,
        pool_pre_ping=True,
    )
    return _engine

def create_app():
    app = Flask(__name__)
    app.json.ensure_ascii = False

    @app.after_request
    def add_cors_headers(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        return resp

    # -------------------------------------------------------------
    # 1. 官方端點: GET / (Health Check)
    # -------------------------------------------------------------
    @app.get("/", endpoint="health")
    def health():
        return "<p>Server working!</p>"

    # -------------------------------------------------------------
    # 2. 官方端點: GET /img (展示大腦切片圖)
    # -------------------------------------------------------------
    @app.get("/img", endpoint="show_img")
    def show_img():
        img_path = os.path.join(os.path.dirname(__file__), "amygdala.gif")
        if os.path.exists(img_path):
            return send_file(img_path, mimetype="image/gif")
        return jsonify({"message": "amygdala.gif not found"}), 404

    # -------------------------------------------------------------
    # 3. 官方端點: GET /test_db (檢查 PostgreSQL 與資料筆數)
    # -------------------------------------------------------------
    @app.get("/test_db", endpoint="test_db")
    def test_db():
        payload = {"ok": False}
        try:
            eng = get_engine()
            payload["dialect"] = eng.dialect.name
            with eng.begin() as conn:
                conn.execute(text("SET search_path TO ns, public;"))
                payload["version"] = conn.exec_driver_sql("SELECT version()").scalar()

                # 統計筆數
                payload["coordinates_count"] = conn.execute(text("SELECT COUNT(*) FROM ns.coordinates")).scalar()
                payload["metadata_count"] = conn.execute(text("SELECT COUNT(*) FROM ns.metadata")).scalar()
                payload["annotations_terms_count"] = conn.execute(text("SELECT COUNT(*) FROM ns.annotations_terms")).scalar()

                # 抽樣檢查
                try:
                    rows = conn.execute(text(
                        "SELECT study_id, ST_X(geom) AS x, ST_Y(geom) AS y, ST_Z(geom) AS z FROM ns.coordinates LIMIT 3"
                    )).mappings().all()
                    payload["coordinates_sample"] = [dict(r) for r in rows]
                except Exception:
                    payload["coordinates_sample"] = []

                try:
                    rows = conn.execute(text("SELECT * FROM ns.metadata LIMIT 3")).mappings().all()
                    payload["metadata_sample"] = [dict(r) for r in rows]
                except Exception:
                    payload["metadata_sample"] = []

                try:
                    rows = conn.execute(text(
                        "SELECT study_id, contrast_id, term, weight FROM ns.annotations_terms LIMIT 3"
                    )).mappings().all()
                    payload["annotations_terms_sample"] = [dict(r) for r in rows]
                except Exception:
                    payload["annotations_terms_sample"] = []

            payload["ok"] = True
            return jsonify(payload), 200

        except Exception as e:
            payload["error"] = str(e)
            return jsonify(payload), 500

    # -------------------------------------------------------------
    # 4. 基礎查詢: GET /terms/<term>/studies
    # 資料庫中詞彙格式為 "terms_abstract_tfidf__<term>"
    # 支援兩種查詢：直接輸入 "fear" 或 "terms_abstract_tfidf__fear" 皆可
    # -------------------------------------------------------------
    @app.get("/terms/<path:term>/studies", endpoint="terms_studies")
    def get_studies_by_term(term):
        term_clean = term.strip().lower()
        # 若使用者輸入不含前綴，自動補上
        if not term_clean.startswith("terms_"):
            term_full = f"terms_abstract_tfidf__{term_clean}"
        else:
            term_full = term_clean
        eng = get_engine()
        with eng.begin() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT study_id FROM ns.annotations_terms WHERE LOWER(term) = :t"),
                {"t": term_full}
            ).fetchall()
            studies = [str(r[0]) for r in rows]

        return jsonify({
            "term": term,
            "count": len(studies),
            "studies": studies
        })

    # -------------------------------------------------------------
    # 5. 基礎查詢: GET /locations/<coords>/studies
    # -------------------------------------------------------------
    @app.get("/locations/<coords>/studies", endpoint="locations_studies")
    def get_studies_by_coordinates(coords):
        try:
            x, y, z = map(float, coords.split("_"))
        except ValueError:
            return jsonify({"error": "Invalid format, use x_y_z e.g. 0_-52_26"}), 400

        r = float(request.args.get("r", 8.0))
        eng = get_engine()
        sql = """
        SELECT DISTINCT study_id FROM ns.coordinates
        WHERE ST_3DDWithin(geom, ST_SetSRID(ST_MakePoint(:x, :y, :z), 4326), :r)
        """
        with eng.begin() as conn:
            rows = conn.execute(text(sql), {"x": x, "y": y, "z": z, "r": r}).fetchall()
            studies = [str(r[0]) for r in rows]

        return jsonify({
            "location": [x, y, z],
            "radius_mm": r,
            "count": len(studies),
            "studies": studies
        })

    # -------------------------------------------------------------
    # 6. Sprint 核心: GET /dissociate/terms/<term_a>/<term_b>
    # 官方範例: /dissociate/terms/posterior cingulate/ventromedial prefrontal
    # -------------------------------------------------------------
    @app.get("/dissociate/terms/<path:term_a>/<path:term_b>", endpoint="dissociate_terms")
    def dissociate_terms(term_a, term_b):
        ta = term_a.strip().lower()
        tb = term_b.strip().lower()
        # 自動補上資料庫前綴
        if not ta.startswith("terms_"):
            ta = f"terms_abstract_tfidf__{ta}"
        if not tb.startswith("terms_"):
            tb = f"terms_abstract_tfidf__{tb}"

        eng = get_engine()
        sql = """
        SELECT DISTINCT study_id FROM ns.annotations_terms WHERE LOWER(term) = :ta
        EXCEPT
        SELECT DISTINCT study_id FROM ns.annotations_terms WHERE LOWER(term) = :tb
        """
        with eng.begin() as conn:
            rows = conn.execute(text(sql), {"ta": ta, "tb": tb}).fetchall()
            studies = [str(r[0]) for r in rows]

        return jsonify({
            "type": "terms_dissociation",
            "term_a": term_a,
            "term_b": term_b,
            "count": len(studies),
            "studies": studies
        })

    # -------------------------------------------------------------
    # 7. Sprint 核心: GET /dissociate/locations/<coords1>/<coords2>
    # 官方範例 (DMN): /dissociate/locations/0_-52_26/-2_50_-6
    # -------------------------------------------------------------
    @app.get("/dissociate/locations/<coords1>/<coords2>", endpoint="dissociate_locations")
    def dissociate_locations(coords1, coords2):
        try:
            x1, y1, z1 = map(float, coords1.split("_"))
            x2, y2, z2 = map(float, coords2.split("_"))
        except ValueError:
            return jsonify({"error": "Invalid format, use x_y_z e.g. 0_-52_26/-2_50_-6"}), 400

        r = float(request.args.get("r", 8.0))
        eng = get_engine()
        sql = """
        SELECT DISTINCT study_id FROM ns.coordinates
        WHERE ST_3DDWithin(geom, ST_SetSRID(ST_MakePoint(:x1, :y1, :z1), 4326), :r)
        EXCEPT
        SELECT DISTINCT study_id FROM ns.coordinates
        WHERE ST_3DDWithin(geom, ST_SetSRID(ST_MakePoint(:x2, :y2, :z2), 4326), :r)
        """
        with eng.begin() as conn:
            rows = conn.execute(text(sql), {
                "x1": x1, "y1": y1, "z1": z1,
                "x2": x2, "y2": y2, "z2": z2,
                "r": r
            }).fetchall()
            studies = [str(r[0]) for r in rows]

        return jsonify({
            "type": "locations_dissociation",
            "loc1": [x1, y1, z1],
            "loc2": [x2, y2, z2],
            "radius_mm": r,
            "count": len(studies),
            "studies": studies
        })

    return app

# WSGI 入口點 (供 gunicorn 調用)
app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"\n🚀 Neurosynth Backend 啟動中！請在瀏覽器打開：http://127.0.0.1:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=True)

