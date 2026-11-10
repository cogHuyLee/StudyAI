"""Phân tích kết quả học tập + gợi ý nội dung ôn tập.
Hỗ trợ trọng số thành phần điểm theo từng môn và GPA / xếp loại tốt nghiệp.
"""
from collections import defaultdict

import numpy as np
from sklearn.linear_model import LinearRegression

from config import Config
from models import GradeComponent, Score, Subject


# ---- Thang điểm & xếp loại tốt nghiệp (chuẩn phổ biến ĐH Việt Nam) ----

def score10_to_grade_point(score10: float) -> float:
    """Quy đổi điểm hệ 10 -> điểm chữ hệ 4 (GPA)."""
    if score10 is None:
        return 0.0
    if score10 >= 9.0:
        return 4.0
    if score10 >= 8.5:
        return 3.7
    if score10 >= 8.0:
        return 3.5
    if score10 >= 7.0:
        return 3.0
    if score10 >= 6.5:
        return 2.5
    if score10 >= 5.5:
        return 2.0
    if score10 >= 5.0:
        return 1.5
    if score10 >= 4.0:
        return 1.0
    return 0.0


def score10_to_letter(score10: float) -> str:
    if score10 is None:
        return "-"
    if score10 >= 9.0:
        return "A+"
    if score10 >= 8.5:
        return "A"
    if score10 >= 8.0:
        return "B+"
    if score10 >= 7.0:
        return "B"
    if score10 >= 6.5:
        return "C+"
    if score10 >= 5.5:
        return "C"
    if score10 >= 5.0:
        return "D+"
    if score10 >= 4.0:
        return "D"
    return "F"


def level(score10: float) -> str:
    """Xếp loại môn (hệ 10)."""
    if score10 is None:
        return "-"
    if score10 >= 9.0:
        return "Xuất sắc"
    if score10 >= 8.0:
        return "Giỏi"
    if score10 >= 7.0:
        return "Khá"
    if score10 >= 5.0:
        return "Trung bình"
    if score10 >= 4.0:
        return "Yếu"
    return "Kém"


def graduation_rank(gpa4: float) -> str:
    """Xếp loại tốt nghiệp theo GPA hệ 4 (tham chiếu quy chế phổ biến)."""
    if gpa4 is None:
        return "Chưa đủ dữ liệu"
    if gpa4 >= 3.6:
        return "Xuất sắc"
    if gpa4 >= 3.2:
        return "Giỏi"
    if gpa4 >= 2.5:
        return "Khá"
    if gpa4 >= 2.0:
        return "Trung bình"
    return "Yếu / Chưa đạt"


def _fit_trend(values):
    if len(values) < 2:
        return 0.0, float(values[-1]) if values else 0.0
    X = np.arange(len(values)).reshape(-1, 1)
    model = LinearRegression().fit(X, np.array(values))
    slope = float(model.coef_[0])
    pred = float(np.clip(model.predict([[len(values)]])[0], 0, 10))
    return slope, pred


def _subject_final_score(subject, scores):
    """
    Tính điểm tổng kết môn (thang 10) theo trọng số thành phần.

    Ưu tiên:
    1. Nếu môn có GradeComponent và điểm gắn component → weighted by component.weight
    2. Nếu điểm có field weight → dùng weight đó
    3. Fallback: trung bình cộng đơn giản
    """
    if not scores:
        return None, [], 0.0  # final, breakdown, total_weight_used

    # Group by component (lấy điểm mới nhất mỗi component)
    by_comp = {}
    no_comp = []
    for s in sorted(scores, key=lambda x: (x.taken_at, x.id)):
        if s.component_id:
            by_comp[s.component_id] = s
        else:
            no_comp.append(s)

    components = list(subject.components) if subject.components else []
    breakdown = []
    weighted_sum = 0.0
    total_w = 0.0

    if components:
        # Dùng cấu hình trọng số của môn
        for comp in components:
            s = by_comp.get(comp.id)
            w = comp.weight or 0
            if s is not None:
                sc10 = s.score10
                weighted_sum += sc10 * w
                total_w += w
                breakdown.append({
                    "name": comp.name, "weight": w, "score10": round(sc10, 2),
                    "score": s.score, "max_score": s.max_score,
                    "exam_name": s.exam_name, "has_score": True,
                })
            else:
                breakdown.append({
                    "name": comp.name, "weight": w, "score10": None,
                    "score": None, "max_score": None,
                    "exam_name": None, "has_score": False,
                })
        # Điểm lẻ không gắn component (nếu còn)
        for s in no_comp:
            w = s.weight or 0
            if w > 0:
                sc10 = s.score10
                weighted_sum += sc10 * w
                total_w += w
                breakdown.append({
                    "name": s.exam_name, "weight": w, "score10": round(sc10, 2),
                    "score": s.score, "max_score": s.max_score,
                    "exam_name": s.exam_name, "has_score": True,
                })
    else:
        # Không có cấu hình component → dùng weight trên từng Score, hoặc TB đều
        has_any_weight = any((s.weight or 0) > 0 for s in scores)
        if has_any_weight:
            for s in scores:
                w = s.weight or 0
                if w <= 0:
                    continue
                sc10 = s.score10
                weighted_sum += sc10 * w
                total_w += w
                breakdown.append({
                    "name": s.exam_name, "weight": w, "score10": round(sc10, 2),
                    "score": s.score, "max_score": s.max_score,
                    "exam_name": s.exam_name, "has_score": True,
                })
        else:
            # Trung bình cộng
            vals = [s.score10 for s in scores]
            avg = float(np.mean(vals))
            for s in scores:
                breakdown.append({
                    "name": s.exam_name, "weight": round(100 / len(scores), 1),
                    "score10": round(s.score10, 2),
                    "score": s.score, "max_score": s.max_score,
                    "exam_name": s.exam_name, "has_score": True,
                })
            return round(avg, 2), breakdown, 100.0

    if total_w <= 0:
        return None, breakdown, 0.0
    # Chuẩn hoá nếu tổng trọng số != 100 (ví dụ mới nhập 1 phần)
    final = weighted_sum / total_w * (100.0 / 100.0)  # đã là tỷ lệ
    # Nếu muốn giữ nguyên tỷ lệ % đã nhập:
    final = weighted_sum / total_w * 10 / 10  # weighted_sum/total_w đã là điểm 10
    return round(final, 2), breakdown, total_w


def subject_stats(user_id):
    """Thống kê theo từng môn: điểm TK có trọng số, GPA môn, xu hướng, dự đoán."""
    result = []
    for sub in Subject.query.filter_by(user_id=user_id).order_by(Subject.name).all():
        scores = (Score.query.filter_by(user_id=user_id, subject_id=sub.id)
                  .order_by(Score.taken_at, Score.id).all())
        final10, breakdown, total_w = _subject_final_score(sub, scores)

        row = {
            "subject": sub, "n": len(scores), "avg10": final10, "level": level(final10),
            "letter": score10_to_letter(final10) if final10 is not None else "-",
            "grade_point": score10_to_grade_point(final10) if final10 is not None else None,
            "trend": "Chưa có dữ liệu", "slope": 0, "pred10": None,
            "gap": None, "labels": [], "values": [],
            "breakdown": breakdown, "total_weight": total_w,
            "components": list(sub.components),
        }
        if scores:
            # Xu hướng theo thời gian (điểm từng lần, thang 10)
            vals = [s.score10 for s in scores]
            slope, pred = _fit_trend(vals)
            row.update(
                slope=slope, pred10=round(pred, 2),
                trend=("Tiến bộ" if slope > 0.3 else "Giảm sút" if slope < -0.3 else "Ổn định")
                if len(vals) >= 2 else "Cần thêm dữ liệu",
                gap=round((sub.target_score or 0) - (final10 or pred), 2) if final10 is not None else None,
                labels=[f"{s.exam_name} ({s.taken_at:%d/%m})" for s in scores],
                values=[round(v, 2) for v in vals],
            )
        result.append(row)
    return result


def overall_summary(stats):
    """GPA tích lũy (hệ 4 & hệ 10) có trọng số tín chỉ + xếp loại tốt nghiệp."""
    valid = [s for s in stats if s["avg10"] is not None and s["grade_point"] is not None]
    if not valid:
        return None

    credits = [s["subject"].credits or 1 for s in valid]
    total_credits = sum(credits)

    # GPA hệ 4 (chuẩn)
    gpa4 = float(np.average([s["grade_point"] for s in valid], weights=credits))
    # Điểm TB hệ 10 có trọng số tín chỉ
    gpa10 = float(np.average([s["avg10"] for s in valid], weights=credits))

    best = max(valid, key=lambda s: s["avg10"])
    worst = min(valid, key=lambda s: s["avg10"])

    rank = graduation_rank(gpa4)

    # Ước lượng nếu hoàn thành tất cả môn với điểm mục tiêu
    all_subjects = stats
    projected = []
    for s in all_subjects:
        if s["avg10"] is not None:
            projected.append((s["avg10"], s["grade_point"], s["subject"].credits or 1))
        else:
            target = s["subject"].target_score or 7.0
            projected.append((target, score10_to_grade_point(target), s["subject"].credits or 1))
    if projected:
        pc = [p[2] for p in projected]
        proj_gpa4 = float(np.average([p[1] for p in projected], weights=pc))
        proj_gpa10 = float(np.average([p[0] for p in projected], weights=pc))
        proj_rank = graduation_rank(proj_gpa4)
    else:
        proj_gpa4 = proj_gpa10 = None
        proj_rank = None

    return {
        "gpa": round(gpa10, 2),           # giữ key cũ (hệ 10) để template không vỡ
        "gpa4": round(gpa4, 2),
        "gpa10": round(gpa10, 2),
        "total_credits": total_credits,
        "rank": rank,
        "best": best["subject"].name,
        "worst": worst["subject"].name,
        "projected_gpa4": round(proj_gpa4, 2) if proj_gpa4 is not None else None,
        "projected_gpa10": round(proj_gpa10, 2) if proj_gpa10 is not None else None,
        "projected_rank": proj_rank,
        "n_subjects": len(valid),
    }


def weak_topics(user_id, threshold=None, subject_id=None, limit=10):
    """Tìm chủ đề yếu: điểm TB có trọng số (bài gần đây nặng hơn) < ngưỡng (%)."""
    threshold = threshold or Config.WEAK_THRESHOLD
    q = Score.query.filter_by(user_id=user_id).order_by(Score.taken_at, Score.id)
    if subject_id:
        q = q.filter_by(subject_id=subject_id)
    groups = defaultdict(list)
    for s in q.all():
        topic = (s.topic or s.exam_name).strip()
        groups[(s.subject_id, topic)].append(s.percent)

    out = []
    for (sid, topic), pcts in groups.items():
        weights = np.array([1.5 ** i for i in range(len(pcts))])
        avg = float(np.average(pcts, weights=weights))
        if avg >= threshold:
            continue
        sub = Subject.query.get(sid)
        if not sub or sub.user_id != user_id:
            continue
        priority = (100 - avg) * (1 + 0.1 * (sub.difficulty or 3))
        out.append({
            "subject": sub, "topic": topic, "avg": round(avg, 1),
            "level": level(avg / 10), "attempts": len(pcts), "priority": priority,
        })
    out.sort(key=lambda x: x["priority"], reverse=True)
    return out[:limit]


def recommendations(user_id, limit=5, subject_id=None):
    recs = []
    for w in weak_topics(user_id, subject_id=subject_id, limit=limit):
        gap = 70 - w["avg"]
        hours = max(1.0, round(gap / 20 * 2) / 2)
        recs.append({
            "subject": w["subject"], "topic": w["topic"], "avg": w["avg"],
            "level": w["level"], "hours": hours, "priority": w["priority"],
            "attempts": w["attempts"],
        })
    return recs
