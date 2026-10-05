"""
scheduler.py - المهام المجدولة
- التقرير اليومي التلقائي
- التقرير الأسبوعي التلقائي
- إغلاق الانصرافات المفتوحة
"""

import datetime
import threading
import time
import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, 'attendance.db')


def get_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_setting(key, default=''):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = cur.fetchone()
        conn.close()
        return row['value'] if row else default
    except Exception:
        return default


def send_whatsapp_sync(phone, message, msg_type='general'):
    """إرسال واتساب متزامن (للـ scheduler)"""
    try:
        conn = get_db()
        cur = conn.cursor()
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if get_setting('whatsapp_enabled', '1') != '1':
            cur.execute('''
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

        cur.execute('''
            INSERT INTO owner_notifications (phone, message, type, status, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (phone, message[:500], msg_type, status, now_str))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ خطأ إرسال: {e}")


def finalize_unclosed_leaves_all():
    """إغلاق الانصرافات المفتوحة في نهاية اليوم"""
    try:
        from app import finalize_unclosed_leaves
        today = datetime.date.today().strftime('%Y-%m-%d')
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT emp_code FROM attendance WHERE date=?", (today,))
        for row in cur.fetchall():
            try:
                finalize_unclosed_leaves(row['emp_code'], today)
            except Exception:
                pass
        conn.close()
    except Exception as e:
        print(f"⚠️ خطأ في إغلاق الانصرافات: {e}")


def daily_report_task():
    """إرسال التقرير اليومي"""
    try:
        from app import build_daily_report
        phone = get_setting('owner_phone', '')
        if not phone:
            return
        if get_setting('notify_daily', '1') != '1':
            return

        # إغلاق الانصرافات المفتوحة أولاً
        finalize_unclosed_leaves_all()

        report = build_daily_report()
        send_whatsapp_sync(phone, report, 'daily')
        print(f"✅ تم إرسال التقرير اليومي - {datetime.datetime.now()}")
    except Exception as e:
        print(f"❌ خطأ في التقرير اليومي: {e}")


def weekly_report_task():
    """إرسال التقرير الأسبوعي"""
    try:
        from app import build_weekly_report
        phone = get_setting('owner_phone', '')
        if not phone:
            return
        if get_setting('notify_weekly', '1') != '1':
            return

        report = build_weekly_report()
        send_whatsapp_sync(phone, report, 'weekly')
        print(f"✅ تم إرسال التقرير الأسبوعي - {datetime.datetime.now()}")
    except Exception as e:
        print(f"❌ خطأ في التقرير الأسبوعي: {e}")


def scheduler_loop():
    """الحلقة الرئيسية - تفحص الوقت كل 30 ثانية"""
    last_daily_date = None
    last_weekly_key = None

    while True:
        try:
            now = datetime.datetime.now()
            today = now.strftime('%Y-%m-%d')
            current_hm = now.strftime('%H:%M')

            # التقرير اليومي
            daily_time = get_setting('daily_report_time', '23:30')
            if current_hm == daily_time and last_daily_date != today:
                last_daily_date = today
                threading.Thread(target=daily_report_task, daemon=True).start()

            # التقرير الأسبوعي
            weekly_day = int(get_setting('weekly_report_day', '6'))
            weekly_time = get_setting('weekly_report_time', '09:00')

            # Python: 0=Mon, 6=Sun → نحتاج تحويل
            py_to_arabic = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}
            arabic_dow = py_to_arabic[now.weekday()]
            weekly_key = f"{today}_{weekly_time}"

            if arabic_dow == weekly_day and current_hm == weekly_time and last_weekly_key != weekly_key:
                last_weekly_key = weekly_key
                threading.Thread(target=weekly_report_task, daemon=True).start()

        except Exception as e:
            print(f"⚠️ خطأ في المجدول: {e}")

        time.sleep(30)


def start_scheduler(app=None):
    """تشغيل المجدول في thread منفصل"""
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    print("🕐 المجدول يعمل في الخلفية")