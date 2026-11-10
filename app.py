from collections import OrderedDict
from datetime import date, datetime, time, timedelta

from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)

from config import Config
from models import (Deadline, DocChunk, Document, GradeComponent, Schedule,
                    Score, StudyPlan, Subject, User, db)
from services import analytics, chatbot, nlp, planner
from course_routes import bp as course_bp

WEEKDAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.login_message = "Vui lòng đăng nhập để tiếp tục."
    login_manager.login_message_category = "warning"
    login_manager.init_app(app)
    app.register_blueprint(course_bp)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    with app.app_context():
        db.create_all()

    @app.context_processor
    def inject_globals():
        subjects = []
        if current_user.is_authenticated:
            subjects = Subject.query.filter_by(user_id=current_user.id).order_by(Subject.name).all()
        return {"WEEKDAYS": WEEKDAYS, "today": date.today(), "all_subjects": subjects}

    # ============================================================ Auth
    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            f = request.form
            username = (f.get("username") or "").strip()
            email = (f.get("email") or "").strip().lower()
            password = f.get("password") or ""
            full_name = (f.get("full_name") or "").strip() or None
            phone = (f.get("phone") or "").strip() or None
            school = (f.get("school") or "").strip() or None
            major = (f.get("major") or "").strip() or None
            class_name = (f.get("class_name") or "").strip() or None
            student_id = (f.get("student_id") or "").strip() or None
            address = (f.get("address") or "").strip() or None
            bio = (f.get("bio") or "").strip() or None
            birth_raw = (f.get("birth_date") or "").strip()
            birth_date = None
            if birth_raw:
                try:
                    birth_date = datetime.strptime(birth_raw, "%Y-%m-%d").date()
                except ValueError:
                    flash("Ngày sinh không hợp lệ.", "danger")
                    return render_template("register.html")

            import re as _re
            email_ok = bool(_re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))
            phone_ok = (not phone) or bool(_re.match(r"^(0|\+84)[0-9]{8,10}$", phone.replace(" ", "").replace(".", "")))

            if not username or not email or not password:
                flash("Vui lòng điền đầy đủ thông tin bắt buộc (tên đăng nhập, email, mật khẩu).", "danger")
            elif not email_ok:
                flash("Email không hợp lệ.", "danger")
            elif not phone_ok:
                flash("Số điện thoại không hợp lệ (vd: 09xxxxxxxx hoặc +84...).", "danger")
            elif len(username) < 3:
                flash("Tên đăng nhập tối thiểu 3 ký tự.", "danger")
            elif len(password) < 6:
                flash("Mật khẩu phải có ít nhất 6 ký tự.", "danger")
            elif User.query.filter((User.username == username) | (User.email == email)).first():
                flash("Tên đăng nhập hoặc email đã tồn tại.", "danger")
            else:
                user = User(
                    username=username, email=email, full_name=full_name,
                    role="student",
                    phone=phone, school=school, major=major, class_name=class_name,
                    student_id=student_id, address=address, bio=bio,
                    birth_date=birth_date,
                )
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                flash("Đăng ký thành công! Hãy đăng nhập.", "success")
                return redirect(url_for("login"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            user = User.query.filter(
                (User.username == username) | (User.email == username)
            ).first()
            if user and user.check_password(password):
                login_user(user, remember=bool(request.form.get("remember")))
                flash(f"Xin chào {user.full_name or user.username}!", "success")
                next_url = request.args.get("next")
                return redirect(next_url or url_for("dashboard"))
            flash("Sai tên đăng nhập hoặc mật khẩu.", "danger")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("Đã đăng xuất.", "info")
        return redirect(url_for("login"))

    @app.route("/profile")
    @login_required
    def profile():
        return render_template("profile.html", user=current_user)

    @app.route("/profile/edit", methods=["GET", "POST"])
    @login_required
    def edit_profile():
        user = current_user
        if request.method == "POST":
            f = request.form
            full_name = (f.get("full_name") or "").strip() or None
            email = (f.get("email") or "").strip().lower()
            phone = (f.get("phone") or "").strip() or None
            school = (f.get("school") or "").strip() or None
            major = (f.get("major") or "").strip() or None
            class_name = (f.get("class_name") or "").strip() or None
            student_id = (f.get("student_id") or "").strip() or None
            address = (f.get("address") or "").strip() or None
            bio = (f.get("bio") or "").strip() or None
            birth_raw = (f.get("birth_date") or "").strip()

            if not email:
                flash("Email không được để trống.", "danger")
                return render_template("edit_profile.html", user=user)

            other = User.query.filter(User.email == email, User.id != user.id).first()
            if other:
                flash("Email đã được tài khoản khác sử dụng.", "danger")
                return render_template("edit_profile.html", user=user)

            user.full_name = full_name
            user.email = email
            user.phone = phone
            user.school = school
            user.major = major
            user.class_name = class_name
            user.student_id = student_id
            user.address = address
            user.bio = bio
            if birth_raw:
                try:
                    user.birth_date = datetime.strptime(birth_raw, "%Y-%m-%d").date()
                except ValueError:
                    flash("Ngày sinh không hợp lệ.", "danger")
                    return render_template("edit_profile.html", user=user)
            else:
                user.birth_date = None

            # Avatar
            from werkzeug.utils import secure_filename
            from pathlib import Path as _P
            av = request.files.get("avatar")
            if av and av.filename:
                ext = _P(secure_filename(av.filename)).suffix.lower()
                if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                    flash("Avatar chỉ nhận ảnh (jpg, png, gif, webp).", "danger")
                    return render_template("edit_profile.html", user=user)
                folder = _P("uploads") / "avatars"
                folder.mkdir(parents=True, exist_ok=True)
                fname = f"u{user.id}_{int(datetime.utcnow().timestamp())}{ext}"
                av.save(folder / fname)
                user.avatar = fname

            db.session.commit()
            flash("Đã cập nhật hồ sơ.", "success")
            return redirect(url_for("profile"))
        return render_template("edit_profile.html", user=user)

    @app.route("/avatar/<path:filename>")
    def serve_avatar(filename):
        from flask import send_from_directory
        from pathlib import Path as _P
        folder = _P("uploads") / "avatars"
        return send_from_directory(folder.resolve(), filename)

    @app.route("/profile/password", methods=["GET", "POST"])
    @login_required
    def change_password():
        if request.method == "POST":
            cur_pw = request.form.get("current_password") or ""
            new_pw = request.form.get("new_password") or ""
            confirm = request.form.get("confirm_password") or ""
            if not current_user.check_password(cur_pw):
                flash("Mật khẩu hiện tại không đúng.", "danger")
            elif len(new_pw) < 6:
                flash("Mật khẩu mới phải có ít nhất 6 ký tự.", "danger")
            elif new_pw != confirm:
                flash("Xác nhận mật khẩu không khớp.", "danger")
            else:
                current_user.set_password(new_pw)
                db.session.commit()
                flash("Đã đổi mật khẩu thành công.", "success")
                return redirect(url_for("profile"))
        return render_template("change_password.html")

    # ============================================================ Dashboard
    @app.route("/")
    @login_required
    def dashboard():
        if current_user.is_admin:
            return redirect(url_for("courses.admin_dashboard"))
        if current_user.is_teacher:
            return redirect(url_for("courses.teacher_classes"))
        # Sinh viên: dashboard dựa trên lớp đã đăng ký
        from models import CourseAssignment, CourseSession, Enrollment
        today = date.today()
        enrolls = (Enrollment.query.filter_by(student_id=current_user.id)
                   .filter(Enrollment.status != "dropped").all())
        course_ids = [e.course_id for e in enrolls]
        classes = []
        upcoming = []
        if course_ids:
            classes = (CourseSession.query.filter(
                CourseSession.course_id.in_(course_ids),
                CourseSession.session_date == today)
                .order_by(CourseSession.start_time).all())
            upcoming = (CourseAssignment.query.filter(
                CourseAssignment.course_id.in_(course_ids),
                CourseAssignment.due_date != None)
                .order_by(CourseAssignment.due_date).limit(5).all())
        unpaid_n = sum(1 for e in enrolls if e.payment_status == "unpaid")
        return render_template(
            "dashboard_student.html",
            enrolls=enrolls, classes=classes, upcoming=upcoming,
            unpaid_n=unpaid_n, balance=current_user.balance or 0,
            today=today)

    # ============================================================ Môn học
    @app.route("/subjects", methods=["GET", "POST"])
    @login_required
    def subjects():
        if current_user.is_student:
            return redirect(url_for("courses.class_list"))
        uid = current_user.id
        if request.method == "POST":
            f = request.form
            db.session.add(Subject(
                user_id=uid, name=f["name"].strip(), code=f.get("code"), teacher=f.get("teacher"),
                credits=int(f.get("credits") or 3), difficulty=int(f.get("difficulty") or 3),
                target_score=float(f.get("target_score") or 8)))
            db.session.commit()
            flash("Đã thêm môn học.", "success")
            return redirect(url_for("subjects"))
        return render_template("subjects.html",
                               items=Subject.query.filter_by(user_id=uid).order_by(Subject.name).all())

    @app.route("/subjects/<int:sid>/edit", methods=["GET", "POST"])
    @login_required
    def edit_subject(sid):
        item = Subject.query.filter_by(id=sid, user_id=current_user.id).first_or_404()
        if request.method == "POST":
            f = request.form
            item.name = f["name"].strip()
            item.code = f.get("code")
            item.teacher = f.get("teacher")
            item.credits = int(f.get("credits") or 3)
            item.difficulty = int(f.get("difficulty") or 3)
            item.target_score = float(f.get("target_score") or 8)
            db.session.commit()
            flash("Đã cập nhật môn học.", "success")
            return redirect(url_for("subjects"))
        return render_template("edit_subject.html", item=item)

    @app.post("/subjects/<int:sid>/delete")
    @login_required
    def delete_subject(sid):
        item = Subject.query.filter_by(id=sid, user_id=current_user.id).first_or_404()
        db.session.delete(item)
        db.session.commit()
        flash("Đã xoá môn học.", "info")
        return redirect(url_for("subjects"))

    # ---- Thành phần điểm của môn ----
    @app.route("/subjects/<int:sid>/components", methods=["GET", "POST"])
    @login_required
    def subject_components(sid):
        sub = Subject.query.filter_by(id=sid, user_id=current_user.id).first_or_404()
        if request.method == "POST":
            names = request.form.getlist("name")
            weights = request.form.getlist("weight")
            # Xoá cũ, ghi mới
            GradeComponent.query.filter_by(subject_id=sub.id).delete()
            total = 0.0
            for i, (n, w) in enumerate(zip(names, weights)):
                n = (n or "").strip()
                if not n:
                    continue
                try:
                    wv = float(w or 0)
                except ValueError:
                    wv = 0
                if wv < 0:
                    wv = 0
                db.session.add(GradeComponent(
                    subject_id=sub.id, name=n, weight=wv, sort_order=i))
                total += wv
            db.session.commit()
            if abs(total - 100) > 0.5 and total > 0:
                flash(f"Đã lưu thành phần điểm (tổng trọng số = {total:.0f}%). "
                      f"Nên đặt tổng = 100% để tính điểm chính xác.", "warning")
            else:
                flash("Đã cập nhật thành phần điểm.", "success")
            return redirect(url_for("subject_components", sid=sub.id))
        comps = GradeComponent.query.filter_by(subject_id=sub.id).order_by(
            GradeComponent.sort_order, GradeComponent.id).all()
        return render_template("subject_components.html", subject=sub, components=comps)

    # ============================================================ Lịch học
    @app.route("/schedule", methods=["GET", "POST"])
    @login_required
    def schedule():
        uid = current_user.id
        if request.method == "POST":
            f = request.form
            start = datetime.strptime(f["start_time"], "%H:%M").time()
            end = datetime.strptime(f["end_time"], "%H:%M").time()
            if end <= start:
                flash("Giờ kết thúc phải sau giờ bắt đầu.", "danger")
            else:
                # kiểm tra subject thuộc user
                sub = Subject.query.filter_by(id=int(f["subject_id"]), user_id=uid).first()
                if not sub:
                    flash("Môn học không hợp lệ.", "danger")
                else:
                    db.session.add(Schedule(
                        user_id=uid, subject_id=sub.id, weekday=int(f["weekday"]),
                        start_time=start, end_time=end, room=f.get("room")))
                    db.session.commit()
                    flash("Đã thêm lịch học.", "success")
            return redirect(url_for("schedule"))
        by_day = OrderedDict((i, []) for i in range(7))
        for s in Schedule.query.filter_by(user_id=uid).order_by(Schedule.weekday, Schedule.start_time).all():
            by_day[s.weekday].append(s)
        return render_template("schedule.html", by_day=by_day)

    @app.route("/schedule/<int:sid>/edit", methods=["GET", "POST"])
    @login_required
    def edit_schedule(sid):
        item = Schedule.query.filter_by(id=sid, user_id=current_user.id).first_or_404()
        if request.method == "POST":
            f = request.form
            start = datetime.strptime(f["start_time"], "%H:%M").time()
            end = datetime.strptime(f["end_time"], "%H:%M").time()
            if end <= start:
                flash("Giờ kết thúc phải sau giờ bắt đầu.", "danger")
            else:
                sub = Subject.query.filter_by(id=int(f["subject_id"]), user_id=current_user.id).first()
                if not sub:
                    flash("Môn học không hợp lệ.", "danger")
                else:
                    item.subject_id = sub.id
                    item.weekday = int(f["weekday"])
                    item.start_time = start
                    item.end_time = end
                    item.room = f.get("room")
                    db.session.commit()
                    flash("Đã cập nhật lịch học.", "success")
                    return redirect(url_for("schedule"))
        return render_template("edit_schedule.html", item=item)

    @app.post("/schedule/<int:sid>/delete")
    @login_required
    def delete_schedule(sid):
        item = Schedule.query.filter_by(id=sid, user_id=current_user.id).first_or_404()
        db.session.delete(item)
        db.session.commit()
        return redirect(url_for("schedule"))

    # ============================================================ Deadline
    @app.route("/deadlines", methods=["GET", "POST"])
    @login_required
    def deadlines():
        uid = current_user.id
        if request.method == "POST":
            f = request.form
            sub = Subject.query.filter_by(id=int(f["subject_id"]), user_id=uid).first()
            if not sub:
                flash("Môn học không hợp lệ.", "danger")
            else:
                db.session.add(Deadline(
                    user_id=uid, subject_id=sub.id, title=f["title"].strip(),
                    due_date=datetime.strptime(f["due_date"], "%Y-%m-%d").date(),
                    est_hours=float(f.get("est_hours") or 2), priority=int(f.get("priority") or 2)))
                db.session.commit()
                flash("Đã thêm deadline. Hãy vào mục 'Kế hoạch' để sinh lại lịch học.", "success")
            return redirect(url_for("deadlines"))
        items = Deadline.query.filter_by(user_id=uid).order_by(Deadline.done, Deadline.due_date).all()
        return render_template("deadlines.html", items=items)

    @app.route("/deadlines/<int:did>/edit", methods=["GET", "POST"])
    @login_required
    def edit_deadline(did):
        item = Deadline.query.filter_by(id=did, user_id=current_user.id).first_or_404()
        if request.method == "POST":
            f = request.form
            sub = Subject.query.filter_by(id=int(f["subject_id"]), user_id=current_user.id).first()
            if not sub:
                flash("Môn học không hợp lệ.", "danger")
            else:
                item.subject_id = sub.id
                item.title = f["title"].strip()
                item.due_date = datetime.strptime(f["due_date"], "%Y-%m-%d").date()
                item.est_hours = float(f.get("est_hours") or 2)
                item.priority = int(f.get("priority") or 2)
                db.session.commit()
                flash("Đã cập nhật deadline.", "success")
                return redirect(url_for("deadlines"))
        return render_template("edit_deadline.html", item=item)

    @app.post("/deadlines/<int:did>/toggle")
    @login_required
    def toggle_deadline(did):
        d = Deadline.query.filter_by(id=did, user_id=current_user.id).first_or_404()
        d.done = not d.done
        db.session.commit()
        return redirect(url_for("deadlines"))

    @app.post("/deadlines/<int:did>/delete")
    @login_required
    def delete_deadline(did):
        item = Deadline.query.filter_by(id=did, user_id=current_user.id).first_or_404()
        db.session.delete(item)
        db.session.commit()
        return redirect(url_for("deadlines"))

    # ============================================================ Kế hoạch học tập
    @app.route("/plan")
    @login_required
    def plan():
        items = (StudyPlan.query.filter_by(user_id=current_user.id)
                 .filter(StudyPlan.date >= date.today())
                 .order_by(StudyPlan.date, StudyPlan.kind).all())
        grouped = OrderedDict()
        for p in items:
            grouped.setdefault(p.date, []).append(p)
        return render_template("plan.html", grouped=grouped)

    @app.post("/plan/generate")
    @login_required
    def generate_plan():
        hours = float(request.form.get("daily_hours") or Config.DAILY_STUDY_HOURS)
        horizon = int(request.form.get("horizon") or 14)
        res = planner.generate_plan(user_id=current_user.id, daily_hours=hours, horizon=horizon)
        flash(f"Đã lập kế hoạch: {res['deadline_sessions']} buổi làm deadline, "
              f"{res['review_sessions']} buổi ôn tập.", "success")
        for w in res["warnings"]:
            flash(w, "warning")
        return redirect(url_for("plan"))

    @app.post("/plan/<int:pid>/toggle")
    @login_required
    def toggle_plan(pid):
        p = StudyPlan.query.filter_by(id=pid, user_id=current_user.id).first_or_404()
        p.done = not p.done
        db.session.commit()
        return redirect(request.referrer or url_for("plan"))

    # ============================================================ Kết quả học tập
    @app.route("/scores", methods=["GET", "POST"])
    @login_required
    def scores():
        uid = current_user.id
        # Sinh viên chỉ xem điểm lớp ở /my-results; điểm cá nhân cũ: chỉ xem
        if request.method == "POST" and current_user.is_student:
            flash("Sinh viên không được tự nhập điểm. Điểm do giảng viên cập nhật.", "warning")
            return redirect(url_for("courses.student_results"))
        if request.method == "POST":
            f = request.form
            sub = Subject.query.filter_by(id=int(f["subject_id"]), user_id=uid).first()
            if not sub:
                flash("Môn học không hợp lệ.", "danger")
            else:
                try:
                    comp_id = int(f["component_id"]) if f.get("component_id") else None
                except (TypeError, ValueError):
                    comp_id = None
                comp = None
                if comp_id:
                    comp = GradeComponent.query.filter_by(id=comp_id, subject_id=sub.id).first()
                    if not comp:
                        comp_id = None
                weight = None
                if f.get("weight"):
                    try:
                        weight = float(f["weight"])
                    except ValueError:
                        weight = None
                exam_name = (f.get("exam_name") or "").strip()
                if not exam_name and comp:
                    exam_name = comp.name
                score_val = float(f["score"])
                max_score = float(f.get("max_score") or 10)
                taken_at = datetime.strptime(f["taken_at"], "%Y-%m-%d").date()
                topic = (f.get("topic") or "").strip() or None

                # Trùng thành phần (vd TX1 nhập 2 lần) → cập nhật bản ghi cũ, không cộng dồn
                existing = None
                if comp_id:
                    existing = (Score.query.filter_by(
                        user_id=uid, subject_id=sub.id, component_id=comp_id
                    ).order_by(Score.taken_at.desc(), Score.id.desc()).first())

                if existing:
                    old_score = existing.score
                    existing.exam_name = exam_name or existing.exam_name
                    existing.topic = topic
                    existing.score = score_val
                    existing.max_score = max_score
                    existing.weight = weight
                    existing.taken_at = taken_at
                    db.session.commit()
                    cname = comp.name if comp else existing.exam_name
                    flash(
                        f"Thành phần «{cname}» đã có điểm ({old_score}). "
                        f"Đã cập nhật thành {score_val} (không cộng dồn).",
                        "warning",
                    )
                else:
                    db.session.add(Score(
                        user_id=uid, subject_id=sub.id, component_id=comp_id,
                        exam_name=exam_name or "Điểm",
                        topic=topic, score=score_val, max_score=max_score,
                        weight=weight, taken_at=taken_at))
                    db.session.commit()
                    flash("Đã lưu điểm.", "success")
            return redirect(url_for("scores"))
        stats = analytics.subject_stats(uid)
        # components map for form
        comp_map = {}
        for s in Subject.query.filter_by(user_id=uid).all():
            comp_map[s.id] = [{"id": c.id, "name": c.name, "weight": c.weight}
                              for c in s.components]
        history = (Score.query.filter_by(user_id=uid)
                   .order_by(Score.taken_at.desc(), Score.id.desc()).limit(50).all())
        # id điểm mới nhất theo (subject_id, component_id) — dùng đánh dấu bản cũ
        latest_component_score_ids = set()
        seen_comp = set()
        for h in history:
            if not h.component_id:
                continue
            key = (h.subject_id, h.component_id)
            if key not in seen_comp:
                seen_comp.add(key)
                latest_component_score_ids.add(h.id)
        return render_template("scores.html", stats=stats, summary=analytics.overall_summary(stats),
                               history=history, comp_map=comp_map,
                               latest_component_score_ids=latest_component_score_ids)

    @app.route("/scores/<int:sid>/edit", methods=["GET", "POST"])
    @login_required
    def edit_score(sid):
        if current_user.is_student:
            flash("Sinh viên chỉ được xem điểm.", "warning")
            return redirect(url_for("courses.student_results"))
        item = Score.query.filter_by(id=sid, user_id=current_user.id).first_or_404()
        if request.method == "POST":
            f = request.form
            sub = Subject.query.filter_by(id=int(f["subject_id"]), user_id=current_user.id).first()
            if not sub:
                flash("Môn học không hợp lệ.", "danger")
            else:
                try:
                    comp_id = int(f["component_id"]) if f.get("component_id") else None
                except (TypeError, ValueError):
                    comp_id = None
                if comp_id:
                    comp = GradeComponent.query.filter_by(id=comp_id, subject_id=sub.id).first()
                    if not comp:
                        comp_id = None
                weight = None
                if f.get("weight"):
                    try:
                        weight = float(f["weight"])
                    except ValueError:
                        weight = None
                item.subject_id = sub.id
                item.component_id = comp_id
                item.exam_name = f["exam_name"].strip() or (item.exam_name)
                item.topic = (f.get("topic") or "").strip() or None
                item.score = float(f["score"])
                item.max_score = float(f.get("max_score") or 10)
                item.weight = weight
                item.taken_at = datetime.strptime(f["taken_at"], "%Y-%m-%d").date()
                db.session.commit()
                flash("Đã cập nhật điểm.", "success")
                return redirect(url_for("scores"))
        comps = GradeComponent.query.filter_by(subject_id=item.subject_id).order_by(
            GradeComponent.sort_order).all()
        return render_template("edit_score.html", item=item, components=comps)

    @app.post("/scores/<int:sid>/delete")
    @login_required
    def delete_score(sid):
        if current_user.is_student:
            flash("Sinh viên chỉ được xem điểm.", "warning")
            return redirect(url_for("courses.student_results"))
        item = Score.query.filter_by(id=sid, user_id=current_user.id).first_or_404()
        db.session.delete(item)
        db.session.commit()
        return redirect(url_for("scores"))

    # ============================================================ Gợi ý ôn tập
    @app.route("/recommend")
    @login_required
    def recommend():
        sid = request.args.get("subject_id", type=int)
        recs = analytics.recommendations(current_user.id, limit=10, subject_id=sid)
        for r in recs:
            r["material"] = chatbot.related_material(r["topic"], r["subject"].id, current_user.id)
        return render_template("recommend.html", recs=recs, selected=sid)

    # ============================================================ Tài liệu + Chatbot
    @app.route("/documents", methods=["GET", "POST"])
    @login_required
    def documents():
        uid = current_user.id
        if request.method == "POST":
            sid = request.form.get("subject_id", type=int)
            if sid:
                sub = Subject.query.filter_by(id=sid, user_id=uid).first()
                if not sub:
                    sid = None
            file = request.files.get("file")
            pasted = (request.form.get("text") or "").strip()
            try:
                if file and file.filename:
                    text, name = nlp.extract_text(file), file.filename
                elif pasted:
                    text, name = pasted, request.form.get("title") or "Ghi chú"
                else:
                    raise ValueError("Hãy chọn file hoặc dán nội dung.")
                chunks = nlp.chunk_text(text)
                if not chunks:
                    raise ValueError("Không trích xuất được nội dung văn bản.")
                doc = Document(user_id=uid, subject_id=sid, filename=name)
                doc.chunks = [DocChunk(text=c) for c in chunks]
                db.session.add(doc)
                db.session.commit()
                flash(f"Đã nạp '{name}' ({len(chunks)} đoạn).", "success")
            except Exception as e:
                flash(str(e), "danger")
            return redirect(url_for("documents"))
        return render_template("documents.html",
                               items=Document.query.filter_by(user_id=uid)
                               .order_by(Document.uploaded_at.desc()).all())

    @app.route("/documents/<int:did>")
    @login_required
    def view_document(did):
        item = Document.query.filter_by(id=did, user_id=current_user.id).first_or_404()
        chunks = (DocChunk.query.filter_by(document_id=item.id)
                  .order_by(DocChunk.id).all())
        return render_template("view_document.html", doc=item, chunks=chunks)

    @app.post("/documents/<int:did>/delete")
    @login_required
    def delete_document(did):
        item = Document.query.filter_by(id=did, user_id=current_user.id).first_or_404()
        db.session.delete(item)
        db.session.commit()
        return redirect(url_for("documents"))

    @app.route("/chat")
    @login_required
    def chat():
        return render_template("chat.html")

    @app.post("/api/chat")
    @login_required
    def api_chat():
        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"answer": "Bạn hãy nhập câu hỏi nhé.", "sources": []}), 400
        return jsonify(chatbot.reply(question, current_user.id, data.get("subject_id") or None))

    # ============================================================ REST nhỏ
    @app.get("/api/stats")
    @login_required
    def api_stats():
        return jsonify([{"subject": s["subject"].name, "avg10": s["avg10"], "level": s["level"],
                         "trend": s["trend"], "predicted10": s["pred10"]}
                        for s in analytics.subject_stats(current_user.id)])


    @app.cli.command("seed")
    def seed():
        """flask --app app seed  -> tạo admin, GV, SV mẫu (CSDL mới)."""
        if User.query.filter_by(username="admin").first():
            print("Đã có dữ liệu (user admin tồn tại) — bỏ qua seed.")
            return

        from datetime import time as dtime
        from models import Course, CourseAssignment, CourseSession

        admin = User(
            username="admin", email="admin@studyai.vn",
            full_name="Nguyễn Quản Trị", role="admin",
            phone="0900000001", school="StudyAI")
        admin.set_password("admin123")

        gv1 = User(
            username="gv.an", email="an.nguyen@studyai.vn",
            full_name="ThS. Nguyễn Văn An", role="teacher",
            phone="0901111001", school="ĐH Công nghệ", major="CNTT")
        gv1.set_password("gv123456")
        gv2 = User(
            username="gv.binh", email="binh.tran@studyai.vn",
            full_name="TS. Trần Thị Bình", role="teacher",
            phone="0901111002", school="ĐH Công nghệ", major="Toán ứng dụng")
        gv2.set_password("gv123456")

        students = []
        student_specs = [
            ("sv.lan", "lan.pham@student.vn", "Phạm Thị Lan", "0902222001", "SV001", "CNTT", "CTK46A"),
            ("sv.hung", "hung.le@student.vn", "Lê Văn Hùng", "0902222002", "SV002", "CNTT", "CTK46A"),
            ("sv.mai", "mai.vo@student.vn", "Võ Thị Mai", "0902222003", "SV003", "HTTT", "CTK46B"),
            ("sv.duc", "duc.hoang@student.vn", "Hoàng Anh Đức", "0902222004", "SV004", "CNTT", "CTK46B"),
        ]
        for un, em, name, phone, mssv, major, lop in student_specs:
            u = User(
                username=un, email=em, full_name=name, role="student",
                phone=phone, student_id=mssv, school="ĐH Công nghệ",
                major=major, class_name=lop)
            u.set_password("sv123456")
            students.append(u)

        db.session.add_all([admin, gv1, gv2] + students)
        db.session.flush()

        c1 = Course(
            teacher_id=gv1.id, name="Lập trình Python", code="PY101",
            description="Python cơ bản đến nâng cao, thực hành hàng tuần.",
            fee=1500000, credits=3, max_students=40, status="open",
            start_date=date.today(), end_date=date.today() + timedelta(days=90))
        c2 = Course(
            teacher_id=gv2.id, name="Toán rời rạc", code="MATH201",
            description="Logic, tập hợp, quan hệ, đồ thị.",
            fee=1200000, credits=3, max_students=35, status="open",
            start_date=date.today(), end_date=date.today() + timedelta(days=90))
        db.session.add_all([c1, c2])
        db.session.flush()

        db.session.add_all([
            CourseSession(course_id=c1.id, session_date=date.today() + timedelta(days=2),
                          start_time=dtime(18, 0), end_time=dtime(20, 0),
                          room="Lab 201", topic="Giới thiệu Python"),
            CourseSession(course_id=c1.id, session_date=date.today() + timedelta(days=9),
                          start_time=dtime(18, 0), end_time=dtime(20, 0),
                          room="Lab 201", topic="Hàm & module"),
            CourseSession(course_id=c2.id, session_date=date.today() + timedelta(days=3),
                          start_time=dtime(13, 0), end_time=dtime(15, 0),
                          room="A305", topic="Mệnh đề logic"),
            CourseAssignment(course_id=c1.id, title="Bài tập tuần 1", kind="homework",
                             due_date=date.today() + timedelta(days=7)),
            CourseAssignment(course_id=c2.id, title="Bài tập logic", kind="homework",
                             due_date=date.today() + timedelta(days=10)),
        ])
        db.session.commit()
        print("Đã khởi tạo CSDL:")
        print("  admin     / admin123")
        print("  gv.an     / gv123456   (Giảng viên)")
        print("  gv.binh   / gv123456   (Giảng viên)")
        print("  sv.lan    / sv123456   (Sinh viên)")
        print("  sv.hung   / sv123456")
        print("  sv.mai    / sv123456")
        print("  sv.duc    / sv123456")
        print("  2 lớp mở: PY101, MATH201")


    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
