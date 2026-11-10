"""
Migrate database cũ (chưa có user / grade_components) lên schema mới.
Chạy:  python migrate_db.py

Hỗ trợ SQLite (USE_SQLITE=1) và MySQL.
An toàn chạy nhiều lần (idempotent): chỉ thêm cột/bảng còn thiếu.
"""
import os
import sys

# Đảm bảo import được app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import inspect, text
from app import app
from models import User, db


def column_exists(insp, table, column):
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def table_exists(insp, table):
    return table in insp.get_table_names()


def add_column(conn, dialect, table, column_def):
    """column_def ví dụ: 'user_id INTEGER' hoặc 'user_id INT NULL'"""
    sql = f"ALTER TABLE {table} ADD COLUMN {column_def}"
    print(f"  + {sql}")
    conn.execute(text(sql))


def migrate():
    with app.app_context():
        eng = db.engine
        dialect = eng.dialect.name  # 'sqlite' | 'mysql' | ...
        insp = inspect(eng)

        print(f"Dialect: {dialect}")
        print(f"URI: {app.config['SQLALCHEMY_DATABASE_URI'][:60]}...")

        # 1) Tạo toàn bộ bảng còn thiếu (users, grade_components, ...)
        db.create_all()
        insp = inspect(eng)  # refresh
        print("create_all() done.")
        try:
            with eng.begin() as conn:
                insp = inspect(eng)
                if table_exists(insp, "courses") and not column_exists(insp, "courses", "credits"):
                    sql_typ = "INTEGER DEFAULT 3" if dialect == "sqlite" else "INT DEFAULT 3"
                    add_column(conn, dialect, "courses", f"credits {sql_typ}")
                    print("  + courses.credits")
        except Exception as ex:
            print("courses.credits:", ex)


        # ---- Bổ sung cột hồ sơ user nếu thiếu (DB cũ) ----
        user_extra_cols = {
            "role": "VARCHAR(20)",
            "is_active": "BOOLEAN",
            "balance": "FLOAT",
            "avatar": "VARCHAR(255)",
            "birth_date": "DATE",
            "phone": "VARCHAR(20)",
            "school": "VARCHAR(160)",
            "major": "VARCHAR(120)",
            "class_name": "VARCHAR(80)",
            "student_id": "VARCHAR(40)",
            "address": "VARCHAR(255)",
            "bio": "VARCHAR(300)",
        }
        with eng.begin() as conn:
            insp = inspect(eng)
            if table_exists(insp, "users"):
                for col, typ in user_extra_cols.items():
                    if not column_exists(insp, "users", col):
                        if dialect == "sqlite":
                            # SQLite type affinity
                            sql_typ = "DATE" if col == "birth_date" else "TEXT"
                        else:
                            sql_typ = typ + " NULL"
                        add_column(conn, dialect, "users", f"{col} {sql_typ}")
                        print(f"  + users.{col}")
            # default role = student
            try:
                conn.execute(text("UPDATE users SET role = 'student' WHERE role IS NULL OR role = ''"))
            except Exception:
                pass



        with eng.begin() as conn:
            # ---- users: đảm bảo có user mặc định ----
            if not table_exists(insp, "users"):
                print("ERROR: bảng users không tồn tại sau create_all.")
                return

            # ---- Thêm cột user_id vào các bảng cũ nếu thiếu ----
            tables_need_user = [
                "subjects", "schedules", "deadlines", "scores",
                "documents", "study_plans",
            ]
            for t in tables_need_user:
                if not table_exists(insp, t):
                    continue
                if not column_exists(insp, t, "user_id"):
                    if dialect == "sqlite":
                        add_column(conn, dialect, t, "user_id INTEGER")
                    else:
                        # MySQL: thêm NULL trước, fill, rồi có thể siết NOT NULL sau
                        add_column(conn, dialect, t, "user_id INT NULL")
                insp = inspect(eng)

            # ---- scores: component_id + weight ----
            if table_exists(insp, "scores"):
                if not column_exists(insp, "scores", "component_id"):
                    if dialect == "sqlite":
                        add_column(conn, dialect, "scores", "component_id INTEGER")
                    else:
                        add_column(conn, dialect, "scores", "component_id INT NULL")
                if not column_exists(insp, "scores", "weight"):
                    if dialect == "sqlite":
                        add_column(conn, dialect, "scores", "weight FLOAT")
                    else:
                        add_column(conn, dialect, "scores", "weight FLOAT NULL")

            # ---- Gán dữ liệu cũ cho 1 user mặc định ----
            # Tìm / tạo user migrate
            row = conn.execute(text("SELECT id FROM users WHERE username = 'migrated' LIMIT 1")).fetchone()
            if row:
                uid = row[0]
                print(f"Dùng user migrated có sẵn (id={uid})")
            else:
                # Tạo qua ORM cho đúng password hash
                pass  # tạo bên ngoài transaction SQLAlchemy thuần

        # Tạo user ngoài block connection ở trên
        user = User.query.filter_by(username="migrated").first()
        if not user:
            user = User(
                username="migrated",
                email="migrated@local",
                full_name="Dữ liệu cũ (migrate)",
            )
            user.set_password("migrated123")
            db.session.add(user)
            db.session.commit()
            print("Đã tạo user: migrated / migrated123")
        uid = user.id

        with eng.begin() as conn:
            for t in tables_need_user:
                if not table_exists(inspect(eng), t):
                    continue
                if not column_exists(inspect(eng), t, "user_id"):
                    continue
                # Chỉ fill những dòng chưa có user_id
                if dialect == "sqlite":
                    r = conn.execute(
                        text(f"UPDATE {t} SET user_id = :uid WHERE user_id IS NULL"),
                        {"uid": uid},
                    )
                else:
                    r = conn.execute(
                        text(f"UPDATE {t} SET user_id = :uid WHERE user_id IS NULL"),
                        {"uid": uid},
                    )
                print(f"  Gán user_id={uid} cho bảng {t}: {r.rowcount} dòng")

        print("\n=== Migrate xong ===")
        print(f"Đăng nhập bằng:  migrated  /  migrated123")
        print("Sau đó vào Môn học → Cấu hình thành phần điểm cho từng môn.")
        print("Nếu muốn, đổi mật khẩu hoặc tạo user mới rồi chuyển dữ liệu sau.")


if __name__ == "__main__":
    migrate()
