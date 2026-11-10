"""Tích hợp VNPay (sandbox / production).

Cấu hình qua biến môi trường:
  VNP_TMN_CODE, VNP_HASH_SECRET, VNP_URL, VNP_RETURN_URL
Nếu thiếu secret → chế độ demo (giả lập thanh toán thành công).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import urllib.parse
from datetime import datetime
from typing import Dict, Tuple


def _cfg():
    return {
        "tmn_code": os.getenv("VNP_TMN_CODE", "DEMO"),
        "hash_secret": os.getenv("VNP_HASH_SECRET", ""),
        "pay_url": os.getenv(
            "VNP_URL",
            "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html",
        ),
        "return_url": os.getenv("VNP_RETURN_URL", ""),
    }


def is_demo_mode() -> bool:
    return not bool(_cfg()["hash_secret"])


def _hmac_sha512(key: str, data: str) -> str:
    return hmac.new(key.encode("utf-8"), data.encode("utf-8"), hashlib.sha512).hexdigest()


def build_payment_url(
    amount_vnd: int,
    txn_ref: str,
    order_info: str,
    return_url: str,
    client_ip: str = "127.0.0.1",
) -> Tuple[str, bool]:
    """
    Trả về (url, is_demo).
    is_demo=True → URL nội bộ giả lập, không gọi VNPay thật.
    """
    cfg = _cfg()
    if not cfg["hash_secret"]:
        # Demo: trỏ về return nội bộ kèm status success
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
    params = {
        "vnp_Version": "2.1.0",
        "vnp_Command": "pay",
        "vnp_TmnCode": cfg["tmn_code"],
        "vnp_Amount": str(int(amount_vnd) * 100),
        "vnp_CurrCode": "VND",
        "vnp_TxnRef": txn_ref,
        "vnp_OrderInfo": order_info[:255],
        "vnp_OrderType": "other",
        "vnp_Locale": "vn",
        "vnp_ReturnUrl": return_url or cfg["return_url"],
        "vnp_IpAddr": client_ip or "127.0.0.1",
        "vnp_CreateDate": create_date,
    }
    sorted_items = sorted(params.items())
    query = urllib.parse.urlencode(sorted_items)
    secure = _hmac_sha512(cfg["hash_secret"], query)
    url = f"{cfg['pay_url']}?{query}&vnp_SecureHash={secure}"
    return url, False


def verify_return(params: Dict[str, str]) -> Tuple[bool, str]:
    """
    Xác thực callback VNPay.
    Trả (ok, message). Demo mode chấp nhận ResponseCode=00.
    """
    data = {k: v for k, v in params.items() if k.startswith("vnp_")}
    if params.get("demo") == "1" or is_demo_mode():
        code = data.get("vnp_ResponseCode") or params.get("vnp_ResponseCode")
        if code == "00":
            return True, "Thanh toán demo thành công"
        return False, f"Demo thất bại (code={code})"

    cfg = _cfg()
    secure = data.pop("vnp_SecureHash", None) or params.get("vnp_SecureHash")
    data.pop("vnp_SecureHashType", None)
    sorted_items = sorted(data.items())
    query = urllib.parse.urlencode(sorted_items)
    check = _hmac_sha512(cfg["hash_secret"], query)
    if secure != check:
        return False, "Sai chữ ký VNPay"
    if data.get("vnp_ResponseCode") == "00":
        return True, "Thanh toán thành công"
    return False, f"VNPay từ chối (code={data.get('vnp_ResponseCode')})"
