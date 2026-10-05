# -*- coding: utf-8 -*-
import sys
import io

# إصلاح مشكلة UTF-8 على Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app import record_attendance_logic
import sqlite3

# حذف سجلات العربي اليوم
conn = sqlite3.connect('attendance.db')
conn.execute("DELETE FROM attendance WHERE TRIM(emp_code) = '1002' AND date = '2026-10-05'")
conn.commit()
conn.close()
print("OK - Deleted records")

print("\n" + "=" * 70)
print("TEST 1: First check-in (action='in')")
print("=" * 70)
result = record_attendance_logic('1002', 'in')
print(f"RESULT: {result}")

print("\n" + "=" * 70)
print("TEST 2: Duplicate check-in (action='in')")
print("=" * 70)
result = record_attendance_logic('1002', 'in')
print(f"RESULT: {result}")

print("\n" + "=" * 70)
print("TEST 3: Immediate checkout (action='out')")
print("=" * 70)
result = record_attendance_logic('1002', 'out')
print(f"RESULT: {result}")

print("\n" + "=" * 70)
print("TEST 4: Auto mode")
print("=" * 70)
result = record_attendance_logic('1002', 'auto')
print(f"RESULT: {result}")

# عرض السجلات
print("\n" + "=" * 70)
print("LOGS in DB:")
print("=" * 70)
conn = sqlite3.connect('attendance.db')
cur = conn.cursor()
cur.execute("""
    SELECT id, date, check_in, check_out, event_type, late_minutes, notes 
    FROM attendance 
    WHERE TRIM(emp_code) = '1002' AND date = '2026-10-05' 
    ORDER BY id
""")
rows = cur.fetchall()
if not rows:
    print("  (no records)")
for row in rows:
    print(f"  ID:{row[0]} | date:{row[1]} | in:{row[2]} | out:{row[3]} | type:{row[4]} | late:{row[5]}")
conn.close()