from datetime import date, datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default="student", nullable=False)  # student|teacher|admin
    full_name = db.Column(db.String(120))
    birth_date = db.Column(db.Date)
    phone = db.Column(db.String(20))
    school = db.Column(db.String(160))
    major = db.Column(db.String(120))
    class_name = db.Column(db.String(80))
    student_id = db.Column(db.String(40))
    address = db.Column(db.String(255))
    bio = db.Column(db.String(300))
    is_active = db.Column(db.Boolean, default=True)
    balance = db.Column(db.Float, default=0)  # số dư ví (VND)
    avatar = db.Column(db.String(255))  # đường dẫn file avatar
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    subjects = db.relationship("Subject", backref="user", cascade="all, delete-orphan")
    schedules = db.relationship("Schedule", backref="user", cascade="all, delete-orphan")
    deadlines = db.relationship("Deadline", backref="user", cascade="all, delete-orphan")
    scores = db.relationship("Score", backref="user", cascade="all, delete-orphan")
    documents = db.relationship("Document", backref="user", cascade="all, delete-orphan")
    plans = db.relationship("StudyPlan", backref="user", cascade="all, delete-orphan")

    teaching_courses = db.relationship(
        "Course", backref="teacher", foreign_keys="Course.teacher_id",
        cascade="all, delete-orphan")
    enrollments = db.relationship(
        "Enrollment", backref="student", foreign_keys="Enrollment.student_id",
        cascade="all, delete-orphan")

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_student(self):
        return self.role == "student"

    @property
    def is_teacher(self):
        return self.role == "teacher"

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def role_label(self):
        return {"student": "Sinh viên", "teacher": "Giảng viên", "admin": "Admin"}.get(
            self.role, self.role)


class Subject(db.Model):
    __tablename__ = "subjects"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(20))
    teacher = db.Column(db.String(120))
    credits = db.Column(db.Integer, default=3)
    difficulty = db.Column(db.Integer, default=3)
    target_score = db.Column(db.Float, default=8.0)

    schedules = db.relationship("Schedule", backref="subject", cascade="all, delete-orphan")
    deadlines = db.relationship("Deadline", backref="subject", cascade="all, delete-orphan")
    scores = db.relationship("Score", backref="subject", cascade="all, delete-orphan")
    plans = db.relationship("StudyPlan", backref="subject", cascade="all, delete-orphan")
    documents = db.relationship("Document", backref="subject")
    components = db.relationship(
        "GradeComponent", backref="subject", cascade="all, delete-orphan",
        order_by="GradeComponent.sort_order")


class GradeComponent(db.Model):
    __tablename__ = "grade_components"
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    weight = db.Column(db.Float, nullable=False, default=0)
    sort_order = db.Column(db.Integer, default=0)
    scores = db.relationship("Score", backref="component")


class Schedule(db.Model):
    __tablename__ = "schedules"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    weekday = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    room = db.Column(db.String(50))

    @property
    def hours(self):
        s = self.start_time.hour + self.start_time.minute / 60
        e = self.end_time.hour + self.end_time.minute / 60
        return max(e - s, 0)


class Deadline(db.Model):
    __tablename__ = "deadlines"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    est_hours = db.Column(db.Float, default=2)
    priority = db.Column(db.Integer, default=2)
    done = db.Column(db.Boolean, default=False)
    plans = db.relationship("StudyPlan", backref="deadline", cascade="all, delete-orphan")

    @property
    def days_left(self):
        return (self.due_date - date.today()).days


class Score(db.Model):
    __tablename__ = "scores"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    component_id = db.Column(db.Integer, db.ForeignKey("grade_components.id"))
    exam_name = db.Column(db.String(120), nullable=False)
    topic = db.Column(db.String(160))
    score = db.Column(db.Float, nullable=False)
    max_score = db.Column(db.Float, default=10)
    weight = db.Column(db.Float)
    taken_at = db.Column(db.Date, nullable=False, default=date.today)

    @property
    def percent(self):
        return self.score / self.max_score * 100 if self.max_score else 0

    @property
    def score10(self):
        return self.score / self.max_score * 10 if self.max_score else 0

    @property
    def effective_weight(self):
        if self.component_id and self.component:
            return self.component.weight or 0
        return self.weight or 0


class Document(db.Model):
    __tablename__ = "documents"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"))
    filename = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    chunks = db.relationship("DocChunk", backref="document", cascade="all, delete-orphan")


class DocChunk(db.Model):
    __tablename__ = "doc_chunks"
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=False)
    text = db.Column(db.Text, nullable=False)


class StudyPlan(db.Model):
    __tablename__ = "study_plans"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    deadline_id = db.Column(db.Integer, db.ForeignKey("deadlines.id"))
    hours = db.Column(db.Float, nullable=False)
    kind = db.Column(db.String(20), default="deadline")
    note = db.Column(db.String(255))
    done = db.Column(db.Boolean, default=False)


# ===================== Lớp học / đăng ký / học phí =====================

class Course(db.Model):
    """Lớp học do giảng viên mở."""
    __tablename__ = "courses"
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    code = db.Column(db.String(40))
    description = db.Column(db.Text)
    fee = db.Column(db.Float, default=0)              # học phí (VND)
    credits = db.Column(db.Integer, default=3)        # số tín chỉ
    max_students = db.Column(db.Integer, default=40)
    status = db.Column(db.String(20), default="open")  # open | closed | ended
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    enrollments = db.relationship("Enrollment", backref="course", cascade="all, delete-orphan")
    sessions = db.relationship("CourseSession", backref="course", cascade="all, delete-orphan",
                               order_by="CourseSession.session_date")
    materials = db.relationship("CourseMaterial", backref="course", cascade="all, delete-orphan")
    assignments = db.relationship("CourseAssignment", backref="course", cascade="all, delete-orphan")

    @property
    def student_count(self):
        return sum(1 for e in self.enrollments if e.status in ("active", "pending"))

    @property
    def seats_left(self):
        return max((self.max_students or 0) - self.student_count, 0)

    @property
    def status_label(self):
        return {"open": "Đang mở", "closed": "Đóng đăng ký", "ended": "Đã kết thúc"}.get(
            self.status, self.status)

    @property
    def paid_count(self):
        return sum(1 for e in self.enrollments if e.payment_status == "paid")

    @property
    def revenue(self):
        return sum(e.fee_amount or 0 for e in self.enrollments if e.payment_status == "paid")


class Enrollment(db.Model):
    """Sinh viên đăng ký lớp."""
    __tablename__ = "enrollments"
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    enrolled_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default="active")  # pending|active|dropped
    fee_amount = db.Column(db.Float, default=0)
    payment_status = db.Column(db.String(20), default="unpaid")  # unpaid|paid|refunded
    paid_at = db.Column(db.DateTime)
    payment_note = db.Column(db.String(255))

    payments = db.relationship("Payment", backref="enrollment", cascade="all, delete-orphan")

    @property
    def payment_label(self):
        return {"unpaid": "Chưa thanh toán", "paid": "Đã thanh toán",
                "refunded": "Đã hoàn tiền"}.get(self.payment_status, self.payment_status)


class Payment(db.Model):
    """Lịch sử thu học phí."""
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("enrollments.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(40), default="cash")  # cash|transfer|other
    status = db.Column(db.String(20), default="paid")
    note = db.Column(db.String(255))
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    paid_at = db.Column(db.DateTime, default=datetime.utcnow)


class CourseSession(db.Model):
    """Buổi học của lớp."""
    __tablename__ = "course_sessions"
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    session_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    room = db.Column(db.String(50))
    topic = db.Column(db.String(200))
    note = db.Column(db.Text)


class CourseMaterial(db.Model):
    """Tài liệu lớp học."""
    __tablename__ = "course_materials"
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text)          # nội dung text / link
    filename = db.Column(db.String(255))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


class CourseAssignment(db.Model):
    """Deadline / bài kiểm tra của lớp."""
    __tablename__ = "course_assignments"
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    kind = db.Column(db.String(30), default="homework")  # homework|quiz|exam
    due_date = db.Column(db.Date)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def kind_kind_label(self):
        return {"homework": "Bài tập", "quiz": "Kiểm tra", "exam": "Thi"}.get(
            self.kind, self.kind)



class CourseGrade(db.Model):
    """Điểm do giảng viên nhập cho SV trong lớp."""
    __tablename__ = "course_grades"
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(120), nullable=False)  # TX1, Giữa kỳ...
    kind = db.Column(db.String(30), default="quiz")    # quiz|exam|homework|other
    score = db.Column(db.Float, nullable=False)
    max_score = db.Column(db.Float, default=10)
    weight = db.Column(db.Float, default=0)            # % trong lớp
    note = db.Column(db.String(255))
    taken_at = db.Column(db.Date, default=date.today)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    course = db.relationship("Course", backref=db.backref("grades", cascade="all, delete-orphan"))
    student = db.relationship("User", backref=db.backref("course_grades", cascade="all, delete-orphan"))

    @property
    def score10(self):
        return self.score / self.max_score * 10 if self.max_score else 0


class Attendance(db.Model):
    """Điểm danh theo buổi học."""
    __tablename__ = "attendances"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("course_sessions.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(20), default="present")  # present|absent|late|excused
    note = db.Column(db.String(255))
    marked_at = db.Column(db.DateTime, default=datetime.utcnow)
    marked_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    session = db.relationship("CourseSession", backref=db.backref("attendances", cascade="all, delete-orphan"))
    student = db.relationship("User", foreign_keys=[student_id],
                              backref=db.backref("attendances", cascade="all, delete-orphan"))

    @property
    def status_label(self):
        return {"present": "Có mặt", "absent": "Vắng", "late": "Đi muộn",
                "excused": "Có phép"}.get(self.status, self.status)


class WalletTransaction(db.Model):
    """Lịch sử ví / nạp tiền VNPay."""
    __tablename__ = "wallet_transactions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)          # + nạp, - trừ
    kind = db.Column(db.String(30), default="topup")      # topup|pay_course|refund|adjust
    status = db.Column(db.String(20), default="pending")  # pending|success|failed
    provider = db.Column(db.String(30), default="vnpay")
    txn_ref = db.Column(db.String(64), unique=True)       # mã giao dịch nội bộ
    provider_txn = db.Column(db.String(64))               # mã VNPay
    description = db.Column(db.String(255))
    enrollment_id = db.Column(db.Integer, db.ForeignKey("enrollments.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    user = db.relationship("User", backref=db.backref("wallet_txns", cascade="all, delete-orphan"))
