"""
license_manager.py
- بصمة الجهاز (Machine ID)
- قراءة/كتابة license.key
"""

import os
import hashlib
import uuid
import platform
import subprocess
import base64

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LICENSE_FILE = os.path.join(BASE_DIR, 'license.key')


def get_machine_id():
    """
    يحسب بصمة فريدة للجهاز
    تعيد: XXXX-XXXX-XXXX-XXXX
    """
    components = []

    # 1. اسم الكمبيوتر
    try:
        components.append(str(platform.node()))
    except Exception:
        components.append('unknown-host')

    # 2. نظام التشغيل
    try:
        components.append(str(platform.system()))
        components.append(str(platform.release()))
    except Exception:
        pass

    # 3. MAC Address
    try:
        components.append(str(uuid.getnode()))
    except Exception:
        pass

    # 4. CPU ID (Windows)
    if platform.system() == 'Windows':
        try:
            result = subprocess.check_output(
                ['wmic', 'cpu', 'get', 'ProcessorId'],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL
            )
            lines = [l.strip() for l in result.split('\n') if l.strip()]
            if len(lines) >= 2:
                components.append(lines[1])
        except Exception:
            pass

        # 5. Motherboard Serial (Windows)
        try:
            result = subprocess.check_output(
                ['wmic', 'baseboard', 'get', 'SerialNumber'],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL
            )
            lines = [l.strip() for l in result.split('\n') if l.strip()]
            if len(lines) >= 2:
                components.append(lines[1])
        except Exception:
            pass

        # 6. Disk Serial (Windows)
        try:
            result = subprocess.check_output(
                ['wmic', 'diskdrive', 'get', 'SerialNumber'],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL
            )
            lines = [l.strip() for l in result.split('\n') if l.strip()]
            if len(lines) >= 2:
                components.append(lines[1])
        except Exception:
            pass

    # دمج الكل
    combined = '||'.join(components)
    hash_hex = hashlib.sha256(combined.encode()).hexdigest().upper()

    # تقسيم إلى مجموعات
    formatted = f"{hash_hex[0:4]}-{hash_hex[4:8]}-{hash_hex[8:12]}-{hash_hex[12:16]}"
    return formatted


def save_license_key(data_dict):
    """
    يحفظ license.key بصيغة:
    <base64>.<signature>
    """
    import json
    payload = json.dumps(data_dict, separators=(',', ':'), ensure_ascii=False)
    encoded = base64.urlsafe_b64encode(payload.encode('utf-8')).decode('ascii')

    try:
        with open(LICENSE_FILE, 'w', encoding='utf-8') as f:
            f.write(encoded)
        return True
    except Exception as e:
        print(f"⚠️ فشل حفظ license.key: {e}")
        return False


def load_license_key():
    """
    يقرأ license.key
    يعيد dict أو None
    """
    if not os.path.exists(LICENSE_FILE):
        return None

    try:
        with open(LICENSE_FILE, 'r', encoding='utf-8') as f:
            encoded = f.read().strip()

        # فك base64
        padding = 4 - (len(encoded) % 4)
        if padding != 4:
            encoded += '=' * padding

        payload = base64.urlsafe_b64decode(encoded.encode()).decode('utf-8')

        import json
        return json.loads(payload)

    except Exception as e:
        print(f"⚠️ خطأ قراءة license.key: {e}")
        return None


def delete_license_key():
    """حذف ملف license.key (لإعادة التفعيل)"""
    try:
        if os.path.exists(LICENSE_FILE):
            os.remove(LICENSE_FILE)
        return True
    except Exception:
        return False


if __name__ == '__main__':
    print("=" * 60)
    print("🔐 بصمة الجهاز")
    print("=" * 60)
    print()
    print(f"  {get_machine_id()}")
    print()
    print("=" * 60)