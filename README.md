# 🎓 Hệ thống trợ lý học tập thông minh bằng AI

Công nghệ: **Python · Flask · Flask-Login · MySQL · Scikit-learn · NLP (TF-IDF tiếng Việt)**

## Tính năng chính

- **Đăng ký / Đăng nhập** đa người dùng (mỗi người có dữ liệu riêng)
- Quản lý môn học, lịch học, deadline — **CRUD đầy đủ** (thêm / sửa / xoá)
- Tự động lập kế hoạch học tập (Earliest-Deadline-First + ôn chủ đề yếu)
- Phân tích kết quả học tập (GPA, xu hướng LinearRegression, dự đoán điểm)
- Gợi ý nội dung cần ôn tập dựa trên điểm kiểm tra
- Chatbot hỏi đáp tài liệu học tập + trả lời lịch / deadline / điểm / kế hoạch

## Cài đặt & chạy

```bash
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 1) Tạo CSDL MySQL
mysql -u root -p < schema.sql        # hoặc bỏ qua: Flask tự tạo bảng khi chạy

# 2) Cấu hình kết nối (biến môi trường)
export MYSQL_USER=root MYSQL_PASSWORD=matkhau MYSQL_HOST=127.0.0.1 MYSQL_DB=study_assistant
#   Chạy thử không cần MySQL:  export USE_SQLITE=1

# 3) (Tuỳ chọn) nạp user demo + dữ liệu mẫu rồi chạy
flask --app app seed
python app.py                         # http://127.0.0.1:5000
```

**Tài khoản demo:** `demo` / `demo123`

## Cấu trúc

```
app.py               Flask: routes + auth + lệnh seed
config.py            Cấu hình MySQL & tham số học tập
models.py            SQLAlchemy models (User + 7 bảng, có user_id)
schema.sql           Script tạo CSDL MySQL
services/
  planner.py         Tự động lập kế hoạch học tập
  analytics.py       Phân tích điểm + gợi ý ôn tập (scikit-learn)
  chatbot.py         Chatbot hỏi đáp (TF-IDF + cosine + intent routing)
  nlp.py             Tách từ, chia đoạn, đọc PDF/DOCX/TXT
templates/           Giao diện Bootstrap 5 + Chart.js
```

## Các chức năng & thuật toán

| Chức năng | Cách hoạt động |
|---|---|
| Đăng nhập đa user | Flask-Login + password hash (werkzeug); mọi bảng gắn `user_id` |
| Quản lý môn / lịch / deadline / điểm | CRUD đầy đủ; mỗi thao tác kiểm tra ownership |
| Lập kế hoạch tự động | Heuristic Earliest-Deadline-First: sức chứa/ngày = giờ tự học − 50% giờ trên lớp; chia đều giờ cho các ngày trước hạn; cảnh báo khi quá tải; giờ dư dùng để ôn chủ đề yếu |
| Phân tích kết quả | Điểm TB, GPA theo tín chỉ, xếp loại, **LinearRegression** để xác định xu hướng và dự đoán điểm bài tới so với mục tiêu |
| Gợi ý ôn tập | Điểm TB có trọng số theo thời gian (bài gần đây nặng hơn) theo từng chủ đề; ưu tiên = (100 − điểm) × hệ số độ khó môn; kèm đoạn tài liệu liên quan |
| Chatbot | Intent routing (lịch, deadline, điểm, ôn tập, kế hoạch, danh sách môn) + DocumentQA: chia đoạn → **TfidfVectorizer** (unigram+bigram) → cosine similarity → extractive QA chọn câu liên quan nhất |


## Nâng cấp từ database cũ (giữ nguyên dữ liệu)

Nếu bạn đã có database từ bản trước (chưa có đăng nhập / thành phần điểm):

```bash
# SQLite
set USE_SQLITE=1          # Windows CMD
# $env:USE_SQLITE="1"     # PowerShell

python migrate_db.py
python app.py
```

Script sẽ:
1. Tạo bảng mới còn thiếu (`users`, `grade_components`, …)
2. Thêm cột `user_id`, `component_id`, `weight` nếu chưa có
3. Gán toàn bộ dữ liệu cũ cho user **`migrated` / `migrated123`**

Sau khi migrate:
- Đăng nhập `migrated` / `migrated123`
- Vào **Môn học → Cấu hình** để set trọng số TX1/TX2/Cuối kỳ cho từng môn
- Khi nhập điểm mới, chọn đúng thành phần để hệ thống tính điểm TK có trọng số

MySQL tương tự (bỏ `USE_SQLITE`, set biến `MYSQL_*` rồi chạy `python migrate_db.py`).

## Hướng mở rộng tiếp

- Cài `underthesea` để tách từ tiếng Việt chính xác hơn
- Thay chatbot bằng embedding (sentence-transformers / PhoBERT) hoặc RAG + LLM
- Dùng KMeans/RandomForest để phân nhóm học lực hoặc dự đoán nguy cơ rớt môn
- Gửi nhắc deadline qua email / Telegram (APScheduler)
- Phân quyền admin / giáo viên
