"""
license_check.py
- التحقق من الترخيص
- إدارة رسائل التحذير والانتهاء
"""

import datetime

from license_manager import get_machine_id, load_license_key
from license_generator import verify_serial


WARNING_DAYS = 15  # التحذير قبل 15 يوم


def check_license():
    """
    يعيد: (status, message, days_remaining, data)

    status:
    - 'valid'    → سيريال صالح
    - 'warning'  → قارب على الانتهاء (< 15 يوم)
    - 'expired'  → انتهى
    - 'invalid'  → سيريال غير صحيح
    - 'missing'  → لا يوجد سيريال
    """
    stored = load_license_key()

    if not stored:
        return ('missing', 'البرنامج غير مفعّل', 0, None)

    serial = stored.get('serial', '')

    if not serial:
        return ('missing', 'البرنامج غير مفعّل', 0, None)

    machine_id = get_machine_id()
    valid, result = verify_serial(serial, machine_id)

    if not valid:
        # التحقق إذا انتهى فقط (وليس تلاعب)
        if 'انتهى' in str(result):
            # قراءة التاريخ من السيريال الأصلي
            try:
                expires_str = stored.get('expires', '')
                if expires_str:
                    return ('expired',
                            f"انتهى الترخيص بتاريخ {expires_str}",
                            0, stored)
            except Exception:
                pass
            return ('expired', 'انتهى الترخيص', 0, stored)

        return ('invalid', str(result), 0, stored)

    # صالح — احسب الأيام
    expires = datetime.datetime.strptime(result['expires'], '%Y-%m-%d')
    days_remaining = (expires - datetime.datetime.now()).days

    if days_remaining < 0:
        return ('expired', f"انتهى الترخيص بتاريخ {result['expires']}", 0, result)

    if days_remaining <= WARNING_DAYS:
        return ('warning',
                f"ينتهي الترخيص خلال {days_remaining} يوم — اتصل بالمطور",
                days_remaining, result)

    return ('valid', f"الترخيص ساري حتى {result['expires']}", days_remaining, result)


def get_license_status_text():
    """نص قصير للحالة (للاستخدام في الواجهة)"""
    status, message, days, data = check_license()

    if status == 'valid':
        return f"✅ ساري — {days} يوم متبقٍ"
    elif status == 'warning':
        return f"⚠️ ينتهي خلال {days} يوم"
    elif status == 'expired':
        return "⛔ منتهي — اتصل بالمطور"
    elif status == 'invalid':
        return "❌ ترخيص غير صالح"
    else:
        return "🔑 غير مفعّل"