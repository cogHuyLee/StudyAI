"""Routes: lớp học (giảng viên), đăng ký (SV), thống kê & thu phí (admin)."""
import os
import uuid
from datetime import date, datetime
from pathlib import Path

from werkzeug.utils import secure_filename

from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required

from models import (Attendance, Course, CourseAssignment, CourseGrade,
                    CourseMaterial, CourseSession, Enrollment, Payment, User,
                    WalletTransaction, db)
from roles import admin_required, teacher_required
from services import vnpay

bp = Blueprint("courses", __name__)


def _money(v):
    try:
        return f"{float(v):,.0f} đ".replace(",", ".")
    except (TypeError, ValueError):
        return "0 đ"


# ───────────────────────── Sinh viên: danh sách & đăng ký ─────────────────────────
@bp.route("/classes")
@login_required
def class_list():
    """SV xem lớp đang mở / lớp của mình; GV xem lớp mình dạy."""
    if current_user.is_teacher:
        return redirect(url_for("courses.teacher_classes"))
    if current_user.is_admin:
        return redirect(url_for("courses.admin_classes"))

    open_courses = (Course.query.filter(Course.status == "open")
                    .order_by(Course.created_at.desc()).all())
    my_enrolls = (Enrollment.query.filter_by(student_id=current_user.id)
                  .filter(Enrollment.status != "dropped")
                  .order_by(Enrollment.enrolled_at.desc()).all())
    enrolled_ids = {e.course_id for e in my_enrolls}
    return render_template(
        "classes_student.html", open_courses=open_courses,
        my_enrolls=my_enrolls, enrolled_ids=enrolled_ids, money=_money)


@bp.route("/classes/<int:cid>")
@login_required
def class_detail(cid):
    course = Course.query.get_or_404(cid)
    enroll = None
    if current_user.is_student:
        enroll = Enrollment.query.filter_by(
            course_id=cid, student_id=current_user.id).filter(
            Enrollment.status != "dropped").first()
    # quyền xem chi tiết: admin, GV phụ trách, SV đã ghi danh, hoặc lớp đang mở
    can_view = (
        current_user.is_admin
        or course.teacher_id == current_user.id
        or enroll is not None
        or course.status == "open"
    )
    if not can_view:
        flash("Bạn không có quyền xem lớp này.", "danger")
        return redirect(url_for("courses.class_list"))
    return render_template(
        "class_detail.html", course=course, enroll=enroll, money=_money)


@bp.post("/classes/<int:cid>/enroll")
@login_required
def enroll(cid):
    if not current_user.is_student:
        flash("Chỉ sinh viên mới đăng ký lớp được.", "warning")
        return redirect(url_for("courses.class_list"))
    course = Course.query.get_or_404(cid)
    if course.status != "open":
        flash("Lớp không còn nhận đăng ký.", "warning")
        return redirect(url_for("courses.class_detail", cid=cid))
    if course.seats_left <= 0:
        flash("Lớp đã đủ sĩ số.", "warning")
        return redirect(url_for("courses.class_detail", cid=cid))
    existed = Enrollment.query.filter_by(
        course_id=cid, student_id=current_user.id).filter(
        Enrollment.status != "dropped").first()
    if existed:
        flash("Bạn đã đăng ký lớp này rồi.", "info")
        return redirect(url_for("courses.class_detail", cid=cid))
    e = Enrollment(
        course_id=cid, student_id=current_user.id,
        fee_amount=course.fee or 0, payment_status="unpaid", status="active")
    db.session.add(e)
    db.session.commit()
    flash(f"Đã đăng ký «{course.name}». Học phí: {_money(e.fee_amount)} (chưa thanh toán).",
          "success")
    return redirect(url_for("courses.class_detail", cid=cid))


@bp.post("/classes/<int:cid>/drop")
@login_required
def drop_class(cid):
    e = Enrollment.query.filter_by(
        course_id=cid, student_id=current_user.id).filter(
        Enrollment.status != "dropped").first_or_404()
    if e.payment_status == "paid":
        flash("Bạn đã thanh toán – liên hệ Admin/GV để hủy và hoàn tiền nếu cần.", "warning")
        return redirect(url_for("courses.class_detail", cid=cid))
    e.status = "dropped"
    db.session.commit()
    flash("Đã hủy đăng ký lớp.", "info")
    return redirect(url_for("courses.class_list"))


# ───────────────────────── Giảng viên ─────────────────────────
@bp.route("/teacher/classes")
@login_required
@teacher_required
def teacher_classes():
    if current_user.is_admin:
        courses = Course.query.order_by(Course.created_at.desc()).all()
    else:
        courses = (Course.query.filter_by(teacher_id=current_user.id)
                   .order_by(Course.created_at.desc()).all())
    return render_template("teacher_classes.html", courses=courses, money=_money)


@bp.route("/teacher/classes/new", methods=["GET", "POST"])
@login_required
@teacher_required
def teacher_class_new():
    if request.method == "POST":
        f = request.form
        name = (f.get("name") or "").strip()
        if not name:
            flash("Tên lớp không được trống.", "danger")
            return render_template("teacher_class_form.html", course=None)
        c = Course(
            teacher_id=current_user.id if current_user.is_teacher else current_user.id,
            name=name,
            code=(f.get("code") or "").strip() or None,
            description=(f.get("description") or "").strip() or None,
            fee=float(f.get("fee") or 0),
            credits=int(f.get("credits") or 3),
            max_students=int(f.get("max_students") or 40),
            status=f.get("status") or "open",
        )
        if f.get("start_date"):
            c.start_date = datetime.strptime(f["start_date"], "%Y-%m-%d").date()
        if f.get("end_date"):
            c.end_date = datetime.strptime(f["end_date"], "%Y-%m-%d").date()
        # admin có thể gán GV khác
        if current_user.is_admin and f.get("teacher_id"):
            c.teacher_id = int(f["teacher_id"])
        db.session.add(c)
        db.session.commit()
        flash("Đã tạo lớp học.", "success")
        return redirect(url_for("courses.teacher_class_manage", cid=c.id))
    teachers = User.query.filter_by(role="teacher").order_by(User.full_name).all()
    return render_template("teacher_class_form.html", course=None, teachers=teachers)


@bp.route("/teacher/classes/<int:cid>/edit", methods=["GET", "POST"])
@login_required
@teacher_required
def teacher_class_edit(cid):
    course = Course.query.get_or_404(cid)
    if not current_user.is_admin and course.teacher_id != current_user.id:
        flash("Không phải lớp của bạn.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    if request.method == "POST":
        f = request.form
        course.name = (f.get("name") or "").strip() or course.name
        course.code = (f.get("code") or "").strip() or None
        course.description = (f.get("description") or "").strip() or None
        course.fee = float(f.get("fee") or 0)
        course.credits = int(f.get("credits") or 3)
        course.max_students = int(f.get("max_students") or 40)
        course.status = f.get("status") or course.status
        if f.get("start_date"):
            course.start_date = datetime.strptime(f["start_date"], "%Y-%m-%d").date()
        else:
            course.start_date = None
        if f.get("end_date"):
            course.end_date = datetime.strptime(f["end_date"], "%Y-%m-%d").date()
        else:
            course.end_date = None
        if current_user.is_admin and f.get("teacher_id"):
            course.teacher_id = int(f["teacher_id"])
        db.session.commit()
        flash("Đã cập nhật lớp học.", "success")
        return redirect(url_for("courses.teacher_class_manage", cid=cid))
    teachers = User.query.filter_by(role="teacher").order_by(User.full_name).all()
    return render_template("teacher_class_form.html", course=course, teachers=teachers)


@bp.post("/teacher/classes/<int:cid>/end")
@login_required
@teacher_required
def teacher_class_end(cid):
    course = Course.query.get_or_404(cid)
    if not current_user.is_admin and course.teacher_id != current_user.id:
        flash("Không phải lớp của bạn.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    course.status = "ended"
    db.session.commit()
    flash(f"Đã kết thúc lớp «{course.name}».", "info")
    return redirect(url_for("courses.teacher_classes"))


@bp.post("/teacher/classes/<int:cid>/delete")
@login_required
@teacher_required
def teacher_class_delete(cid):
    course = Course.query.get_or_404(cid)
    if not current_user.is_admin and course.teacher_id != current_user.id:
        flash("Không phải lớp của bạn.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    db.session.delete(course)
    db.session.commit()
    flash("Đã xoá lớp học.", "info")
    return redirect(url_for("courses.teacher_classes"))


@bp.route("/teacher/classes/<int:cid>")
@login_required
@teacher_required
def teacher_class_manage(cid):
    course = Course.query.get_or_404(cid)
    if not current_user.is_admin and course.teacher_id != current_user.id:
        flash("Không phải lớp của bạn.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    enrolls = (Enrollment.query.filter_by(course_id=cid)
               .filter(Enrollment.status != "dropped")
               .order_by(Enrollment.enrolled_at.desc()).all())
    return render_template(
        "teacher_class_manage.html", course=course, enrolls=enrolls, money=_money)


# --- Buổi học ---
@bp.post("/teacher/classes/<int:cid>/sessions")
@login_required
@teacher_required
def add_session(cid):
    course = Course.query.get_or_404(cid)
    if not current_user.is_admin and course.teacher_id != current_user.id:
        flash("Không có quyền.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    f = request.form
    s = CourseSession(
        course_id=cid,
        session_date=datetime.strptime(f["session_date"], "%Y-%m-%d").date(),
        topic=(f.get("topic") or "").strip() or None,
        room=(f.get("room") or "").strip() or None,
        note=(f.get("note") or "").strip() or None,
    )
    if f.get("start_time"):
        s.start_time = datetime.strptime(f["start_time"], "%H:%M").time()
    if f.get("end_time"):
        s.end_time = datetime.strptime(f["end_time"], "%H:%M").time()
    db.session.add(s)
    db.session.commit()
    flash("Đã thêm buổi học.", "success")
    return redirect(url_for("courses.teacher_class_manage", cid=cid))


@bp.post("/teacher/sessions/<int:sid>/delete")
@login_required
@teacher_required
def delete_session(sid):
    s = CourseSession.query.get_or_404(sid)
    cid = s.course_id
    if not current_user.is_admin and s.course.teacher_id != current_user.id:
        flash("Không có quyền.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    db.session.delete(s)
    db.session.commit()
    flash("Đã xoá buổi học.", "info")
    return redirect(url_for("courses.teacher_class_manage", cid=cid))


# --- Tài liệu lớp ---
@bp.post("/teacher/classes/<int:cid>/materials")
@login_required
@teacher_required
def add_material(cid):
    course = Course.query.get_or_404(cid)
    if not current_user.is_admin and course.teacher_id != current_user.id:
        flash("Không có quyền.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    f = request.form
    title = (f.get("title") or "").strip()
    content = (f.get("content") or "").strip() or None
    upload = request.files.get("file")
    stored_name = None
    orig_name = None
    if upload and upload.filename:
        orig_name = secure_filename(upload.filename)
        if not title:
            title = orig_name
        ext = Path(orig_name).suffix
        stored_name = f"c{cid}_{uuid.uuid4().hex[:12]}{ext}"
        folder = Path("uploads") / "materials"
        folder.mkdir(parents=True, exist_ok=True)
        upload.save(folder / stored_name)
    if not title:
        flash("Thiếu tiêu đề hoặc file tài liệu.", "danger")
        return redirect(url_for("courses.teacher_class_manage", cid=cid))
    m = CourseMaterial(
        course_id=cid, title=title, content=content,
        filename=stored_name or orig_name,
    )
    # lưu tên gốc vào content prefix nếu có file
    if stored_name:
        m.filename = stored_name
        if orig_name:
            m.content = (m.content or "")
            # giữ original name ở đầu content dạng meta
            meta = f"[file:{orig_name}]"
            if not (m.content or "").startswith("[file:"):
                m.content = meta + (chr(10) + m.content if m.content else "")
    db.session.add(m)
    db.session.commit()
    flash("Đã thêm tài liệu.", "success")
    return redirect(url_for("courses.teacher_class_manage", cid=cid))


@bp.route("/materials/<int:mid>/download")
@login_required
def download_material(mid):
    from flask import send_from_directory, abort
    m = CourseMaterial.query.get_or_404(mid)
    course = m.course
    # quyền: GV lớp, admin, hoặc SV đã ghi danh
    allowed = current_user.is_admin or course.teacher_id == current_user.id
    if not allowed and current_user.is_student:
        en = Enrollment.query.filter_by(
            course_id=course.id, student_id=current_user.id).filter(
            Enrollment.status != "dropped").first()
        allowed = en is not None
    if not allowed:
        flash("Bạn không có quyền tải tài liệu này.", "danger")
        return redirect(url_for("dashboard"))
    if not m.filename:
        flash("Tài liệu không có file đính kèm.", "warning")
        return redirect(request.referrer or url_for("courses.class_detail", cid=course.id))
    folder = Path("uploads") / "materials"
    fpath = folder / m.filename
    if not fpath.is_file():
        flash("File không tồn tại trên máy chủ.", "danger")
        return redirect(request.referrer or url_for("courses.class_detail", cid=course.id))
    # tên tải về
    download_name = m.filename
    if m.content and m.content.startswith("[file:"):
        end = m.content.find("]")
        if end > 6:
            download_name = m.content[6:end]
    return send_from_directory(folder.resolve(), m.filename, as_attachment=True,
                               download_name=download_name)


@bp.post("/teacher/materials/<int:mid>/delete")
@login_required
@teacher_required
def delete_material(mid):
    m = CourseMaterial.query.get_or_404(mid)
    cid = m.course_id
    if not current_user.is_admin and m.course.teacher_id != current_user.id:
        flash("Không có quyền.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    db.session.delete(m)
    db.session.commit()
    flash("Đã xoá tài liệu.", "info")
    return redirect(url_for("courses.teacher_class_manage", cid=cid))


# --- Bài tập / kiểm tra ---
@bp.post("/teacher/classes/<int:cid>/assignments")
@login_required
@teacher_required
def add_assignment(cid):
    course = Course.query.get_or_404(cid)
    if not current_user.is_admin and course.teacher_id != current_user.id:
        flash("Không có quyền.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    f = request.form
    title = (f.get("title") or "").strip()
    if not title:
        flash("Thiếu tiêu đề.", "danger")
        return redirect(url_for("courses.teacher_class_manage", cid=cid))
    a = CourseAssignment(
        course_id=cid, title=title,
        kind=f.get("kind") or "homework",
        description=(f.get("description") or "").strip() or None,
    )
    if f.get("due_date"):
        a.due_date = datetime.strptime(f["due_date"], "%Y-%m-%d").date()
    db.session.add(a)
    db.session.commit()
    flash("Đã thêm bài tập / kiểm tra.", "success")
    return redirect(url_for("courses.teacher_class_manage", cid=cid))


@bp.post("/teacher/assignments/<int:aid>/delete")
@login_required
@teacher_required
def delete_assignment(aid):
    a = CourseAssignment.query.get_or_404(aid)
    cid = a.course_id
    if not current_user.is_admin and a.course.teacher_id != current_user.id:
        flash("Không có quyền.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    db.session.delete(a)
    db.session.commit()
    flash("Đã xoá.", "info")
    return redirect(url_for("courses.teacher_class_manage", cid=cid))


# ───────────────────────── Admin ─────────────────────────
@bp.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    n_students = User.query.filter_by(role="student").count()
    n_teachers = User.query.filter_by(role="teacher").count()
    n_courses = Course.query.count()
    n_open = Course.query.filter_by(status="open").count()
    enrolls = Enrollment.query.filter(Enrollment.status != "dropped").all()
    n_enrolls = len(enrolls)
    revenue = sum(e.fee_amount or 0 for e in enrolls if e.payment_status == "paid")
    unpaid = sum(e.fee_amount or 0 for e in enrolls if e.payment_status == "unpaid")
    recent_payments = (Payment.query.order_by(Payment.paid_at.desc()).limit(10).all())
    return render_template(
        "admin_dashboard.html",
        n_students=n_students, n_teachers=n_teachers,
        n_courses=n_courses, n_open=n_open, n_enrolls=n_enrolls,
        revenue=revenue, unpaid=unpaid, recent_payments=recent_payments,
        money=_money)


@bp.route("/admin/users")
@login_required
@admin_required
def admin_users():
    role = request.args.get("role")
    q = User.query.order_by(User.created_at.desc())
    if role in ("student", "teacher", "admin"):
        q = q.filter_by(role=role)
    users = q.limit(200).all()
    return render_template("admin_users.html", users=users, role=role)


@bp.post("/admin/users/<int:uid>/role")
@login_required
@admin_required
def admin_set_role(uid):
    user = User.query.get_or_404(uid)
    role = request.form.get("role")
    if role not in ("student", "teacher", "admin"):
        flash("Vai trò không hợp lệ.", "danger")
        return redirect(url_for("courses.admin_users"))
    if user.id == current_user.id and role != "admin":
        flash("Không thể tự bỏ quyền admin của chính mình.", "warning")
        return redirect(url_for("courses.admin_users"))
    user.role = role
    db.session.commit()
    flash(f"Đã đổi vai trò «{user.username}» → {user.role_label}.", "success")
    return redirect(url_for("courses.admin_users"))


@bp.route("/admin/classes")
@login_required
@admin_required
def admin_classes():
    courses = Course.query.order_by(Course.created_at.desc()).all()
    return render_template("admin_classes.html", courses=courses, money=_money)


@bp.route("/admin/payments")
@login_required
@admin_required
def admin_payments():
    status = request.args.get("status", "unpaid")
    q = Enrollment.query.filter(Enrollment.status != "dropped")
    if status in ("unpaid", "paid", "refunded"):
        q = q.filter_by(payment_status=status)
    enrolls = q.order_by(Enrollment.enrolled_at.desc()).limit(100).all()
    return render_template(
        "admin_payments.html", enrolls=enrolls, status=status, money=_money)


@bp.post("/admin/enrollments/<int:eid>/pay")
@login_required
@admin_required
def admin_mark_paid(eid):
    e = Enrollment.query.get_or_404(eid)
    amount = float(request.form.get("amount") or e.fee_amount or 0)
    method = request.form.get("method") or "cash"
    note = (request.form.get("note") or "").strip() or None
    e.payment_status = "paid"
    e.paid_at = datetime.utcnow()
    e.payment_note = note
    p = Payment(
        enrollment_id=e.id, amount=amount, method=method,
        status="paid", note=note, recorded_by=current_user.id)
    db.session.add(p)
    db.session.commit()
    flash(f"Đã thu {_money(amount)} – {e.student.full_name or e.student.username} "
          f"/ {e.course.name}.", "success")
    return redirect(request.referrer or url_for("courses.admin_payments"))


@bp.post("/admin/enrollments/<int:eid>/unpay")
@login_required
@admin_required
def admin_mark_unpaid(eid):
    e = Enrollment.query.get_or_404(eid)
    e.payment_status = "unpaid"
    e.paid_at = None
    db.session.commit()
    flash("Đã đánh dấu chưa thanh toán.", "info")
    return redirect(request.referrer or url_for("courses.admin_payments"))


# ───────────────────────── Ví & VNPay ─────────────────────────
@bp.route("/wallet")
@login_required
def wallet():
    if not current_user.is_student and not current_user.is_admin:
        flash("Chức năng ví dành cho sinh viên.", "info")
        return redirect(url_for("dashboard"))
    txns = (WalletTransaction.query.filter_by(user_id=current_user.id)
            .order_by(WalletTransaction.created_at.desc()).limit(30).all())
    unpaid = (Enrollment.query.filter_by(student_id=current_user.id, payment_status="unpaid")
              .filter(Enrollment.status != "dropped").all())
    return render_template(
        "wallet.html", txns=txns, unpaid=unpaid, money=_money,
        demo=vnpay.is_demo_mode(), balance=current_user.balance or 0)


@bp.post("/wallet/topup")
@login_required
def wallet_topup():
    if not current_user.is_student and not current_user.is_admin:
        flash("Không có quyền.", "danger")
        return redirect(url_for("dashboard"))
    try:
        amount = int(float(request.form.get("amount") or 0))
    except ValueError:
        amount = 0
    if amount < 10000:
        flash("Số tiền nạp tối thiểu 10.000 đ.", "warning")
        return redirect(url_for("courses.wallet"))
    import uuid
    txn_ref = f"TP{current_user.id}{uuid.uuid4().hex[:10].upper()}"
    wt = WalletTransaction(
        user_id=current_user.id, amount=amount, kind="topup",
        status="pending", provider="vnpay", txn_ref=txn_ref,
        description=f"Nạp tiền ví {amount:,.0f}đ".replace(",", "."),
    )
    db.session.add(wt)
    db.session.commit()
    return_url = url_for("courses.vnpay_return", _external=True)
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "127.0.0.1")
    url, is_demo = vnpay.build_payment_url(
        amount, txn_ref, wt.description, return_url, client_ip=ip.split(",")[0].strip())
    if is_demo:
            return redirect(url)


@bp.route("/wallet/vnpay-return")
@login_required
def vnpay_return():
    params = request.args.to_dict()
    ok, msg = vnpay.verify_return(params)
    txn_ref = params.get("vnp_TxnRef") or ""
    wt = WalletTransaction.query.filter_by(txn_ref=txn_ref, user_id=current_user.id).first()
    if not wt:
        flash("Không tìm thấy giao dịch.", "danger")
        return redirect(url_for("courses.wallet"))
    if wt.status == "success":
        flash("Giao dịch đã được xử lý trước đó.", "info")
        return redirect(url_for("courses.wallet"))
    if ok:
        wt.status = "success"
        wt.completed_at = datetime.utcnow()
        wt.provider_txn = params.get("vnp_TransactionNo") or params.get("vnp_BankTranNo")
        user = User.query.get(current_user.id)
        user.balance = (user.balance or 0) + float(wt.amount)
        db.session.commit()
        flash(f"Nạp thành công { _money(wt.amount) }. Số dư: {_money(user.balance)}.", "success")
    else:
        wt.status = "failed"
        wt.completed_at = datetime.utcnow()
        db.session.commit()
        flash(f"Thanh toán thất bại: {msg}", "danger")
    return redirect(url_for("courses.wallet"))


@bp.post("/wallet/pay-enrollment/<int:eid>")
@login_required
def pay_enrollment_wallet(eid):
    e = Enrollment.query.filter_by(id=eid, student_id=current_user.id).first()
    if not e or e.status == "dropped":
        flash("Không tìm thấy đăng ký lớp hợp lệ.", "danger")
        return redirect(url_for("courses.wallet"))
    if e.payment_status == "paid":
        flash("Lớp này đã thanh toán rồi.", "info")
        return redirect(url_for("courses.wallet"))
    amount = float(e.fee_amount or 0)
    if amount <= 0:
        # miễn phí
        e.payment_status = "paid"
        e.paid_at = datetime.utcnow()
        e.payment_note = "Miễn phí"
        db.session.commit()
        flash("Lớp không thu học phí — đã đánh dấu thanh toán.", "success")
        return redirect(url_for("courses.wallet"))
    user = User.query.get(current_user.id)
    bal = float(user.balance or 0)
    if bal + 1e-6 < amount:
        flash(
            f"Số dư không đủ. Cần {_money(amount)}, hiện có {_money(bal)}. "
            f"Hãy nạp thêm ít nhất {_money(amount - bal)}.",
            "warning",
        )
        return redirect(url_for("courses.wallet"))
    user.balance = bal - amount
    e.payment_status = "paid"
    e.paid_at = datetime.utcnow()
    e.payment_note = "Thanh toán bằng ví StudyAI"
    wt = WalletTransaction(
        user_id=user.id, amount=-amount, kind="pay_course", status="success",
        provider="wallet", txn_ref=f"PAY{uuid.uuid4().hex[:12].upper()}",
        description=f"Thanh toán lớp {e.course.name}",
        enrollment_id=e.id, completed_at=datetime.utcnow(),
    )
    p = Payment(
        enrollment_id=e.id, amount=amount, method="wallet",
        status="paid", note="Ví StudyAI", recorded_by=user.id)
    # Cộng doanh thu vào ví giảng viên (demo)
    teacher = User.query.get(e.course.teacher_id)
    if teacher:
        teacher.balance = float(teacher.balance or 0) + amount
        db.session.add(WalletTransaction(
            user_id=teacher.id, amount=amount, kind="pay_course", status="success",
            provider="wallet", txn_ref=f"RV{uuid.uuid4().hex[:12].upper()}",
            description=f"Học phí từ {user.full_name or user.username} – {e.course.name}",
            enrollment_id=e.id, completed_at=datetime.utcnow(),
        ))
    db.session.add_all([wt, p])
    db.session.commit()
    flash(f"Đã thanh toán {_money(amount)} cho «{e.course.name}». Số dư còn {_money(user.balance)}.", "success")
    return redirect(url_for("courses.wallet"))


@bp.post("/classes/<int:cid>/pay")
@login_required
def pay_class_direct(cid):
    """Thanh toán học phí ngay từ trang lớp."""
    e = Enrollment.query.filter_by(
        course_id=cid, student_id=current_user.id).filter(
        Enrollment.status != "dropped").first()
    if not e:
        flash("Bạn chưa đăng ký lớp này.", "warning")
        return redirect(url_for("courses.class_detail", cid=cid))
    return pay_enrollment_wallet(e.id)


# ───────────────────────── Điểm lớp (GV nhập) ─────────────────────────
@bp.route("/teacher/classes/<int:cid>/grades", methods=["GET", "POST"])
@login_required
@teacher_required
def teacher_grades(cid):
    course = Course.query.get_or_404(cid)
    if not current_user.is_admin and course.teacher_id != current_user.id:
        flash("Không phải lớp của bạn.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    enrolls = (Enrollment.query.filter_by(course_id=cid)
               .filter(Enrollment.status != "dropped").all())
    FIXED = {"TX1": 20.0, "TX2": 20.0, "Thi": 60.0}

    def _rebalance(sid):
        """TX1+TX2+Thi luôn = 100. Thi = 100 - TX1 - TX2 khi đủ 3 cột."""
        grades = {g.title: g for g in CourseGrade.query.filter_by(
            course_id=cid, student_id=sid).all()}
        if "TX1" in grades and "TX2" in grades and "Thi" in grades:
            w1 = float(grades["TX1"].weight or 0)
            w2 = float(grades["TX2"].weight or 0)
            # giữ TX1, TX2; Thi nhận phần còn lại
            if w1 + w2 > 100:
                # scale TX1 TX2 về 100, Thi=0
                s = w1 + w2
                grades["TX1"].weight = round(w1 / s * 100, 1)
                grades["TX2"].weight = round(100 - grades["TX1"].weight, 1)
                grades["Thi"].weight = 0
            else:
                grades["Thi"].weight = round(100 - w1 - w2, 1)
        elif "TX1" in grades and "TX2" in grades:
            w1 = float(grades["TX1"].weight or 0)
            w2 = float(grades["TX2"].weight or 0)
            s = w1 + w2
            if s > 0 and abs(s - 100) > 0.05:
                grades["TX1"].weight = round(w1 / s * 100, 1)
                grades["TX2"].weight = round(100 - grades["TX1"].weight, 1)
        elif len(grades) == 1:
            only = next(iter(grades.values()))
            only.weight = 100.0

    if request.method == "POST":
        f = request.form
        action = f.get("action") or "save"
        if action == "delete_grade":
            gid = int(f.get("grade_id") or 0)
            g = CourseGrade.query.filter_by(id=gid, course_id=cid).first()
            if g:
                sid = g.student_id
                db.session.delete(g)
                db.session.flush()
                _rebalance(sid)
                db.session.commit()
                flash("Đã xoá điểm.", "info")
            return redirect(url_for("courses.teacher_grades", cid=cid))

        sid = int(f["student_id"])
        if not any(e.student_id == sid for e in enrolls):
            flash("Sinh viên không thuộc lớp.", "danger")
            return redirect(url_for("courses.teacher_grades", cid=cid))
        title = (f.get("title") or "").strip()
        if title not in FIXED:
            flash("Chỉ chọn TX1, TX2 hoặc Thi.", "danger")
            return redirect(url_for("courses.teacher_grades", cid=cid))

        existing = {g.title: g for g in CourseGrade.query.filter_by(
            course_id=cid, student_id=sid).all()}
        # Thi chỉ khi đã có TX1 và TX2
        if title == "Thi" and ("TX1" not in existing or "TX2" not in existing):
            flash("Chỉ nhập điểm **Thi** sau khi đã có đủ TX1 và TX2.", "danger")
            return redirect(url_for("courses.teacher_grades", cid=cid,
                                    student_id=sid, title=title))

        try:
            score = float(f["score"])
        except (KeyError, ValueError):
            flash("Điểm không hợp lệ.", "danger")
            return redirect(url_for("courses.teacher_grades", cid=cid))
        max_score = float(f.get("max_score") or 10)
        try:
            weight = float(f.get("weight") if f.get("weight") not in (None, "") else FIXED[title])
        except ValueError:
            weight = FIXED[title]
        weight = max(0.0, min(100.0, weight))
        taken = date.today()
        if f.get("taken_at"):
            try:
                taken = datetime.strptime(f["taken_at"], "%Y-%m-%d").date()
            except ValueError:
                pass

        old = existing.get(title)
        if old:
            old.score = score
            old.max_score = max_score
            old.weight = weight
            old.taken_at = taken
            msg = f"Đã cập nhật «{title}»."
        else:
            db.session.add(CourseGrade(
                course_id=cid, student_id=sid, title=title,
                kind="exam" if title == "Thi" else "quiz",
                score=score, max_score=max_score, weight=weight, taken_at=taken))
            msg = f"Đã lưu «{title}»."
        db.session.flush()
        _rebalance(sid)
        db.session.commit()
        # thông báo TS sau cân
        gs = CourseGrade.query.filter_by(course_id=cid, student_id=sid).all()
        parts = ", ".join(f"{g.title}={g.weight:.0f}%" for g in sorted(gs, key=lambda x: x.title))
        flash(msg + f" Trọng số: {parts} (tổng {sum(g.weight or 0 for g in gs):.0f}%).", "success")
        return redirect(url_for("courses.teacher_grades", cid=cid, student_id=sid))

    grades = CourseGrade.query.filter_by(course_id=cid).order_by(
        CourseGrade.student_id, CourseGrade.taken_at).all()
    by_student = {}
    for e in enrolls:
        by_student[e.student_id] = {
            "student": e.student, "grades": [], "avg": None,
            "has_tx1": False, "has_tx2": False}
    for g in grades:
        if g.student_id in by_student:
            by_student[g.student_id]["grades"].append(g)
            if g.title == "TX1":
                by_student[g.student_id]["has_tx1"] = True
            if g.title == "TX2":
                by_student[g.student_id]["has_tx2"] = True
    for sid, row in by_student.items():
        gs = row["grades"]
        if not gs:
            continue
        tw = sum(g.weight or 0 for g in gs)
        if tw > 0:
            row["avg"] = round(sum(g.score10 * (g.weight or 0) for g in gs) / tw, 2)
        else:
            row["avg"] = round(sum(g.score10 for g in gs) / len(gs), 2)

    edit = {
        "student_id": request.args.get("student_id", type=int),
        "title": request.args.get("title") or "TX1",
        "score": request.args.get("score") or "",
        "weight": request.args.get("weight") or "",
        "max_score": request.args.get("max_score") or "10",
        "taken_at": request.args.get("taken_at") or "",
        "grade_id": request.args.get("grade_id", type=int),
    }
    return render_template(
        "teacher_grades.html", course=course, enrolls=enrolls,
        by_student=by_student, money=_money, edit=edit, today=date.today())


@bp.route("/teacher/classes/<int:cid>/students/<int:sid>")
@login_required
@teacher_required
def teacher_student_detail(cid, sid):
    """GV xem hồ sơ SV trong lớp + điểm các lớp."""
    course = Course.query.get_or_404(cid)
    if not current_user.is_admin and course.teacher_id != current_user.id:
        flash("Không phải lớp của bạn.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    en = Enrollment.query.filter_by(course_id=cid, student_id=sid).filter(
        Enrollment.status != "dropped").first_or_404()
    student = en.student
    # điểm lớp hiện tại
    grades_here = CourseGrade.query.filter_by(
        course_id=cid, student_id=sid).order_by(CourseGrade.title).all()
    # điểm các lớp khác (cùng GV hoặc tất cả lớp SV đã học)
    other_enrolls = (Enrollment.query.filter_by(student_id=sid)
                     .filter(Enrollment.status != "dropped").all())
    other_data = []
    for e in other_enrolls:
        gs = CourseGrade.query.filter_by(
            course_id=e.course_id, student_id=sid).all()
        avg = None
        if gs:
            tw = sum(g.weight or 0 for g in gs)
            if tw > 0:
                avg = round(sum(g.score10 * (g.weight or 0) for g in gs) / tw, 2)
            else:
                avg = round(sum(g.score10 for g in gs) / len(gs), 2)
        other_data.append({"course": e.course, "grades": gs, "avg": avg, "enroll": e})
    atts = (Attendance.query.join(CourseSession)
            .filter(CourseSession.course_id == cid,
                    Attendance.student_id == sid).all())
    return render_template(
        "teacher_student_detail.html", course=course, student=student,
        enroll=en, grades_here=grades_here, other_data=other_data,
        attendances=atts, money=_money)


@bp.post("/teacher/grades/<int:gid>/delete")
@login_required
@teacher_required
def delete_grade(gid):
    g = CourseGrade.query.get_or_404(gid)
    cid = g.course_id
    if not current_user.is_admin and g.course.teacher_id != current_user.id:
        flash("Không có quyền.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    db.session.delete(g)
    db.session.commit()
    flash("Đã xoá điểm.", "info")
    return redirect(url_for("courses.teacher_grades", cid=cid))


# ───────────────────────── Điểm danh ─────────────────────────
@bp.route("/teacher/sessions/<int:sid>/attendance", methods=["GET", "POST"])
@login_required
@teacher_required
def session_attendance(sid):
    session = CourseSession.query.get_or_404(sid)
    course = session.course
    if not current_user.is_admin and course.teacher_id != current_user.id:
        flash("Không có quyền.", "danger")
        return redirect(url_for("courses.teacher_classes"))
    enrolls = (Enrollment.query.filter_by(course_id=course.id)
               .filter(Enrollment.status != "dropped").all())
    if request.method == "POST":
        for e in enrolls:
            st = request.form.get(f"st_{e.student_id}", "absent")
            if st not in ("present", "absent", "late", "excused"):
                st = "absent"
            note = (request.form.get(f"note_{e.student_id}") or "").strip() or None
            att = Attendance.query.filter_by(
                session_id=sid, student_id=e.student_id).first()
            if att:
                att.status = st
                att.note = note
                att.marked_at = datetime.utcnow()
                att.marked_by = current_user.id
            else:
                db.session.add(Attendance(
                    session_id=sid, student_id=e.student_id, status=st,
                    note=note, marked_by=current_user.id))
        db.session.commit()
        flash("Đã lưu điểm danh.", "success")
        return redirect(url_for("courses.session_attendance", sid=sid))

    existing = {a.student_id: a for a in session.attendances}
    return render_template(
        "teacher_attendance.html", course=course, session=session,
        enrolls=enrolls, existing=existing)


# SV xem điểm lớp + điểm danh
@bp.route("/my-results")
@login_required
def student_results():
    if current_user.is_teacher:
        return redirect(url_for("courses.teacher_classes"))
    enrolls = (Enrollment.query.filter_by(student_id=current_user.id)
               .filter(Enrollment.status != "dropped")
               .order_by(Enrollment.enrolled_at.desc()).all())
    data = []
    for e in enrolls:
        course = e.course
        if not course:
            continue
        grades = (CourseGrade.query.filter_by(course_id=e.course_id, student_id=current_user.id)
                  .order_by(CourseGrade.taken_at.asc(), CourseGrade.id.asc()).all())
        # TB có trọng số
        avg = None
        if grades:
            tw = sum((g.weight or 0) for g in grades)
            if tw > 0:
                avg = round(sum(g.score10 * (g.weight or 0) for g in grades) / tw, 2)
            else:
                avg = round(sum(g.score10 for g in grades) / len(grades), 2)
        session_ids = [s.id for s in course.sessions]
        atts = []
        if session_ids:
            atts = (Attendance.query.filter(
                Attendance.session_id.in_(session_ids),
                Attendance.student_id == current_user.id).all())
        present = sum(1 for a in atts if a.status in ("present", "late"))
        data.append({
            "enrollment": e,
            "course": course,
            "grades": grades,
            "avg": avg,
            "attendances": atts,
            "present": present,
            "total_att": len(atts),
        })
    # Dữ liệu biểu đồ: mỗi lớp 1 điểm TB
    chart_labels = [r["course"].name for r in data]
    chart_values = [r["avg"] if r["avg"] is not None else 0 for r in data]

    # GPA theo tín chỉ (thang 4.0 Việt Nam)
    def to_gpa4(score10):
        if score10 is None:
            return None
        if score10 >= 8.5: return 4.0
        if score10 >= 8.0: return 3.5
        if score10 >= 7.0: return 3.0
        if score10 >= 6.5: return 2.5
        if score10 >= 5.5: return 2.0
        if score10 >= 5.0: return 1.5
        if score10 >= 4.0: return 1.0
        return 0.0

    def classify(gpa):
        if gpa is None: return "—"
        if gpa >= 3.6: return "Xuất sắc"
        if gpa >= 3.2: return "Giỏi"
        if gpa >= 2.5: return "Khá"
        if gpa >= 2.0: return "Trung bình"
        if gpa >= 1.0: return "Yếu"
        return "Kém"

    total_credits = 0
    weighted = 0.0
    graded_courses = 0
    for r in data:
        if r["avg"] is None:
            r["gpa4"] = None
            r["credits"] = getattr(r["course"], "credits", None) or 3
            continue
        cr = getattr(r["course"], "credits", None) or 3
        g4 = to_gpa4(r["avg"])
        r["gpa4"] = g4
        r["credits"] = cr
        total_credits += cr
        weighted += g4 * cr
        graded_courses += 1
    gpa = round(weighted / total_credits, 2) if total_credits else None

    return render_template(
        "student_results.html", data=data,
        chart_labels=chart_labels, chart_values=chart_values,
        gpa=gpa, total_credits=total_credits,
        classification=classify(gpa), graded_courses=graded_courses)


@bp.route("/my-schedule")
@login_required
def student_schedule():
    """Lịch học các lớp đã đăng ký — xem theo danh sách hoặc theo tuần."""
    if current_user.is_teacher:
        return redirect(url_for("courses.teacher_classes"))
    view = request.args.get("view", "week")  # week | list
    # tuần: ?week=0 hiện tại, -1 tuần trước, +1 tuần sau
    try:
        week_offset = int(request.args.get("week", 0))
    except ValueError:
        week_offset = 0
    enrolls = (Enrollment.query.filter_by(student_id=current_user.id)
               .filter(Enrollment.status != "dropped").all())
    course_ids = [e.course_id for e in enrolls]
    sessions = []
    if course_ids:
        sessions = (CourseSession.query.filter(CourseSession.course_id.in_(course_ids))
                    .order_by(CourseSession.session_date, CourseSession.start_time).all())

    from collections import OrderedDict
    from datetime import timedelta
    today = date.today()
    # Thứ 2 của tuần hiện tại
    monday = today - timedelta(days=today.weekday())
    monday = monday + timedelta(weeks=week_offset)
    week_days = [monday + timedelta(days=i) for i in range(7)]  # T2..CN
    week_labels = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
    week_map = {d: [] for d in week_days}
    for s in sessions:
        if s.session_date in week_map:
            week_map[s.session_date].append(s)

    grouped = OrderedDict()
    for s in sessions:
        grouped.setdefault(s.session_date, []).append(s)

    return render_template(
        "student_schedule.html",
        view=view, grouped=grouped, today=today,
        week_days=week_days, week_labels=week_labels, week_map=week_map,
        week_offset=week_offset, monday=monday)


@bp.route("/my-deadlines")
@login_required
def student_deadlines():
    """Deadline / bài tập từ các lớp đã đăng ký."""
    if current_user.is_teacher:
        return redirect(url_for("courses.teacher_classes"))
    enrolls = (Enrollment.query.filter_by(student_id=current_user.id)
               .filter(Enrollment.status != "dropped").all())
    course_ids = [e.course_id for e in enrolls]
    items = []
    if course_ids:
        items = (CourseAssignment.query.filter(CourseAssignment.course_id.in_(course_ids))
                 .order_by(CourseAssignment.due_date.asc(), CourseAssignment.created_at.desc()).all())
    return render_template("student_deadlines.html", items=items, today=date.today())



# ───────────────────────── Admin: giao dịch & rút tiền GV ─────────────────────────
@bp.route("/admin/transactions")
@login_required
@admin_required
def admin_transactions():
    kind = request.args.get("kind")  # topup|pay_course|withdraw|all
    q = WalletTransaction.query.order_by(WalletTransaction.created_at.desc())
    if kind in ("topup", "pay_course", "withdraw", "refund", "adjust"):
        q = q.filter_by(kind=kind)
    txns = q.limit(200).all()
    # tổng nạp thành công
    topups = WalletTransaction.query.filter_by(kind="topup", status="success").all()
    sum_topup = sum(t.amount for t in topups)
    withdraws = WalletTransaction.query.filter_by(kind="withdraw", status="success").all()
    sum_withdraw = sum(abs(t.amount) for t in withdraws)
    return render_template(
        "admin_transactions.html", txns=txns, kind=kind, money=_money,
        sum_topup=sum_topup, sum_withdraw=sum_withdraw)


@bp.route("/admin/withdrawals", methods=["GET", "POST"])
@login_required
@admin_required
def admin_withdrawals():
    """Admin ghi nhận rút tiền cho giảng viên (chi trả từ doanh thu)."""
    teachers = User.query.filter_by(role="teacher").order_by(User.full_name).all()
    if request.method == "POST":
        tid = int(request.form.get("teacher_id") or 0)
        try:
            amount = float(request.form.get("amount") or 0)
        except ValueError:
            amount = 0
        note = (request.form.get("note") or "").strip() or None
        teacher = User.query.filter_by(id=tid, role="teacher").first()
        if not teacher or amount <= 0:
            flash("Chọn giảng viên và số tiền hợp lệ.", "danger")
            return redirect(url_for("courses.admin_withdrawals"))
        # ghi giao dịch rút (số âm trên ví GV — hoặc chỉ log)
        import uuid as _uuid
        wt = WalletTransaction(
            user_id=teacher.id, amount=-abs(amount), kind="withdraw",
            status="success", provider="admin",
            txn_ref=f"WD{_uuid.uuid4().hex[:12].upper()}",
            description=note or f"Rút tiền GV {teacher.full_name or teacher.username}",
            completed_at=datetime.utcnow(),
        )
        # trừ balance GV nếu có
        teacher.balance = (teacher.balance or 0) - abs(amount)
        db.session.add(wt)
        db.session.commit()
        flash(f"Đã ghi nhận rút {_money(amount)} cho {teacher.full_name or teacher.username}.", "success")
        return redirect(url_for("courses.admin_withdrawals"))
    history = (WalletTransaction.query.filter_by(kind="withdraw")
               .order_by(WalletTransaction.created_at.desc()).limit(50).all())
    return render_template(
        "admin_withdrawals.html", teachers=teachers, history=history, money=_money)


@bp.route("/admin/users/<int:uid>")
@login_required
@admin_required
def admin_user_detail(uid):
    user = User.query.get_or_404(uid)
    enrolls = (Enrollment.query.filter_by(student_id=uid)
               .order_by(Enrollment.enrolled_at.desc()).limit(50).all())
    teaching = Course.query.filter_by(teacher_id=uid).all()
    txns = (WalletTransaction.query.filter_by(user_id=uid)
            .order_by(WalletTransaction.created_at.desc()).limit(30).all())
    return render_template(
        "admin_user_detail.html", u=user, enrolls=enrolls,
        teaching=teaching, txns=txns, money=_money)


@bp.route("/teacher/wallet", methods=["GET", "POST"])
@login_required
@teacher_required
def teacher_wallet():
    """Giảng viên xem số dư và rút tiền (demo)."""
    if current_user.is_admin and not current_user.is_teacher:
        return redirect(url_for("courses.admin_withdrawals"))
    user = User.query.get(current_user.id)
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount") or 0)
        except ValueError:
            amount = 0
        bank = (request.form.get("bank") or "").strip()
        account = (request.form.get("account") or "").strip()
        if amount < 10000:
            flash("Số tiền rút tối thiểu 10.000 đ.", "warning")
            return redirect(url_for("courses.teacher_wallet"))
        bal = float(user.balance or 0)
        if amount > bal + 1e-6:
            flash(f"Số dư không đủ (còn {_money(bal)}).", "danger")
            return redirect(url_for("courses.teacher_wallet"))
        user.balance = bal - amount
        note = f"Rút về {bank or 'NH'} / {account or '***'}"
        wt = WalletTransaction(
            user_id=user.id, amount=-amount, kind="withdraw", status="success",
            provider="demo_bank", txn_ref=f"WD{uuid.uuid4().hex[:12].upper()}",
            description=note, completed_at=datetime.utcnow(),
        )
        db.session.add(wt)
        db.session.commit()
        flash(f"Đã rút {_money(amount)}. Số dư còn {_money(user.balance)}.", "success")
        return redirect(url_for("courses.teacher_wallet"))
    txns = (WalletTransaction.query.filter_by(user_id=user.id)
            .order_by(WalletTransaction.created_at.desc()).limit(30).all())
    # doanh thu lớp
    courses = Course.query.filter_by(teacher_id=user.id).all()
    revenue = sum(c.revenue for c in courses)
    return render_template(
        "teacher_wallet.html", balance=user.balance or 0, txns=txns,
        revenue=revenue, money=_money)


@bp.route("/my-materials")
@login_required
def student_materials():
    """Tài liệu từ các lớp SV đã đăng ký."""
    if current_user.is_teacher:
        return redirect(url_for("courses.teacher_classes"))
    enrolls = (Enrollment.query.filter_by(student_id=current_user.id)
               .filter(Enrollment.status != "dropped").all())
    course_ids = [e.course_id for e in enrolls]
    materials = []
    if course_ids:
        materials = (CourseMaterial.query.filter(
            CourseMaterial.course_id.in_(course_ids))
            .order_by(CourseMaterial.uploaded_at.desc()).all())
    return render_template("student_materials.html", materials=materials)
