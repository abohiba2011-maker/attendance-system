"""
license_generator.py
⚠️ ملف خاص بالمطور فقط — لا تشاركه مع العميل
- يولّد السيريال الموقّع
"""

import hmac
import hashlib
import base64
import json
from datetime import datetime, timedelta


# ⚠️⚠️⚠️ المفتاح السري — احتفظ به في مكان آمن ⚠️⚠️⚠️
SECRET_KEY = b'PHARMACY_ATTENDANCE_2026_SECRET_KEY_KEEP_SAFE'


def generate_serial(machine_id, days=365):
    """
    يولّد سيريال صالح للجهاز بعدد أيام معين
    """
    machine_id = machine_id.strip().upper()
    expiry = datetime.now() + timedelta(days=days)

    data = {
        'machine_id': machine_id,
        'issued': datetime.now().strftime('%Y-%m-%d'),
        'expires': expiry.strftime('%Y-%m-%d'),
        'days': days,
        'version': '1.0'
    }

    # ترميز
    payload = json.dumps(data, separators=(',', ':'), ensure_ascii=False).encode('utf-8')

    # توقيع HMAC
    signature = hmac.new(SECRET_KEY, payload, hashlib.sha256).hexdigest()

    # دمج
    combined = payload + b'||SIG||' + signature.encode('ascii')

    # base64
    serial = base64.urlsafe_b64encode(combined).decode('ascii').rstrip('=')

    return serial


def verify_serial(serial, machine_id):
    """
    يتحقق من السيريال
    يعيد: (valid: bool, result: dict or str)
    """
    try:
        machine_id = machine_id.strip().upper()

        # فك base64
        padding = 4 - (len(serial) % 4)
        if padding != 4:
            serial += '=' * padding

        combined = base64.urlsafe_b64decode(serial.encode('ascii'))

        # تقسيم
        if b'||SIG||' not in combined:
            return False, "صيغة سيريال غير صحيحة"

        payload, sig_bytes = combined.split(b'||SIG||', 1)
        signature = sig_bytes.decode('ascii')

        # تحقق من التوقيع
        expected_sig = hmac.new(SECRET_KEY, payload, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(signature, expected_sig):
            return False, "توقيع غير صحيح"

        # فك JSON
        data = json.loads(payload.decode('utf-8'))

        # تحقق من بصمة الجهاز
        if data.get('machine_id') != machine_id:
            return False, "هذا السيريال لا يخص هذا الجهاز"

        # تحقق من التاريخ
        expires = datetime.strptime(data['expires'], '%Y-%m-%d')

        if datetime.now() > expires:
            return False, f"انتهى الترخيص بتاريخ {data['expires']}"

        return True, data

    except Exception as e:
        return False, f"سيريال غير صالح: {e}"


# ==================================================
#                    واجهة المطور
# ==================================================
if __name__ == '__main__':
    print()
    print("=" * 70)
    print("  🔐 مولّد السيريال — صيدلية المقري شنن عثمان")
    print("=" * 70)
    print()

    machine_id = input("  📟 أدخل بصمة الجهاز: ").strip()

    if not machine_id:
        print("  ❌ بصمة فارغة")
        exit()

    days_input = input("  📅 عدد الأيام (افتراضي 365): ").strip()
    days = int(days_input) if days_input.isdigit() else 365

    print()
    print("=" * 70)

    serial = generate_serial(machine_id, days)

    print()
    print("  ✅ السيريال الجاهز:")
    print()
    print("  ┌" + "─" * 66 + "┐")
    # تقسيم السيريال على أسطر
    for i in range(0, len(serial), 60):
        chunk = serial[i:i + 60]
        print(f"  │ {chunk:<64} │")
    print("  └" + "─" * 66 + "┘")
    print()

    expiry = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
    print(f"  📅 صالح حتى: {expiry}")
    print(f"  📟 للجهاز: {machine_id}")
    print()
    print("=" * 70)
    print()

    # اختبار التحقق
    valid, result = verify_serial(serial, machine_id)
    if valid:
        print("  ✅ اختبار التحقق: نجح")
    else:
        print(f"  ❌ اختبار التحقق: فشل - {result}")

    print()