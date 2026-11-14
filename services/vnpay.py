"""Tích hợp VNPay (sandbox / production) – hỗ trợ quét mã VNPAYQR.

Cấu hình (biến môi trường hoặc config.py):
  VNP_TMN_CODE      Mã terminal do VNPay cấp
  VNP_HASH_SECRET   Chuỗi bí mật tạo chữ ký
  VNP_URL           URL cổng thanh toán
  VNP_RETURN_URL    (tuỳ chọn) URL trả về sau thanh toán
  VNP_IPN_URL       (tuỳ chọn) URL nhận IPN server-to-server

Sandbox (test):
  VNP_URL = https://sandbox.vnpayment.vn/paymentv2/vpcpay.html
  Đăng ký tài khoản test tại https://sandbox.vnpayment.vn
"""
from __future__ import annotations

import hashlib
import hmac
import os
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple


def _cfg() -> dict:
    return {
        "tmn_code": (os.getenv("VNP_TMN_CODE") or "").strip(),
        "hash_secret": (os.getenv("VNP_HASH_SECRET") or "").strip(),
        "pay_url": (
            os.getenv("VNP_URL")
            or "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
        ).strip(),
        "return_url": (os.getenv("VNP_RETURN_URL") or "").strip(),
        "ipn_url": (os.getenv("VNP_IPN_URL") or "").strip(),
    }


def is_configured() -> bool:
    c = _cfg()
    return bool(c["tmn_code"] and c["hash_secret"])


def debug_config() -> dict:
    """Trạng thái cấu hình (không lộ full secret)."""
    c = _cfg()
    secret = c["hash_secret"]
    return {
        "configured": is_configured(),
        "tmn_code": c["tmn_code"] or "(trống)",
        "hash_secret": (secret[:4] + "…" + secret[-4:]) if len(secret) >= 8 else ("(trống)" if not secret else "(quá ngắn)"),
        "pay_url": c["pay_url"],
    }


def is_demo_mode() -> bool:
    """Giữ tên cũ cho tương thích: True khi chưa cấu hình merchant."""
    return not is_configured()


def _hmac_sha512(key: str, data: str) -> str:
    return hmac.new(
        key.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()


def _build_query(params: Dict[str, str]) -> str:
    """Query string theo thứ tự key tăng dần, value URL-encoded."""
    items = sorted((k, v) for k, v in params.items() if v is not None and v != "")
    return "&".join(
        f"{k}={urllib.parse.quote_plus(str(v))}" for k, v in items
    )


def build_payment_url(
    amount_vnd: int,
    txn_ref: str,
    order_info: str,
    return_url: str,
    client_ip: str = "127.0.0.1",
    bank_code: str = "",
    ipn_url: str = "",
) -> Tuple[str, bool]:
    """
    Tạo URL thanh toán VNPay.
    bank_code rỗng → VNPay cho chọn phương thức (ATM/QR/thẻ).
    bank_code="VNPAYQR" chỉ dùng khi merchant đã bật QR.
    Trả (url, is_demo). is_demo=True khi chưa cấu hình secret.
    """
    cfg = _cfg()
    amount_vnd = int(amount_vnd)
    if amount_vnd < 5000:
        raise ValueError("Số tiền tối thiểu 5.000 VND")

    if not is_configured():
        # Fallback nội bộ khi chưa có merchant key
        q = urllib.parse.urlencode({
            "vnp_TxnRef": txn_ref,
            "vnp_Amount": str(amount_vnd * 100),
            "vnp_ResponseCode": "00",
            "vnp_TransactionStatus": "00",
            "vnp_OrderInfo": order_info,
            "demo": "1",
        })
        sep = "&" if "?" in return_url else "?"
        return f"{return_url}{sep}{q}", True

    create_date = datetime.now().strftime("%Y%m%d%H%M%S")
    expire_date = (datetime.now() + timedelta(minutes=15)).strftime("%Y%m%d%H%M%S")

    params: Dict[str, str] = {
        "vnp_Version": "2.1.0",
        "vnp_Command": "pay",
        "vnp_TmnCode": cfg["tmn_code"],
        "vnp_Amount": str(amount_vnd * 100),  # VNPay tính theo x100
        "vnp_CurrCode": "VND",
        "vnp_TxnRef": txn_ref,
        "vnp_OrderInfo": (order_info or f"Nap tien {txn_ref}")[:255],
        "vnp_OrderType": "other",
        "vnp_Locale": "vn",
        "vnp_ReturnUrl": return_url or cfg["return_url"],
        "vnp_IpAddr": (client_ip or "127.0.0.1").split(",")[0].strip(),
        "vnp_CreateDate": create_date,
        "vnp_ExpireDate": expire_date,
    }
    if bank_code:
        params["vnp_BankCode"] = bank_code  # VNPAYQR = quét mã

    # IPN không phải field bắt buộc trên URL pay; cấu hình trên merchant portal.
    query = _build_query(params)
    secure = _hmac_sha512(cfg["hash_secret"], query)
    url = f"{cfg['pay_url']}?{query}&vnp_SecureHash={secure}"
    return url, False


def verify_return(params: Dict[str, str]) -> Tuple[bool, str, Optional[str]]:
    """
    Xác thực dữ liệu VNPay trả về (Return URL hoặc IPN).
    Trả (ok, message, response_code).
    """
    data = {k: v for k, v in params.items() if k.startswith("vnp_")}
    code = data.get("vnp_ResponseCode") or params.get("vnp_ResponseCode")

    # Chế độ chưa cấu hình / callback demo
    if params.get("demo") == "1" or not is_configured():
        if code == "00":
            return True, "Thanh toán thành công", code
        return False, f"Thanh toán thất bại (mã {code})", code

    cfg = _cfg()
    secure_hash = data.pop("vnp_SecureHash", None) or params.get("vnp_SecureHash")
    data.pop("vnp_SecureHashType", None)

    query = _build_query(data)
    check = _hmac_sha512(cfg["hash_secret"], query)
    if not secure_hash or secure_hash.lower() != check.lower():
        return False, "Sai chữ ký VNPay — giao dịch không hợp lệ", code

    if code == "00":
        return True, "Thanh toán thành công", code
    messages = {
        "07": "Trừ tiền thành công nhưng bị nghi ngờ (liên hệ ngân hàng).",
        "09": "Thẻ/Tài khoản chưa đăng ký InternetBanking.",
        "10": "Xác thực thông tin thẻ/tài khoản không đúng quá 3 lần.",
        "11": "Đã hết hạn chờ thanh toán. Vui lòng thử lại.",
        "12": "Thẻ/Tài khoản bị khóa.",
        "13": "Sai OTP.",
        "24": "Khách hàng hủy giao dịch.",
        "51": "Tài khoản không đủ số dư.",
        "65": "Vượt hạn mức giao dịch trong ngày.",
        "75": "Ngân hàng thanh toán đang bảo trì.",
        "79": "Sai mật khẩu thanh toán quá số lần quy định.",
        "99": "Lỗi khác / người dùng chưa hoàn tất.",
    }
    return False, messages.get(code, f"VNPay từ chối (mã {code})"), code


def get_txn_ref(params: Dict[str, str]) -> str:
    return (params.get("vnp_TxnRef") or "").strip()


def get_provider_txn(params: Dict[str, str]) -> str:
    return (
        params.get("vnp_TransactionNo")
        or params.get("vnp_BankTranNo")
        or ""
    ).strip()


def get_amount_vnd(params: Dict[str, str]) -> int:
    """vnp_Amount là VND * 100."""
    try:
        return int(params.get("vnp_Amount") or 0) // 100
    except (TypeError, ValueError):
        return 0
