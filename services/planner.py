"""Tự động lập kế hoạch học tập.

Thuật toán (heuristic dựa trên Earliest-Deadline-First):
1. Sức chứa mỗi ngày = giờ tự học mong muốn - 50% số giờ trên lớp của ngày đó.
2. Duyệt các deadline theo (hạn nộp gần nhất, ưu tiên cao, môn khó).
3. Mỗi deadline được chia đều cho các ngày còn lại trước hạn (lượt 1),
   phần còn thiếu được lấp vào các ngày còn chỗ (lượt 2).
4. Giờ dư được dùng cho buổi ôn tập các chủ đề yếu (từ module analytics).
"""
from collections import defaultdict
from datetime import date, timedelta

from config import Config
from models import Deadline, Schedule, StudyPlan, db
from services import analytics


def _round_half(x: float) -> float:
    return round(x * 2) / 2


def generate_plan(user_id, daily_hours=None, horizon=14, today=None):
    today = today or date.today()
    daily_hours = daily_hours or Config.DAILY_STUDY_HOURS
    warnings = []

    # Xoá phần kế hoạch chưa hoàn thành từ hôm nay trở đi để sinh lại
    StudyPlan.query.filter(
        StudyPlan.user_id == user_id,
        StudyPlan.date >= today,
        StudyPlan.done.is_(False)
    ).delete()
    db.session.flush()

    days = [today + timedelta(i) for i in range(horizon)]

    # --- 1. Sức chứa mỗi ngày ---
    cap = {}
    for d in days:
        class_h = sum(s.hours for s in Schedule.query.filter_by(user_id=user_id, weekday=d.weekday()))
        done_h = sum(p.hours for p in StudyPlan.query.filter_by(user_id=user_id, date=d, done=True))
        cap[d] = max(max(daily_hours - 0.5 * class_h, 1.0) - done_h, 0.0)

    # --- 2. Sắp xếp deadline ---
    tasks = (Deadline.query.filter(
        Deadline.user_id == user_id,
        Deadline.done.is_(False),
        Deadline.due_date >= today
    ).all())
    tasks.sort(key=lambda t: (t.due_date, -(t.priority or 2), -(t.subject.difficulty or 3)))

    alloc = defaultdict(float)  # (ngày, deadline_id) -> giờ
    for t in tasks:
        already = sum(p.hours for p in StudyPlan.query.filter_by(
            user_id=user_id, deadline_id=t.id, done=True))
        remaining = max((t.est_hours or 1) - already, 0)
        if remaining <= 0:
            continue
        window = [d for d in days if d < t.due_date] or [today]
        span = max((t.due_date - today).days, 1)
        per_day = remaining / span

        # Lượt 1: chia đều
        for d in window:
            h = min(_round_half(per_day) or 0.5, cap[d], remaining)
            h = _round_half(h)
            if h > 0:
                alloc[(d, t.id)] += h
                cap[d] -= h
                remaining -= h
        # Lượt 2: lấp phần còn thiếu vào ngày còn chỗ (ưu tiên ngày gần hạn)
        for d in reversed(window):
            if remaining <= 0.25:
                break
            h = _round_half(min(cap[d], remaining))
            if h > 0:
                alloc[(d, t.id)] += h
                cap[d] -= h
                remaining -= h
        # Chỉ cảnh báo nếu toàn bộ thời gian tới hạn nằm trong tầm nhìn
        if remaining > 0.25 and t.due_date <= days[-1]:
            warnings.append(f"Quá tải: '{t.title}' ({t.subject.name}) còn thiếu "
                            f"{remaining:.1f}h trước hạn {t.due_date:%d/%m}.")

    by_id = {t.id: t for t in tasks}
    for (d, tid), h in alloc.items():
        t = by_id[tid]
        db.session.add(StudyPlan(
            user_id=user_id, date=d, subject_id=t.subject_id, deadline_id=tid,
            hours=h, kind="deadline", note=t.title))

    # --- 4. Buổi ôn tập chủ đề yếu bằng giờ dư ---
    weak = analytics.weak_topics(user_id, limit=5)
    review_count = 0
    if weak:
        i = 0
        for d in days[:7]:
            if cap[d] >= 1:
                w = weak[i % len(weak)]
                db.session.add(StudyPlan(
                    user_id=user_id, date=d, subject_id=w["subject"].id, hours=1.0,
                    kind="review", note=f"Ôn tập: {w['topic']}"))
                cap[d] -= 1
                review_count += 1
                i += 1

    db.session.commit()
    return {"deadline_sessions": len(alloc), "review_sessions": review_count,
            "warnings": warnings}
