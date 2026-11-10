import os


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me-in-production")

    # --- MySQL ---
    MYSQL_USER = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")
    MYSQL_DB = os.getenv("MYSQL_DB", "study_assistant")

    # Đặt USE_SQLITE=1 để chạy thử nhanh không cần MySQL
    if os.getenv("USE_SQLITE") == "1":
        SQLALCHEMY_DATABASE_URI = "sqlite:///study_assistant.db"
    else:
        SQLALCHEMY_DATABASE_URI = (
            f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
            f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}?charset=utf8mb4"
        )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB

    # --- Tham số học tập ---
    DAILY_STUDY_HOURS = float(os.getenv("DAILY_STUDY_HOURS", 4))
    WEAK_THRESHOLD = 65  # % dưới ngưỡng này coi là "yếu"

    # --- VNPay ---
    # Để trống VNP_HASH_SECRET để dùng chế độ DEMO (giả lập thanh toán).
    VNP_TMN_CODE = os.getenv("VNP_TMN_CODE", "")
    VNP_HASH_SECRET = os.getenv("VNP_HASH_SECRET", "")
    VNP_URL = os.getenv("VNP_URL", "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html")
