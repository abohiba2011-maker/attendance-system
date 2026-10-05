import os
import sqlite3
import datetime
import io
import hashlib
import hmac
import qrcode
import secrets
import subprocess
import platform
import time
import pandas as pd
import shutil
import threading
import json
from functools import wraps
from flask import (Flask, request, redirect, url_for, render_template,
                   jsonify, send_file, session, flash)
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ✅ نظام الترخيص
from license_manager import get_machine_id, save_license_key, load_license_key
from license_check import check_license, get_license_status_text
from license_generator import verify_serial

# ==================================================
#         ✅ مكتبة PDF (pdfkit)
# ==================================================
PDF_AVAILABLE = False
PDF_LIBRARY = None
PDFKIT_CONFIG = None

try:
    import pdfkit

    WKHTMLTOPDF_PATHS = [
        r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe',
        r'C:\Program Files (x86)\wkhtmltopdf\bin\wkhtmltopdf.exe',
    ]

    WKHTMLTOPDF_PATH = None
    for path in WKHTMLTOPDF_PATHS:
        if os.path.exists(path):
            WKHTMLTOPDF_PATH = path
            break

    if WKHTMLTOPDF_PATH:
        PDFKIT_CONFIG = pdfkit.configuration(wkhtmltopdf=WKHTMLTOPDF_PATH)
        PDF_AVAILABLE = True
        PDF_LIBRARY = 'pdfkit'
        print(f"✅ PDF: pdfkit يعمل")
        print(f"   المسار: {WKHTMLTOPDF_PATH}")
    else:
        print(f"⚠️ wkhtmltopdf غير موجود")
except ImportError:
    print("⚠️ pdfkit غير مثبت — نفّذ: pip install pdfkit")


# ==================================================
#                    الإعدادات
# ==================================================
app = Flask(__name__)
app.secret_key = 'super_secret_key_attendance_2026_change_me'
app.permanent_session_lifetime = datetime.timedelta(hours=1)

SECRET_HMAC_KEY = b'dynamic_qr_super_secret_key_2026'

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = hashlib.sha256("admin123".encode()).hexdigest()
HOME_PASSWORD_HASH = hashlib.sha256("home123".encode()).hexdigest()
ADMIN_2FA_HASH = hashlib.sha256("secret2026".encode()).hexdigest()
MANAGE_PASSWORD_HASH = hashlib.sha256("manage2026".encode()).hexdigest()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
QR_FOLDER = os.path.join(BASE_DIR, 'static', 'qr_codes')
BACKUP_FOLDER = os.path.join(BASE_DIR, 'backups')
STATIC_FOLDER = os.path.join(BASE_DIR, 'static')
ALLOWED_IPS_FILE = os.path.join(BASE_DIR, 'allowed_ips.txt')

for folder in [UPLOAD_FOLDER, QR_FOLDER, BACKUP_FOLDER, STATIC_FOLDER]:
    os.makedirs(folder, exist_ok=True)

DB_NAME = os.path.join(BASE_DIR, 'attendance.db')

WEEKDAYS_AR = {
    0: 'الأحد', 1: 'الاثنين', 2: 'الثلاثاء', 3: 'الأربعاء',
    4: 'الخميس', 5: 'الجمعة', 6: 'السبت'
}

# ✅ الأسباب المبررة
JUSTIFIED_REASONS = [
    'صلاة', 'غداء', 'موعد طبي', 'ظرف طارئ', 'مهمة خارجية', 'راحة قصيرة'
]


def get_server_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ==================================================
#         الأجهزة المسموحة لـ /
# ==================================================
def get_allowed_ips():
    allowed = ['127.0.0.1', 'localhost']
    if os.path.exists(ALLOWED_IPS_FILE):
        try:
            with open(ALLOWED_IPS_FILE, 'r') as f:
                for line in f:
                    ip = line.strip()
                    if ip and not ip.startswith('#'):
                        allowed.append(ip)
        except Exception:
            pass
    return allowed


def save_allowed_ips(ips_list):
    try:
        with open(ALLOWED_IPS_FILE, 'w') as f:
            f.write("# IPs المسموح لها بالوصول لصفحة /\n\n")
            for ip in ips_list:
                f.write(f"{ip}\n")
        return True
    except Exception:
        return False


# ==================================================
#                  دوال مساعدة
# ==================================================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def get_db_connection():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def log_audit(action, details):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute(
            "INSERT INTO audit_logs (action, details, timestamp) VALUES (?, ?, ?)",
            (action, details, now_str)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_setting(key, default_val=''):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row['value'] if row else default_val


def set_setting(key, value):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, str(value))
    )
    conn.commit()
    conn.close()


def create_auto_backup():
    if os.path.exists(DB_NAME):
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        backup_path = os.path.join(BACKUP_FOLDER, f'attendance_backup_{today_str}.db')
        if not os.path.exists(backup_path):
            try:
                shutil.copy(DB_NAME, backup_path)
                cleanup_old_backups(30)
            except Exception:
                pass


def cleanup_old_backups(keep_days=30):
    cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
    for f in os.listdir(BACKUP_FOLDER):
        path = os.path.join(BACKUP_FOLDER, f)
        try:
            if os.path.getmtime(path) < cutoff.timestamp():
                os.remove(path)
        except Exception:
            pass


def py_to_arabic_dow(py_dow):
    mapping = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}
    return mapping[py_dow]


# ==================================================
#                تهيئة قاعدة البيانات
# ==================================================
def init_db():
    create_auto_backup()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT,
            phone TEXT,
            address TEXT,
            photo TEXT,
            qr_code TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT
        )
    ''')

    cursor.execute("PRAGMA table_info(employees)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'personal_token' not in columns:
        cursor.execute("ALTER TABLE employees ADD COLUMN personal_token TEXT")
    if 'locked_ip' not in columns:
        cursor.execute("ALTER TABLE employees ADD COLUMN locked_ip TEXT")
    if 'pin_hash' not in columns:
        cursor.execute("ALTER TABLE employees ADD COLUMN pin_hash TEXT")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employee_shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_code TEXT NOT NULL,
            shift_name TEXT NOT NULL,
            shift_start TEXT NOT NULL,
            shift_end TEXT NOT NULL,
            grace_period INTEGER DEFAULT 15,
            active INTEGER DEFAULT 1,
            UNIQUE(emp_code, shift_name)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS weekly_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_code TEXT NOT NULL,
            day_of_week INTEGER NOT NULL,
            shift_name TEXT,
            is_rest_day INTEGER DEFAULT 0,
            UNIQUE(emp_code, day_of_week)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_code TEXT NOT NULL,
            shift_name TEXT,
            date TEXT NOT NULL,
            check_in TEXT,
            check_out TEXT,
            event_type TEXT,
            leave_reason_id INTEGER,
            duration_min INTEGER,
            late_minutes INTEGER DEFAULT 0,
            is_justified INTEGER DEFAULT 1,
            notes TEXT,
            created_at TEXT
        )
    ''')

    cursor.execute("PRAGMA table_info(attendance)")
    att_cols = [c[1] for c in cursor.fetchall()]
    if 'is_justified' not in att_cols:
        cursor.execute("ALTER TABLE attendance ADD COLUMN is_justified INTEGER DEFAULT 1")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leave_reasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reason_name TEXT NOT NULL UNIQUE,
            icon TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS owner_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            message TEXT,
            type TEXT,
            status TEXT,
            created_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            details TEXT,
            timestamp TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS device_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT UNIQUE NOT NULL,
            emp_code TEXT NOT NULL,
            last_ip TEXT,
            registered_at TEXT,
            last_seen TEXT
        )
    ''')

    cursor.execute("PRAGMA table_info(device_registry)")
    dr_cols = [c[1] for c in cursor.fetchall()]
    if 'last_ip' not in dr_cols:
        cursor.execute("ALTER TABLE device_registry ADD COLUMN last_ip TEXT")
    if 'last_seen' not in dr_cols:
        cursor.execute("ALTER TABLE device_registry ADD COLUMN last_seen TEXT")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS presence_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_code TEXT NOT NULL,
            ip_address TEXT,
            status TEXT,
            timestamp TEXT,
            date TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS absences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_code TEXT NOT NULL,
            date TEXT NOT NULL,
            reason TEXT,
            recorded_by TEXT,
            created_at TEXT,
            UNIQUE(emp_code, date)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leave_balance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_code TEXT NOT NULL UNIQUE,
            annual_quota INTEGER DEFAULT 30,
            used_days INTEGER DEFAULT 0,
            updated_at TEXT
        )
    ''')

    cursor.execute("SELECT id FROM employees WHERE personal_token IS NULL OR personal_token = ''")
    for row in cursor.fetchall():
        token = secrets.token_urlsafe(16)
        cursor.execute("UPDATE employees SET personal_token = ? WHERE id = ?", (token, row['id']))

    defaults = {
        'owner_phone': '+213669659661',
        'daily_report_time': '23:30',
        'weekly_report_day': '6',
        'weekly_report_time': '09:00',
        'whatsapp_enabled': '1',
        'notify_daily': '1',
        'notify_weekly': '1',
        'lock_ip_to_token': '0',
        'pin_required': '0',
        'presence_enabled': '0',
        'presence_interval': '300',
        'default_annual_quota': '30',
        'working_days_per_month': '26',
    }
    for k, v in defaults.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # ✅ الأسباب مع "بدون سبب"
    default_reasons = [
        ('صلاة', '🕌'),
        ('غداء', '🍽️'),
        ('موعد طبي', '🏥'),
        ('ظرف طارئ', '📞'),
        ('مهمة خارجية', '🚗'),
        ('راحة قصيرة', '☕'),
        ('بدون سبب', '⚠️'),
    ]
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for name, icon in default_reasons:
        cursor.execute(
            "INSERT OR IGNORE INTO leave_reasons (reason_name, icon, active, created_at) VALUES (?, ?, 1, ?)",
            (name, icon, now_str)
        )

    conn.commit()
    conn.close()


init_db()


# ==================================================
#         توليد الأكواد
# ==================================================
def generate_qr_data(emp_code):
    today_compact = datetime.date.today().strftime('%Y%m%d')
    message = f"{emp_code}:{today_compact}".encode('utf-8')
    sig = hmac.new(SECRET_HMAC_KEY, message, hashlib.sha256).hexdigest()[:16]
    return f"{emp_code}.{today_compact}.{sig}"


def generate_short_daily_code(emp_code):
    today = datetime.date.today().strftime('%Y%m%d')
    message = f"{emp_code}:{today}:SHORT".encode('utf-8')
    sig_hex = hmac.new(SECRET_HMAC_KEY, message, hashlib.sha256).hexdigest()
    num = int(sig_hex[:6], 16) % 10000
    return f"{num:04d}"


def verify_short_code(code_input):
    code_input = code_input.strip()
    if not code_input.isdigit():
        return None, "الكود يجب أن يكون أرقاماً"
    if len(code_input) != 4:
        return None, "الكود يجب أن يكون 4 أرقام"

    today = datetime.date.today().strftime('%Y%m%d')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT code FROM employees WHERE active=1")
    all_emps = cursor.fetchall()
    conn.close()

    for emp in all_emps:
        message = f"{emp['code']}:{today}:SHORT".encode('utf-8')
        sig_hex = hmac.new(SECRET_HMAC_KEY, message, hashlib.sha256).hexdigest()
        expected = f"{int(sig_hex[:6], 16) % 10000:04d}"
        if hmac.compare_digest(code_input, expected):
            return emp['code'], None

    return None, "❌ الكود غير صحيح أو انتهت صلاحيته"


def verify_dynamic_token(scanned_text):
    scanned_text = scanned_text.strip()
    scanned_text = scanned_text.replace('\t', '').replace('\n', '').replace('\r', '')
    scanned_text = ''.join(c for c in scanned_text if ord(c) >= 32)

    for sep in ['/', '°', '*', ',', ';', '|', ':', '-', '_']:
        scanned_text = scanned_text.replace(sep, '.')

    today_compact = datetime.date.today().strftime('%Y%m%d')
    today_short = datetime.date.today().strftime('%y%m%d')

    parts = [p for p in scanned_text.split('.') if p]

    if len(parts) == 3:
        emp_code = parts[0].strip()
        token_date = parts[1].strip()
        scanned_sig = parts[2].strip()

        if token_date == today_compact:
            message = f"{emp_code}:{token_date}".encode('utf-8')
            expected_hex = hmac.new(SECRET_HMAC_KEY, message, hashlib.sha256).hexdigest()
            expected_numeric = str(int(expected_hex[:8], 16) % 100000).zfill(5)
            if hmac.compare_digest(scanned_sig, expected_numeric):
                return emp_code, None
            expected_hex_16 = expected_hex[:16]
            for candidate in [scanned_sig.lower(), scanned_sig.lower().replace('q', 'a')]:
                if hmac.compare_digest(candidate, expected_hex_16):
                    return emp_code, None

        if token_date == today_short and len(scanned_sig) == 4:
            message = f"{emp_code}:{token_date}".encode('utf-8')
            expected = hmac.new(SECRET_HMAC_KEY, message, hashlib.sha256).hexdigest()[:4].upper()
            if hmac.compare_digest(scanned_sig.upper(), expected):
                return emp_code, None

    if scanned_text and len(scanned_text) <= 20:
        return scanned_text, None

    return None, "تنسيق QR غير معروف"


# ==================================================
#         الورديات
# ==================================================
def get_today_shift(emp_code):
    py_dow = datetime.datetime.now().weekday()
    arabic_dow = py_to_arabic_dow(py_dow)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM weekly_schedule
        WHERE TRIM(emp_code) = TRIM(?) AND day_of_week = ?
    ''', (emp_code, arabic_dow))
    sched = cursor.fetchone()

    if not sched:
        conn.close()
        return None, "لم يتم إعداد جدول المناوبات لهذا اليوم"

    if sched['is_rest_day']:
        conn.close()
        return None, "يوم راحة"

    shift_name = sched['shift_name']
    cursor.execute('''
        SELECT * FROM employee_shifts
        WHERE TRIM(emp_code) = TRIM(?) AND shift_name = ? AND active = 1
    ''', (emp_code, shift_name))
    shift = cursor.fetchone()
    conn.close()

    if not shift:
        return None, f"لم يتم إعداد وردية {shift_name}"

    return dict(shift), None


def get_leave_reasons():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM leave_reasons WHERE active = 1 ORDER BY id")
    reasons = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return reasons


def get_today_counts():
    conn = get_db_connection()
    cursor = conn.cursor()
    today = datetime.date.today().strftime('%Y-%m-%d')

    cursor.execute(
        "SELECT COUNT(DISTINCT emp_code) FROM attendance WHERE date=? AND event_type='in'",
        (today,)
    )
    present_count = cursor.fetchone()[0] or 0

    cursor.execute(
        "SELECT COUNT(*) FROM attendance WHERE date=? AND event_type='in' AND late_minutes > 0",
        (today,)
    )
    late_count = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM employees WHERE active = 1")
    total_emp = cursor.fetchone()[0] or 0
    conn.close()

    return present_count, late_count, total_emp


# ==================================================
#         حالة الموظف
# ==================================================
def get_today_records(emp_code):
    today = datetime.date.today().strftime('%Y-%m-%d')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM attendance
        WHERE TRIM(emp_code) = TRIM(?) AND date = ?
        ORDER BY id ASC
    ''', (emp_code, today))
    records = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return records


def get_current_state(records):
    if not records:
        return 'not_checked_in'
    last = records[-1]
    ev = last.get('event_type', '')
    if ev in ['in', 'return']:
        return 'checked_in'
    elif ev == 'leave':
        return 'on_leave'
    elif ev == 'out':
        return 'checked_out'
    return 'not_checked_in'


# ==================================================
#              تسجيل الحضور
# ==================================================
def record_attendance_logic(emp_code, action='auto', leave_reason_id=None, recorded_by='self'):
    """
    تسجيل الحضور/الانصراف
    القواعد:
    - منع نفس النوع مرتين متتاليتين
    - السماح بـ out → in (عودة بعد انصراف) بعد 30 ثانية
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM employees WHERE TRIM(code) = TRIM(?) AND active = 1", (emp_code,))
    employee = cursor.fetchone()
    if not employee:
        conn.close()
        return False, "الموظف غير مسجل أو غير مفعّل"

    emp_name = f"{employee['first_name']} {employee['last_name'] or ''}".strip()

    shift, error = get_today_shift(emp_code)
    if error:
        conn.close()
        return False, f"⚠️ {emp_name}: {error}"

    shift_name = shift['shift_name']
    shift_start = shift['shift_start']
    grace = shift['grace_period']

    now = datetime.datetime.now()
    current_date = now.strftime('%Y-%m-%d')
    current_time = now.strftime('%H:%M:%S')
    current_hm = now.strftime('%H:%M')

    records = get_today_records(emp_code)
    state = get_current_state(records)

    source_tag = " (بواسطة الأدمن)" if recorded_by == 'admin' else ""

    last_event = None
    last_record = None
    if records:
        last_record = records[-1]
        last_event = last_record.get('event_type', '')

    if state == 'not_checked_in':
        record_type = 'in'
    elif state == 'checked_in':
        if action == 'leave':
            record_type = 'leave'
        else:
            record_type = 'out'
    elif state == 'on_leave':
        record_type = 'return'
    elif state == 'checked_out':
        record_type = 'in'

        if last_record and last_record.get('created_at'):
            try:
                last_time = datetime.datetime.strptime(last_record['created_at'], '%Y-%m-%d %H:%M:%S')
                diff_seconds = (now - last_time).total_seconds()
                if diff_seconds < 300:
                    conn.close()
                    remaining = int(300 - diff_seconds / 60) + 1
                    return False, f"⚠️ {emp_name} أنهى دوامه للتو — انتظر {remaining} ثانية"
            except Exception:
                pass
    else:
        record_type = 'in'

    if last_event == record_type:
        type_ar = {
            'in': 'حضور',
            'out': 'انصراف نهائي',
            'leave': 'انصراف',
            'return': 'عودة',
        }
        type_name = type_ar.get(record_type, record_type)

        time_str = ''
        if last_record and last_record.get('created_at'):
            try:
                last_time = datetime.datetime.strptime(last_record['created_at'], '%Y-%m-%d %H:%M:%S')
                time_str = f" ({last_time.strftime('%H:%M')})"
            except Exception:
                pass

        conn.close()
        return False, f"⚠️ {emp_name} سجّل ({type_name}) بالفعل اليوم{time_str}"

    if record_type == 'in':
        is_return_after_out = (last_event == 'out')

        late_mins = 0
        if not is_return_after_out:
            try:
                t_start = datetime.datetime.strptime(shift_start, "%H:%M")
                t_now = datetime.datetime.strptime(current_hm, "%H:%M")
                t_deadline = t_start + datetime.timedelta(minutes=grace)
                if t_now > t_deadline:
                    diff = t_now - t_start
                    late_mins = int(diff.total_seconds() // 60)
            except Exception:
                pass

        note = f'recorded_by:{recorded_by}'
        if is_return_after_out:
            note += '|return_after_out'

        cursor.execute('''
            INSERT INTO attendance
            (emp_code, shift_name, date, check_in, event_type, late_minutes, is_justified, notes, created_at)
            VALUES (?, ?, ?, ?, 'in', ?, 1, ?, ?)
        ''', (emp_code, shift_name, current_date, current_time, late_mins, note, current_time))
        conn.commit()
        conn.close()

        if is_return_after_out:
            return True, f"🔄 {emp_name} - عودة بعد انصراف ({shift_name}){source_tag}"

        if late_mins > 0:
            return True, f"✅ {emp_name} - حضور ({shift_name}) - تأخير {late_mins} د{source_tag}"
        return True, f"✅ {emp_name} - حضور ({shift_name}){source_tag}"

    elif record_type == 'leave':
        if not leave_reason_id:
            conn.close()
            return False, "يجب اختيار سبب الانصراف"

        cursor.execute("SELECT * FROM leave_reasons WHERE id=? AND active=1", (leave_reason_id,))
        reason = cursor.fetchone()
        if not reason:
            conn.close()
            return False, "السبب غير متاح"

        is_justified = 1 if reason['reason_name'] in JUSTIFIED_REASONS else 0

        cursor.execute('''
            INSERT INTO attendance
            (emp_code, shift_name, date, check_out, event_type, leave_reason_id,
             is_justified, notes, created_at)
            VALUES (?, ?, ?, ?, 'leave', ?, ?, ?, ?)
        ''', (emp_code, shift_name, current_date, current_time, leave_reason_id,
              is_justified, f'recorded_by:{recorded_by}', current_time))
        conn.commit()
        conn.close()

        status_tag = "✅ مبرر" if is_justified else "⚠️ غير مبرر"
        return True, f"🚪 {emp_name} - {reason['icon']} {reason['reason_name']} ({status_tag}){source_tag}"

    elif record_type == 'return':
        last_leave = None
        for r in reversed(records):
            if r['event_type'] == 'leave':
                last_leave = r
                break

        duration_min = 0
        if last_leave and last_leave['check_out']:
            try:
                t_out = datetime.datetime.strptime(last_leave['check_out'], '%H:%M:%S')
                t_now = datetime.datetime.strptime(current_time, '%H:%M:%S')
                duration_min = int((t_now - t_out).total_seconds() // 60)
                if duration_min < 0:
                    duration_min = 0
            except Exception:
                pass

        if last_leave:
            cursor.execute(
                "UPDATE attendance SET duration_min = ? WHERE id = ?",
                (duration_min, last_leave['id'])
            )

        cursor.execute('''
            INSERT INTO attendance
            (emp_code, shift_name, date, check_in, event_type, is_justified, notes, created_at)
            VALUES (?, ?, ?, ?, 'return', 1, ?, ?)
        ''', (emp_code, shift_name, current_date, current_time,
              f'recorded_by:{recorded_by}', current_time))
        conn.commit()
        conn.close()
        return True, f"🔄 {emp_name} - عودة (بعد {duration_min} د){source_tag}"
    elif record_type == 'out':
        # ✅ فحص: هل مرّ 5 دقائق من الحضور الأول؟
        # هذا يُطبَّق فقط إذا كانت آخر حركة = in
        if last_record and last_record.get('created_at') and last_record.get('event_type') == 'in':
            try:
                last_time = datetime.datetime.strptime(last_record['created_at'], '%Y-%m-%d %H:%M:%S')
                diff_seconds = (now - last_time).total_seconds()

                if diff_seconds < 300:  # 5 دقائق
                    remaining_min = int((300 - diff_seconds) / 60) + 1
                    conn.close()
                    return False, f"⚠️ {emp_name} — انتظر {remaining_min} دقيقة قبل الانصراف"
            except Exception:
                pass

        cursor.execute('''
            INSERT INTO attendance
            (emp_code, shift_name, date, check_out, event_type, is_justified, notes, created_at)
            VALUES (?, ?, ?, ?, 'out', 1, ?, ?)
        ''', (emp_code, shift_name, current_date, current_time,
              f'recorded_by:{recorded_by}', current_time))
        conn.commit()
        conn.close()
        return True, f"📤 {emp_name} - انصراف نهائي{source_tag}"
    
    conn.close()
    return False, "حالة غير معروفة"


def finalize_unclosed_leaves(emp_code, date_str):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM attendance
        WHERE TRIM(emp_code)=TRIM(?) AND date=?
        ORDER BY id ASC
    ''', (emp_code, date_str))
    records = [dict(r) for r in cursor.fetchall()]

    if not records:
        conn.close()
        return

    state = get_current_state(records)
    if state == 'on_leave':
        last_leave = None
        for r in reversed(records):
            if r['event_type'] == 'leave':
                last_leave = r
                break

        if last_leave:
            shift_name = last_leave['shift_name']
            cursor.execute('''
                SELECT shift_end FROM employee_shifts
                WHERE TRIM(emp_code)=TRIM(?) AND shift_name=?
            ''', (emp_code, shift_name))
            sh = cursor.fetchone()

            if sh:
                t_end = sh['shift_end']
                t_out = last_leave['check_out'] or '00:00:00'
                try:
                    tt_out = datetime.datetime.strptime(t_out, '%H:%M:%S')
                    tt_end = datetime.datetime.strptime(t_end + ':00', '%H:%M:%S')
                    dur = int((tt_end - tt_out).total_seconds() // 60)
                    if dur < 0:
                        dur = 0
                    cursor.execute(
                        "UPDATE attendance SET duration_min=? WHERE id=?",
                        (dur, last_leave['id'])
                    )
                    conn.commit()
                except Exception:
                    pass

    conn.close()


# ==================================================
#         كشف التواجد
# ==================================================
def ping_host(ip, timeout=2):
    if not ip:
        return False
    try:
        param = '-n' if platform.system().lower() == 'windows' else '-c'
        cmd = ['ping', param, '1']
        if platform.system().lower() == 'windows':
            cmd += ['-w', str(timeout * 1000)]
        else:
            cmd += ['-W', str(timeout)]
        cmd.append(ip)

        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 1, text=True)
        return result.returncode == 0
    except Exception:
        return False


def log_presence(emp_code, ip, status):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        now = datetime.datetime.now()
        cursor.execute('''
            INSERT INTO presence_log (emp_code, ip_address, status, timestamp, date)
            VALUES (?, ?, ?, ?, ?)
        ''', (emp_code, ip, status,
              now.strftime('%Y-%m-%d %H:%M:%S'),
              now.strftime('%Y-%m-%d')))
        conn.commit()
        conn.close()
    except Exception:
        pass


def record_presence_leave(emp_code, ip):
    today = datetime.date.today().strftime('%Y-%m-%d')
    now = datetime.datetime.now().strftime('%H:%M:%S')
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM attendance
            WHERE TRIM(emp_code) = TRIM(?) AND date = ?
            ORDER BY id DESC LIMIT 1
        ''', (emp_code, today))
        last = cursor.fetchone()
        if not last:
            conn.close()
            return
        if last['event_type'] in ['in', 'return']:
            cursor.execute('''
                INSERT INTO attendance
                (emp_code, date, check_out, event_type, is_justified, notes, created_at)
                VALUES (?, ?, ?, 'leave', 1, '🚪 انصراف تلقائي (WiFi)', ?)
            ''', (emp_code, today, now, now))
            conn.commit()
            log_audit("انصراف تلقائي", f"{emp_code} - WiFi cut")
        conn.close()
    except Exception:
        pass


def record_presence_return(emp_code, ip):
    today = datetime.date.today().strftime('%Y-%m-%d')
    now = datetime.datetime.now().strftime('%H:%M:%S')
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM attendance
            WHERE TRIM(emp_code) = TRIM(?) AND date = ? AND event_type = 'leave'
            ORDER BY id DESC LIMIT 1
        ''', (emp_code, today))
        last_leave = cursor.fetchone()

        duration = 0
        if last_leave and last_leave['check_out']:
            try:
                t1 = datetime.datetime.strptime(last_leave['check_out'], '%H:%M:%S')
                t2 = datetime.datetime.strptime(now, '%H:%M:%S')
                duration = int((t2 - t1).total_seconds() // 60)
                if duration < 0:
                    duration = 0
            except Exception:
                pass

        if last_leave:
            cursor.execute("UPDATE attendance SET duration_min = ? WHERE id = ?",
                          (duration, last_leave['id']))

        cursor.execute('''
            INSERT INTO attendance
            (emp_code, date, check_in, event_type, is_justified, notes, created_at)
            VALUES (?, ?, ?, 'return', 1, '🔄 عودة تلقائية (WiFi)', ?)
        ''', (emp_code, today, now, now))
        conn.commit()
        conn.close()
        log_audit("عودة تلقائية", f"{emp_code} - WiFi restored")
    except Exception:
        pass


def check_presence_once():
    today = datetime.date.today().strftime('%Y-%m-%d')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT a.emp_code, e.first_name, e.last_name
        FROM attendance a
        LEFT JOIN employees e ON TRIM(a.emp_code) = TRIM(e.code)
        WHERE a.date = ?
          AND a.emp_code NOT IN (
              SELECT emp_code FROM attendance
              WHERE date = ? AND event_type = 'out'
          )
    ''', (today, today))
    employees_present = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not employees_present:
        return

    results = []
    for emp in employees_present:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT last_ip FROM device_registry
            WHERE TRIM(emp_code) = TRIM(?)
            ORDER BY id DESC LIMIT 1
        ''', (emp['emp_code'],))
        dev = cursor.fetchone()
        conn.close()

        ip = dev['last_ip'] if dev else None
        is_online = ping_host(ip) if ip else False
        results.append({
            'emp_code': emp['emp_code'],
            'ip': ip,
            'is_online': is_online
        })

    if results and not any(r['is_online'] for r in results):
        return

    for r in results:
        if not r['ip']:
            continue

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT status FROM presence_log
            WHERE TRIM(emp_code) = TRIM(?) AND date = ?
            ORDER BY id DESC LIMIT 1
        ''', (r['emp_code'], today))
        last = cursor.fetchone()
        conn.close()

        last_status = last['status'] if last else None

        if r['is_online']:
            if last_status == 'away':
                log_presence(r['emp_code'], r['ip'], 'returned')
                record_presence_return(r['emp_code'], r['ip'])
            elif last_status != 'present':
                log_presence(r['emp_code'], r['ip'], 'present')
        else:
            if last_status == 'present':
                log_presence(r['emp_code'], r['ip'], 'away')
                record_presence_leave(r['emp_code'], r['ip'])

    for r in results:
        if r['is_online'] and r['ip']:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute('''
                    UPDATE device_registry SET last_seen = ?
                    WHERE TRIM(emp_code) = TRIM(?) AND last_ip = ?
                ''', (now_str, r['emp_code'], r['ip']))
                conn.commit()
                conn.close()
            except Exception:
                pass


def presence_monitor_loop():
    time.sleep(30)
    while True:
        try:
            if get_setting('presence_enabled', '0') == '1':
                check_presence_once()
        except Exception:
            pass
        try:
            interval = int(get_setting('presence_interval', '300'))
        except Exception:
            interval = 300
        time.sleep(interval)


def start_presence_monitor():
    t = threading.Thread(target=presence_monitor_loop, daemon=True)
    t.start()
    print("✅ Presence Monitor يعمل")


# ==================================================
#         بصمة الجهاز
# ==================================================
def check_device_fingerprint(fingerprint, emp_code, client_ip=''):
    if not fingerprint or fingerprint.strip() == '':
        if client_ip:
            fingerprint = f"IP_{client_ip}"
        else:
            return True, ""

    fingerprint = fingerprint.strip()[:200]

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT emp_code FROM device_registry WHERE fingerprint = ?
    ''', (fingerprint,))
    row = cursor.fetchone()

    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if row:
        registered_emp = row['emp_code']
        if registered_emp != emp_code:
            cursor.execute('''
                SELECT first_name, last_name FROM employees
                WHERE TRIM(code) = TRIM(?) AND active = 1
            ''', (registered_emp,))
            other = cursor.fetchone()
            conn.close()
            other_name = f"{other['first_name']} {other['last_name'] or ''}".strip() if other else "موظف آخر"
            return False, f"❌ هذا الهاتف مسجّل لـ {other_name}"

        if client_ip:
            cursor.execute('''
                UPDATE device_registry SET last_ip = ?, last_seen = ?
                WHERE fingerprint = ?
            ''', (client_ip, now_str, fingerprint))
            conn.commit()

        conn.close()
        return True, ""
    else:
        try:
            cursor.execute('''
                INSERT INTO device_registry (fingerprint, emp_code, last_ip, registered_at, last_seen)
                VALUES (?, ?, ?, ?, ?)
            ''', (fingerprint, emp_code, client_ip, now_str, now_str))
            conn.commit()
        except sqlite3.IntegrityError:
            pass
        conn.close()
        return True, ""


# ==================================================
#         دوال التقارير المتقدمة
# ==================================================
def get_leave_balance(emp_code):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM leave_balance WHERE TRIM(emp_code) = TRIM(?)", (emp_code,))
    row = cursor.fetchone()

    if not row:
        default_quota = int(get_setting('default_annual_quota', '30'))
        cursor.execute('''
            INSERT INTO leave_balance (emp_code, annual_quota, used_days, updated_at)
            VALUES (?, ?, 0, ?)
        ''', (emp_code, default_quota, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        cursor.execute("SELECT * FROM leave_balance WHERE TRIM(emp_code) = TRIM(?)", (emp_code,))
        row = cursor.fetchone()

    conn.close()

    if row:
        quota = row['annual_quota']
        used = row['used_days']
        return {
            'quota': quota,
            'used': used,
            'remaining': max(0, quota - used)
        }
    return {'quota': 30, 'used': 0, 'remaining': 30}


def calculate_employee_stats(emp_code, start_date, end_date):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT COUNT(DISTINCT date) as days_present
        FROM attendance
        WHERE TRIM(emp_code) = TRIM(?)
          AND date BETWEEN ? AND ?
          AND event_type = 'in'
    ''', (emp_code, start_date, end_date))
    days_present = cursor.fetchone()['days_present'] or 0

    cursor.execute('''
        SELECT COUNT(*) as late_count, COALESCE(SUM(late_minutes), 0) as total_late
        FROM attendance
        WHERE TRIM(emp_code) = TRIM(?)
          AND date BETWEEN ? AND ?
          AND event_type = 'in'
          AND late_minutes > 0
    ''', (emp_code, start_date, end_date))
    late_row = cursor.fetchone()
    late_count = late_row['late_count'] or 0
    total_late = late_row['total_late'] or 0

    cursor.execute('''
        SELECT date,
               MIN(CASE WHEN event_type = 'in' THEN check_in END) as first_in,
               MAX(CASE WHEN event_type = 'out' THEN check_out END) as last_out,
               COALESCE(SUM(CASE WHEN event_type = 'leave' THEN duration_min ELSE 0 END), 0) as leave_total
        FROM attendance
        WHERE TRIM(emp_code) = TRIM(?)
          AND date BETWEEN ? AND ?
        GROUP BY date
    ''', (emp_code, start_date, end_date))
    daily_records = cursor.fetchall()

    total_work_min = 0
    for rec in daily_records:
        if rec['first_in'] and rec['last_out']:
            try:
                t1 = datetime.datetime.strptime(rec['first_in'], '%H:%M:%S')
                t2 = datetime.datetime.strptime(rec['last_out'], '%H:%M:%S')
                diff = int((t2 - t1).total_seconds() // 60)
                work_min = max(0, diff - (rec['leave_total'] or 0))
                total_work_min += work_min
            except Exception:
                pass

    total_hours = total_work_min / 60

    cursor.execute('''
        SELECT
            COUNT(CASE WHEN is_justified = 1 THEN 1 END) as justified_count,
            COUNT(CASE WHEN is_justified = 0 THEN 1 END) as unjustified_count,
            COALESCE(SUM(CASE WHEN is_justified = 1 THEN duration_min ELSE 0 END), 0) as justified_min,
            COALESCE(SUM(CASE WHEN is_justified = 0 THEN duration_min ELSE 0 END), 0) as unjustified_min,
            COUNT(*) as total_leaves,
            COALESCE(SUM(duration_min), 0) as leaves_total
        FROM attendance
        WHERE TRIM(emp_code) = TRIM(?)
          AND date BETWEEN ? AND ?
          AND event_type = 'leave'
    ''', (emp_code, start_date, end_date))
    leaves_stats = cursor.fetchone()

    justified_count = leaves_stats['justified_count'] or 0
    unjustified_count = leaves_stats['unjustified_count'] or 0
    justified_min = leaves_stats['justified_min'] or 0
    unjustified_min = leaves_stats['unjustified_min'] or 0
    leaves_count = leaves_stats['total_leaves'] or 0
    leaves_total = leaves_stats['leaves_total'] or 0

    cursor.execute('''
        SELECT COUNT(*) as absent_days
        FROM absences
        WHERE TRIM(emp_code) = TRIM(?)
          AND date BETWEEN ? AND ?
    ''', (emp_code, start_date, end_date))
    absent_days = cursor.fetchone()['absent_days'] or 0

    conn.close()

    working_days = int(get_setting('working_days_per_month', '26'))

    try:
        start_dt = datetime.datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.datetime.strptime(end_date, '%Y-%m-%d')
        total_days = (end_dt - start_dt).days + 1
        expected_days = int(total_days * 6 / 7)
    except Exception:
        expected_days = working_days

    if expected_days > 0:
        compliance = round((days_present / expected_days) * 100, 1)
    else:
        compliance = 0

    return {
        'days_present': days_present,
        'absent_days': absent_days,
        'late_count': late_count,
        'total_late': total_late,
        'total_work_hours': round(total_hours, 1),
        'leaves_count': leaves_count,
        'leaves_total': leaves_total,
        'justified_count': justified_count,
        'unjustified_count': unjustified_count,
        'justified_min': justified_min,
        'unjustified_min': unjustified_min,
        'expected_days': expected_days,
        'compliance': min(100, compliance)
    }


# ==================================================
#         فحص الترخيص قبل كل طلب
# ==================================================
@app.before_request
def license_middleware():
    allowed_prefixes = ['/license', '/activate', '/static', '/favicon']
    allowed_exacts = ['/lock_home']

    if request.path in allowed_exacts:
        return None

    for prefix in allowed_prefixes:
        if request.path.startswith(prefix):
            return None

    try:
        status, message, days, data = check_license()
    except Exception as e:
        print(f"⚠️ خطأ فحص الترخيص: {e}")
        return None

    if status in ('missing', 'invalid', 'expired'):
        return redirect(url_for('license_page'))

    if status == 'warning':
        session['license_warning'] = message
        session['license_days'] = days

    return None


# ==================================================
#         صفحات الترخيص
# ==================================================
@app.route('/license')
def license_page():
    machine_id = get_machine_id()
    status, message, days, data = check_license()

    return render_template('license.html',
                         machine_id=machine_id,
                         status=status,
                         message=message,
                         days_remaining=days)


@app.route('/activate', methods=['POST'])
def activate_license():
    serial = request.form.get('serial', '').strip()

    if not serial:
        flash('⚠️ الرجاء إدخال السيريال', 'error')
        return redirect(url_for('license_page'))

    machine_id = get_machine_id()
    valid, result = verify_serial(serial, machine_id)

    if not valid:
        flash(f'❌ {result}', 'error')
        log_audit("محاولة تفعيل فاشلة", str(result)[:100])
        return redirect(url_for('license_page'))

    data_to_save = {
        'serial': serial,
        'machine_id': machine_id,
        'expires': result['expires'],
        'issued': result['issued']
    }

    if save_license_key(data_to_save):
        log_audit("تفعيل ناجح", f"ينتهي: {result['expires']}")
        flash(f'✅ تم التفعيل — صالح حتى {result["expires"]}', 'success')
        return redirect(url_for('license_page'))
    else:
        flash('❌ فشل حفظ السيريال', 'error')
        return redirect(url_for('license_page'))


# ==================================================
#         تسجيل غياب موظف
# ==================================================
@app.route('/record_absent', methods=['POST'])
def record_absent():
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'بيانات غير صالحة'})

    emp_code = data.get('emp_code', '').strip()
    reason = data.get('reason', '').strip()

    if not emp_code:
        return jsonify({'status': 'error', 'message': 'الرجاء إدخال كود الموظف'})

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM employees WHERE TRIM(code) = TRIM(?) AND active = 1", (emp_code,))
    employee = cursor.fetchone()

    if not employee:
        conn.close()
        return jsonify({'status': 'error', 'message': 'الموظف غير مسجل'})

    emp_name = f"{employee['first_name']} {employee['last_name'] or ''}".strip()
    today = datetime.date.today().strftime('%Y-%m-%d')
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('''
        SELECT id FROM absences WHERE TRIM(emp_code) = TRIM(?) AND date = ?
    ''', (emp_code, today))
    if cursor.fetchone():
        conn.close()
        return jsonify({'status': 'error', 'message': f'⚠️ {emp_name} مسجّل غائباً مسبقاً اليوم'})

    cursor.execute('''
        SELECT id FROM attendance
        WHERE TRIM(emp_code) = TRIM(?) AND date = ? AND event_type = 'in'
    ''', (emp_code, today))
    if cursor.fetchone():
        conn.close()
        return jsonify({'status': 'error', 'message': f'⚠️ {emp_name} سجّل حضوره اليوم'})

    try:
        cursor.execute('''
            INSERT INTO absences (emp_code, date, reason, recorded_by, created_at)
            VALUES (?, ?, ?, 'admin', ?)
        ''', (emp_code, today, reason or 'بدون سبب', now_str))
        conn.commit()
        conn.close()

        log_audit("تسجيل غياب", f"{emp_name} - {reason or 'بدون سبب'}")
        return jsonify({
            'status': 'success',
            'message': f'✅ تم تسجيل {emp_name} غائباً اليوم'
        })
    except Exception as e:
        conn.close()
        return jsonify({'status': 'error', 'message': f'❌ خطأ: {str(e)[:100]}'})


# ==================================================
#         إنهاء خدمة موظف
# ==================================================
@app.route('/terminate_employee', methods=['POST'])
def terminate_employee():
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'بيانات غير صالحة'})

    emp_code = data.get('emp_code', '').strip()

    if not emp_code:
        return jsonify({'status': 'error', 'message': 'الرجاء إدخال كود الموظف'})

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM employees WHERE TRIM(code) = TRIM(?)", (emp_code,))
    employee = cursor.fetchone()

    if not employee:
        conn.close()
        return jsonify({'status': 'error', 'message': 'الموظف غير مسجل'})

    if employee['active'] == 0:
        conn.close()
        return jsonify({'status': 'error', 'message': f'⚠️ {employee["first_name"]} منتهي الخدمة مسبقاً'})

    emp_name = f"{employee['first_name']} {employee['last_name'] or ''}".strip()

    try:
        cursor.execute('UPDATE employees SET active = 0 WHERE id = ?', (employee['id'],))

        today = datetime.date.today().strftime('%Y-%m-%d')
        now_time = datetime.datetime.now().strftime('%H:%M:%S')

        cursor.execute('''
            SELECT * FROM attendance
            WHERE TRIM(emp_code) = TRIM(?) AND date = ?
            ORDER BY id DESC LIMIT 1
        ''', (emp_code, today))
        last = cursor.fetchone()

        if last and last['event_type'] in ['in', 'return']:
            cursor.execute('''
                INSERT INTO attendance
                (emp_code, shift_name, date, check_out, event_type, is_justified, notes, created_at)
                VALUES (?, ?, ?, ?, 'out', 1, 'إنهاء خدمة', ?)
            ''', (emp_code, last['shift_name'], today, now_time, now_time))

        conn.commit()
        conn.close()

        log_audit("إنهاء خدمة", f"{emp_name} ({emp_code})")
        return jsonify({
            'status': 'success',
            'message': f'✅ تم إنهاء خدمة {emp_name}'
        })

    except Exception as e:
        conn.close()
        return jsonify({'status': 'error', 'message': f'❌ خطأ: {str(e)[:100]}'})


@app.route('/restore_employee/<int:emp_id>', methods=['POST'])
@login_required
def restore_employee(emp_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM employees WHERE id=?", (emp_id,))
    emp = cursor.fetchone()

    if not emp:
        conn.close()
        flash('الموظف غير موجود', 'error')
        return redirect(url_for('admin'))

    cursor.execute("UPDATE employees SET active = 1 WHERE id = ?", (emp_id,))
    conn.commit()
    conn.close()

    name = f"{emp['first_name']} {emp['last_name'] or ''}".strip()
    log_audit("استرجاع موظف", f"{name} ({emp['code']})")
    flash(f'✅ تم استرجاع {name}', 'success')
    return redirect(url_for('admin'))


# ==================================================
#         لوحة الأدمن (مع 2FA)
# ==================================================
@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if not session.get('admin_2fa_verified'):
        error = None
        if request.method == 'POST':
            password = request.form.get('password_2fa', '').strip()
            pwd_hash = hashlib.sha256(password.encode()).hexdigest()

            if pwd_hash == ADMIN_2FA_HASH:
                session['admin_2fa_verified'] = True
                session.permanent = True
                log_audit("دخول 2FA", f"IP: {request.remote_addr}")
                return redirect(url_for('admin'))
            else:
                error = "❌ كلمة السر الثانوية غير صحيحة"
                log_audit("محاولة 2FA فاشلة", f"IP: {request.remote_addr}")

        return render_template('admin_login_2fa.html', error=error)

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM employees WHERE active=1 ORDER BY id DESC")
    employees = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM employees WHERE active=0 ORDER BY id DESC")
    terminated_employees = [dict(r) for r in cursor.fetchall()]

    for emp in employees:
        cursor.execute('''
            SELECT * FROM employee_shifts
            WHERE TRIM(emp_code)=TRIM(?) AND active=1
        ''', (emp['code'],))
        emp['shifts'] = {s['shift_name']: dict(s) for s in cursor.fetchall()}

        cursor.execute('''
            SELECT * FROM weekly_schedule
            WHERE TRIM(emp_code)=TRIM(?)
        ''', (emp['code'],))
        emp['schedule'] = {s['day_of_week']: dict(s) for s in cursor.fetchall()}

    cursor.execute('''
        SELECT a.*, e.first_name, e.last_name
        FROM attendance a
        LEFT JOIN employees e ON TRIM(a.emp_code) = TRIM(e.code)
        ORDER BY a.id DESC LIMIT 100
    ''')
    attendance = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 20")
    audit_logs = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM owner_notifications ORDER BY id DESC LIMIT 20")
    notifications = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM leave_reasons ORDER BY id")
    reasons = [dict(r) for r in cursor.fetchall()]

    conn.close()

    present_count, late_count, total_employees = get_today_counts()
    stats = {
        'total': total_employees,
        'present': present_count,
        'late': late_count,
        'absent': max(0, total_employees - present_count)
    }

    settings_dict = {
        'owner_phone': get_setting('owner_phone', '+213669659661'),
        'daily_report_time': get_setting('daily_report_time', '23:30'),
        'weekly_report_day': get_setting('weekly_report_day', '6'),
        'weekly_report_time': get_setting('weekly_report_time', '09:00'),
        'whatsapp_enabled': get_setting('whatsapp_enabled', '1'),
        'notify_daily': get_setting('notify_daily', '1'),
        'notify_weekly': get_setting('notify_weekly', '1'),
        'default_annual_quota': get_setting('default_annual_quota', '30'),
        'working_days_per_month': get_setting('working_days_per_month', '26')
    }

    server_ip = get_server_ip()
    machine_id = get_machine_id()

    return render_template('admin.html',
                           employees=employees,
                           terminated_employees=terminated_employees,
                           attendance=attendance,
                           audit_logs=audit_logs,
                           notifications=notifications,
                           reasons=reasons,
                           stats=stats,
                           settings=settings_dict,
                           server_ip=server_ip,
                           machine_id=machine_id)


@app.route('/admin/lock_2fa')
def admin_lock_2fa():
    session.pop('admin_2fa_verified', None)
    flash('✅ تم قفل لوحة الأدمن', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/change_2fa_password', methods=['POST'])
@login_required
def change_2fa_password():
    global ADMIN_2FA_HASH
    new_password = request.form.get('new_2fa_password', '').strip()

    if len(new_password) < 6:
        flash('كلمة السر يجب أن تكون 6 أحرف على الأقل', 'error')
        return redirect(url_for('admin'))

    ADMIN_2FA_HASH = hashlib.sha256(new_password.encode()).hexdigest()
    log_audit("تغيير كلمة سر 2FA", "تم التغيير")
    flash('✅ تم تغيير كلمة السر الثانوية', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/change_manage_password', methods=['POST'])
@login_required
def change_manage_password():
    global MANAGE_PASSWORD_HASH
    new_password = request.form.get('new_manage_password', '').strip()

    if len(new_password) < 6:
        flash('كلمة السر يجب أن تكون 6 أحرف على الأقل', 'error')
        return redirect(url_for('admin'))

    MANAGE_PASSWORD_HASH = hashlib.sha256(new_password.encode()).hexdigest()
    log_audit("تغيير كلمة سر إدارة الحركات", "تم التغيير")
    flash('✅ تم تغيير كلمة سر إدارة الحركات', 'success')
    return redirect(url_for('admin'))


# ==================================================
#         التقرير الشهري المجمّع
# ==================================================
@app.route('/report/summary')
@login_required
def report_summary():
    today = datetime.date.today()
    first_day = today.replace(day=1).strftime('%Y-%m-%d')
    last_day = today.strftime('%Y-%m-%d')

    start_date_str = request.args.get('start_date', first_day).strip()
    end_date_str = request.args.get('end_date', last_day).strip()
    emp_code_filter = request.args.get('emp_code', 'all').strip()

    conn = get_db_connection()
    cursor = conn.cursor()

    if emp_code_filter == 'all':
        cursor.execute("SELECT code, first_name, last_name FROM employees WHERE active=1 ORDER BY first_name")
    else:
        cursor.execute("SELECT code, first_name, last_name FROM employees WHERE TRIM(code) = TRIM(?) AND active=1", (emp_code_filter,))

    employees_list = [dict(r) for r in cursor.fetchall()]
    conn.close()

    summary = []
    totals = {
        'present': 0, 'absent': 0, 'late_count': 0, 'total_late': 0,
        'total_work_hours': 0, 'leaves_count': 0, 'leaves_total': 0,
        'justified_count': 0, 'unjustified_count': 0,
        'justified_min': 0, 'unjustified_min': 0
    }

    for emp in employees_list:
        stats = calculate_employee_stats(emp['code'], start_date_str, end_date_str)
        balance = get_leave_balance(emp['code'])

        summary.append({
            'code': emp['code'],
            'name': f"{emp['first_name']} {emp['last_name'] or ''}".strip(),
            **stats,
            'leave_balance': balance
        })

        totals['present'] += stats['days_present']
        totals['absent'] += stats['absent_days']
        totals['late_count'] += stats['late_count']
        totals['total_late'] += stats['total_late']
        totals['total_work_hours'] += stats['total_work_hours']
        totals['leaves_count'] += stats['leaves_count']
        totals['leaves_total'] += stats['leaves_total']
        totals['justified_count'] += stats['justified_count']
        totals['unjustified_count'] += stats['unjustified_count']
        totals['justified_min'] += stats['justified_min']
        totals['unjustified_min'] += stats['unjustified_min']

    summary.sort(key=lambda x: -x['compliance'])

    if summary:
        avg_compliance = round(sum(s['compliance'] for s in summary) / len(summary), 1)
        best_employee = summary[0] if summary else None
        worst_compliance = summary[-1] if summary else None
    else:
        avg_compliance = 0
        best_employee = None
        worst_compliance = None

    return render_template('report_summary.html',
                         summary=summary,
                         totals=totals,
                         start_date=start_date_str,
                         end_date=end_date_str,
                         selected_emp=emp_code_filter,
                         employees=employees_list,
                         avg_compliance=avg_compliance,
                         best_employee=best_employee,
                         worst_compliance=worst_compliance,
                         print_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))


# ==================================================
#         رصيد الإجازات
# ==================================================
@app.route('/report/balance')
@login_required
def report_balance():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT code, first_name, last_name FROM employees WHERE active=1 ORDER BY first_name")
    employees_list = [dict(r) for r in cursor.fetchall()]
    conn.close()

    balances = []
    for emp in employees_list:
        balance = get_leave_balance(emp['code'])
        balances.append({
            'code': emp['code'],
            'name': f"{emp['first_name']} {emp['last_name'] or ''}".strip(),
            **balance
        })

    return render_template('report_balance.html',
                         balances=balances,
                         print_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))


@app.route('/report/update_balance/<emp_code>', methods=['POST'])
@login_required
def update_balance(emp_code):
    quota = request.form.get('quota', '30').strip()
    used = request.form.get('used', '0').strip()

    try:
        quota_int = int(quota)
        used_int = int(used)
    except ValueError:
        flash('❌ قيم غير صحيحة', 'error')
        return redirect(url_for('report_balance'))

    conn = get_db_connection()
    cursor = conn.cursor()
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('''
        INSERT OR REPLACE INTO leave_balance (emp_code, annual_quota, used_days, updated_at)
        VALUES (?, ?, ?, ?)
    ''', (emp_code, quota_int, used_int, now_str))
    conn.commit()
    conn.close()

    log_audit("تعديل رصيد", f"{emp_code}: {used_int}/{quota_int}")
    flash('✅ تم تحديث الرصيد', 'success')
    return redirect(url_for('report_balance'))


# ==================================================
#         تصدير PDF
# ==================================================
@app.route('/export_pdf')
@login_required
def export_pdf():
    if not PDF_AVAILABLE:
        flash('❌ مكتبة PDF غير متوفرة. تحقق من تثبيت wkhtmltopdf', 'error')
        return redirect(url_for('report'))

    emp_code = request.args.get('emp_code', 'all').strip()
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    start_date_str = request.args.get('start_date', today_str).strip()
    end_date_str = request.args.get('end_date', today_str).strip()

    conn = get_db_connection()
    cursor = conn.cursor()

    if emp_code == 'all':
        cursor.execute('''
            SELECT a.*, e.first_name, e.last_name, lr.reason_name, lr.icon
            FROM attendance a
            LEFT JOIN employees e ON TRIM(a.emp_code) = TRIM(e.code)
            LEFT JOIN leave_reasons lr ON a.leave_reason_id = lr.id
            WHERE a.date BETWEEN ? AND ?
            ORDER BY a.date DESC, a.id DESC
        ''', (start_date_str, end_date_str))
    else:
        cursor.execute('''
            SELECT a.*, e.first_name, e.last_name, lr.reason_name, lr.icon
            FROM attendance a
            LEFT JOIN employees e ON TRIM(a.emp_code) = TRIM(e.code)
            LEFT JOIN leave_reasons lr ON a.leave_reason_id = lr.id
            WHERE TRIM(a.emp_code)=TRIM(?) AND a.date BETWEEN ? AND ?
            ORDER BY a.date DESC, a.id DESC
        ''', (emp_code, start_date_str, end_date_str))

    records = [dict(r) for r in cursor.fetchall()]
    conn.close()

    total_records = len(records)
    total_late = sum((r.get('late_minutes') or 0) for r in records)

    present_codes = set()
    late_codes = set()
    for r in records:
        if r['event_type'] == 'in':
            present_codes.add(r['emp_code'])
            if r.get('late_minutes', 0) > 0:
                late_codes.add(r['emp_code'])

    stats = {
        'total_records': total_records,
        'present_count': len(present_codes),
        'late_count': len(late_codes),
        'total_late': total_late,
    }

    type_ar = {'in': 'حضور', 'out': 'انصراف نهائي', 'leave': 'انصراف بسبب', 'return': 'عودة'}

    formatted_records = []
    for r in records[:200]:
        justified = ''
        if r['event_type'] == 'leave':
            justified = 'justified' if r.get('is_justified', 1) else 'unjustified'

        formatted_records.append({
            'date': r['date'],
            'name': f"{r['first_name'] or '?'} {r['last_name'] or ''}".strip(),
            'code': r['emp_code'],
            'event_type': type_ar.get(r['event_type'], r['event_type']),
            'time': r['check_in'] or r['check_out'] or '-',
            'late': r.get('late_minutes') or 0,
            'duration': r.get('duration_min') or 0,
            'reason': f"{r['icon'] or ''} {r['reason_name'] or ''}".strip() or '-',
            'justified': justified,
        })

    html_content = render_template('pdf_template.html',
                                   records=formatted_records,
                                   stats=stats,
                                   start_date=start_date_str,
                                   end_date=end_date_str,
                                   emp_code=emp_code,
                                   print_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))

    try:
        import pdfkit
        pdf_bytes = pdfkit.from_string(
            html_content,
            False,
            configuration=PDFKIT_CONFIG,
            options={
                'encoding': 'UTF-8',
                'enable-local-file-access': '',
                'page-size': 'A4',
                'margin-top': '15mm',
                'margin-right': '15mm',
                'margin-bottom': '15mm',
                'margin-left': '15mm',
                'footer-center': 'صفحة [page] من [topage]',
                'footer-font-size': '9',
            }
        )

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"Report_{start_date_str}_to_{end_date_str}.pdf"
        )
    except Exception as e:
        flash(f'❌ خطأ في توليد PDF: {str(e)[:200]}', 'error')
        return redirect(url_for('report'))


# ==================================================
#         الصفحة الشخصية للموظف
# ==================================================
@app.route('/my/<token>', methods=['GET', 'POST'])
def personal_page(token):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM employees
        WHERE personal_token = ? AND active = 1
    ''', (token,))
    employee = cursor.fetchone()
    conn.close()

    if not employee:
        return render_template('my_page.html',
                             error="🔒 الرابط غير صالح أو منتهي"), 404

    emp = dict(employee)
    client_ip = request.remote_addr

    if request.method == 'POST' and 'fingerprint' in request.form:
        form_fp = request.form.get('fingerprint', '').strip()
        allowed, msg = check_device_fingerprint(form_fp, emp['code'], client_ip)

        if not allowed:
            return jsonify({'status': 'error', 'message': msg}), 403
        return jsonify({'status': 'success'})

    pin_required = get_setting('pin_required', '0')
    if pin_required == '1' and emp.get('pin_hash'):
        session_key = f'pin_verified_{token}'
        today_key = f'{session_key}_{datetime.date.today().strftime("%Y%m%d")}'

        if not session.get(today_key):
            if request.method == 'POST':
                entered_pin = request.form.get('pin', '').strip()
                pin_hash = hashlib.sha256(entered_pin.encode()).hexdigest()
                if pin_hash == emp['pin_hash']:
                    session[today_key] = True
                    return redirect(url_for('personal_page', token=token))
                else:
                    return render_template('my_page.html',
                                         employee=emp,
                                         need_pin=True,
                                         error="❌ PIN خاطئ")
            return render_template('my_page.html',
                                 employee=emp,
                                 need_pin=True)

    qr_data = generate_qr_data(emp['code'])
    daily_code = generate_short_daily_code(emp['code'])

    return render_template('my_page.html',
                         employee=emp,
                         qr_data=qr_data,
                         daily_code=daily_code,
                         today=datetime.date.today().strftime('%Y-%m-%d'),
                         weekday=WEEKDAYS_AR[py_to_arabic_dow(datetime.datetime.now().weekday())])


# ==================================================
#         إدارة الروابط
# ==================================================
@app.route('/admin/tokens')
@login_required
def admin_tokens():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM employees WHERE active=1 ORDER BY first_name")
    employees = [dict(r) for r in cursor.fetchall()]
    conn.close()

    server_ip = get_server_ip()
    base_url = f"http://{server_ip}:8000"

    return render_template('admin_tokens.html',
                         employees=employees,
                         base_url=base_url,
                         settings={
                             'lock_ip_to_token': get_setting('lock_ip_to_token', '0'),
                             'pin_required': get_setting('pin_required', '0')
                         })


@app.route('/admin/regenerate_token/<int:emp_id>', methods=['POST'])
@login_required
def regenerate_token(emp_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    new_token = secrets.token_urlsafe(16)
    cursor.execute("UPDATE employees SET personal_token = ? WHERE id = ?", (new_token, emp_id))
    conn.commit()
    conn.close()
    log_audit("تجديد رابط", f"موظف ID: {emp_id}")
    flash('✅ تم توليد رابط جديد.', 'success')
    return redirect(url_for('admin_tokens'))


@app.route('/admin/lock_ip/<int:emp_id>', methods=['POST'])
@login_required
def lock_ip_to_employee(emp_id):
    client_ip = request.remote_addr
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE employees SET locked_ip = ? WHERE id = ?", (client_ip, emp_id))
    conn.commit()
    conn.close()
    log_audit("ربط IP", f"موظف ID: {emp_id}")
    flash(f'✅ تم ربط الموظف بـ IP: {client_ip}', 'success')
    return redirect(url_for('admin_tokens'))


@app.route('/admin/unlock_ip/<int:emp_id>', methods=['POST'])
@login_required
def unlock_ip(emp_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE employees SET locked_ip = NULL WHERE id = ?", (emp_id,))
    conn.commit()
    conn.close()
    flash('✅ تم فك الربط', 'success')
    return redirect(url_for('admin_tokens'))


@app.route('/admin/update_token_settings', methods=['POST'])
@login_required
def update_token_settings():
    for k in ['lock_ip_to_token', 'pin_required']:
        set_setting(k, '1' if request.form.get(k) else '0')
    flash('✅ تم حفظ الإعدادات', 'success')
    return redirect(url_for('admin_tokens'))


# ==================================================
#         إدارة الأجهزة
# ==================================================
@app.route('/admin/devices')
@login_required
def admin_devices():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT dr.*, e.first_name, e.last_name
        FROM device_registry dr
        LEFT JOIN employees e ON TRIM(dr.emp_code) = TRIM(e.code)
        ORDER BY dr.registered_at DESC
    ''')
    devices = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return render_template('admin_devices.html', devices=devices)


@app.route('/admin/unregister_device/<int:device_id>', methods=['POST'])
@login_required
def admin_unregister_device(device_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM device_registry WHERE id = ?", (device_id,))
    dev = cursor.fetchone()
    if dev:
        cursor.execute("DELETE FROM device_registry WHERE id = ?", (device_id,))
        conn.commit()
        flash('✅ تم فك ربط الجهاز', 'success')
    conn.close()
    return redirect(url_for('admin_devices'))


@app.route('/admin/clear_all_devices', methods=['POST'])
@login_required
def admin_clear_all_devices():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM device_registry")
    conn.commit()
    conn.close()
    flash('✅ تم مسح كل الأجهزة', 'success')
    return redirect(url_for('admin_devices'))


# ==================================================
#         تسجيل سريع للأدمن
# ==================================================
@app.route('/admin/quick')
@login_required
def admin_quick():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM employees WHERE active=1 ORDER BY first_name")
    employees = [dict(r) for r in cursor.fetchall()]

    today = datetime.date.today().strftime('%Y-%m-%d')
    for emp in employees:
        cursor.execute('''
            SELECT * FROM attendance
            WHERE TRIM(emp_code) = TRIM(?) AND date = ?
            ORDER BY id DESC LIMIT 1
        ''', (emp['code'], today))
        last = cursor.fetchone()

        if last:
            emp['current_state'] = last['event_type']
            emp['current_time'] = last['check_in'] or last['check_out']
        else:
            emp['current_state'] = 'not_checked_in'
            emp['current_time'] = None

    conn.close()

    return render_template('admin_quick.html', employees=employees)


@app.route('/admin/quick/checkin/<emp_code>', methods=['POST'])
@login_required
def admin_quick_checkin(emp_code):
    action = request.form.get('action', 'auto')
    reason_id = request.form.get('reason_id')

    success, message = record_attendance_logic(emp_code, action, reason_id, recorded_by='admin')

    return jsonify({
        'status': 'success' if success else 'error',
        'message': message
    })


# ==================================================
#         إدارة حركات الموظفين
# ==================================================
@app.route('/admin/manage_attendance', methods=['GET', 'POST'])
@login_required
def admin_manage_attendance():
    if not session.get('manage_unlocked'):
        error = None
        if request.method == 'POST':
            password = request.form.get('password', '').strip()
            pwd_hash = hashlib.sha256(password.encode()).hexdigest()

            if pwd_hash == MANAGE_PASSWORD_HASH or pwd_hash == ADMIN_PASSWORD_HASH:
                session['manage_unlocked'] = True
                log_audit("دخول إدارة الحركات", f"IP: {request.remote_addr}")
                return redirect(url_for('admin_manage_attendance'))
            else:
                error = "❌ كلمة المرور غير صحيحة"
                log_audit("محاولة دخول مرفوضة", f"إدارة الحركات - IP: {request.remote_addr}")

        return render_template('admin_manage_login.html', error=error)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT code, first_name, last_name FROM employees WHERE active=1 ORDER BY first_name")
    employees = [dict(r) for r in cursor.fetchall()]
    conn.close()

    emp_code = request.args.get('emp_code', 'all').strip()
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    start_date_str = request.args.get('start_date', today_str).strip()
    end_date_str = request.args.get('end_date', today_str).strip()

    records = []
    if emp_code and start_date_str and end_date_str:
        conn = get_db_connection()
        cursor = conn.cursor()

        if emp_code == 'all':
            cursor.execute('''
                SELECT a.*, e.first_name, e.last_name, lr.reason_name, lr.icon
                FROM attendance a
                LEFT JOIN employees e ON TRIM(a.emp_code) = TRIM(e.code)
                LEFT JOIN leave_reasons lr ON a.leave_reason_id = lr.id
                WHERE a.date BETWEEN ? AND ?
                ORDER BY a.date DESC, a.id DESC
            ''', (start_date_str, end_date_str))
        else:
            cursor.execute('''
                SELECT a.*, e.first_name, e.last_name, lr.reason_name, lr.icon
                FROM attendance a
                LEFT JOIN employees e ON TRIM(a.emp_code) = TRIM(e.code)
                LEFT JOIN leave_reasons lr ON a.leave_reason_id = lr.id
                WHERE TRIM(a.emp_code) = TRIM(?) AND a.date BETWEEN ? AND ?
                ORDER BY a.date DESC, a.id DESC
            ''', (emp_code, start_date_str, end_date_str))

        records = [dict(r) for r in cursor.fetchall()]
        conn.close()

    reasons = get_leave_reasons()

    return render_template('admin_manage_attendance.html',
                         employees=employees,
                         records=records,
                         reasons=reasons,
                         selected_emp=emp_code,
                         start_date=start_date_str,
                         end_date=end_date_str,
                         total_records=len(records))


@app.route('/admin/manage_attendance/lock')
@login_required
def admin_manage_attendance_lock():
    session.pop('manage_unlocked', None)
    flash('✅ تم قفل إدارة الحركات', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/manage_attendance/delete_bulk', methods=['POST'])
@login_required
def admin_delete_bulk_attendance():
    record_ids = request.form.getlist('record_ids')

    if not record_ids:
        flash('⚠️ لم يتم تحديد أي حركة', 'error')
        return redirect(request.referrer or url_for('admin_manage_attendance'))

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholders = ','.join(['?' for _ in record_ids])
        cursor.execute(f"DELETE FROM attendance WHERE id IN ({placeholders})", record_ids)
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()

        log_audit("حذف جماعي", f"تم حذف {deleted_count} حركة")
        flash(f'✅ تم حذف {deleted_count} حركة بنجاح', 'success')
    except Exception as e:
        flash(f'❌ خطأ: {e}', 'error')

    return redirect(request.referrer or url_for('admin_manage_attendance'))


@app.route('/admin/manage_attendance/delete_range', methods=['POST'])
@login_required
def admin_delete_range_attendance():
    emp_code = request.form.get('emp_code', '').strip()
    start_date = request.form.get('start_date', '').strip()
    end_date = request.form.get('end_date', '').strip()
    confirm = request.form.get('confirm', '')

    if confirm != 'DELETE':
        flash('❌ يجب كتابة DELETE للتأكيد', 'error')
        return redirect(url_for('admin_manage_attendance'))

    if not start_date or not end_date:
        flash('❌ يجب تحديد الفترة', 'error')
        return redirect(url_for('admin_manage_attendance'))

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        if emp_code == 'all':
            cursor.execute('DELETE FROM attendance WHERE date BETWEEN ? AND ?', (start_date, end_date))
        else:
            cursor.execute('DELETE FROM attendance WHERE TRIM(emp_code) = TRIM(?) AND date BETWEEN ? AND ?', (emp_code, start_date, end_date))

        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()

        log_audit("حذف فترة", f"{emp_code} - {start_date} → {end_date} - {deleted_count} حركة")
        flash(f'✅ تم حذف {deleted_count} حركة', 'success')
    except Exception as e:
        flash(f'❌ خطأ: {e}', 'error')

    return redirect(url_for('admin_manage_attendance'))


@app.route('/admin/manage_attendance/edit/<int:record_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_attendance_record(record_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == 'POST':
        event_type = request.form.get('event_type', '').strip()
        check_in = request.form.get('check_in', '').strip()
        check_out = request.form.get('check_out', '').strip()
        date = request.form.get('date', '').strip()
        late_minutes = request.form.get('late_minutes', '0').strip()
        duration_min = request.form.get('duration_min', '0').strip()
        notes = request.form.get('notes', '').strip()
        leave_reason_id = request.form.get('leave_reason_id', '').strip()
        is_justified = request.form.get('is_justified', '1')

        try:
            late_min = int(late_minutes) if late_minutes else 0
            dur_min = int(duration_min) if duration_min else 0
            reason_id = int(leave_reason_id) if leave_reason_id else None
            just_val = int(is_justified)
        except ValueError:
            late_min = 0
            dur_min = 0
            reason_id = None
            just_val = 1

        cursor.execute('''
            UPDATE attendance
            SET event_type = ?, check_in = ?, check_out = ?, date = ?,
                late_minutes = ?, duration_min = ?, leave_reason_id = ?,
                is_justified = ?, notes = ?
            WHERE id = ?
        ''', (event_type,
              check_in if check_in else None,
              check_out if check_out else None,
              date, late_min, dur_min, reason_id, just_val, notes, record_id))
        conn.commit()
        conn.close()

        log_audit("تعديل حركة", f"رقم {record_id}")
        flash('✅ تم حفظ التعديلات', 'success')
        return redirect(url_for('admin_manage_attendance'))

    cursor.execute('''
        SELECT a.*, e.first_name, e.last_name, e.code as emp_code
        FROM attendance a
        LEFT JOIN employees e ON TRIM(a.emp_code) = TRIM(e.code)
        WHERE a.id = ?
    ''', (record_id,))
    record = cursor.fetchone()
    conn.close()

    if not record:
        flash('الحركة غير موجودة', 'error')
        return redirect(url_for('admin_manage_attendance'))

    reasons = get_leave_reasons()

    return render_template('admin_edit_attendance.html',
                         record=dict(record),
                         reasons=reasons)


# ==================================================
#         إدارة التواجد
# ==================================================
@app.route('/admin/presence')
@login_required
def admin_presence():
    today = datetime.date.today().strftime('%Y-%m-%d')
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM employees WHERE active=1 ORDER BY first_name")
    employees = [dict(r) for r in cursor.fetchall()]

    for emp in employees:
        cursor.execute('''
            SELECT * FROM presence_log
            WHERE TRIM(emp_code) = TRIM(?) AND date = ?
            ORDER BY id DESC LIMIT 1
        ''', (emp['code'], today))
        last = cursor.fetchone()
        emp['presence_status'] = last['status'] if last else 'unknown'
        emp['presence_time'] = last['timestamp'] if last else None

        cursor.execute('''
            SELECT last_ip FROM device_registry
            WHERE TRIM(emp_code) = TRIM(?)
            ORDER BY id DESC LIMIT 1
        ''', (emp['code'],))
        dev = cursor.fetchone()
        emp['registered_ip'] = dev['last_ip'] if dev else None
        emp['has_device'] = bool(dev)

    cursor.execute('''
        SELECT pl.*, e.first_name, e.last_name
        FROM presence_log pl
        LEFT JOIN employees e ON TRIM(pl.emp_code) = TRIM(e.code)
        ORDER BY pl.id DESC LIMIT 50
    ''')
    logs = [dict(r) for r in cursor.fetchall()]
    conn.close()

    settings_dict = {
        'presence_enabled': get_setting('presence_enabled', '0'),
        'presence_interval': get_setting('presence_interval', '300')
    }

    return render_template('admin_presence.html',
                         employees=employees,
                         logs=logs,
                         settings=settings_dict)


@app.route('/admin/presence/settings', methods=['POST'])
@login_required
def admin_presence_settings():
    set_setting('presence_enabled', '1' if request.form.get('presence_enabled') else '0')
    interval = request.form.get('presence_interval', '300').strip()
    if interval.isdigit() and int(interval) >= 60:
        set_setting('presence_interval', interval)
    flash('✅ تم حفظ الإعدادات', 'success')
    return redirect(url_for('admin_presence'))


@app.route('/admin/presence/test', methods=['POST'])
@login_required
def admin_presence_test():
    try:
        check_presence_once()
        flash('✅ تم تنفيذ فحص يدوي', 'success')
    except Exception as e:
        flash(f'❌ خطأ: {e}', 'error')
    return redirect(url_for('admin_presence'))


# ==================================================
#         إدارة IPs المسموحة
# ==================================================
@app.route('/admin/allowed_ips', methods=['GET', 'POST'])
@login_required
def admin_allowed_ips():
    if request.method == 'POST':
        ips_text = request.form.get('ips', '').strip()
        ips_list = [ip.strip() for ip in ips_text.split('\n') if ip.strip() and not ip.strip().startswith('#')]

        if '127.0.0.1' not in ips_list:
            ips_list.insert(0, '127.0.0.1')

        if save_allowed_ips(ips_list):
            log_audit("تحديث IPs", f"عدد: {len(ips_list)}")
            flash('✅ تم حفظ IPs المسموحة', 'success')
        else:
            flash('❌ فشل الحفظ', 'error')

        return redirect(url_for('admin_allowed_ips'))

    current_ips = get_allowed_ips()
    server_ip = get_server_ip()
    my_ip = request.remote_addr

    return render_template('admin_allowed_ips.html',
                         ips=current_ips,
                         server_ip=server_ip,
                         my_ip=my_ip)


# ==================================================
#         بوابة الباب
# ==================================================
@app.route('/door')
def door_display():
    server_ip = get_server_ip()
    base_url = f"http://{server_ip}:8000"
    return render_template('door.html', base_url=base_url)


@app.route('/api/door_token')
def get_door_token():
    now = int(time.time())
    slot = now // 30
    message = f"door:{slot}".encode('utf-8')
    sig = hmac.new(SECRET_HMAC_KEY, message, hashlib.sha256).hexdigest()[:8]
    server_ip = get_server_ip()
    url = f"http://{server_ip}:8000/scan/{slot}.{sig}"
    ttl = 30 - (now % 30)
    return jsonify({'url': url, 'ttl': ttl, 'slot': slot})


@app.route('/scan/<token>')
def scan_at_door(token):
    parts = token.split('.')
    if len(parts) != 2:
        return render_template('scan_error.html', error="❌ رمز غير صالح")
    try:
        slot = int(parts[0])
    except ValueError:
        return render_template('scan_error.html', error="❌ رمز غير صالح")

    sig = parts[1]
    message = f"door:{slot}".encode('utf-8')
    expected_sig = hmac.new(SECRET_HMAC_KEY, message, hashlib.sha256).hexdigest()[:8]

    if not hmac.compare_digest(sig, expected_sig):
        return render_template('scan_error.html', error="❌ توقيع غير صحيح")

    current_slot = int(time.time()) // 30
    if current_slot - slot > 2:
        return render_template('scan_error.html', error="⏰ انتهت صلاحية الرمز")

    return render_template('scan_checkin.html', token=token)


@app.route('/scan/<token>/submit', methods=['POST'])
def scan_checkin_submit(token):
    emp_code = request.form.get('emp_code', '').strip()
    pin = request.form.get('pin', '').strip()

    if not emp_code:
        return render_template('scan_checkin.html', token=token, error="الرجاء إدخال الكود")

    if emp_code.isdigit() and len(emp_code) == 4:
        code, err = verify_short_code(emp_code)
        if err:
            return render_template('scan_checkin.html', token=token, error=err)
        emp_code = code

    pin_required = get_setting('pin_required', '0')
    if pin_required == '1':
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT pin_hash FROM employees
            WHERE TRIM(code) = TRIM(?) AND active = 1
        ''', (emp_code,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return render_template('scan_checkin.html', token=token, error="❌ كود غير مسجل")

        if row['pin_hash']:
            entered_hash = hashlib.sha256(pin.encode()).hexdigest()
            if entered_hash != row['pin_hash']:
                return render_template('scan_checkin.html', token=token, error="❌ PIN خاطئ")

    success, message = record_attendance_logic(emp_code, 'auto')

    return render_template('scan_result.html',
                         success=success,
                         message=message,
                         emp_code=emp_code,
                         token=token)


# ==================================================
#              تسجيل الدخول
# ==================================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        pwd_hash = hashlib.sha256(password.encode()).hexdigest()
        if username == ADMIN_USERNAME and pwd_hash == ADMIN_PASSWORD_HASH:
            session['admin_logged_in'] = True
            session.permanent = True
            log_audit("تسجيل دخول", f"الأدمن: {username}")
            return redirect(url_for('admin'))
        error = "بيانات الدخول غير صحيحة"
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    session.pop('admin_2fa_verified', None)
    session.pop('manage_unlocked', None)
    session.pop('home_unlocked', None)
    flash('✅ تم تسجيل الخروج', 'success')
    return redirect(url_for('login'))


@app.route('/change_password', methods=['POST'])
@login_required
def change_password():
    global ADMIN_PASSWORD_HASH
    new_password = request.form.get('new_password', '').strip()
    if len(new_password) < 6:
        flash('كلمة المرور يجب أن تكون 6 أحرف على الأقل', 'error')
        return redirect(url_for('admin'))
    ADMIN_PASSWORD_HASH = hashlib.sha256(new_password.encode()).hexdigest()
    log_audit("تغيير كلمة مرور", "تم التغيير")
    flash('تم تغيير كلمة المرور', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/change_home_password', methods=['POST'])
@login_required
def change_home_password():
    global HOME_PASSWORD_HASH
    new_password = request.form.get('new_home_password', '').strip()
    if len(new_password) < 4:
        flash('كلمة المرور يجب أن تكون 4 أحرف على الأقل', 'error')
        return redirect(url_for('admin'))
    HOME_PASSWORD_HASH = hashlib.sha256(new_password.encode()).hexdigest()
    log_audit("تغيير كلمة مرور الصفحة", "تم التغيير")
    flash('✅ تم تغيير كلمة مرور الصفحة', 'success')
    return redirect(url_for('admin'))


# ==================================================
#         Context Processor
# ==================================================
@app.context_processor
def inject_global_vars():
    present_count, late_count, total_emp = get_today_counts()

    try:
        lic_status, lic_message, lic_days, _ = check_license()
    except Exception:
        lic_status, lic_message, lic_days = 'valid', '', 0

    return {
        'stats': {
            'total': total_emp,
            'present': present_count,
            'late': late_count,
            'absent': max(0, total_emp - present_count)
        },
        'weekdays': WEEKDAYS_AR,
        'leave_reasons': get_leave_reasons(),
        'license_status': lic_status,
        'license_message': lic_message,
        'license_days': lic_days
    }


# ==================================================
#         الصفحة الرئيسية /
# ==================================================
@app.route('/', methods=['GET', 'POST'])
def home():
    client_ip = request.remote_addr
    allowed_ips = get_allowed_ips()

    if client_ip in allowed_ips:
        present_count, late_count, total_emp = get_today_counts()
        return render_template('index.html',
                             present_count=present_count,
                             late_count=late_count,
                             is_admin_device=True)

    if session.get('home_unlocked'):
        present_count, late_count, total_emp = get_today_counts()
        return render_template('index.html',
                             present_count=present_count,
                             late_count=late_count,
                             is_admin_device=True)

    error = None
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        pwd_hash = hashlib.sha256(password.encode()).hexdigest()

        if pwd_hash == HOME_PASSWORD_HASH or pwd_hash == ADMIN_PASSWORD_HASH:
            session['home_unlocked'] = True
            session.permanent = True
            log_audit("فتح صفحة البصمة", f"IP: {client_ip}")
            return redirect(url_for('home'))
        else:
            error = "❌ كلمة المرور غير صحيحة"
            log_audit("محاولة فتح مرفوضة", f"IP: {client_ip}")

    return render_template('home_login.html', error=error, client_ip=client_ip)


@app.route('/lock_home')
def lock_home():
    session.pop('home_unlocked', None)
    flash('✅ تم قفل الصفحة', 'success')
    return redirect(url_for('home'))


@app.route('/kiosk')
def kiosk():
    present_count, late_count, total_emp = get_today_counts()
    conn = get_db_connection()
    cursor = conn.cursor()
    today = datetime.date.today().strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT a.*, e.first_name, e.last_name
        FROM attendance a
        LEFT JOIN employees e ON TRIM(a.emp_code) = TRIM(e.code)
        WHERE a.date = ? AND a.event_type IN ('in', 'return')
        ORDER BY a.id DESC LIMIT 10
    ''', (today,))
    recent = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return render_template('kiosk.html',
                           present_count=present_count,
                           late_count=late_count,
                           total_emp=total_emp,
                           recent=recent)


# ==================================================
#         APIs
# ==================================================
@app.route('/api/live_stats')
def live_stats():
    present_count, late_count, total_emp = get_today_counts()
    return jsonify({
        'status': 'success',
        'present_count': present_count,
        'late_count': late_count,
        'total_employees': total_emp,
        'absent_count': max(0, total_emp - present_count)
    })


@app.route('/api/chart_data')
def chart_data():
    conn = get_db_connection()
    cursor = conn.cursor()
    labels, present_data, late_data = [], [], []
    for i in range(6, -1, -1):
        day = (datetime.date.today() - datetime.timedelta(days=i)).strftime('%Y-%m-%d')
        labels.append(day)
        cursor.execute("SELECT COUNT(DISTINCT emp_code) FROM attendance WHERE date=? AND event_type='in'", (day,))
        present_data.append(cursor.fetchone()[0] or 0)
        cursor.execute("SELECT COUNT(*) FROM attendance WHERE date=? AND event_type='in' AND late_minutes>0", (day,))
        late_data.append(cursor.fetchone()[0] or 0)
    conn.close()
    return jsonify({'labels': labels, 'present': present_data, 'late': late_data})


@app.route('/record_attendance', methods=['POST'])
def record_attendance_api():
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'بيانات غير صالحة'})

    scanned = data.get('qr_code', '').strip()
    action = data.get('action', 'auto')
    leave_reason_id = data.get('leave_reason_id')
    fingerprint = data.get('fingerprint', '').strip()
    client_ip = request.remote_addr

    if not scanned:
        return jsonify({'status': 'error', 'message': 'الرجاء إدخال الكود'})

    emp_code = None

    if scanned.isdigit() and len(scanned) == 4:
        emp_code, err = verify_short_code(scanned)
        if err:
            return jsonify({'status': 'error', 'message': err})
    else:
        emp_code, err = verify_dynamic_token(scanned)
        if err:
            return jsonify({'status': 'error', 'message': err})

    if not emp_code:
        return jsonify({'status': 'error', 'message': 'كود غير صالح'})

    if fingerprint:
        allowed, fp_msg = check_device_fingerprint(fingerprint, emp_code, client_ip)
        if not allowed:
            return jsonify({'status': 'error', 'message': fp_msg})

    success, message = record_attendance_logic(emp_code, action, leave_reason_id)

    if success:
        p, l, _ = get_today_counts()
        return jsonify({
            'status': 'success',
            'message': message,
            'present_count': p,
            'late_count': l
        })
    else:
        return jsonify({'status': 'error', 'message': message})


# ==================================================
#         التقارير التفصيلية
# ==================================================
@app.route('/report')
@login_required
def report():
    emp_code = request.args.get('emp_code', 'all').strip()
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    start_date_str = request.args.get('start_date', today_str).strip()
    end_date_str = request.args.get('end_date', today_str).strip()

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT code, first_name, last_name FROM employees WHERE active=1 ORDER BY first_name")
    employees_list = [dict(r) for r in cursor.fetchall()]

    if emp_code == 'all':
        cursor.execute('''
            SELECT a.*, e.first_name, e.last_name, lr.reason_name, lr.icon
            FROM attendance a
            LEFT JOIN employees e ON TRIM(a.emp_code) = TRIM(e.code)
            LEFT JOIN leave_reasons lr ON a.leave_reason_id = lr.id
            WHERE a.date BETWEEN ? AND ?
            ORDER BY a.date DESC, a.id DESC
        ''', (start_date_str, end_date_str))
    else:
        cursor.execute('''
            SELECT a.*, e.first_name, e.last_name, lr.reason_name, lr.icon
            FROM attendance a
            LEFT JOIN employees e ON TRIM(a.emp_code) = TRIM(e.code)
            LEFT JOIN leave_reasons lr ON a.leave_reason_id = lr.id
            WHERE TRIM(a.emp_code)=TRIM(?) AND a.date BETWEEN ? AND ?
            ORDER BY a.date DESC, a.id DESC
        ''', (emp_code, start_date_str, end_date_str))

    records = [dict(r) for r in cursor.fetchall()]
    conn.close()

    type_ar = {'in': 'حضور', 'out': 'انصراف نهائي', 'leave': 'انصراف بسبب', 'return': 'عودة'}

    grouped = {}
    for r in records:
        key = (r['emp_code'], r['date'])
        if key not in grouped:
            grouped[key] = {
                'emp_code': r['emp_code'],
                'name': f"{r['first_name'] or '?'} {r['last_name'] or ''}".strip(),
                'date': r['date'],
                'shift': r['shift_name'] or '-',
                'check_in': None,
                'check_out': None,
                'leaves': [],
                'late_minutes': 0,
                'events': []
            }
        g = grouped[key]
        if r['event_type'] == 'in' and not g['check_in']:
            g['check_in'] = r['check_in']
            g['late_minutes'] = r['late_minutes'] or 0
        if r['event_type'] == 'out':
            g['check_out'] = r['check_out']
        if r['event_type'] == 'leave':
            g['leaves'].append({
                'reason': f"{r['icon'] or ''} {r['reason_name'] or ''}".strip() or 'تلقائي',
                'duration': r['duration_min'] or 0,
                'time': r['check_out'],
                'is_justified': bool(r.get('is_justified', 1))
            })
        g['events'].append({
            'id': r['id'],
            'event_ar': type_ar.get(r['event_type'], r['event_type']),
            'time': r['check_in'] or r['check_out'] or '-',
            'reason': f"{r['icon'] or ''} {r['reason_name'] or ''}".strip() or '-',
            'late': r['late_minutes'] or 0
        })

    formatted = []
    for key, g in sorted(grouped.items(), key=lambda x: (x[0][1], x[0][0]), reverse=True):
        total_leave_min = sum(l['duration'] for l in g['leaves'])
        justified_count = len([l for l in g['leaves'] if l['is_justified']])
        unjustified_count = len(g['leaves']) - justified_count

        work_min = 0
        if g['check_in']:
            try:
                t1 = datetime.datetime.strptime(g['check_in'], '%H:%M:%S')
                end_time = g['check_out']
                if not end_time:
                    end_time = g['events'][-1]['time'] if g['events'] else None
                if end_time and end_time != '-':
                    t2 = datetime.datetime.strptime(end_time, '%H:%M:%S')
                    diff = int((t2 - t1).total_seconds() // 60)
                    work_min = max(0, diff - total_leave_min)
            except Exception:
                pass

        hours = work_min // 60
        mins = work_min % 60

        formatted.append({
            'emp_code': g['emp_code'],
            'name': g['name'],
            'date': g['date'],
            'shift': g['shift'],
            'check_in': g['check_in'] or '-',
            'check_out': g['check_out'] or '-',
            'leaves': g['leaves'],
            'leaves_count': len(g['leaves']),
            'leaves_total_min': total_leave_min,
            'justified_count': justified_count,
            'unjustified_count': unjustified_count,
            'work_hours': f"{hours}س {mins}د" if work_min > 0 else '-',
            'work_minutes': work_min,
            'late_minutes': g['late_minutes'],
            'events': g['events']
        })

    total_records = len(formatted)
    total_work_min = sum(f['work_minutes'] for f in formatted)
    total_late = sum(f['late_minutes'] for f in formatted)

    return render_template('report.html',
                           records=formatted,
                           employees=employees_list,
                           selected_emp=emp_code,
                           start_date=start_date_str,
                           end_date=end_date_str,
                           total_records=total_records,
                           total_work_hours=f"{total_work_min // 60} ساعة و {total_work_min % 60} دقيقة",
                           total_late_mins=total_late,
                           print_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))


@app.route('/export_excel')
@login_required
def export_excel():
    emp_code = request.args.get('emp_code', 'all').strip()
    start_date = request.args.get('start_date', datetime.date.today().strftime('%Y-%m-%d')).strip()
    end_date = request.args.get('end_date', datetime.date.today().strftime('%Y-%m-%d')).strip()

    conn = get_db_connection()
    query = '''
        SELECT a.date AS "التاريخ", a.emp_code AS "الكود",
               (COALESCE(e.first_name, '?') || ' ' || COALESCE(e.last_name, '')) AS "الاسم",
               a.shift_name AS "الوردية",
               a.event_type AS "الحدث",
               COALESCE(a.check_in, a.check_out, '-') AS "الوقت",
               (COALESCE(lr.icon, '') || ' ' || COALESCE(lr.reason_name, '')) AS "السبب",
               CASE WHEN a.event_type = 'leave' THEN
                    CASE WHEN a.is_justified = 1 THEN 'مبرر' ELSE 'غير مبرر' END
                    ELSE '-' END AS "نوع الانصراف",
               a.duration_min AS "المدة (د)",
               a.late_minutes AS "التأخير (د)"
        FROM attendance a
        LEFT JOIN employees e ON TRIM(a.emp_code) = TRIM(e.code)
        LEFT JOIN leave_reasons lr ON a.leave_reason_id = lr.id
        WHERE a.date BETWEEN ? AND ?
    '''
    params = [start_date, end_date]
    if emp_code != 'all':
        query += " AND TRIM(a.emp_code) = TRIM(?)"
        params.append(emp_code)
    query += " ORDER BY a.date DESC, a.id DESC"

    df = pd.read_sql_query(query, conn, params=params)
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Attendance')
        ws = writer.sheets['Attendance']
        ws.views.sheetView[0].rightToLeft = True

        header_font = Font(bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='0D9488', end_color='0D9488', fill_type='solid')
        align_center = Alignment(horizontal='center', vertical='center')

        for col_num in range(1, len(df.columns) + 1):
            cell = ws.cell(row=1, column=col_num)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = align_center

        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            ws.column_dimensions[get_column_letter(col[0].column)].width = max(max_len + 5, 14)

    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f"Report_{start_date}_to_{end_date}.xlsx"
    )


# ==================================================
#         إشعارات صاحب الصيدلية
# ==================================================
def send_whatsapp_async(phone, message, msg_type='general'):
    def _send():
        conn = get_db_connection()
        cursor = conn.cursor()
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if get_setting('whatsapp_enabled', '1') != '1':
            cursor.execute('''
                INSERT INTO owner_notifications (phone, message, type, status, created_at)
                VALUES (?, ?, ?, 'disabled', ?)
            ''', (phone, message[:200], msg_type, now_str))
            conn.commit()
            conn.close()
            return

        try:
            import pywhatkit
            pywhatkit.sendwhatmsg_instantly(phone, message, wait_time=20, tab_close=True)
            status = 'sent'
        except ImportError:
            status = 'lib_missing'
        except Exception as e:
            status = f'failed: {str(e)[:50]}'

        cursor.execute('''
            INSERT INTO owner_notifications (phone, message, type, status, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (phone, message[:500], msg_type, status, now_str))
        conn.commit()
        conn.close()

    threading.Thread(target=_send, daemon=True).start()


def build_daily_report(date_str=None):
    if not date_str:
        date_str = datetime.date.today().strftime('%Y-%m-%d')

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM employees WHERE active=1")
    total = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(DISTINCT emp_code) FROM attendance WHERE date=? AND event_type='in'", (date_str,))
    present = cursor.fetchone()[0] or 0

    cursor.execute('''
        SELECT e.first_name, e.last_name, a.late_minutes
        FROM attendance a
        LEFT JOIN employees e ON TRIM(a.emp_code) = TRIM(e.code)
        WHERE a.date=? AND a.event_type='in' AND a.late_minutes > 0
    ''', (date_str,))
    late_list = cursor.fetchall()

    cursor.execute('''
        SELECT e.first_name, e.last_name FROM employees e
        WHERE e.active=1 AND TRIM(e.code) NOT IN (
            SELECT DISTINCT TRIM(emp_code) FROM attendance
            WHERE date=? AND event_type='in'
        )
    ''', (date_str,))
    absent_list = cursor.fetchall()

    cursor.execute('''
        SELECT COUNT(*) as unjustified_count
        FROM attendance
        WHERE date=? AND event_type='leave' AND is_justified = 0
    ''', (date_str,))
    unjustified_count = cursor.fetchone()['unjustified_count'] or 0

    conn.close()

    lines = [
        "📊 *تقرير الحضور اليومي*",
        f"📅 {date_str}",
        "",
        f"👥 الحاضرون: {present}/{total}",
        f"⏰ المتأخرون: {len(late_list)}",
        f"❌ الغائبون: {len(absent_list)}",
    ]

    if unjustified_count > 0:
        lines.append(f"⚠️ *انصرافات غير مبررة:* {unjustified_count}")

    if late_list:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("⏰ *المتأخرون:*")
        for r in late_list:
            lines.append(f"• {r['first_name']} {r['last_name'] or ''} ({r['late_minutes']} د)")

    if absent_list:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("❌ *الغائبون:*")
        for r in absent_list:
            lines.append(f"• {r['first_name']} {r['last_name'] or ''}")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("🏥 صيدلية المقري شنن عثمان")

    return "\n".join(lines)


def build_weekly_report(start_date=None, end_date=None):
    if not end_date:
        end_date = datetime.date.today()
    if not start_date:
        start_date = end_date - datetime.timedelta(days=6)

    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT e.code, e.first_name, e.last_name,
               COUNT(DISTINCT a.date) as present_days,
               SUM(CASE WHEN a.late_minutes > 0 THEN 1 ELSE 0 END) as late_count,
               SUM(a.late_minutes) as total_late
        FROM employees e
        LEFT JOIN attendance a ON TRIM(e.code) = TRIM(a.emp_code)
            AND a.date BETWEEN ? AND ? AND a.event_type='in'
        WHERE e.active=1
        GROUP BY e.code
        ORDER BY present_days DESC, total_late ASC
    ''', (start_str, end_str))
    emp_stats = cursor.fetchall()
    conn.close()

    lines = [
        "📊 *التقرير الأسبوعي*",
        f"📅 {start_str} → {end_str}",
        "",
        "━━━━━━━━━━━━━━━",
        "🏆 *الأكثر التزاماً:*"
    ]

    sorted_emp = sorted(emp_stats, key=lambda x: (-(x['present_days'] or 0), x['total_late'] or 0))
    for i, r in enumerate(sorted_emp[:5], 1):
        days = r['present_days'] or 0
        lines.append(f"{i}. {r['first_name']} {r['last_name'] or ''} - {days} يوم")

    late_emp = [r for r in emp_stats if (r['late_count'] or 0) > 0]
    if late_emp:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("⚠️ *الأكثر تأخراً:*")
        sorted_late = sorted(late_emp, key=lambda x: -(x['late_count'] or 0))
        for i, r in enumerate(sorted_late[:5], 1):
            lines.append(f"{i}. {r['first_name']} {r['last_name'] or ''} - {r['late_count']} مرة")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("🏥 صيدلية المقري شنن عثمان")

    return "\n".join(lines)


@app.route('/send_daily_report_now', methods=['POST'])
@login_required
def send_daily_report_now():
    phone = get_setting('owner_phone', '')
    if not phone:
        flash('لم يتم تحديد رقم صاحب الصيدلية', 'error')
        return redirect(url_for('admin'))
    report = build_daily_report()
    send_whatsapp_async(phone, report, 'daily')
    flash('تم إرسال التقرير اليومي', 'success')
    return redirect(url_for('admin'))


@app.route('/send_weekly_report_now', methods=['POST'])
@login_required
def send_weekly_report_now():
    phone = get_setting('owner_phone', '')
    if not phone:
        flash('لم يتم تحديد الرقم', 'error')
        return redirect(url_for('admin'))
    report = build_weekly_report()
    send_whatsapp_async(phone, report, 'weekly')
    flash('تم إرسال التقرير الأسبوعي', 'success')
    return redirect(url_for('admin'))


@app.route('/test_whatsapp', methods=['POST'])
@login_required
def test_whatsapp():
    phone = get_setting('owner_phone', '')
    if not phone:
        flash('لم يتم تحديد الرقم', 'error')
        return redirect(url_for('admin'))
    send_whatsapp_async(phone, "🧪 *رسالة اختبار*\nمن نظام الحضور\n✅ يعمل بنجاح", 'test')
    flash('تم إرسال رسالة اختبار', 'success')
    return redirect(url_for('admin'))


# ==================================================
#         إدارة الورديات
# ==================================================
@app.route('/employee/<emp_code>/shifts', methods=['GET', 'POST'])
@login_required
def manage_shifts(emp_code):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM employees WHERE TRIM(code)=TRIM(?) AND active=1", (emp_code,))
    emp = cursor.fetchone()
    if not emp:
        conn.close()
        flash('الموظف غير موجود', 'error')
        return redirect(url_for('admin'))

    if request.method == 'POST':
        for shift_name in ['صباحية', 'مسائية']:
            enabled = request.form.get(f'{shift_name}_enabled')
            if enabled:
                start = request.form.get(f'{shift_name}_start', '08:00')
                end = request.form.get(f'{shift_name}_end', '16:00')
                grace = request.form.get(f'{shift_name}_grace', '15')

                cursor.execute('''
                    INSERT OR REPLACE INTO employee_shifts
                    (emp_code, shift_name, shift_start, shift_end, grace_period, active)
                    VALUES (?, ?, ?, ?, ?, 1)
                ''', (emp_code, shift_name, start, end, grace))
            else:
                cursor.execute('''
                    UPDATE employee_shifts SET active=0
                    WHERE TRIM(emp_code)=TRIM(?) AND shift_name=?
                ''', (emp_code, shift_name))

        for dow in range(7):
            is_rest = request.form.get(f'day_{dow}_rest')
            shift_name = request.form.get(f'day_{dow}_shift', '')

            if is_rest:
                cursor.execute('''
                    INSERT OR REPLACE INTO weekly_schedule
                    (emp_code, day_of_week, shift_name, is_rest_day)
                    VALUES (?, ?, NULL, 1)
                ''', (emp_code, dow))
            elif shift_name:
                cursor.execute('''
                    INSERT OR REPLACE INTO weekly_schedule
                    (emp_code, day_of_week, shift_name, is_rest_day)
                    VALUES (?, ?, ?, 0)
                ''', (emp_code, dow, shift_name))
            else:
                cursor.execute('''
                    DELETE FROM weekly_schedule
                    WHERE TRIM(emp_code)=TRIM(?) AND day_of_week=?
                ''', (emp_code, dow))

        conn.commit()
        conn.close()
        log_audit("تعديل ورديات", f"الموظف {emp_code}")
        flash('تم حفظ الورديات والجدول', 'success')
        return redirect(url_for('manage_shifts', emp_code=emp_code))

    cursor.execute('''
        SELECT * FROM employee_shifts
        WHERE TRIM(emp_code)=TRIM(?) AND active=1
    ''', (emp_code,))
    shifts = {s['shift_name']: dict(s) for s in cursor.fetchall()}

    cursor.execute('''
        SELECT * FROM weekly_schedule
        WHERE TRIM(emp_code)=TRIM(?)
    ''', (emp_code,))
    schedule = {s['day_of_week']: dict(s) for s in cursor.fetchall()}
    conn.close()

    return render_template('manage_shifts.html',
                           emp=dict(emp),
                           shifts=shifts,
                           schedule=schedule)


# ==================================================
#         إدارة أسباب الانصراف
# ==================================================
@app.route('/add_leave_reason', methods=['POST'])
@login_required
def add_leave_reason():
    name = request.form.get('reason_name', '').strip()
    icon = request.form.get('icon', '').strip() or '📌'
    if not name:
        flash('اسم السبب مطلوب', 'error')
        return redirect(url_for('admin'))
    conn = get_db_connection()
    cursor = conn.cursor()
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        cursor.execute('''
            INSERT INTO leave_reasons (reason_name, icon, active, created_at)
            VALUES (?, ?, 1, ?)
        ''', (name, icon, now_str))
        conn.commit()
        flash(f'تمت إضافة السبب: {name}', 'success')
    except sqlite3.IntegrityError:
        flash('السبب موجود مسبقاً', 'error')
    conn.close()
    return redirect(url_for('admin'))


@app.route('/delete_leave_reason/<int:reason_id>', methods=['POST'])
@login_required
def delete_leave_reason(reason_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM leave_reasons WHERE id=?", (reason_id,))
    conn.commit()
    conn.close()
    log_audit("حذف سبب", f"سبب رقم {reason_id}")
    return redirect(url_for('admin'))


@app.route('/toggle_leave_reason/<int:reason_id>', methods=['POST'])
@login_required
def toggle_leave_reason(reason_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT active FROM leave_reasons WHERE id=?", (reason_id,))
    row = cursor.fetchone()
    if row:
        new_val = 0 if row['active'] else 1
        cursor.execute("UPDATE leave_reasons SET active=? WHERE id=?", (new_val, reason_id))
        conn.commit()
    conn.close()
    return redirect(url_for('admin'))


# ==================================================
#         تحديث الإعدادات
# ==================================================
@app.route('/update_settings', methods=['POST'])
@login_required
def update_settings():
    keys = ['owner_phone', 'daily_report_time', 'weekly_report_day', 'weekly_report_time',
            'default_annual_quota', 'working_days_per_month']
    for k in keys:
        v = request.form.get(k, '').strip()
        if v:
            set_setting(k, v)

    for k in ['whatsapp_enabled', 'notify_daily', 'notify_weekly']:
        set_setting(k, '1' if request.form.get(k) else '0')

    log_audit("تحديث إعدادات", "تم تحديث الإعدادات")
    flash('تم حفظ الإعدادات', 'success')
    return redirect(url_for('admin'))


# ==================================================
#         إدارة الموظفين
# ==================================================
@app.route('/add_employee', methods=['POST'])
@login_required
def add_employee():
    code = request.form.get('code', '').strip()
    first_name = request.form.get('first_name', '').strip()
    last_name = request.form.get('last_name', '').strip()
    phone = request.form.get('phone', '').strip()

    if not code or not first_name:
        flash('الكود والاسم مطلوبان', 'error')
        return redirect(url_for('admin'))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, first_name, last_name, active FROM employees WHERE TRIM(code)=TRIM(?)", (code,))
    existing = cursor.fetchone()

    if existing:
        conn.close()
        if existing['active'] == 1:
            flash(f"⚠️ الكود '{code}' مستخدم للموظف: {existing['first_name']} {existing['last_name'] or ''}", 'error')
        else:
            flash(f"⚠️ الكود '{code}' موجود لموظف محذوف.", 'error')
        return redirect(url_for('admin'))

    personal_token = secrets.token_urlsafe(16)

    photo_filename = None
    photo = request.files.get('photo')
    if photo and photo.filename:
        ext = os.path.splitext(photo.filename)[1].lower()
        if ext in ['.jpg', '.jpeg', '.png', '.webp']:
            photo_filename = f"emp_{code}{ext}"
            photo.save(os.path.join(UPLOAD_FOLDER, photo_filename))

    qr_filename = f"qr_{code}.png"
    try:
        qrcode.make(code).save(os.path.join(QR_FOLDER, qr_filename))
    except Exception:
        pass

    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        cursor.execute('''
            INSERT INTO employees
            (code, first_name, last_name, phone, photo, qr_code, active, created_at, personal_token)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        ''', (code, first_name, last_name, phone, photo_filename, qr_filename, now_str, personal_token))
        conn.commit()
        log_audit("إضافة موظف", f"{first_name} - {code}")
        flash(f'✅ تمت إضافة {first_name}', 'success')
    except sqlite3.IntegrityError:
        flash('الكود مكرر', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin'))


@app.route('/delete_employee/<int:emp_id>', methods=['POST'])
@login_required
def delete_employee(emp_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM employees WHERE id=?", (emp_id,))
    emp = cursor.fetchone()

    if not emp:
        conn.close()
        flash('الموظف غير موجود', 'error')
        return redirect(url_for('admin'))

    code = emp['code']
    name = f"{emp['first_name']} {emp['last_name'] or ''}".strip()

    if emp['photo']:
        try:
            photo_path = os.path.join(UPLOAD_FOLDER, emp['photo'])
            if os.path.exists(photo_path):
                os.remove(photo_path)
        except Exception:
            pass

    if emp['qr_code']:
        try:
            qr_path = os.path.join(QR_FOLDER, emp['qr_code'])
            if os.path.exists(qr_path):
                os.remove(qr_path)
        except Exception:
            pass

    cursor.execute("DELETE FROM employee_shifts WHERE TRIM(emp_code)=TRIM(?)", (code,))
    cursor.execute("DELETE FROM weekly_schedule WHERE TRIM(emp_code)=TRIM(?)", (code,))
    cursor.execute("DELETE FROM device_registry WHERE TRIM(emp_code)=TRIM(?)", (code,))
    cursor.execute("DELETE FROM employees WHERE id=?", (emp_id,))

    conn.commit()
    conn.close()

    log_audit("حذف نهائي لموظف", f"{name} - كود: {code}")
    flash(f'✅ تم حذف {name} نهائياً.', 'success')
    return redirect(url_for('admin'))


@app.route('/delete_attendance/<int:record_id>', methods=['POST'])
@login_required
def delete_attendance(record_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM attendance WHERE id=?", (record_id,))
    conn.commit()
    conn.close()
    log_audit("حذف سجل", f"رقم {record_id}")
    return redirect(url_for('admin'))


# ==================================================
#                    التشغيل
# ==================================================
if __name__ == '__main__':
    try:
        from scheduler import start_scheduler
        start_scheduler(app)
        print("✅ تم تشغيل المجدول")
    except ImportError:
        print("⚠️ scheduler.py غير موجود")
    except Exception as e:
        print(f"⚠️ خطأ في المجدول: {e}")

    try:
        start_presence_monitor()
    except Exception as e:
        print(f"⚠️ خطأ في Presence Monitor: {e}")

    server_ip = get_server_ip()
    machine_id = get_machine_id()

    print("\n" + "=" * 60)
    print("🏥 نظام الحضور - صيدلية المقري شنن عثمان")
    print("=" * 60)
    print(f"🌐 الرئيسية:      http://localhost:8000/")
    print(f"🔐 تسجيل دخول:   http://localhost:8000/login")
    print(f"🔑 الترخيص:      http://localhost:8000/license")
    print(f"📊 التقارير:      http://localhost:8000/report")
    print(f"📈 التقرير المجمّع: http://localhost:8000/report/summary")
    print(f"🎫 رصيد الإجازات: http://localhost:8000/report/balance")
    print(f"")
    print(f"📟 بصمة الجهاز:  {machine_id}")
    print(f"📡 من الهاتف:    http://{server_ip}:8000/")
    print(f"")
    print("👤 admin / admin123")
    print("🔓 كلمة مرور /: home123")
    print("🔐 2FA: secret2026")
    print("🗂️ إدارة الحركات: manage2026")
    print("=" * 60 + "\n")

    app.run(host='0.0.0.0', port=8000, debug=False)