import csv
import os
from app import get_db_connection, get_cursor, qmarks

def seed_students():
    conn = None
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)

        # Clear existing students if any
        cursor.execute("DELETE FROM students")

        csv_files = [
            'students_DAYSCHOLAR.csv',
            'students_HOSTEL.csv',
            'students_TRANSPORT.csv'
        ]

        count = 0
        for file_path in csv_files:
            if not os.path.exists(file_path):
                print(f"File not found: {file_path}")
                continue
                
            with open(file_path, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        cursor.execute(
                            qmarks("INSERT INTO students (adm_no, student_name, father_name, class, section, mobile, category) VALUES (?, ?, ?, ?, ?, ?, ?)"),
                            (row['ADMNO'], row['SNAME'], row['FNAME'], row['CLASS'], row['SECTION'], row['MOBILE1'], row['CATEGORY'])
                        )
                        count += 1
                    except Exception as e:
                        if "Duplicate entry" not in str(e) and "UNIQUE constraint failed" not in str(e):
                            print(f"Error inserting row {row['ADMNO']}: {e}")

        conn.commit()
        print(f"Successfully seeded {count} students.")
    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if conn: conn.close()

if __name__ == '__main__':
    seed_students()
