"""Chatbot StudyAI – intent linh hoạt + hỏi đáp tài liệu (TF-IDF).

Không còn bắt cứng cụm từ: dùng bảng intent + điểm khớp từ khóa / regex,
hỗ trợ nhận diện môn học trong câu, tính điểm cần đạt cuối kỳ, v.v.
"""
from datetime import date, timedelta
import re
from collections import defaultdict

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from models import (Course, CourseAssignment, CourseGrade, CourseMaterial,
                    CourseSession, Deadline, DocChunk, Document, Enrollment,
                    GradeComponent, Schedule, Score, Subject, StudyPlan, User)
from services import analytics, nlp

MIN_SCORE = 0.06
WEEKDAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]


# ─────────────────────────────────────────────────────────────
# Document QA
# ─────────────────────────────────────────────────────────────
class DocumentQA:
    def __init__(self):
        self._cache = {}

    def _index(self, user_id, subject_id=None):
        q = DocChunk.query.join(Document).filter(Document.user_id == user_id)
        if subject_id:
            q = q.filter(Document.subject_id == subject_id)
        chunks = q.order_by(DocChunk.id).all()
        if not chunks:
            return None
        key = (len(chunks), chunks[-1].id)
        cache_key = (user_id, subject_id)
        cached = self._cache.get(cache_key)
        if cached and cached[0] == key:
            return cached
        vec = TfidfVectorizer(
            tokenizer=nlp.tokenize, token_pattern=None, lowercase=False,
            ngram_range=(1, 2), sublinear_tf=True, max_df=0.95, min_df=1,
        )
        try:
            matrix = vec.fit_transform([c.text for c in chunks])
        except ValueError:
            return None
        meta = [(c.document.filename, c.text, c.document.subject_id) for c in chunks]
        self._cache[cache_key] = (key, vec, matrix, meta)
        return self._cache[cache_key]

    def search(self, question, user_id, subject_id=None, k=4):
        idx = self._index(user_id, subject_id)
        if not idx:
            return []
        _, vec, matrix, meta = idx
        try:
            q_vec = vec.transform([question])
        except ValueError:
            return []
        scores = linear_kernel(q_vec, matrix).ravel()
        order = np.argsort(scores)[::-1][:k]
        return [
            {"source": meta[i][0], "text": meta[i][1], "score": float(scores[i]),
             "subject_id": meta[i][2]}
            for i in order if scores[i] > 0
        ]

    def answer(self, question, user_id, subject_id=None):
        idx = self._index(user_id, subject_id)
        if not idx:
            return {
                "answer": (
                    "Chưa có tài liệu để tra cứu. Bạn có thể:\n"
                    "• Tải tài liệu ở mục Tài liệu\n"
                    "• Hỏi về lịch học, deadline, điểm, GPA, kế hoạch, ôn tập\n"
                    "• Hỏi \"cần bao nhiêu điểm cuối kỳ để đạt 8?\""
                ),
                "sources": [],
            }
        hits = self.search(question, user_id, subject_id, k=4)
        if (not hits or hits[0]["score"] < MIN_SCORE) and subject_id:
            hits = self.search(question, user_id, None, k=3)
        if not hits or hits[0]["score"] < MIN_SCORE:
            return {
                "answer": (
                    "Mình chưa tìm thấy nội dung phù hợp trong tài liệu.\n"
                    "Gợi ý: dùng từ khóa cụ thể hơn, hoặc hỏi về lịch / deadline / điểm / GPA / kế hoạch."
                ),
                "sources": [],
            }
        _, vec, _, _ = idx
        sentences = []
        for h in hits[:3]:
            for s in nlp.split_sentences(h["text"]):
                if len(s) > 15:
                    sentences.append((s, h["source"]))
        if not sentences:
            return {"answer": hits[0]["text"][:800], "sources": [hits[0]["source"]]}
        try:
            qv = vec.transform([question])
            sv = vec.transform([s for s, _ in sentences])
            sims = linear_kernel(qv, sv).ravel()
        except ValueError:
            return {
                "answer": hits[0]["text"][:800],
                "sources": sorted({h["source"] for h in hits if h["score"] >= MIN_SCORE}),
            }
        top_idx = sorted(np.argsort(sims)[::-1][:4])
        selected, seen = [], set()
        for i in top_idx:
            if sims[i] > 0.02 and sentences[i][0] not in seen:
                selected.append(sentences[i][0])
                seen.add(sentences[i][0])
        text = " ".join(selected) if selected else hits[0]["text"]
        if len(text) > 900:
            text = text[:900].rsplit(" ", 1)[0] + "…"
        sources = sorted({h["source"] for h in hits if h["score"] >= MIN_SCORE})
        return {"answer": text, "sources": sources}


qa = DocumentQA()


def related_material(topic, subject_id=None, user_id=None):
    if user_id is None:
        return None
    hits = qa.search(topic, user_id, subject_id, k=1)
    return hits[0] if hits and hits[0]["score"] >= MIN_SCORE else None


# ─────────────────────────────────────────────────────────────
# Intent engine (linh hoạt, có điểm số)
# ─────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    # bỏ dấu câu thừa ở cuối
    s = re.sub(r"[?!.]+$", "", s)
    return s


def _score_keywords(q: str, keywords, weight=1.0):
    """Điểm = số keyword xuất hiện (substring) * weight. Hỗ trợ cụm nhiều từ."""
    score = 0.0
    for kw in keywords:
        if kw in q:
            # cụm dài được ưu tiên
            score += weight * (1.0 + 0.15 * max(0, len(kw.split()) - 1))
    return score


def _extract_subject(q: str, user_id: int):
    subjects = Subject.query.filter_by(user_id=user_id).all()
    best, best_len = None, 0
    for s in subjects:
        for key in filter(None, [s.name, s.code]):
            k = key.lower()
            if k in q and len(k) > best_len:
                best, best_len = s, len(k)
    return best


def _extract_number(q: str, default=None):
    """Lấy số thực đầu tiên trong câu (vd: 8, 8.5, 70%)."""
    m = re.search(r"(\d+[.,]?\d*)\s*%?", q)
    if not m:
        return default
    return float(m.group(1).replace(",", "."))


def _extract_weekday(q: str):
    mapping = {
        "thứ 2": 0, "thứ hai": 0, "thu 2": 0,
        "thứ 3": 1, "thứ ba": 1, "thu 3": 1,
        "thứ 4": 2, "thứ tư": 2, "thu 4": 2,
        "thứ 5": 3, "thứ năm": 3, "thu 5": 3,
        "thứ 6": 4, "thứ sáu": 4, "thu 6": 4,
        "thứ 7": 5, "thứ bảy": 5, "thu 7": 5,
        "chủ nhật": 6, "cn": 6,
        "hôm nay": "today", "ngày mai": "tomorrow",
    }
    for k, v in mapping.items():
        if k in q:
            return v
    return None


# ---- Intent handlers ----
def _h_greeting(q, user_id, subject_id, ctx):
    return {
        "answer": (
            "Xin chào! Mình là **StudyAI** – trợ lý học tập của bạn.\n"
            "Bạn có thể hỏi mình:\n"
            "• Lịch học hôm nay / thứ 3 / cả tuần\n"
            "• Deadline sắp tới, tuần này, quá hạn\n"
            "• GPA, xếp loại, điểm từng môn\n"
            "• Cần bao nhiêu điểm cuối kỳ để đạt 8?\n"
            "• Nên ôn gì / chủ đề yếu\n"
            "• Kế hoạch học hôm nay\n"
            "• Nội dung trong tài liệu đã tải\n"
            "Cứ hỏi tự nhiên nhé!"
        ),
        "sources": [],
    }


def _h_help(q, user_id, subject_id, ctx):
    return {
        "answer": (
            "**Hướng dẫn nhanh StudyAI**\n"
            "1. Thêm môn học & cấu hình trọng số điểm (TX1/TX2/CK)\n"
            "2. Nhập lịch học, deadline, điểm thành phần\n"
            "3. Tải tài liệu để hỏi nội dung bài giảng\n"
            "4. Sinh kế hoạch học tự động ở mục Kế hoạch\n"
            "5. Xem GPA & xếp loại tốt nghiệp dự kiến ở Kết quả\n\n"
            "Ví dụ câu hỏi:\n"
            "• \"Thứ 6 tôi học gì?\"\n"
            "• \"Deadline môn Giải tích\"\n"
            "• \"GPA hiện tại\"\n"
            "• \"Muốn đạt 8.0 Giải tích thì cuối kỳ cần bao nhiêu?\"\n"
            "• \"Tuần này ôn gì?\""
        ),
        "sources": [],
    }


def _h_schedule(q, user_id, subject_id, ctx):
    wd = _extract_weekday(q)
    today = date.today()
    if wd == "tomorrow":
        target = today + timedelta(days=1)
        day_idx = target.weekday()
        label = f"Ngày mai ({WEEKDAYS[day_idx]})"
    elif wd == "today" or wd is None:
        if any(k in q for k in ["tuần", "cả tuần", "tuần này", "tuần tới"]):
            return _schedule_week(user_id)
        day_idx = today.weekday()
        label = f"Hôm nay ({WEEKDAYS[day_idx]})"
    else:
        day_idx = wd
        label = WEEKDAYS[day_idx]

    items = (Schedule.query.filter_by(user_id=user_id, weekday=day_idx)
             .order_by(Schedule.start_time).all())
    if not items:
        return {"answer": f"{label} bạn không có tiết học.", "sources": []}
    lines = [
        f"• {s.start_time:%H:%M}–{s.end_time:%H:%M}: **{s.subject.name}**"
        + (f" – {s.room}" if s.room else "")
        for s in items
    ]
    return {"answer": f"Lịch học {label}:\n" + "\n".join(lines), "sources": []}


def _schedule_week(user_id):
    by_day = defaultdict(list)
    for s in Schedule.query.filter_by(user_id=user_id).order_by(
            Schedule.weekday, Schedule.start_time).all():
        by_day[s.weekday].append(s)
    if not by_day:
        return {"answer": "Bạn chưa có lịch học nào.", "sources": []}
    lines = []
    for i in range(7):
        if by_day[i]:
            slots = ", ".join(
                f"{s.start_time:%H:%M}–{s.end_time:%H:%M} {s.subject.name}"
                for s in by_day[i]
            )
            lines.append(f"• **{WEEKDAYS[i]}**: {slots}")
    return {"answer": "Lịch học trong tuần:\n" + "\n".join(lines), "sources": []}


def _h_deadline(q, user_id, subject_id, ctx):
    sub = ctx.get("subject")
    query = Deadline.query.filter_by(user_id=user_id, done=False)
    if sub:
        query = query.filter_by(subject_id=sub.id)
    items = query.order_by(Deadline.due_date).limit(10).all()

    # lọc theo thời gian nếu có
    if any(k in q for k in ["tuần này", "trong tuần"]):
        end = date.today() + timedelta(days=7)
        items = [d for d in items if d.due_date <= end]
    elif any(k in q for k in ["quá hạn", "trễ", "quá hạn nộp"]):
        items = [d for d in Deadline.query.filter_by(user_id=user_id, done=False).all()
                 if d.due_date < date.today()]
        if sub:
            items = [d for d in items if d.subject_id == sub.id]

    if not items:
        msg = f"Không có deadline nào" + (f" cho **{sub.name}**" if sub else "") + "."
        return {"answer": msg + " 🎉", "sources": []}

    lines = []
    for d in items:
        flag = "⚠️" if d.days_left <= 2 else "•"
        lines.append(
            f"{flag} **{d.title}** ({d.subject.name}) – hạn {d.due_date:%d/%m/%Y}, "
            f"còn {d.days_left} ngày, ~{d.est_hours}h, ưu tiên {d.priority}/3"
        )
    header = "Deadline" + (f" môn **{sub.name}**" if sub else "") + ":\n"
    return {"answer": header + "\n".join(lines), "sources": []}


def _h_plan(q, user_id, subject_id, ctx):
    today = date.today()
    if "tuần" in q:
        end = today + timedelta(days=7)
        plans = (StudyPlan.query.filter_by(user_id=user_id)
                 .filter(StudyPlan.date >= today, StudyPlan.date <= end)
                 .order_by(StudyPlan.date, StudyPlan.kind).all())
        if not plans:
            return {
                "answer": "Chưa có kế hoạch trong 7 ngày tới. Vào mục **Kế hoạch** → Sinh kế hoạch nhé.",
                "sources": [],
            }
        by_date = defaultdict(list)
        for p in plans:
            by_date[p.date].append(p)
        lines = []
        for d in sorted(by_date):
            items = ", ".join(
                f"{p.note or p.subject.name} ({p.hours}h)" + ("✓" if p.done else "")
                for p in by_date[d]
            )
            lines.append(f"• **{d:%d/%m}**: {items}")
        return {"answer": "Kế hoạch 7 ngày tới:\n" + "\n".join(lines), "sources": []}

    plans = (StudyPlan.query.filter_by(user_id=user_id, date=today)
             .order_by(StudyPlan.kind).all())
    if not plans:
        return {
            "answer": (
                "Hôm nay chưa có kế hoạch. Vào **Kế hoạch** → bấm Sinh kế hoạch "
                "để hệ thống tự xếp lịch theo deadline và chủ đề yếu."
            ),
            "sources": [],
        }
    lines = [
        f"• [{'ôn' if p.kind == 'review' else 'deadline'}] {p.note or p.subject.name} – {p.hours}h"
        + (" ✓" if p.done else "")
        for p in plans
    ]
    return {"answer": f"Kế hoạch hôm nay ({today:%d/%m}):\n" + "\n".join(lines), "sources": []}


def _h_scores(q, user_id, subject_id, ctx):
    sub = ctx.get("subject")
    stats = analytics.subject_stats(user_id)
    summary = analytics.overall_summary(stats)
    if not summary:
        return {"answer": "Chưa có điểm nào. Hãy nhập điểm ở mục **Kết quả** nhé!", "sources": []}

    if sub:
        row = next((s for s in stats if s["subject"].id == sub.id), None)
        if not row or row["avg10"] is None:
            return {
                "answer": f"Môn **{sub.name}** chưa có đủ điểm để tính tổng kết.",
                "sources": [],
            }
        lines = [
            f"**{sub.name}** ({sub.credits} TC)",
            f"• Điểm TK: **{row['avg10']}**/10 ({row['letter']}, hệ 4: {row['grade_point']})",
            f"• Xếp loại môn: {row['level']} · Xu hướng: {row['trend']}",
        ]
        if row["breakdown"]:
            lines.append("• Thành phần:")
            for b in row["breakdown"]:
                sc = f"{b['score10']}" if b["has_score"] else "chưa có"
                lines.append(f"  – {b['name']} ({b['weight']:.0f}%): {sc}")
        if row["gap"] is not None:
            if row["gap"] > 0:
                lines.append(f"• Còn thiếu **{row['gap']}** điểm so với mục tiêu {sub.target_score}")
            else:
                lines.append(f"• Đã vượt mục tiêu {sub.target_score} (chênh {row['gap']})")
        return {"answer": "\n".join(lines), "sources": []}

    lines = [
        f"• {s['subject'].name}: TK **{s['avg10']}**/10 ({s['letter']}) – {s['level']}"
        for s in stats if s["avg10"] is not None
    ]
    ans = (
        f"**GPA hệ 4: {summary['gpa4']}**  |  Hệ 10: {summary['gpa10']}\n"
        f"Xếp loại: **{summary['rank']}** · {summary['total_credits']} tín chỉ\n"
    )
    if summary.get("projected_rank"):
        ans += (
            f"Dự kiến tốt nghiệp: **{summary['projected_rank']}** "
            f"(GPA4 ≈ {summary['projected_gpa4']})\n"
        )
    ans += f"Mạnh: {summary['best']} · Yếu: {summary['worst']}\n\n" + "\n".join(lines)
    return {"answer": ans, "sources": []}


def _h_need_score(q, user_id, subject_id, ctx):
    """Tính điểm cuối kỳ cần đạt để được điểm TK mục tiêu."""
    sub = ctx.get("subject")
    if not sub:
        return {
            "answer": (
                "Bạn muốn tính điểm cần đạt cho môn nào?\n"
                "Ví dụ: *\"Muốn đạt 8.0 môn Giải tích thì cuối kỳ cần bao nhiêu?\"*"
            ),
            "sources": [],
        }
    target = _extract_number(q)
    if target is None:
        target = sub.target_score or 8.0
    if target > 10:
        target = target / 10.0  # người dùng gõ 80 thay vì 8

    comps = list(sub.components)
    if not comps:
        return {
            "answer": (
                f"Môn **{sub.name}** chưa cấu hình thành phần điểm.\n"
                "Vào **Môn học → Cấu hình** để set TX1/TX2/Cuối kỳ trước nhé."
            ),
            "sources": [],
        }

    # Tìm component "cuối" (tên chứa cuối kỳ / ck / final) hoặc weight lớn nhất
    final_comp = None
    for c in comps:
        if any(k in c.name.lower() for k in ["cuối", "ck", "final", "thi"]):
            final_comp = c
            break
    if not final_comp:
        final_comp = max(comps, key=lambda c: c.weight or 0)

    scores = Score.query.filter_by(user_id=user_id, subject_id=sub.id).all()
    by_comp = {}
    for s in sorted(scores, key=lambda x: (x.taken_at, x.id)):
        if s.component_id:
            by_comp[s.component_id] = s

    known_sum = 0.0
    known_w = 0.0
    detail = []
    for c in comps:
        if c.id == final_comp.id:
            continue
        s = by_comp.get(c.id)
        if s:
            known_sum += s.score10 * (c.weight or 0)
            known_w += c.weight or 0
            detail.append(f"• {c.name} ({c.weight:.0f}%): {s.score10:.2f}")
        else:
            detail.append(f"• {c.name} ({c.weight:.0f}%): chưa có")

    fw = final_comp.weight or 0
    if fw <= 0:
        return {"answer": "Trọng số cuối kỳ = 0, không tính được.", "sources": []}

    # target = (known_sum + final_score * fw) / (known_w + fw)
    # => final_score = (target * total_w - known_sum) / fw
    total_w = known_w + fw
    need = (target * total_w - known_sum) / fw
    need = round(need, 2)

    lines = [
        f"Môn **{sub.name}** – mục tiêu TK **{target}**/10",
        f"Thành phần còn lại: **{final_comp.name}** ({fw:.0f}%)",
        *detail,
    ]
    if need > 10:
        lines.append(
            f"\n⚠️ Cần **{need}**/10 ở {final_comp.name} — vượt quá 10. "
            f"Với điểm hiện tại khó đạt {target}. Hãy xem lại mục tiêu hoặc điểm thành phần."
        )
    elif need <= 0:
        lines.append(
            f"\n✅ Bạn đã đủ điểm để đạt {target} dù {final_comp.name} = 0. Tuyệt!"
        )
    else:
        lines.append(
            f"\n🎯 Cần đạt khoảng **{need}**/10 ở **{final_comp.name}** để có TK {target}."
        )
    return {"answer": "\n".join(lines), "sources": []}


def _h_review(q, user_id, subject_id, ctx):
    sub = ctx.get("subject")
    sid = sub.id if sub else subject_id
    recs = analytics.recommendations(user_id, limit=5, subject_id=sid)
    if not recs:
        return {
            "answer": "Chưa phát hiện chủ đề yếu — hãy nhập thêm điểm kiểm tra (kèm chủ đề) nhé!",
            "sources": [],
        }
    lines = [
        f"• **{r['subject'].name}** – {r['topic']}: TB {r['avg']}% ({r['level']}), ~{r['hours']}h"
        for r in recs
    ]
    header = "Gợi ý ôn tập" + (f" cho **{sub.name}**" if sub else "") + ":\n"
    return {"answer": header + "\n".join(lines), "sources": []}


def _h_subjects(q, user_id, subject_id, ctx):
    subjects = Subject.query.filter_by(user_id=user_id).order_by(Subject.name).all()
    if not subjects:
        return {"answer": "Bạn chưa thêm môn nào. Vào **Môn học** để thêm nhé.", "sources": []}
    lines = []
    for s in subjects:
        comps = list(s.components)
        comp_str = ", ".join(f"{c.name} {c.weight:.0f}%" for c in comps) if comps else "chưa cấu hình TS"
        lines.append(
            f"• **{s.name}** ({s.code or '—'}) – {s.credits} TC, GV: {s.teacher or '—'}, "
            f"mục tiêu {s.target_score} · {comp_str}"
        )
    return {"answer": "Các môn học của bạn:\n" + "\n".join(lines), "sources": []}


def _h_components(q, user_id, subject_id, ctx):
    sub = ctx.get("subject")
    if not sub:
        return {
            "answer": "Bạn muốn xem thành phần điểm môn nào? Ví dụ: *\"thành phần điểm Giải tích\"*",
            "sources": [],
        }
    comps = list(sub.components)
    if not comps:
        return {
            "answer": f"Môn **{sub.name}** chưa cấu hình thành phần điểm. Vào **Môn học → Cấu hình** nhé.",
            "sources": [],
        }
    total = sum(c.weight or 0 for c in comps)
    lines = [f"• {c.name}: **{c.weight:.0f}%**" for c in comps]
    return {
        "answer": f"Thành phần điểm **{sub.name}** (tổng {total:.0f}%):\n" + "\n".join(lines),
        "sources": [],
    }


def _h_workload(q, user_id, subject_id, ctx):
    """Tổng giờ deadline còn lại / quá tải."""
    items = Deadline.query.filter_by(user_id=user_id, done=False).order_by(Deadline.due_date).all()
    if not items:
        return {"answer": "Không còn deadline nào đang mở. Bạn rảnh tay rồi! 🎉", "sources": []}
    total_h = sum(d.est_hours or 0 for d in items)
    urgent = [d for d in items if d.days_left <= 3]
    lines = [
        f"Còn **{len(items)}** deadline · tổng ước tính **{total_h:.1f} giờ**.",
    ]
    if urgent:
        lines.append(f"⚠️ {len(urgent)} deadline trong ≤3 ngày:")
        for d in urgent:
            lines.append(f"• {d.title} ({d.subject.name}) – còn {d.days_left} ngày, ~{d.est_hours}h")
    else:
        lines.append("Không có deadline gấp trong 3 ngày tới.")
    return {"answer": "\n".join(lines), "sources": []}


# Bảng intent: (tên, keywords, regex patterns, handler, priority_bonus)

def _h_profile(q, user_id, subject_id, ctx):
    u = User.query.get(user_id)
    if not u:
        return {"answer": "Không tìm thấy thông tin tài khoản.", "sources": []}
    lines = [
        f"**Hồ sơ của bạn**",
        f"- Họ tên: {u.full_name or '—'}",
        f"- Username: @{u.username}",
        f"- Email: {u.email}",
        f"- SĐT: {u.phone or '—'}",
        f"- MSSV: {u.student_id or '—'}",
        f"- Trường: {u.school or '—'}",
        f"- Ngành / Lớp: {u.major or '—'} / {u.class_name or '—'}",
        f"- Địa chỉ: {u.address or '—'}",
        f"- Vai trò: {u.role_label}",
        f"- Số dư ví: {float(u.balance or 0):,.0f} đ".replace(",", "."),
    ]
    return {"answer": "\n".join(lines), "sources": []}


def _h_enrollments(q, user_id, subject_id, ctx):
    enrolls = (Enrollment.query.filter_by(student_id=user_id)
               .filter(Enrollment.status != "dropped")
               .order_by(Enrollment.enrolled_at.desc()).all())
    if not enrolls:
        return {"answer": "Bạn chưa đăng ký lớp nào. Vào mục **Đăng ký lớp** để chọn lớp.", "sources": []}
    lines = [f"Bạn đang học **{len(enrolls)}** lớp:"]
    for e in enrolls:
        c = e.course
        fee = f"{float(e.fee_amount or 0):,.0f} đ".replace(",", ".")
        lines.append(
            f"- **{c.name}** ({c.code or '—'}) · {e.payment_label} · {fee} · "
            f"GV {c.teacher.full_name or c.teacher.username if c.teacher else '—'}"
        )
    return {"answer": "\n".join(lines), "sources": []}


def _h_balance(q, user_id, subject_id, ctx):
    u = User.query.get(user_id)
    bal = float(u.balance or 0) if u else 0
    unpaid = (Enrollment.query.filter_by(student_id=user_id, payment_status="unpaid")
              .filter(Enrollment.status != "dropped").all())
    lines = [f"Số dư ví hiện tại: **{bal:,.0f} đ**".replace(",", ".")]
    if unpaid:
        lines.append("Học phí chưa thanh toán:")
        for e in unpaid:
            fee = float(e.fee_amount or 0)
            ok = "đủ" if bal >= fee else "thiếu"
            lines.append(
                f"- {e.course.name}: {fee:,.0f} đ ({ok})".replace(",", ".")
            )
    else:
        lines.append("Không có khoản học phí nào đang chờ.")
    lines.append("Vào **Thanh toán** để nạp ví hoặc trả học phí.")
    return {"answer": "\n".join(lines), "sources": []}


def _h_course_grades(q, user_id, subject_id, ctx):
    grades = (CourseGrade.query.filter_by(student_id=user_id)
              .order_by(CourseGrade.taken_at.desc()).limit(30).all())
    if not grades:
        return {"answer": "Chưa có điểm lớp nào do giảng viên nhập. Xem chi tiết ở **Kết quả**.", "sources": []}
    by = defaultdict(list)
    for g in grades:
        by[g.course_id].append(g)
    lines = ["**Điểm các lớp** (do GV nhập):"]
    for cid, gs in by.items():
        cname = gs[0].course.name if gs[0].course else f"Lớp #{cid}"
        parts = [f"{g.title}={g.score}/{g.max_score:g} ({g.weight or 0:.0f}%)" for g in gs]
        # TB
        tw = sum(g.weight or 0 for g in gs)
        if tw > 0:
            avg = sum(g.score10 * (g.weight or 0) for g in gs) / tw
        else:
            avg = sum(g.score10 for g in gs) / len(gs)
        lines.append(f"- **{cname}** TB≈{avg:.2f}: " + "; ".join(parts))
    return {"answer": "\n".join(lines), "sources": []}


def _h_materials(q, user_id, subject_id, ctx):
    enrolls = (Enrollment.query.filter_by(student_id=user_id)
               .filter(Enrollment.status != "dropped").all())
    cids = [e.course_id for e in enrolls]
    if not cids:
        return {"answer": "Bạn chưa đăng ký lớp nên chưa có tài liệu lớp.", "sources": []}
    mats = (CourseMaterial.query.filter(CourseMaterial.course_id.in_(cids))
            .order_by(CourseMaterial.uploaded_at.desc()).limit(15).all())
    if not mats:
        return {"answer": "Các lớp của bạn chưa có tài liệu. GV sẽ upload trong quản lý lớp.", "sources": []}
    lines = ["**Tài liệu lớp gần đây:**"]
    for m in mats:
        flag = "📎 file" if m.filename else "📝"
        lines.append(f"- {flag} **{m.title}** ({m.course.name if m.course else ''})")
    lines.append("Vào **Tài liệu** để tải về.")
    return {"answer": "\n".join(lines), "sources": []}


def _h_class_schedule(q, user_id, subject_id, ctx):
    enrolls = (Enrollment.query.filter_by(student_id=user_id)
               .filter(Enrollment.status != "dropped").all())
    cids = [e.course_id for e in enrolls]
    if not cids:
        return {"answer": "Bạn chưa đăng ký lớp — chưa có lịch học lớp.", "sources": []}
    today = date.today()
    sessions = (CourseSession.query.filter(
        CourseSession.course_id.in_(cids),
        CourseSession.session_date >= today)
        .order_by(CourseSession.session_date, CourseSession.start_time).limit(10).all())
    if not sessions:
        return {"answer": "Không có buổi học sắp tới trong các lớp đã đăng ký.", "sources": []}
    lines = ["**Lịch học sắp tới:**"]
    for s in sessions:
        t = s.start_time.strftime("%H:%M") if s.start_time else ""
        lines.append(
            f"- {s.session_date.strftime('%d/%m')}"
            f"{(' ' + t) if t else ''}: **{s.course.name}**"
            f"{(' – ' + s.topic) if s.topic else ''}"
            f"{(' · ' + s.room) if s.room else ''}"
        )
    return {"answer": "\n".join(lines), "sources": []}



INTENTS = [
    ("profile",
     ["hồ sơ", "thông tin của tôi", "tôi là ai", "profile", "thông tin cá nhân",
      "mssv", "số điện thoại tôi", "email của tôi", "địa chỉ của tôi"],
     [r"hồ\s*sơ", r"thông\s*tin\s*(của\s*)?(tôi|bạn)", r"\bprofile\b"],
     _h_profile, 2.5),
    ("balance",
     ["số dư", "ví tiền", "nạp tiền", "học phí", "thanh toán", "còn bao nhiêu tiền",
      "chưa thanh toán", "nợ học phí"],
     [r"số\s*dư", r"học\s*phí", r"ví\s*tiền", r"thanh\s*toán"],
     _h_balance, 2.5),
    ("enrollments",
     ["lớp đã đăng ký", "đăng ký lớp", "tôi học lớp", "các lớp của tôi",
      "lớp đang học", "enroll"],
     [r"đăng\s*ký\s*lớp", r"lớp\s*(của\s*tôi|đang\s*học)"],
     _h_enrollments, 2.5),
    ("course_grades",
     ["điểm lớp", "điểm tx", "điểm thi", "giảng viên cho điểm", "điểm thành phần"],
     [r"điểm\s*(lớp|tx|thi)", r"tx\s*[12]"],
     _h_course_grades, 2),
    ("materials",
     ["tài liệu lớp", "slide", "file bài giảng", "tải tài liệu", "giáo trình"],
     [r"tài\s*liệu", r"bài\s*giảng", r"\bslide\b"],
     _h_materials, 2),
    ("class_schedule",
     ["buổi học", "lịch lớp", "học khi nào", "tiết học lớp"],
     [r"buổi\s*học", r"lịch\s*lớp"],
     _h_class_schedule, 2),
    ("greeting",
     ["xin chào", "chào bạn", "hello", "hi study", "hey", "chào study", "bạn là ai"],
     [r"^(chào|hi|hello|hey)\b"],
     _h_greeting, 2),
    ("help",
     ["giúp", "hướng dẫn", "làm sao", "cách dùng", "help", "trợ giúp", "bạn làm được gì",
      "chức năng", "có thể làm gì"],
     [r"\bhelp\b", r"làm (được )?gì"],
     _h_help, 1.5),
    ("need_score",
     ["cần bao nhiêu", "cần bao nhieu", "phải được", "cần đạt", "để đạt", "để được",
      "cuối kỳ cần", "ck cần", "thi cần", "bao nhiêu điểm mới"],
     [r"cần\s+bao\s+nhiêu", r"để\s+đạt\s+\d", r"muốn\s+đạt\s+\d"],
     _h_need_score, 3),
    ("components",
     ["thành phần điểm", "trọng số", "cột điểm", "cấu hình điểm", "% điểm", "phần trăm điểm"],
     [r"thành phần", r"trọng số"],
     _h_components, 2),
    ("workload",
     ["bao nhiêu giờ", "còn bao nhiêu việc", "khối lượng", "có quá tải", "workload",
      "bận không", "còn nhiều deadline"],
     [r"quá tải", r"bao nhiêu giờ"],
     _h_workload, 1.5),
    ("deadline",
     ["deadline", "hạn nộp", "hạn chót", "bài tập", "sắp đến hạn", "nộp bài",
      "assignment", "bài tập về nhà", "nộp khi nào"],
     [r"dead\s*line", r"hạn\s*(nộp|chót)"],
     _h_deadline, 2),
    ("schedule",
     ["lịch học", "thời khoá biểu", "thời khóa biểu", "tiết học", "học gì",
      "hôm nay học", "ngày mai học", "lịch hôm", "có học không"],
     [r"lịch\s*học", r"học\s*gì", r"thứ\s*[2-7]", r"thứ\s*(hai|ba|tư|năm|sáu|bảy)"],
     _h_schedule, 2),
    ("plan",
     ["kế hoạch", "lịch ôn", "plan", "buổi học hôm nay", "hôm nay làm gì", "to-do", "todo"],
     [r"kế\s*hoạch", r"\bplan\b"],
     _h_plan, 1.5),
    ("scores",
     ["điểm", "gpa", "kết quả", "điểm trung bình", "xếp loại", "học lực",
      "điểm tổng kết", "điểm tk", "tích lũy"],
     [r"\bgpa\b", r"điểm\s*(số|tk|tổng)?", r"xếp\s*loại"],
     _h_scores, 2),
    ("review",
     ["ôn tập", "ôn gì", "nên ôn", "yếu", "cải thiện", "review", "ôn lại",
      "chủ đề yếu", "phần yếu", "cần ôn"],
     [r"ôn\s*(tập|gì|lại)", r"chủ đề yếu"],
     _h_review, 2),
    ("subjects",
     ["môn học", "danh sách môn", "tôi học môn", "các môn", "môn nào"],
     [r"môn\s*học", r"các\s*môn"],
     _h_subjects, 1.5),
]


def _detect_intent(q: str):
    """Trả về (handler, score) tốt nhất hoặc (None, 0)."""
    best_h, best_s = None, 0.0
    for name, keywords, patterns, handler, bonus in INTENTS:
        s = _score_keywords(q, keywords, weight=1.0)
        for pat in patterns:
            if re.search(pat, q, re.IGNORECASE):
                s += 1.8
        if s > 0:
            s += bonus * 0.1
        if s > best_s:
            best_s, best_h = s, handler
    # ngưỡng tối thiểu để không bắt nhầm
    if best_s < 1.0:
        return None, 0.0
    return best_h, best_s


def reply(question: str, user_id: int, subject_id=None):
    q = _norm(question)
    if not q:
        return {"answer": "Bạn hãy nhập câu hỏi nhé.", "sources": []}

    sub = _extract_subject(q, user_id)
    ctx = {"subject": sub, "raw": question}

    handler, score = _detect_intent(q)
    if handler:
        try:
            return handler(q, user_id, subject_id, ctx)
        except Exception as e:
            return {
                "answer": f"Có lỗi khi xử lý câu hỏi ({type(e).__name__}). Bạn thử diễn đạt lại nhé.",
                "sources": [],
            }

    # Không khớp intent → hỏi đáp tài liệu
    return qa.answer(question, user_id, subject_id)
