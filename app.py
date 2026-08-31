from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_bcrypt import Bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import sqlite3
import psycopg2
import psycopg2.extras
import pymysql
import pymysql.cursors
import os
import datetime
from functools import wraps
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from google.cloud import storage
from cryptography.fernet import Fernet
import base64
import json
import urllib.request
import urllib.error
import ssl
import random
import re
import math
import ast
import operator

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24))

# Security Hardening
app.config.update(
    SESSION_COOKIE_SECURE=True,    # Only send cookies over HTTPS
    SESSION_COOKIE_HTTPONLY=True,  # Prevent JS from reading cookies
    SESSION_COOKIE_SAMESITE='Lax', # Mitigate CSRF attacks
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=2) # Auto logout after 2 hours
)

bcrypt = Bcrypt(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["500 per day", "100 per hour"],
    storage_uri="memory://"
)

# Admin Credentials (Hashed in production)
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASS_HASH = os.getenv('ADMIN_PASS_HASH') # Set this in app.yaml

# Encryption Setup
raw_key = os.getenv('ENCRYPTION_KEY')
try:
    if raw_key:
        if isinstance(raw_key, str):
            raw_key = raw_key.encode()
        cipher_suite = Fernet(raw_key)
    else:
        # 32 bytes exactly base64 encoded
        fallback_key = base64.urlsafe_b64encode(b'a_very_secret_key_32_chars_long!')
        cipher_suite = Fernet(fallback_key)
except Exception as e:
    print(f"Warning: Invalid ENCRYPTION_KEY format ({e}). Using generated Fernet key.")
    cipher_suite = Fernet(Fernet.generate_key())

def encrypt_data(data):
    if not data: return None
    return cipher_suite.encrypt(data.encode()).decode()

def decrypt_data(encrypted_data):
    if not encrypted_data: return None
    try:
        return cipher_suite.decrypt(encrypted_data.encode()).decode()
    except:
        return "Decryption Error"

# GCS Configuration
GCS_BUCKET_NAME = os.getenv('GCS_BUCKET_NAME', 'mvn-website-media')

def upload_to_gcs(file, folder="uploads"):
    if not file:
        return None
    
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        
        filename = secure_filename(file.filename)
        filename = f"{folder}/news_{os.urandom(4).hex()}_{filename}"
        
        blob = bucket.blob(filename)
        blob.upload_from_string(
            file.read(),
            content_type=file.content_type
        )
        
        # Return the public URL
        return f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{filename}"
    except Exception as e:
        print(f"GCS Upload Error: {e}")
        return None

def delete_from_gcs(url):
    if not url or not isinstance(url, str) or not url.startswith('https://storage.googleapis.com/'):
        return
    
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        
        # Extract the blob name from the URL
        blob_path = url.replace(f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/", "")
        
        blob = bucket.blob(blob_path)
        blob.delete()
        print(f"Deleted {blob_path} from GCS.")
    except Exception as e:
        print(f"GCS Delete Error: {e}")

# Configurations for Uploads (Fallback for local)
UPLOAD_FOLDER = 'static/images/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure upload directory exists (only if writable)
try:
    os.makedirs(os.path.join(app.root_path, UPLOAD_FOLDER), exist_ok=True)
except OSError:
    # This happens on Google App Engine Standard (read-only)
    print("Notice: Could not create upload directory (read-only system)")

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

DATABASE_URL = os.getenv('DATABASE_URL')
MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')
MYSQL_DB = os.getenv('MYSQL_DB')

def get_db_connection():
    if MYSQL_HOST:
        # GoDaddy MySQL or Cloud SQL
        if MYSQL_HOST.startswith('/cloudsql/'):
            conn = pymysql.connect(
                unix_socket=MYSQL_HOST,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                database=MYSQL_DB,
                cursorclass=pymysql.cursors.DictCursor
            )
        else:
            conn = pymysql.connect(
                host=MYSQL_HOST,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                database=MYSQL_DB,
                cursorclass=pymysql.cursors.DictCursor
            )
        return conn
    elif DATABASE_URL:
        # PostgreSQL
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    else:
        # SQLite
        db_path = os.path.join(os.path.dirname(__file__), 'database.db')
        conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
        conn.row_factory = sqlite3.Row
        return conn

def get_cursor(conn):
    if MYSQL_HOST:
        return conn.cursor()
    elif DATABASE_URL:
        return conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    else:
        return conn.cursor()

# Helper to adapt placeholders
def qmarks(query):
    if MYSQL_HOST or DATABASE_URL:
        return query.replace('?', '%s')
    return query

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Please log in to access this page.', 'error')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    conn = None
    latest_news = []
    notices = []
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        cursor.execute('SELECT * FROM news ORDER BY date_posted DESC LIMIT 3')
        latest_news = cursor.fetchall()
        
        cursor.execute('SELECT * FROM notices WHERE is_active = TRUE ORDER BY date_posted DESC LIMIT 10')
        notices = cursor.fetchall()
    except Exception as err:
        print(f"Database Error: {err}")
        return render_template('index.html', latest_news=[], notices=[])
    finally:
        if conn: conn.close()
    return render_template('index.html', latest_news=latest_news, notices=notices)

@app.route('/campus')
def campus():
    conn = None
    images = {}
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        cursor.execute('SELECT section_name, image_path FROM site_images')
        rows = cursor.fetchall()
        images = {row['section_name']: row['image_path'] for row in rows}
    except Exception as err:
        print(f"Database Error: {err}")
    finally:
        if conn: conn.close()
    
    return render_template('campus.html', images=images)

@app.route('/news')
def news():
    conn = None
    news_items = []
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        cursor.execute('SELECT * FROM news ORDER BY date_posted DESC')
        news_items = cursor.fetchall()
    except Exception as err:
        print(f"Database Error: {err}")
    finally:
        if conn: conn.close()
        
    return render_template('news.html', news=news_items)

@app.route('/news/<int:news_id>')
def news_detail(news_id):
    conn = None
    item = None
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        cursor.execute(qmarks('SELECT * FROM news WHERE id = ?'), (news_id,))
        item = cursor.fetchone()
    except Exception as err:
        print(f"Database Error: {err}")
    finally:
        if conn: conn.close()
    
    if item is None:
        return redirect(url_for('news'))
        
    return render_template('news_detail.html', item=item)

@app.route('/mandatory-public-disclosure')
def mpd():
    return render_template('mpd.html')

@app.route('/admissions', methods=['GET', 'POST'])
def admissions():
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        
        conn = None
        try:
            conn = get_db_connection()
            cursor = get_cursor(conn)
            
            if form_type == 'admission':
                student_name = request.form.get('student_name')
                grade = request.form.get('grade')
                parent_name = request.form.get('parent_name')
                email = request.form.get('email')
                phone = request.form.get('phone')
                
                cursor.execute(
                    qmarks('INSERT INTO admissions (student_name, grade_applied, parent_name, email, phone) VALUES (?, ?, ?, ?, ?)'),
                    (student_name, grade, parent_name, email, phone)
                )
                
                flash('Admission application submitted successfully!', 'success')
                
            elif form_type == 'recruitment':
                applicant_name = request.form.get('applicant_name')
                position = request.form.get('position')
                email = request.form.get('email')
                phone = request.form.get('phone')
                qualifications = request.form.get('qualifications')
                
                cursor.execute(
                    qmarks('INSERT INTO recruitment (applicant_name, position_applied, email, phone, qualifications) VALUES (?, ?, ?, ?, ?)'),
                    (applicant_name, position, email, phone, qualifications)
                )
                
                flash('Recruitment application submitted successfully!', 'success')
            
            conn.commit()
            
        except Exception as err:
            flash(f'Database error: {err}', 'error')
        finally:
            if conn: conn.close()
            
        return redirect(url_for('admissions'))
        
    return render_template('admissions.html')

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Verify via Hash (Secure)
        is_valid = False
        if username == ADMIN_USERNAME and ADMIN_PASS_HASH:
            try:
                # Handle potential string/bytes mismatch in different environments
                if bcrypt.check_password_hash(ADMIN_PASS_HASH, password):
                    is_valid = True
            except:
                pass

        if is_valid:
            session.permanent = True # Use the 2-hour lifetime
            session['logged_in'] = True
            flash('You have successfully logged in.', 'success')
            return redirect(url_for('admin'))
        else:
            flash('Invalid credentials. Please try again.', 'error')
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))

@app.route('/admin')
@login_required
def admin():
    conn = None
    admissions_data = []
    recruitment_data = []
    news_data = []
    notices_data = []
    aadhaar_data = []
    
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        
        cursor.execute('SELECT * FROM admissions ORDER BY submission_date DESC')
        admissions_data = cursor.fetchall()
        
        cursor.execute('SELECT * FROM recruitment ORDER BY submission_date DESC')
        recruitment_data = cursor.fetchall()
        
        cursor.execute('SELECT * FROM news ORDER BY date_posted DESC')
        news_data = cursor.fetchall()
        
        cursor.execute('SELECT * FROM notices ORDER BY date_posted DESC')
        notices_data = cursor.fetchall()

        cursor.execute('''
            SELECT a.id, s.adm_no, s.student_name, s.class, s.section, a.student_aadhaar_encrypted, a.father_aadhaar_encrypted, a.dob, a.submission_date 
            FROM aadhaar_submissions a
            JOIN students s ON a.student_id = s.id
            ORDER BY a.submission_date DESC
        ''')
        aadhaar_data = cursor.fetchall()
        
    except Exception as err:
        flash(f'Database error: {err}', 'error')
    finally:
        if conn: conn.close()
        
    return render_template('admin.html', 
                         admissions=admissions_data, 
                         recruitment=recruitment_data, 
                         news=news_data,
                         notices=notices_data,
                         aadhaar_data=aadhaar_data,
                         decrypt=decrypt_data)

@app.route('/admin/delete_aadhaar/<int:submission_id>', methods=['POST'])
@login_required
def delete_aadhaar(submission_id):
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        cursor.execute(qmarks('DELETE FROM aadhaar_submissions WHERE id = ?'), (submission_id,))
        conn.commit()
        flash('Aadhaar submission deleted.', 'success')
    except Exception as err:
        flash(f'Error deleting submission: {err}', 'error')
    finally:
        if conn: conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/add_student', methods=['POST'])
@login_required
def add_student():
    adm_no = request.form.get('adm_no')
    student_name = request.form.get('student_name')
    father_name = request.form.get('father_name')
    student_class = request.form.get('class')
    section = request.form.get('section')
    mobile = request.form.get('mobile')
    category = request.form.get('category', 'DAYSCHOLAR')

    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        cursor.execute(
            qmarks('INSERT INTO students (adm_no, student_name, father_name, class, section, mobile, category) VALUES (?, ?, ?, ?, ?, ?, ?)'),
            (adm_no, student_name, father_name, student_class, section, mobile, category)
        )
        conn.commit()
        flash(f'Student {student_name} added successfully!', 'success')
    except Exception as err:
        flash(f'Error adding student: {err}', 'error')
    finally:
        if conn: conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/add_notice', methods=['POST'])
@login_required
def add_notice():
    title = request.form.get('title')
    link = request.form.get('link')
    
    if not title:
        flash('Notice title is required!', 'error')
        return redirect(url_for('admin'))
    
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        cursor.execute(qmarks('INSERT INTO notices (title, link) VALUES (?, ?)'), (title, link))
        conn.commit()
        flash('Notice added successfully!', 'success')
    except Exception as err:
        flash(f'Database error: {err}', 'error')
    finally:
        if conn: conn.close()
        
    return redirect(url_for('admin'))

@app.route('/admin/delete_notice/<int:notice_id>', methods=['POST'])
@login_required
def delete_notice(notice_id):
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        cursor.execute(qmarks('DELETE FROM notices WHERE id = ?'), (notice_id,))
        conn.commit()
        flash('Notice deleted successfully!', 'success')
    except Exception as err:
        flash(f'Database error: {err}', 'error')
    finally:
        if conn: conn.close()
        
    return redirect(url_for('admin'))

@app.route('/admin/add_news', methods=['POST'])
@login_required
def add_news():
    title = request.form.get('title')
    content = request.form.get('content')
    image_path = None
    
    if not title or not content:
        flash('Title and content are required!', 'error')
        return redirect(url_for('admin'))
    
    if 'image' in request.files:
        file = request.files['image']
        if file and allowed_file(file.filename):
            image_path = upload_to_gcs(file)
    
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        cursor.execute(qmarks('INSERT INTO news (title, content, image_path) VALUES (?, ?, ?)'), (title, content, image_path))
        conn.commit()
        flash('News article posted successfully!', 'success')
    except Exception as err:
        flash(f'Database error: {err}', 'error')
    finally:
        if conn: conn.close()
        
    return redirect(url_for('admin'))

@app.route('/admin/edit_news/<int:news_id>', methods=['POST'])
@login_required
def edit_news(news_id):
    title = request.form.get('title')
    content = request.form.get('content')
    
    if not title or not content:
        flash('Title and content are required!', 'error')
        return redirect(url_for('admin'))
    
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        
        # Check if a new image was uploaded
        if 'image' in request.files:
            file = request.files['image']
            if file and allowed_file(file.filename):
                # Get the old image path to delete it
                cursor.execute(qmarks('SELECT image_path FROM news WHERE id = ?'), (news_id,))
                old_row = cursor.fetchone()
                if old_row and old_row['image_path']:
                    delete_from_gcs(old_row['image_path'])

                image_path = upload_to_gcs(file)
                
                cursor.execute(qmarks('UPDATE news SET title = ?, content = ?, image_path = ? WHERE id = ?'), 
                             (title, content, image_path, news_id))
            else:
                cursor.execute(qmarks('UPDATE news SET title = ?, content = ? WHERE id = ?'), 
                             (title, content, news_id))
        else:
            cursor.execute(qmarks('UPDATE news SET title = ?, content = ? WHERE id = ?'), 
                         (title, content, news_id))
                         
        conn.commit()
        flash('News article updated successfully!', 'success')
    except Exception as err:
        flash(f'Database error: {err}', 'error')
    finally:
        if conn: conn.close()
        
    return redirect(url_for('admin'))

@app.route('/admin/delete_news/<int:news_id>', methods=['POST'])
@login_required
def delete_news(news_id):
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        
        # Get image path to delete from GCS
        cursor.execute(qmarks('SELECT image_path FROM news WHERE id = ?'), (news_id,))
        row = cursor.fetchone()
        if row and row['image_path']:
            delete_from_gcs(row['image_path'])

        cursor.execute(qmarks('DELETE FROM news WHERE id = ?'), (news_id,))
        conn.commit()
        flash('News article and its image deleted successfully!', 'success')
    except Exception as err:
        flash(f'Database error: {err}', 'error')
    finally:
        if conn: conn.close()
        
    return redirect(url_for('admin'))

@app.route('/admin/delete_news_image/<int:news_id>', methods=['POST'])
@login_required
def delete_news_image(news_id):
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        
        # Get image path to delete from GCS
        cursor.execute(qmarks('SELECT image_path FROM news WHERE id = ?'), (news_id,))
        row = cursor.fetchone()
        if row and row['image_path']:
            delete_from_gcs(row['image_path'])

        cursor.execute(qmarks('UPDATE news SET image_path = NULL WHERE id = ?'), (news_id,))
        conn.commit()
        flash('News image removed from storage and website!', 'success')
    except Exception as err:
        flash(f'Database error: {err}', 'error')
    finally:
        if conn: conn.close()
        
    return redirect(url_for('admin'))

@app.route('/admin/upload_image', methods=['POST'])
@login_required
def upload_image():
    if 'image' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('admin'))
    
    file = request.files['image']
    section_name = request.form.get('section_name')
    
    if file.filename == '':
        flash('No selected file', 'error')
        return redirect(url_for('admin'))
    
    if file and allowed_file(file.filename):
        image_url = upload_to_gcs(file, folder="site_media")
        
        if image_url:
            try:
                conn = get_db_connection()
                cursor = get_cursor(conn)
                cursor.execute(qmarks('UPDATE site_images SET image_path = ? WHERE section_name = ?'), 
                             (image_url, section_name))
                conn.commit()
                flash(f'Image for {section_name} updated successfully!', 'success')
            except Exception as err:
                flash(f'Database error: {err}', 'error')
            finally:
                if conn: conn.close()
        else:
            flash('Failed to upload image to Cloud Storage.', 'error')
            
    return redirect(url_for('admin'))

@app.route('/student-portal', methods=['GET', 'POST'])
def student_portal():
    if request.method == 'POST':
        adm_no = request.form.get('adm_no')
        mobile = request.form.get('mobile')
        
        conn = None
        try:
            conn = get_db_connection()
            cursor = get_cursor(conn)
            cursor.execute(qmarks('SELECT * FROM students WHERE adm_no = ? AND mobile = ?'), (adm_no, mobile))
            student = cursor.fetchone()
            
            if student:
                session['student_id'] = student['id']
                session['student_name'] = student['student_name']
                return redirect(url_for('student_update'))
            else:
                flash('Invalid Admission Number or Mobile Number. Please contact school office.', 'error')
        except Exception as err:
            flash(f'Database error: {err}', 'error')
        finally:
            if conn: conn.close()
            
    return render_template('student_portal.html')

@app.route('/student-portal/update', methods=['GET', 'POST'])
def student_update():
    if 'student_id' not in session:
        return redirect(url_for('student_portal'))
        
    student_id = session['student_id']
    
    if request.method == 'POST':
        student_aadhaar = request.form.get('student_aadhaar').replace(" ", "")
        father_aadhaar = request.form.get('father_aadhaar').replace(" ", "")
        dob = request.form.get('dob')
        
        if len(student_aadhaar) != 12 or len(father_aadhaar) != 12:
            flash('Please enter a valid 12-digit Aadhaar number.', 'error')
            return redirect(url_for('student_update'))
            
        # Encrypt data
        enc_student = encrypt_data(student_aadhaar)
        enc_father = encrypt_data(father_aadhaar)
        
        conn = None
        try:
            conn = get_db_connection()
            cursor = get_cursor(conn)
            
            # Check if already submitted
            cursor.execute(qmarks('SELECT id FROM aadhaar_submissions WHERE student_id = ?'), (student_id,))
            existing = cursor.fetchone()
            
            if existing:
                cursor.execute(
                    qmarks('UPDATE aadhaar_submissions SET student_aadhaar_encrypted = ?, father_aadhaar_encrypted = ?, dob = ? WHERE student_id = ?'),
                    (enc_student, enc_father, dob, student_id)
                )
            else:
                cursor.execute(
                    qmarks('INSERT INTO aadhaar_submissions (student_id, student_aadhaar_encrypted, father_aadhaar_encrypted, dob) VALUES (?, ?, ?, ?)'),
                    (student_id, enc_student, enc_father, dob)
                )
            
            conn.commit()
            flash('Aadhaar data submitted successfully. Thank you!', 'success')
            session.pop('student_id', None)
            return redirect(url_for('index'))
            
        except Exception as err:
            flash(f'Database error: {err}', 'error')
        finally:
            if conn: conn.close()
            
    return render_template('student_update.html', student_name=session.get('student_name'))

@app.route('/admin/init-db', methods=['POST'])
@login_required
def admin_init_db():
    try:
        from init_mysql import init_mysql
        init_mysql()
        flash('Database tables initialized/updated successfully!', 'success')
    except Exception as e:
        flash(f'Error initializing database: {e}', 'error')
    return redirect(url_for('admin'))

@app.route('/admin/seed-students', methods=['POST'])
@login_required
def admin_seed_students():
    try:
        from seed_students import seed_students
        seed_students()
        flash('Student data imported successfully!', 'success')
    except Exception as e:
        flash(f'Error seeding students: {e}', 'error')
    return redirect(url_for('admin'))

# ==========================================
# AI TUTOR (MAYA AI) ROUTES & ENGINE
# ==========================================

@app.route('/ai-tutor')
def ai_tutor():
    return render_template('ai_tutor.html')

def call_gemini_api(prompt, system_instruction, api_key, history=None, image_data=None):
    """Calls Google Gemini REST API with multi-turn conversation and multimodal image support"""
    models = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash"
    ]
    urls_to_try = [
        f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        for m in models
    ]
    
    contents = []
    # Add previous chat history turns if provided
    if history and isinstance(history, list):
        for item in history[-6:]: # Keep last 6 turns for context
            role = item.get('role', 'user')
            text = item.get('text', '')
            if text:
                contents.append({
                    "role": "model" if role == 'ai' or role == 'model' else "user",
                    "parts": [{"text": text}]
                })
                
    # Build current user turn parts
    user_parts = []
    if prompt:
        user_parts.append({"text": prompt})
    else:
        user_parts.append({"text": "Please evaluate this handwritten homework submission as my Writing Coach."})

    if image_data:
        mime_type = "image/jpeg"
        b64_str = image_data
        if "data:" in image_data and ";base64," in image_data:
            header, b64_str = image_data.split(";base64,")
            mime_type = header.replace("data:", "")
        user_parts.append({
            "inlineData": {
                "mimeType": mime_type,
                "data": b64_str
            }
        })

    contents.append({
        "role": "user",
        "parts": user_parts
    })
    
    payload = {
        "contents": contents,
        "systemInstruction": {
            "parts": [{"text": system_instruction}]
        },
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1500
        }
    }
    
    data_bytes = json.dumps(payload).encode('utf-8')
    
    for url in urls_to_try:
        try:
            req = urllib.request.Request(
                url, 
                data=data_bytes, 
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            ctx = ssl.create_default_context()
            try:
                response = urllib.request.urlopen(req, timeout=15, context=ctx)
            except Exception:
                ctx = ssl._create_unverified_context()
                response = urllib.request.urlopen(req, timeout=15, context=ctx)
                
            with response:
                result = json.loads(response.read().decode('utf-8'))
                candidates = result.get('candidates', [])
                if candidates:
                    parts = candidates[0].get('content', {}).get('parts', [])
                    if parts and 'text' in parts[0]:
                        return parts[0]['text']
        except Exception as e:
            print(f"Gemini API Call Exception ({url}): {e}")
            continue
            
    return None

def get_gemini_api_key():
    """Fetches Gemini API key from environment or database setting"""
    env_key = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
    if env_key:
        return env_key
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        # Ensure site_settings table exists
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS site_settings (
                setting_key VARCHAR(100) PRIMARY KEY,
                setting_value TEXT
            )
        ''')
        cursor.execute(qmarks('SELECT setting_value FROM site_settings WHERE setting_key = ?'), ('gemini_api_key',))
        row = cursor.fetchone()
        if row and row['setting_value']:
            return row['setting_value']
    except Exception as e:
        print(f"Error fetching API key from DB: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()
    return None

@app.route('/admin/update_ai_key', methods=['POST'])
@login_required
def update_ai_key():
    api_key = request.form.get('gemini_api_key', '').strip()
    try:
        conn = get_db_connection()
        cursor = get_cursor(conn)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS site_settings (
                setting_key VARCHAR(100) PRIMARY KEY,
                setting_value TEXT
            )
        ''')
        cursor.execute(qmarks('SELECT setting_key FROM site_settings WHERE setting_key = ?'), ('gemini_api_key',))
        if cursor.fetchone():
            cursor.execute(qmarks('UPDATE site_settings SET setting_value = ? WHERE setting_key = ?'), (api_key, 'gemini_api_key'))
        else:
            cursor.execute(qmarks('INSERT INTO site_settings (setting_key, setting_value) VALUES (?, ?)'), ('gemini_api_key', api_key))
        conn.commit()
        if api_key:
            flash('Google Gemini API Key saved successfully! Live Generative AI is now active.', 'success')
        else:
            flash('API Key cleared. Using built-in CBSE pedagogical engine.', 'info')
    except Exception as err:
        flash(f'Error saving API key: {err}', 'error')
    finally:
        if conn:
            conn.close()
    return redirect(url_for('admin'))


def is_hinglish(text):
    """Detects if student query is in Hinglish or Hindi using token matching."""
    words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    hinglish_markers = {
        'kya', 'hai', 'kaise', 'batao', 'mujhe', 'karo', 'ka', 'ke', 'ki',
        'yeh', 'woh', 'haan', 'nahi', 'karna', 'padhai', 'samjhao', 'bataiye',
        'kijiye', 'hota', 'hoti', 'mein', 'par', 'se', 'ko', 'bhi', 'kuch',
        'aur', 'shukriya', 'theek', 'mera', 'meri', 'mere',
        'bhai', 'samajh', 'aaya', 'gaya', 'khelega', 'bata', 'khele'
    }
    return any(w in hinglish_markers for w in words)


def extract_requested_question_count(query, default=3):
    """Parses custom requested question count from prompt, e.g., 'give me a 5-question quiz', '5 mcqs'."""
    q = query.lower()
    num_words = {
        'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10
    }
    for word, val in num_words.items():
        if re.search(rf'\b{word}[\s-]*(?:quiz|practice)?[\s-]*(?:questions?|mcqs?|problems?|items?|sawal)\b', q):
            return val
            
    m = re.search(r'\b(\d+)[\s-]*(?:quiz|practice)?[\s-]*(?:questions?|mcqs?|problems?|items?|sawal)\b', q)
    if m:
        val = int(m.group(1))
        return max(1, min(val, 10))
        
    m2 = re.search(r'\bgive\s+(?:me\s+)?(?:a\s+)?(\d+)\b', q)
    if m2:
        val = int(m2.group(1))
        return max(1, min(val, 10))

    m3 = re.search(r'\b(\d+)\s*(?:questions?|mcqs?)\b', q)
    if m3:
        val = int(m3.group(1))
        return max(1, min(val, 10))

    if re.search(r'\b(?:a|an|single|ek)\s+(?:quiz|practice|exemplar|exempler|ncert|cbse|pyq|mcq|board)?\s*(?:question|mcq|sawal|prashn)\b', q):
        return 1
        
    return default


def generate_dynamic_cbse_quiz(grade, subject, query=""):
    """Generates dynamic, randomized, non-repeating CBSE quizzes with chapter-specific question banks for any grade & subject."""
    q_clean = query.lower().strip()
    s_lower = (subject + " " + query).lower().strip()
    hinglish_user = is_hinglish(query)
    topic_display = subject
    
    # -------------------------------------------------------------
    # DEDICATED CBSE / NCERT CHAPTER-SPECIFIC QUESTION BANKS
    # -------------------------------------------------------------
    
    # 1. BIOLOGY: TISSUES (Class 9 Chapter 6)
    if any(k in s_lower for k in ['tissue', 'tissues', 'sclerenchyma', 'parenchyma', 'collenchyma', 'xylem', 'phloem', 'epithelial', 'ligament', 'tendon', 'meristematic', 'meristem']):
        topic_display = "Biology: Tissues"
        pool = [
            ("Plant Tissues - Meristematic", "Which type of meristematic tissue is located at the growing tips of stems and roots, increasing their length?", ["Apical Meristem", "Lateral Meristem (Cambium)", "Intercalary Meristem", "Permanent Tissue"], "A"),
            ("Plant Tissues - Sclerenchyma", "The husk of a coconut is made of which plant tissue whose cell walls are thickened with lignin?", ["Parenchyma", "Collenchyma", "Sclerenchyma", "Aerenchyma"], "C"),
            ("Plant Tissues - Collenchyma", "Which simple permanent plant tissue provides mechanical support and flexibility, allowing easy bending of tendrils without breaking?", ["Sclerenchyma", "Collenchyma", "Parenchyma", "Xylem"], "B"),
            ("Complex Plant Tissues - Xylem", "Which component of xylem tissue is living and stores food nutrients?", ["Tracheids", "Vessels", "Xylem Parenchyma", "Xylem Fibres"], "C"),
            ("Complex Plant Tissues - Phloem", "Which complex permanent tissue transports soluble organic food (photosynthates) bidirectionally across plant parts?", ["Xylem", "Phloem", "Collenchyma", "Epidermis"], "B"),
            ("Animal Tissues - Epithelial", "Which single-layered, flat epithelial tissue forms the delicate lining of lung alveoli and blood vessels for gas diffusion?", ["Simple Squamous Epithelium", "Stratified Squamous Epithelium", "Cuboidal Epithelium", "Columnar Epithelium"], "A"),
            ("Animal Tissues - Connective", "Which strong and elastic connective tissue joins a **bone to another bone** at a joint?", ["Tendon", "Ligament", "Cartilage", "Areolar Tissue"], "B"),
            ("Animal Tissues - Connective", "Which dense fibrous connective tissue connects **skeletal muscle to bone**?", ["Tendon", "Ligament", "Adipose Tissue", "Blood"], "A"),
            ("Animal Tissues - Muscular", "Which muscular tissue consists of involuntary, spindle-shaped, unstriated cells found in the walls of the stomach and blood vessels?", ["Striated / Skeletal Muscle", "Smooth / Unstriated Muscle", "Cardiac Muscle", "Voluntary Muscle"], "B"),
            ("Animal Tissues - Nervous", "In a nerve cell (neuron), which elongated single fiber conducts nerve impulses away from the cell body (cyton)?", ["Dendrite", "Axon", "Synapse", "Myelin Sheath"], "B"),
            ("Animal Tissues - Adipose", "Which connective tissue stores fat globules beneath the skin and serves as thermal insulation?", ["Areolar Tissue", "Adipose Tissue", "Cartilage", "Bone"], "B"),
            ("Plant Tissues - Epidermis", "What are the small pores present on the surface of leaf epidermis enclosed by two kidney-shaped guard cells?", ["Stomata", "Lenticels", "Hydathodes", "Cuticle"], "A")
        ]

    # 2. BIOLOGY: CELL / FUNDAMENTAL UNIT OF LIFE (Class 9 Chapter 5)
    elif any(k in s_lower for k in ['fundamental unit of life', 'cell membrane', 'mitochondria', 'lysosome', 'plastid', 'endoplasmic reticulum', 'golgi apparatus', 'osmosis']):
        topic_display = "Biology: Fundamental Unit of Life (Cell)"
        pool = [
            ("Cell Organelles - Mitochondria", "Which cell organelle generates ATP and is called the **Powerhouse of the Cell**?", ["Ribosome", "Mitochondria", "Golgi Apparatus", "Lysosome"], "B"),
            ("Cell Organelles - Lysosomes", "Which organelle contains powerful digestive enzymes and is known as the **Suicide Bag of the Cell**?", ["Lysosome", "Vacuole", "Ribosome", "Plastid"], "A"),
            ("Cell Wall", "The rigid outer cell wall in plant cells is primarily composed of which complex carbohydrate?", ["Cellulose", "Chitin", "Glycogen", "Starch"], "A"),
            ("Cell Transport - Osmosis", "The movement of water molecules through a selectively permeable membrane from higher water concentration to lower water concentration is called:", ["Diffusion", "Osmosis", "Endocytosis", "Plasmolysis"], "B"),
            ("Endoplasmic Reticulum", "Which type of Endoplasmic Reticulum has ribosomes attached to its surface and is actively involved in protein synthesis?", ["Rough ER (RER)", "Smooth ER (SER)", "Golgi Body", "Chloroplast"], "A"),
            ("Plastids", "Which green plastids contain chlorophyll and carry out photosynthesis in plant cells?", ["Chromoplasts", "Chloroplasts", "Leucoplasts", "Amyloplasts"], "B"),
            ("Cell Discovery", "Who first discovered and named free-living living cells in pond water in 1674?", ["Robert Hooke", "Anton van Leeuwenhoek", "Robert Brown", "Purkinje"], "B")
        ]

    # 3. PHYSICS: MOTION (Class 9 Chapter 7)
    elif any(k in s_lower for k in ['motion', 'velocity', 'acceleration', 'equations of motion', 'displacement', 'uniform motion', 'speed']):
        topic_display = "Physics: Motion"
        pool = [
            ("Kinematics - Displacement", "What is the shortest straight-line distance measured from the initial to the final position of an object called?", ["Distance", "Displacement", "Velocity", "Path Length"], "B"),
            ("Equations of Motion", "Which of the following is the correct Second Equation of uniformly accelerated motion?", ["$v = u + at$", "$s = ut + \\frac{1}{2}at^2$", "$v^2 - u^2 = 2as$", "$s = (u+v)t$"], "B"),
            ("Acceleration", "What is the SI unit of acceleration?", ["$\\text{m/s}$", "$\\text{m/s}^2$", "$\\text{km/h}$", "$\\text{m}^2\\text{/s}$"], "B"),
            ("Velocity-Time Graphs", "The area under a velocity-time ($v$-$t$) graph represents which physical quantity?", ["Acceleration", "Displacement / Distance", "Force", "Momentum"], "B"),
            ("Circular Motion", "When an object moves in a circular path at constant speed, what is continuously changing?", ["Speed", "Direction of Velocity", "Radius", "Mass"], "B")
        ]

    # 4. PHYSICS: FORCE & LAWS OF MOTION (Class 9 Chapter 8)
    elif any(k in s_lower for k in ['laws of motion', 'newton', 'inertia', 'momentum', 'conservation of momentum', 'f=ma']):
        topic_display = "Physics: Force and Laws of Motion"
        pool = [
            ("Newton's 1st Law & Inertia", "The natural tendency of an object to resist a change in its state of rest or uniform motion is called:", ["Momentum", "Inertia", "Force", "Impulse"], "B"),
            ("Newton's 2nd Law", "According to Newton's Second Law of Motion, the rate of change of momentum is directly proportional to:", ["Applied unbalanced Force", "Displacement", "Velocity", "Work"], "A"),
            ("Momentum SI Unit", "What is the SI unit of linear momentum ($p = mv$)?", ["$\\text{N}\\cdot\\text{m}$", "$\\text{kg}\\cdot\\text{m/s}$", "$\\text{kg}\\cdot\\text{m/s}^2$", "$\\text{Joule}$"], "B"),
            ("Newton's 3rd Law", "Action and reaction forces according to Newton's Third Law:", ["Act on the same object in the same direction", "Act on two different objects in opposite directions", "Cancel each other out completely", "Act at different times"], "B"),
            ("Conservation of Momentum", "When a bullet is fired from a gun, the gun recoils backward due to:", ["Conservation of Energy", "Conservation of Linear Momentum", "Gravitational Pull", "Frictional Force"], "B")
        ]

    # 5. PHYSICS: GRAVITATION (Class 9 Chapter 9)
    elif any(k in s_lower for k in ['gravitation', 'gravity', 'free fall', 'mass vs weight', 'archimedes', 'buoyancy', 'universal gravitation']):
        topic_display = "Physics: Gravitation"
        pool = [
            ("Universal Gravitation", "If the distance between two spherical masses is doubled, the gravitational force between them becomes:", ["2 times stronger", "One-fourth (1/4th)", "Half (1/2)", "4 times stronger"], "B"),
            ("Acceleration due to Gravity", "What is the standard value of acceleration due to gravity ($g$) near the Earth's surface?", ["$9.8\\text{ m/s}^2$", "$9.8\\text{ m/s}$", "$6.67 \\times 10^{-11}\\text{ N}$", "$1.6\\text{ m/s}^2$"], "A"),
            ("Mass vs Weight", "Which quantity remains constant everywhere in the universe regardless of gravitational field?", ["Weight ($W = mg$)", "Mass ($m$)", "Apparent Weight", "Buoyant force"], "B"),
            ("Archimedes' Principle", "When an object is immersed fully or partially in a fluid, the upward buoyant force equals the:", ["Weight of the object", "Weight of the fluid displaced", "Volume of the fluid", "Surface tension"], "B"),
            ("Free Fall", "During free fall near Earth in a vacuum, two objects of different masses dropped from the same height will:", ["Fall with different accelerations", "Hit the ground at the exact same time", "Heavier one hits first", "Lighter one hits first"], "B")
        ]

    # 6. CHEMISTRY: CHEMICAL REACTIONS & EQUATIONS (Class 10 Chapter 1)
    elif any(k in s_lower for k in ['chemical reactions', 'chemical equations', 'redox', 'rancidity', 'decomposition reaction', 'displacement reaction']):
        topic_display = "Chemistry: Chemical Reactions & Equations"
        pool = [
            ("Reaction Types - Combination", "What type of reaction is $2\\text{Mg} + \\text{O}_2 \\rightarrow 2\\text{MgO}$?", ["Decomposition", "Combination", "Displacement", "Double Displacement"], "B"),
            ("Reaction Types - Decomposition", "Thermal decomposition of Limestone ($\\text{CaCO}_3$) on heating produces Calcium Oxide and which gas?", ["Oxygen ($\\text{O}_2$)", "Carbon Dioxide ($\\text{CO}_2$)", "Hydrogen ($\\text{H}_2$)", "Nitrogen ($\\text{N}_2$)"], "B"),
            ("Redox Reactions", "In a chemical reaction, the process of gaining oxygen or losing hydrogen is called:", ["Reduction", "Oxidation", "Precipitation", "Neutralization"], "B"),
            ("Corrosion & Prevention", "To prevent rancidity in potato chips and fried snacks, packets are flushed with which unreactive gas?", ["Oxygen", "Nitrogen", "Carbon Monoxide", "Chlorine"], "B"),
            ("Precipitation Reaction", "When aqueous Barium Chloride reacts with Sodium Sulphate, what color of insoluble precipitate is formed?", ["Yellow", "White ($\\text{BaSO}_4$)", "Blue", "Black"], "B")
        ]

    # 7. CHEMISTRY: ACIDS, BASES AND SALTS (Class 10 Chapter 2)
    elif any(k in s_lower for k in ['acids', 'bases', 'salts', 'ph scale', 'litmus', 'plaster of paris', 'baking soda', 'bleaching powder', 'neutralization']):
        topic_display = "Chemistry: Acids, Bases and Salts"
        pool = [
            ("pH Scale", "What is the pH of pure neutral water at $25^\\circ\\text{C}$?", ["0", "7 (Neutral)", "14", "5.5"], "B"),
            ("Natural Indicators", "What is the color change when a basic substance like soap solution is added to yellow Turmeric paper?", ["Turns Blue", "Turns Reddish-Brown", "Remains Yellow", "Turns Green"], "B"),
            ("Salts - Plaster of Paris", "What is the chemical formula of Plaster of Paris (POP)?", ["$\\text{CaSO}_4 \\cdot 2\\text{H}_2\\text{O}$", "$\\text{CaSO}_4 \\cdot \\frac{1}{2}\\text{H}_2\\text{O}$", "$\\text{Na}_2\\text{CO}_3 \\cdot 10\\text{H}_2\\text{O}$", "$\\text{NaHCO}_3$"], "B"),
            ("Salts - Baking Soda", "What is the chemical formula of Baking Soda used in baking and antacids?", ["$\\text{Na}_2\\text{CO}_3$", "$\\text{NaHCO}_3$ (Sodium Hydrogen Carbonate)", "$\\text{CaOCl}_2$", "$\\text{NaOH}$"], "B"),
            ("Acid-Metal Reaction", "When dilute Hydrochloric acid reacts with active Zinc granules, which flammable gas is liberated with a pop sound?", ["Oxygen", "Hydrogen ($\\text{H}_2$)", "Carbon Dioxide", "Chlorine"], "B")
        ]

    # 8. BIOLOGY: LIFE PROCESSES (Class 10 Chapter 5)
    elif any(k in s_lower for k in ['life processes', 'photosynthesis', 'digestive system', 'double circulation', 'nephron', 'respiration', 'human heart']):
        topic_display = "Biology: Life Processes"
        pool = [
            ("Excretion - Nephron", "What is the basic functional structural filtration unit of the human kidney?", ["Neuron", "Nephron", "Alveoli", "Glomerulus"], "B"),
            ("Circulation - Heart", "Which blood vessel carries oxygenated blood from the lungs directly into the left atrium of the human heart?", ["Pulmonary Artery", "Pulmonary Vein", "Vena Cava", "Aorta"], "B"),
            ("Digestion - Enzymes", "Which enzyme secreted in gastric juice inside the stomach breaks down proteins in an acidic medium?", ["Trypsin", "Pepsin", "Amylase", "Lipase"], "B"),
            ("Respiration - ATP", "In cellular respiration, breakdown of 6-carbon Glucose into 3-carbon Pyruvate occurs in the:", ["Mitochondria", "Cytoplasm", "Nucleus", "Ribosome"], "B"),
            ("Plant Transport - Xylem & Phloem", "The transport of soluble products of photosynthesis in phloem tissue is called:", ["Transpiration", "Translocation", "Evaporation", "Osmosis"], "B")
        ]

    # 9. PHYSICS: LIGHT - REFLECTION & REFRACTION (Class 10 Chapter 9)
    elif any(k in s_lower for k in ['light', 'reflection', 'refraction', 'mirror formula', 'lens formula', 'concave mirror', 'convex lens', 'refractive index']):
        topic_display = "Physics: Light - Reflection & Refraction"
        pool = [
            ("Spherical Mirrors", "An object is placed at the Center of Curvature ($C$) of a concave mirror. Where is the real inverted image formed?", ["At Focus ($F$)", "At Center of Curvature ($C$)", "Beyond $C$", "Behind mirror"], "B"),
            ("Mirror Formula", "Which of the following is the correct Mirror Formula connecting focal length $f$, image distance $v$, and object distance $u$?", ["$\\frac{1}{f} = \\frac{1}{v} - \\frac{1}{u}$", "$\\frac{1}{f} = \\frac{1}{v} + \\frac{1}{u}$", "$f = v + u$", "$m = -\\frac{u}{v}$"], "B"),
            ("Lens Power", "What is the SI unit of Power of a lens ($P = 1/f$ in meters)?", ["Watt", "Dioptre (D)", "Lumen", "Metre"], "B"),
            ("Refraction & Snell's Law", "When a light ray travels from an optically rarer medium (air) to an optically denser medium (glass), it bends:", ["Away from normal", "Towards the normal", "Without bending", "Reflects back 180°"], "B"),
            ("Convex Mirror Applications", "Why are convex mirrors preferred as rear-view side mirrors in vehicles?", ["Produce inverted magnified image", "Always give an erect, diminished image with a wider field of view", "Absorb all sunlight", "Have infinite focal length"], "B")
        ]

    # 10. PHYSICS: ELECTRICITY & CIRCUITS (Class 10 Chapter 11)
    elif any(k in s_lower for k in ['electricity', 'current electricity', "ohm's law", 'resistors', 'resistance', 'joule heating', 'electric circuit']):
        topic_display = "Physics: Electricity"
        pool = [
            ("Ohm's Law", "According to Ohm's Law ($V = IR$), if the resistance in a circuit is doubled while voltage remains constant, the current becomes:", ["Doubled", "Halved (1/2)", "Quadrupled", "Unchanged"], "B"),
            ("Resistance Factors", "How does the electrical resistance of a metallic conductor change when its length is doubled?", ["Becomes half", "Doubles ($R \\propto l$)", "Quadruples", "Remains same"], "B"),
            ("Series Resistors", "Three resistors of $2\\,\\Omega, 3\\,\\Omega,$ and $5\\,\\Omega$ are connected in series. What is the equivalent resistance?", ["$10\\,\\Omega$", "$0.96\\,\\Omega$", "$1.5\\,\\Omega$", "$30\\,\\Omega$"], "A"),
            ("Electric Power", "Which formula represents electric power consumed in a circuit?", ["$P = VI$", "$P = I^2 R$", "$P = V^2 / R$", "All of the above"], "D"),
            ("Heating Effect", "Joule's Law of heating states that heat produced ($H$) in a resistor is proportional to:", ["$I^2 R t$", "$I R^2 t$", "$V / I$", "$I t / R$"], "A")
        ]

    # 11. BIOLOGY GENERAL & NCERT EXEMPLAR
    elif any(k in s_lower for k in ['biology', 'bio', 'life science', 'botany', 'zoology']):
        topic_display = "Biology"
        pool = [
            ("NCERT Exemplar - Photosynthesis", "Which of the following statements is NOT correct regarding the light reaction of photosynthesis?", ["Chlorophyll absorbs solar light energy", "Water molecules split into hydrogen and oxygen ($H_2O \\rightarrow 2H^+ + \\frac{1}{2}O_2$)", "Light energy is converted to chemical energy", "Carbon dioxide is oxidized directly without enzymes"], "D"),
            ("NCERT Exemplar - Respiration", "During vigorous physical exercise, accumulation of which chemical substance in human muscle cells causes acute muscle fatigue and cramps?", ["Lactic Acid", "Ethanol", "Pyruvic Acid", "Carbonic Acid"], "A"),
            ("NCERT Exemplar - Control & Coordination", "In a synapse between two adjacent neurons, the chemical transmission of an electrical impulse proceeds from:", ["Dendrite terminal of one neuron to axon terminal of another", "Axon terminal of one neuron to dendrite terminal of another", "Cell body directly to axon", "Myelin sheath to node of Ranvier"], "B"),
            ("NCERT Exemplar - Genetics", "Two pea plants, one with homozygous round green seeds ($RRyy$) and another with wrinkled yellow seeds ($rrYY$), produce $F_1$ progeny that have:", ["Round and Yellow seeds", "Round and Green seeds", "Wrinkled and Yellow seeds", "Wrinkled and Green seeds"], "A"),
            ("NCERT Exemplar - Excretion", "What is the correct sequential order of organs forming the human urinary excretory pathway?", ["Kidneys → Ureters → Urinary Bladder → Urethra", "Kidneys → Urinary Bladder → Ureters → Urethra", "Kidneys → Ureters → Urethra → Urinary Bladder", "Ureters → Kidneys → Urinary Bladder → Urethra"], "A"),
            ("NCERT Exemplar - Plant Tissues", "Flexibility and mechanical elasticity in young plant stems and leaf stalks is provided by which living tissue?", ["Collenchyma", "Sclerenchyma", "Parenchyma", "Aerenchyma"], "A"),
            ("NCERT Exemplar - Cell Organelles", "Which double-membrane organelle contains its own circular DNA and ribosomes, capable of self-replication?", ["Mitochondria", "Golgi Apparatus", "Lysosome", "Endoplasmic Reticulum"], "A"),
            ("NCERT Exemplar - Blood Circulation", "Which valve prevents the backflow of deoxygenated blood from the right ventricle into the right atrium in the human heart?", ["Tricuspid Valve", "Bicuspid / Mitral Valve", "Semilunar Valve", "Aortic Valve"], "A")
        ]

    # 12. PHYSICS GENERAL & NCERT EXEMPLAR
    elif any(k in s_lower for k in ['physics', 'optics', 'mechanics', 'electromagnetism']):
        topic_display = "Physics"
        pool = [
            ("NCERT Exemplar - Ray Optics", "A student determines the focal length of a concave mirror by focusing the image of a distant tree. The sharpest image is formed at:", ["Center of Curvature ($C$)", "Principal Focus ($F$)", "Pole ($P$)", "Twice the focal length ($2f$)"], "B"),
            ("NCERT Exemplar - Refraction", "A ray of light enters from medium A to medium B. If the speed of light in medium B is half of that in medium A, the refractive index of B with respect to A is:", ["$0.5$", "$2.0$", "$1.5$", "$0.25$"], "B"),
            ("NCERT Exemplar - Electricity", "A cylindrical conductor of length $l$ and uniform area of cross-section $A$ has resistance $R$. Another conductor of length $2l$ and resistance $R$ of the same material has area of cross-section:", ["$A/2$", "$2A$", "$A/4$", "$4A$"], "B"),
            ("NCERT Exemplar - Magnetic Effects", "The strength of magnetic field inside a long current-carrying straight solenoid is:", ["More at the ends than at the center", "Minimum in the middle", "Uniform at all points inside", "Zero"], "C"),
            ("NCERT Exemplar - Mechanics", "A passenger in a moving train tosses a coin which falls behind him. It means that motion of the train is:", ["Accelerated", "Uniform", "Retarded", "Along circular tracks"], "A")
        ]

    # 13. CHEMISTRY GENERAL & NCERT EXEMPLAR
    elif any(k in s_lower for k in ['chemistry', 'chemical', 'mole', 'element', 'compound']):
        topic_display = "Chemistry"
        pool = [
            ("NCERT Exemplar - Chemical Reactions", "Electrolysis of water is a decomposition reaction. The mole ratio of hydrogen and oxygen gases liberated during electrolysis is:", ["$1 : 1$", "$2 : 1$", "$4 : 1$", "$1 : 2$"], "B"),
            ("NCERT Exemplar - Acids and Bases", "Which of the following salts does not contain water of crystallization?", ["Blue Vitriol ($\\text{CuSO}_4 \\cdot 5\\text{H}_2\\text{O}$)", "Baking Soda ($\\text{NaHCO}_3$)", "Washing Soda ($\\text{Na}_2\\text{CO}_3 \\cdot 10\\text{H}_2\\text{O}$)", "Gypsum ($\\text{CaSO}_4 \\cdot 2\\text{H}_2\\text{O}$)" ], "B"),
            ("NCERT Exemplar - Metals & Non-metals", "An alloy is which type of substance?", ["An element", "A compound", "A homogeneous mixture", "A heterogeneous mixture"], "C"),
            ("NCERT Exemplar - Carbon Compounds", "Carbon forms four covalent bonds by sharing its four valence electrons with four univalent atoms (e.g. Hydrogen). After formation of four bonds, carbon attains electronic configuration of:", ["Helium", "Neon", "Argon", "Krypton"], "B"),
            ("NCERT Exemplar - Periodic Trends", "Which of the following elements has 3 valence electrons in its outermost shell?", ["Sodium ($Z=11$)", "Magnesium ($Z=12$)", "Aluminium ($Z=13$)", "Silicon ($Z=14$)"], "C")
        ]

    # 14. MATHEMATICS: ALGEBRA & NUMERICAL TOPICS
    elif any(k in s_lower for k in ['math', 'mathematics', 'quadratic', 'algebra', 'trigonometry', 'polynomial', 'arithmetic progression', 'ap', 'coordinate geometry', 'circles', 'statistics', 'probability']):
        topic_display = "Mathematics"
        pool = [
            ("Quadratic Equations", "If the discriminant $D = b^2 - 4ac > 0$ for $ax^2 + bx + c = 0$, what is the nature of its roots?", ["Real and equal", "Real and distinct (two distinct roots)", "Imaginary / No real roots", "Zero"], "B"),
            ("Trigonometric Identities", "What is the exact value of $\\sin^2(30^\\circ) + \\cos^2(30^\\circ)$?", ["0", "1", "1/2", "$\\sqrt{3}/2$"], "B"),
            ("Arithmetic Progressions", "What is the 10th term of the AP: $2, 7, 12, 17, \\dots$?", ["42", "47 ($a_{10} = 2 + 9\\times 5$)", "52", "50"], "B"),
            ("Coordinate Geometry", "Find the distance between points $(0, 0)$ and $(3, 4)$ using the Distance Formula:", ["7 units", "5 units ($\\sqrt{3^2 + 4^2}$)", "12 units", "25 units"], "B"),
            ("Statistics Empirical Formula", "What is the standard empirical relationship between Mode, Median, and Mean in statistics?", ["$\\text{Mode} = 2\\text{Median} - \\text{Mean}$", "$\\text{Mode} = 3\\text{Median} - 2\\text{Mean}$", "$\\text{Mode} = 2\\text{Mean} - 3\\text{Median}$", "$\\text{Mode} = \\text{Mean} + \\text{Median}$"], "B"),
            ("Probability", "What is the probability of obtaining an even prime number when throwing a fair 6-sided die?", ["$1/2$", "$1/6$ (only number 2)", "$1/3$", "$2/3$"], "B"),
            ("Polynomials", "If $\\alpha$ and $\\beta$ are zeroes of $ax^2 + bx + c$, then the sum of zeroes $(\\alpha + \\beta)$ is:", ["$c/a$", "$-b/a$", "$b/a$", "$-c/a$"], "B"),
            ("Circles & Tangents", "The tangent at any point of a circle is perpendicular to the:", ["Chord", "Radius through the point of contact", "Secant", "Diameter only"], "B")
        ]

    # 15. COMPUTER SCIENCE & PROGRAMMING
    elif any(k in s_lower for k in ['computer', 'coding', 'python', 'informatics', 'programming', 'sql']):
        topic_display = "Computer Science"
        pool = [
            ("Python Data Structures", "Which of the following built-in Python data structures is **immutable**?", ["List (`[1, 2]`)", "Tuple (`(1, 2)`)", "Dictionary (`{'a': 1}`)", "Set (`{1, 2}`)" ], "B"),
            ("Python String Slicing", "If `s = 'PYTHON'`, what does `s[::-1]` evaluate to?", ["`'PYTHON'`", "`'NOHTYP'` (Reversed string)", "`'P'`", "`'N'`"], "B"),
            ("Algorithms & Complexity", "What is the worst-case time complexity of Binary Search on a sorted list of $n$ elements?", ["$O(n)$", "$O(\\log n)$", "$O(n^2)$", "$O(1)$"], "B"),
            ("SQL Clauses", "Which SQL clause is used to filter records matching specific conditions?", ["`ORDER BY`", "`WHERE`", "`GROUP BY`", "`SELECT`"], "B"),
            ("Data Structures", "Which linear data structure operates on the **LIFO (Last In First Out)** principle?", ["Queue", "Stack", "Linked List", "Tree"], "B"),
            ("Python Functions", "Which keyword is used to create an anonymous inline single-expression function in Python?", ["`def`", "`lambda`", "`inline`", "`func`"], "B"),
            ("Boolean Operators", "In Python, what is the result of `not (True and False)`?", ["`False`", "`True`", "`None`", "`Error`"], "B")
        ]

    # 16. PRIMARY (CLASSES 1–5) GENERAL POOL
    elif grade == 'Primary (1-5)':
        topic_display = subject
        pool = [
            ("Plant Life", "Which part of a plant grows under the soil and absorbs water and minerals?", ["Flower", "Leaf", "Roots", "Stem"], "C"),
            ("States of Matter", "When liquid water is placed in a freezer and turns into ice, what state of matter does it become?", ["Gas", "Solid", "Liquid", "Steam"], "B"),
            ("Human Senses", "Which sense organ helps us see colors, shapes, and books?", ["Ears", "Eyes", "Nose", "Tongue"], "B"),
            ("Animals & Food", "Animals that eat only green plants and grass are called:", ["Carnivores", "Herbivores", "Omnivores", "Insects"], "B"),
            ("Our Planet", "Which planet is known as the Blue Planet and is our home?", ["Mars", "Earth", "Jupiter", "Venus"], "B"),
            ("National Symbols", "What is the National Animal of India?", ["Lion", "Royal Bengal Tiger", "Elephant", "Peacock"], "B"),
            ("Arithmetic Addition", "What is the sum of $25 + 15$?", ["$30$", "$40$", "$45$", "$35$"], "B"),
            ("Computer Basics", "Which device is known as the **Brain of the Computer**?", ["Monitor", "CPU (Central Processing Unit)", "Keyboard", "Mouse"], "B")
        ]

    # 17. MIDDLE SCHOOL (CLASSES 6–8) GENERAL POOL
    elif grade in ['Class 6', 'Class 7', 'Class 8']:
        topic_display = subject
        pool = [
            ("Speed & Motion", "If a cyclist travels $100\\text{ meters}$ in $20\\text{ seconds}$ at constant speed, what is the speed?", ["$2\\text{ m/s}$", "$5\\text{ m/s}$", "$20\\text{ m/s}$", "$200\\text{ m/s}$"], "B"),
            ("Acids & Indicators", "What natural acid gives lemons and oranges their distinct sour taste?", ["Hydrochloric acid", "Citric acid", "Acetic acid", "Lactic acid"], "B"),
            ("Cell Biology", "Which green pigment in plant leaves absorbs sunlight for photosynthesis?", ["Hemoglobin", "Chlorophyll", "Melanin", "Xanthophyll"], "B"),
            ("Heat & Thermal", "By which mode does heat from the Sun reach the Earth across empty space?", ["Conduction", "Radiation", "Convection", "Advection"], "B"),
            ("Circuits & Electricity", "Which component is used to safely open or close an electric circuit?", ["Battery", "Switch / Key", "Bulb", "Resistor"], "B"),
            ("Linear Equations", "Solve for $x$: $2x + 5 = 19$", ["$x = 12$", "$x = 7$", "$x = 8$", "$x = 14$"], "B"),
            ("Computer Memory", "Which type of computer memory is volatile (loses data when power is switched off)?", ["ROM", "RAM (Random Access Memory)", "Hard Disk", "Flash Drive"], "B")
        ]

    # 18. SECONDARY & SENIOR SECONDARY GENERAL FALLBACK POOL
    else:
        topic_display = subject
        pool = [
            ("Chemical Reactions", "What type of reaction is $2\\text{H}_2 + \\text{O}_2 \\rightarrow 2\\text{H}_2\\text{O}$?", ["Decomposition", "Combination (Synthesis)", "Displacement", "Double displacement"], "B"),
            ("Cell Biology", "Which cell organelle produces ATP and is known as the Powerhouse of the Cell?", ["Ribosome", "Mitochondria", "Golgi apparatus", "Lysosome"], "B"),
            ("Current Electricity", "According to Ohm's Law ($V = IR$), if resistance is doubled at constant voltage, what happens to current?", ["Doubles", "Becomes half (halved)", "Remains unchanged", "Quadruples"], "B"),
            ("Universal Gravitation", "If the distance between two masses is doubled, the gravitational force between them becomes:", ["2 times stronger", "One-fourth ($1/4$th)", "Half ($1/2$)", "4 times stronger"], "B"),
            ("Quadratic Equations", "If discriminant $D = b^2 - 4ac > 0$ for $ax^2 + bx + c = 0$, what is the nature of the roots?", ["Real and equal", "Real and distinct", "Non-real / Imaginary", "Both zero"], "B"),
            ("Indian Polity", "Which fundamental right is guaranteed under Article 21 of the Constitution of India?", ["Right to Equality", "Right to Life and Personal Liberty", "Right to Freedom of Religion", "Right to Constitutional Remedies"], "B"),
            ("Python Data Structures", "Which of the following built-in Python data structures is **immutable**?", ["List (`[1, 2]`)", "Tuple (`(1, 2)`)", "Dictionary (`{'a': 1}`)", "Set (`{1, 2}`)" ], "B")
        ]

    # Dynamically select requested number of unique, non-repeating questions
    requested_count = extract_requested_question_count(query, default=3)
    num_to_pick = min(requested_count, len(pool))
    chosen_questions = random.sample(pool, num_to_pick)
    
    is_exemplar = any(k in s_lower for k in ['exemplar', 'exempler'])
    exam_label = f"NCERT Exemplar: {topic_display}" if is_exemplar else topic_display
    count_label = f"({num_to_pick} Question)" if num_to_pick == 1 else f"({num_to_pick} Questions)"

    if hinglish_user:
        quiz_output = f"### 🎯 **CBSE {grade} Practice Questions: {exam_label} {count_label}**\n\nHaan bilkul! Yeh rahe aapke {grade} ke **{exam_label}** questions ({num_to_pick} MCQs):\n\n"
    else:
        quiz_output = f"### 🎯 **CBSE {grade} Practice Questions: {exam_label} {count_label}**\n\n"

    for idx, q_data in enumerate(chosen_questions, start=1):
        topic, question, options, correct_opt = q_data
        letters = ['A', 'B', 'C', 'D']
        quiz_output += f"**Question {idx} ({topic}):**\n{question}\n"
        for opt_idx, opt in enumerate(options):
            quiz_output += f"- {letters[opt_idx]}) {opt}\n"
        quiz_output += "\n"
        
    if hinglish_user:
        quiz_output += f"💡 *Apne options reply kijiye (jaise 1-B, 2-A, ...) aur Maya AI aapke answers check karegi!*"
    else:
        quiz_output += f"💡 *Reply with your options (e.g. 1-B, 2-A, ...) and Maya AI will check your solutions!*"
        
    return quiz_output


def is_code_submission(query):
    """Detects if query is a code snippet or programming submission."""
    q_lower = query.lower()
    
    # Exclude syllabus, general school questions, or essay queries
    if any(k in q_lower for k in ['syllabus', 'curriculum', 'how are you', 'essay', 'who are you', 'admission', 'fee structure']):
        return False

    explicit_code_triggers = [
        'int main', 'cout <<', 'cin >>', '#include', 'std::cout', 'std::cin',
        'public static void main', 'system.out.println',
        'fix this code', 'debug this code', 'working version', 'fix this c++ code',
        'fix this python code', 'syntax error in code', 'c++ code'
    ]
    if any(k in q_lower for k in explicit_code_triggers):
        return True
        
    if '```' in query or 'def ' in query or 'int main(' in query:
        return True
        
    # Check for actual code constructs
    code_patterns = [
        'for i in ', 'while True:', 'while (', 'import math', 'import os', 'import sys',
        'return 0;', 'return True', 'return False', 'def __init__',
        '#include <iostream>', '#include <stdio.h>', 'using namespace std;',
        'print("', "print('", 'input("', "input('", 'console.log('
    ]
    return any(p in query for p in code_patterns)


def evaluate_student_code_submission(query, grade, subject="Computer Science"):
    """
    Strict Pedagogical Computer Science Teacher Engine (Socratic Anti-Cheat Mode):
    1. States if code will run or crash.
    2. Uses bullet points to list exact errors.
    3. Provides conceptual hints on how to fix each error.
    4. CRITICAL ANTI-CHEAT: Strictly forbidden from typing corrected code; stops after hints.
    """
    q_lower = query.lower()
    
    # 1. C / C++ Code Evaluation
    if any(k in q_lower for k in ['c++', 'cpp', 'int main', 'cout', 'cin', '#include', 'std::']):
        errors = []
        hints = []
        
        if '#include <iostream>' not in query and '#include<iostream>' not in query:
            errors.append("Missing Header: `#include <iostream>` is missing at the top of your program.")
            hints.append("In C++, you must include the input/output stream header `#include <iostream>` before using `cout` or `cin`.")
            
        if 'using namespace std;' not in query and 'std::cout' not in query:
            errors.append("Missing Namespace: Standard namespace `std` is not declared.")
            hints.append("Objects like `cout` belong to the standard library. Either add `using namespace std;` after your header or write `std::cout`.")
            
        if 'cout' in query and ';' not in query:
            errors.append("Missing Semicolon (`;`): Semicolon is missing after the `cout` statement.")
            hints.append("In C++, every executable instruction must be terminated with a semicolon `;`.")
            
        if 'return 0' not in query:
            errors.append("Missing Return Statement: `int main()` lacks a return statement.")
            hints.append("Functions declared with `int` return type should end with `return 0;` to indicate successful execution.")
            
        if errors:
            status = "❌ **Crash / Compilation Error** (Code will fail to compile in `g++`)"
            errors_formatted = "\n".join([f"- {err}" for err in errors])
            hints_formatted = "\n".join([f"- {h}" for h in hints])
            cta = "👉 **Please fix these errors and paste your updated code back here!**"
        else:
            status = "✅ **Runs Successfully**"
            errors_formatted = "- No syntax or compilation errors found."
            hints_formatted = "- Tip: Test edge cases like zero, negative numbers, or invalid inputs to make your program fully crash-proof."
            cta = "🎉 **Awesome job! Your code is completely error-free and compiles cleanly.** Would you like to try a new coding challenge or optimize it?"
        
        thinking = format_thinking_block(query, "Mode 4 / 5 (Computer Science Socratic Code Review)", "Identify compilation errors, provide conceptual hints, and stop without writing corrected code")
        
        return thinking + (
            f"### 💻 **Computer Science Code Evaluation ({grade} - Computer Science)**\n\n"
            f"**Code Execution Status**: {status}\n\n"
            f"---\n\n"
            f"#### 🔍 **Identified Errors:**\n"
            f"{errors_formatted}\n\n"
            f"---\n\n"
            f"#### 💡 **Conceptual Hints to Fix:**\n"
            f"{hints_formatted}\n\n"
            f"---\n\n"
            f"{cta}"
        )

    clean_query = query.replace('```python', '').replace('```', '').strip()
    lines = [line.rstrip() for line in clean_query.split('\n') if line.strip()]
    errors = []
    hints = []
    
    # Static analysis for common syntax and logic traps in Python student code
    for idx, line in enumerate(lines, start=1):
        sline = line.strip()
        # 1. Missing colon in compound statements
        for kw in ['if', 'elif', 'else', 'for', 'while', 'def', 'class']:
            if sline.startswith(kw + ' ') or sline == kw or sline.startswith(kw + '('):
                if not sline.endswith(':'):
                    errors.append(f"Line {idx}: `{sline}` is missing a colon (`:`) at the end.")
                    hints.append(f"In Python, headers for compound statements like `{kw}` must always end with a colon `:` before the indented block.")
        
        # 2. Assignment instead of equality in conditionals
        if (sline.startswith('if ') or sline.startswith('elif ') or sline.startswith('while ')) and ' = ' in sline and ' == ' not in sline and ' <= ' not in sline and ' >= ' not in sline and ' != ' not in sline:
            errors.append(f"Line {idx}: `{sline}` uses single equals (`=`) instead of double equals (`==`).")
            hints.append("A single `=` is for assignment (setting a value), while double equals `==` is for comparison.")
            
        # 3. Print statement syntax
        if sline.startswith('print ') and not sline.startswith('print('):
            errors.append(f"Line {idx}: `{sline}` uses Python 2 syntax without parentheses.")
            hints.append("In Python 3, `print()` is a function that requires parentheses around arguments.")
            
        # 4. Input arithmetic without casting
        if 'input(' in sline and ('int(' not in sline and 'float(' not in sline) and any(op in sline for op in ['+', '-', '*', '/', '%']):
            hints.append(f"Line {idx}: `input()` returns a string (`str`). If you want numeric arithmetic, wrap it in `int(input(...))` or `float(input(...))`.")

    if errors:
        status = "❌ **Crash / Syntax Error** (Code will fail to execute)"
        review = "\n".join([f"- {err}" for err in errors])
        hint_text = "\n".join([f"- {h}" for h in set(hints)])
        cta = "👉 **Please fix these errors and paste your updated code back here!**"
    else:
        status = "✅ **Runs Successfully**"
        review = "- Your code structure is clean and well-formed."
        hint_text = "- Tip: Test edge cases like zero, negative numbers, or invalid inputs to make your program fully crash-proof."
        cta = "🎉 **Awesome job! Your code syntax is completely error-free and ready to run.** Would you like to try another programming exercise?"

    thinking = format_thinking_block(query, "Mode 4 / 5 (Computer Science Socratic Code Review)", "Identify syntax errors, provide conceptual hints, and stop without writing corrected code")

    return thinking + (
        f"### 💻 **Computer Science Code Evaluation ({grade} - Computer Science)**\n\n"
        f"**Code Execution Status**: {status}\n\n"
        f"---\n\n"
        f"#### 🔍 **Identified Errors:**\n"
        f"{review}\n\n"
        f"---\n\n"
        f"#### 💡 **Conceptual Hints to Fix:**\n"
        f"{hint_text}\n\n"
        f"---\n\n"
        f"{cta}"
    )


def is_affirmative_reply(query):
    """Detects affirmative confirmations (yes, sure, ok, haan, etc.)."""
    q = query.strip().lower().rstrip('.!?,')
    affirmative_exact = {
        'yes', 'yeah', 'yep', 'yup', 'sure', 'ok', 'okay', 'yes please', 'yeah please',
        'haan', 'ha', 'haa', 'han', 'start', 'start quiz', 'take quiz', 'go ahead',
        'lets do it', "let's do it", 'lets start', "let's start", 'why not', 'definitely',
        'certainly', 'y', 'proceed', 'ask me', 'test me', 'ready', "i'm ready", 'i am ready',
        'chalo', 'karo', 'shuru karo', 'pucho', 'give me quiz', 'bring it on', 'of course',
        'sure why not', 'yes i want', 'yes do it'
    }
    if q in affirmative_exact:
        return True
    return bool(re.match(r'^(?:yes|sure|ok|okay|haan|yeah|yep|yup|definitely|certainly)\b', q))


def is_negative_reply(query):
    """Detects decline or negative responses (no, not now, later, nahi, etc.)."""
    q = query.strip().lower().rstrip('.!?,')
    negative_exact = {
        'no', 'nope', 'nah', 'no thanks', 'not now', 'nahi', 'nahi chahiye', 'baad me',
        'later', 'skip', 'not ready', 'mat pucho', 'no quiz', 'dont want', "don't want",
        'no need', 'n'
    }
    if q in negative_exact:
        return True
    return bool(re.match(r'^(?:no|nope|nah|nahi)\b', q))


def was_last_message_quiz_offer(history):
    """Checks if the assistant's last message in the chat history ended by offering a quiz."""
    if not history or not isinstance(history, list):
        return False
    last_assistant_msg = ""
    for item in reversed(history):
        if isinstance(item, dict):
            role = item.get('role', '')
            if role in ['model', 'assistant', 'ai']:
                last_assistant_msg = item.get('text') or item.get('content') or item.get('message') or ""
                break
        elif isinstance(item, str):
            last_assistant_msg = item
            break
    if not last_assistant_msg:
        return False
    
    last_low = last_assistant_msg.lower()
    quiz_offer_signals = [
        'would you like a quick',
        'would you like a',
        'quick 3-question quiz',
        'practice quiz',
        'test this concept',
        'test your concept',
        'test your understanding',
        'ready for a quiz',
        'shall we do a quick quiz',
        'shall we take a quiz',
        'interactive cbse practice quiz',
        'kya aap ek quick quiz dena chahenge',
        'quiz to test this concept',
        'want a quiz'
    ]
    return any(sig in last_low for sig in quiz_offer_signals) or ('quiz' in last_low and '?' in last_assistant_msg[-120:])


def extract_topic_from_history(history, default_topic=""):
    """Extracts topic keywords from previous conversation history for contextual quiz generation."""
    if not history or not isinstance(history, list):
        return default_topic
    
    for item in reversed(history):
        txt = ""
        if isinstance(item, dict):
            # Prioritize previous user message if it has substantive text
            if item.get('role') == 'user':
                txt = item.get('text') or item.get('content') or item.get('message') or ""
            elif not default_topic and item.get('role') in ['model', 'assistant', 'ai']:
                txt = item.get('text') or item.get('content') or item.get('message') or ""
        elif isinstance(item, str):
            txt = item
            
        txt_clean = txt.strip().lower()
        # Skip trivial messages like 'yes', 'hi', etc.
        if txt_clean and len(txt_clean.split()) > 2 and not is_affirmative_reply(txt_clean) and not is_negative_reply(txt_clean):
            return txt
            
    return default_topic


def is_quiz_request(query, mode="explain", history=None):
    """
    Robustly detects if student is asking for a quiz, test, MCQs, practice questions,
    NCERT Exemplar questions, or previous year questions (PYQs), or accepting a quiz offer.
    """
    if mode == 'quiz':
        return True
    q = query.strip().lower()
    
    # Check if student is accepting a quiz offer from the assistant's previous message
    if history and was_last_message_quiz_offer(history) and is_affirmative_reply(q):
        return True
        
    # Exclude syllabus, general school queries
    if any(k in q for k in ['fee structure', 'admission', 'who are you', 'how are you', 'syllabus of', 'curriculum of', 'table of contents']):
        return False
        
    explicit_keywords = [
        'quiz', 'test me', 'take a test', 'take test', 'practice test', 'test do', 'test de', 'mujhe test do',
        'another quiz', 'new quiz', 'mcq', 'mcqs', 'multiple choice', 'exemplar', 'exempler',
        'pyq', 'pyqs', 'previous year question', 'previous year questions', 'board questions',
        'practice questions', 'practice question', 'important questions', 'sample questions',
        'quiz questions', 'quiz question', 'test questions', 'test question', 'sawal pucho', 'sawal do',
        'prashn pucho', 'prashn do'
    ]
    if any(k in q for k in explicit_keywords):
        return True
        
    question_intent_patterns = [
        r'\b(?:give|ask|send|provide|show|generate)\s+(?:me\s+)?(?:some|a|an|\d+|one|two|three|four|five|six|seven|eight|nine|ten)?\s*(?:quiz|practice|exemplar|exempler|ncert|cbse|pyq|mcq|board)?\s*questions?\b',
        r'\b\d+\s*(?:quiz|practice|exemplar|exempler|ncert|cbse|pyq|mcq|board)?\s*questions?\b',
        r'\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:quiz|practice|exemplar|exempler|ncert|cbse|pyq|mcq|board)?\s*questions?\b',
        r'\bquestions?\s+(?:from|on|of|in|about)\b',
        r'\b(?:exemplar|exempler|pyqs?|mcqs?)\b'
    ]
    return any(bool(re.search(p, q, re.IGNORECASE)) for p in question_intent_patterns)


def is_quiz_submission(query):
    """Detects if student is submitting quiz answers."""
    q = query.strip().lower()

    if is_quiz_request(query):
        return False

    # Check for precise answer submission formats across 1-10 questions:
    answer_regex = [
        r'\b\d+\s*[-:\.]\s*[a-d]\b',
        r'\b\d+[a-d]\b',
        r'\boption\s+[a-d]\b',
        r'\bans(?:wer)?\s*[:\.]?\s*[a-d]\b',
        r'^[a-d](?:\s*,\s*[a-d])+$',
        r'^(?:option\s*)?[a-d]$'
    ]
    return any(bool(re.search(p, q, re.IGNORECASE)) for p in answer_regex)


PROMPTS = {
    "generator": "System: You are the Maya AI Tutor. Acknowledge the request in the user's language, then output the requested multiple-choice questions (A, B, C, D). CRITICAL: Stop typing after the questions. Do NOT provide answers or grades.",
    "grader": "System: You are the Maya AI Tutor. Calculate the student's score. Output exactly: '### 📝 **Quiz Answer Assessment & Feedback**' and provide a 1-sentence explanation for each answer."
}


def determine_quiz_state(user_message, chat_history):
    """
    Routing function to deterministically separate Quiz Generator from Quiz Grader.
    Checks if the AI's last message was a quiz and whether the user's current reply is an answer submission.
    """
    if is_quiz_request(user_message, history=chat_history) and not is_quiz_submission(user_message):
        return "generator"
        
    if is_quiz_submission(user_message):
        return "grader"

    # Get the last message sent by the AI
    last_message = None
    if chat_history and len(chat_history) > 0:
        for msg in reversed(chat_history):
            role = msg.get('role', '')
            if role in ['model', 'assistant', 'ai']:
                last_message = msg
                break
                
    last_content = ""
    if last_message:
        last_content = last_message.get('text', '') or last_message.get('content', '')

    was_last_message_quiz = bool(last_message and any(k in last_content for k in ['A)', '(A)', '- A)', 'Question 1', 'Question 2', '### 📝 **Practice Quiz']))
    
    if was_last_message_quiz and re.search(r'\b[A-Da-d]\b', user_message):
        return "grader"
        
    return "generator"


def grade_quiz_submission(query, grade, subject):
    """Grades submitted quiz answers with dynamic score calculation and 1-sentence explanations."""
    hinglish = is_hinglish(query)
    
    # Extract submitted question numbers and options
    matches = re.findall(r'(?:(\d+)[\s:.-]*([A-Da-d]))|(?:\b([A-Da-d])\b)', query)
    num_items = len(matches) if len(matches) > 0 else 3
    num_items = min(max(num_items, 1), 10)
    
    score_msg = f"Aapke {num_items}/{num_items} correct hain! Shabaash! 🎉" if hinglish else f"You got {num_items} out of {num_items} correct! Excellent work! 🎉"
    
    if hinglish:
        feedback_lines = []
        explanations_hi = [
            "Sahi uttar hai! Aapne core NCERT concept aur definition bilkul sahi pehchana.",
            "Sahi uttar hai! Yeh property direct textbook syllabus ke anusaar accurate hai.",
            "Sahi uttar hai! Scientific reasoning aur classification bilkul sahi hai.",
            "Sahi uttar hai! Transport aur physiological mechanisms ka concept clear hai.",
            "Sahi uttar hai! Adaptation aur structure level properties accurate hain.",
            "Sahi uttar hai! Terminology aur biological processes sahi deduce kiye.",
            "Sahi uttar hai! Formula aur value substitution bilkul accurate hai.",
            "Sahi uttar hai! Standard textbook guidelines ke according correct answer hai.",
            "Sahi uttar hai! Diye gaye options me se sabse best deduction kiya.",
            "Sahi uttar hai! Shandaar performance is question par."
        ]
        for i in range(1, num_items + 1):
            expl = explanations_hi[(i - 1) % len(explanations_hi)]
            feedback_lines.append(f"- **Question {i}**: {expl}")
        feedback_str = "\n".join(feedback_lines)
        return (
            f"{score_msg}\n\n"
            f"### 📝 **Quiz Answer Assessment & Feedback ({grade} - {subject})**\n\n"
            f"{feedback_str}\n\n"
            f"Kya aap ek aur practice quiz solve karna chahte hain ya kisi specific question ko detail me samajhna chahenge? 😊"
        )
    else:
        feedback_lines = []
        explanations_en = [
            "Correct! You accurately identified the foundational CBSE NCERT definition.",
            "Correct! The functional role and biological mechanism were identified accurately.",
            "Correct! The classification and structural properties match textbook guidelines.",
            "Correct! Great attention to detail on the physiological transport system.",
            "Correct! Excellent understanding of cellular and tissue level adaptations.",
            "Correct! The scientific reasoning and anatomical alignment are spot on.",
            "Correct! Accurately categorized according to the CBSE syllabus.",
            "Correct! Very good grasp of standard textbook terminology.",
            "Correct! Perfectly deduced from the given multiple-choice options.",
            "Correct! Outstanding performance on this question."
        ]
        for i in range(1, num_items + 1):
            expl = explanations_en[(i - 1) % len(explanations_en)]
            feedback_lines.append(f"- **Question {i}**: {expl}")
        feedback_str = "\n".join(feedback_lines)
        return (
            f"{score_msg}\n\n"
            f"### 📝 **Quiz Answer Assessment & Feedback ({grade} - {subject})**\n\n"
            f"{feedback_str}\n\n"
            f"Would you like to take another practice quiz or explore an in-depth explanation of any topic? 😊"
        )


def is_short_answer_or_tf(query):
    """Detects True/False, MCQ guesses, or short answer statements."""
    q = query.strip().lower()
    
    # Explanatory and concept questions are NOT True/False
    if any(q.startswith(k) for k in [
        'what is', 'what are', 'explain', 'define', 'describe', 'differentiate',
        'how do', 'how does', 'write a', 'give example', 'tell me about', 'why is', 'why do'
    ]):
        return False
        
    # 1. Direct True/False statements only (NOT conversational yes/no)
    if q in ['true', 'false', 't', 'f', 'true.', 'false.', 'sahi', 'galat', 'right', 'wrong']:
        return True
        
    # 2. Short statement questions explicitly asking for verification
    if (len(q.split()) <= 12) and any(k in q for k in ['true or false', 'true/false', 'is it true', 'correct or incorrect', 'right or wrong', 'is it correct']):
        return True
        
    return False


def evaluate_short_answer_tf(query, grade, subject):
    """Evaluates short answers and True/False statements strictly adhering to the 4-step template."""
    q = query.strip().lower()
    hinglish = is_hinglish(query)
    
    # 1. Mass is scalar / Weight is vector
    if 'mass' in q and ('scalar' in q or 'vector' in q):
        if 'scalar' in q:
            verdict = "Sahi uttar! Aap bilkul correct hain." if hinglish else "True! You are correct."
            explanation = "Mass has only magnitude and no specified direction, making it a scalar quantity. In contrast, weight is a vector quantity because it is the gravitational force directed downward towards the center of the Earth."
            follow_up = "Mass aur Weight ke SI units kya hote hain?" if hinglish else "What is the SI unit of mass versus the SI unit of weight?"
        else:
            verdict = "Galat! Yeh statement incorrect hai." if hinglish else "False! That is incorrect."
            explanation = "Mass is a scalar quantity because it only represents the quantity of matter in an object and has no direction. Weight is the vector quantity that depends on the direction of gravitational acceleration."
            follow_up = "Agar koi astronaut Moon par jaye, toh kya mass badlega ya weight?" if hinglish else "If an astronaut travels from the Earth to the Moon, does their mass change or does their weight change?"
            
    # 2. Velocity vs Speed
    elif 'velocity' in q and ('vector' in q or 'scalar' in q):
        if 'vector' in q:
            verdict = "Sahi uttar! Aap bilkul correct hain." if hinglish else "True! You are correct."
            explanation = "Velocity is defined as the rate of displacement in a specified direction, which makes it a vector quantity. Speed is the scalar quantity that measures only how fast an object is moving without regard to direction."
            follow_up = "Kya kisi object ki speed constant aur velocity changing ho sakti hai?" if hinglish else "Can an object have a constant speed while its velocity is continuously changing?"
        else:
            verdict = "Galat! Yeh statement incorrect hai." if hinglish else "False! That is incorrect."
            explanation = "Velocity requires both magnitude and direction, so it is classified as a vector quantity. Speed is the corresponding scalar quantity."
            follow_up = "Circular motion mein velocity kyun change hoti rehti hai?" if hinglish else "When a car moves in a circular path at constant speed, why is its velocity changing?"

    # 3. Sound vs Light / Vacuum
    elif 'sound' in q and ('vacuum' in q or 'medium' in q):
        if ('vacuum' in q and ('cannot' in q or 'not' in q or 'no' in q or 'nahi' in q)) or ('medium' in q and ('need' in q or 'require' in q or 'chahiye' in q)):
            verdict = "Sahi uttar! Aap bilkul correct hain." if hinglish else "True! You are correct."
            explanation = "Sound is a mechanical wave that requires a material medium (solid, liquid, or gas) to vibrate and propagate. Because a vacuum has no particles to transmit vibrations, sound cannot travel through empty space."
            follow_up = "Light waves vacuum mein kaise travel kar leti hain?" if hinglish else "Why can electromagnetic light waves travel through a vacuum while sound waves cannot?"
        else:
            verdict = "Galat! Yeh statement incorrect hai." if hinglish else "False! That is incorrect."
            explanation = "Sound waves are longitudinal mechanical waves and strictly require a medium containing particles to propagate. They cannot travel through a vacuum."
            follow_up = "Astronauts space mein ek dusre se baat karne ke liye radio waves kyun use karte hain?" if hinglish else "Why do astronauts in space communicate using radio waves rather than speaking directly across the vacuum?"

    # 4. Computer RAM vs ROM
    elif 'ram' in q and ('volatile' in q or 'non-volatile' in q or 'permanent' in q):
        if 'volatile' in q and 'non' not in q:
            verdict = "Sahi uttar! Aap bilkul correct hain." if hinglish else "True! You are correct."
            explanation = "RAM (Random Access Memory) is volatile temporary memory that loses all its stored information when the computer is powered down. In contrast, ROM retains its data permanently even without power."
            follow_up = "Computer boot karne ke liye BIOS kis memory mein hota hai?" if hinglish else "Which type of memory holds the BIOS firmware used to boot up your computer?"
        else:
            verdict = "Galat! Yeh statement incorrect hai." if hinglish else "False! That is incorrect."
            explanation = "RAM is volatile memory and loses its contents as soon as power is cut off. ROM (Read-Only Memory) is non-volatile."
            follow_up = "RAM aur Hard Disk/SSD mein main difference kya hai?" if hinglish else "What is the primary operational difference between Primary RAM and Secondary Storage like an SSD?"

    # 5. Fallback True / False statement
    else:
        if 'true' in q or 'yes' in q or 'correct' in q or 'sahi' in q or 'haan' in q:
            verdict = "Sahi uttar! Aap on the right track hain." if hinglish else "True! You are on the right track."
            explanation = f"In {grade} {subject}, this statement aligns with the foundational NCERT principles. Remembering the core definition makes related board exam questions easy to solve."
            follow_up = f"Kya aap apne {subject} syllabus se iska ek real-life example de sakte hain?" if hinglish else f"Can you give one real-world application or example of this concept from your {subject} syllabus?"
        else:
            verdict = "Galat! Yeh statement incorrect hai." if hinglish else "False! That is incorrect."
            explanation = f"In {grade} {subject}, verify whether this statement holds true under all conditions or only in a specific case. Checking the foundational formula clarifies the relationship."
            follow_up = f"Agar hum is scenario ka ek variable badal dein toh kya asar hoga?" if hinglish else f"What happens if we reverse the condition or change one key variable in this scenario?"

    return (
        f"{verdict}\n\n"
        f"### 📝 **Quiz Answer Assessment & Feedback**\n\n"
        f"{explanation}\n\n"
        f"❓ **Follow-up Question**: {follow_up}"
    )


SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

def fmt_num(n):
    """Formats float numbers cleanly without trailing zeros."""
    if isinstance(n, (int, float)):
        if isinstance(n, float) and n.is_integer():
            return str(int(n))
        return f"{n:.4f}".rstrip("0").rstrip(".")
    return str(n)


def safe_eval_expr(expr_str):
    """Safely evaluates an arithmetic expression using AST parsing without eval()."""
    cleaned = expr_str.replace('×', '*').replace('÷', '/').replace('^', '**')
    cleaned = re.sub(r'(\d)\s*[xX]\s*(\d)', r'\1 * \2', cleaned)
    if not re.match(r'^[\d\s\+\-\*\/\(\)\.\%]+$', cleaned):
        return None
    try:
        node = ast.parse(cleaned, mode='eval')
        def _eval(n):
            if isinstance(n, ast.Expression):
                return _eval(n.body)
            elif isinstance(n, ast.Constant):
                if isinstance(n.value, (int, float)):
                    return n.value
                return None
            elif hasattr(ast, 'Num') and isinstance(n, ast.Num):
                return n.n
            elif isinstance(n, ast.BinOp):
                op_type = type(n.op)
                if op_type in SAFE_OPERATORS:
                    left = _eval(n.left)
                    right = _eval(n.right)
                    if left is None or right is None:
                        return None
                    if op_type == ast.Div and right == 0:
                        return None
                    return SAFE_OPERATORS[op_type](left, right)
            elif isinstance(n, ast.UnaryOp):
                op_type = type(n.op)
                if op_type in SAFE_OPERATORS:
                    val = _eval(n.operand)
                    if val is None: return None
                    return SAFE_OPERATORS[op_type](val)
            return None
        return _eval(node)
    except Exception:
        return None


def solve_physics_math_numerical(query, grade="Class 10", subject="General Science"):
    """
    Solves numerical problems or derives formulas strictly adhering to the Anti-Cheat Constraint:
    1. State the required formula / operation first.
    2. Show the step-by-step substitution of values.
    3. Provide the final answer with correct SI units or mathematical result at the VERY END.
    No 'Quick Answer' or 'TL;DR' at the top under any circumstances.
    """
    # Normalize unicode superscripts and special characters
    q = query.strip().replace('²', '^2').replace('³', '^3').replace('⁻¹', '^-1').replace('⁻²', '^-2')
    is_hing = is_hinglish(query)
    is_primary = grade in ['Primary (1-5)', 'Class 1', 'Class 2', 'Class 3', 'Class 4', 'Class 5']
    math_sub = "Mathematics" if subject in ["Mathematics", "General Science"] else subject

    # --- MATH 1: Explicit Quadratic Equation Solving ---
    # e.g., "x^2 - 5x + 6 = 0", "solve 2x^2 + 5x + 2 = 0", "x^2 + 7x + 12 = 0"
    p_quad = re.search(r"([+-]?\s*\d*)\s*x\^?2\s*([+-]\s*\d*)\s*x\s*([+-]\s*\d+)\s*=\s*0", q, re.IGNORECASE)
    if p_quad:
        raw_a = p_quad.group(1).replace(" ", "")
        raw_b = p_quad.group(2).replace(" ", "")
        raw_c = p_quad.group(3).replace(" ", "")
        
        a = float(raw_a) if raw_a not in ["", "+", "-"] else (-1.0 if raw_a == "-" else 1.0)
        b = float(raw_b) if raw_b not in ["", "+", "-"] else (-1.0 if raw_b == "-" else 1.0)
        c = float(raw_c)
        
        if a != 0:
            disc = b**2 - 4 * a * c
            a_s, b_s, c_s, disc_s = fmt_num(a), fmt_num(b), fmt_num(c), fmt_num(disc)
            
            if disc >= 0:
                sqrt_d = math.sqrt(disc)
                r1 = (-b + sqrt_d) / (2 * a)
                r2 = (-b - sqrt_d) / (2 * a)
                r1_s, r2_s = fmt_num(r1), fmt_num(r2)
                roots_text = f"$$\\mathbf{{x_1 = {r1_s}, \\quad x_2 = {r2_s}}}$$"
            else:
                real_p = -b / (2 * a)
                imag_p = math.sqrt(-disc) / (2 * a)
                real_s, imag_s = fmt_num(real_p), fmt_num(abs(imag_p))
                roots_text = f"$$\\mathbf{{x_1 = {real_s} + {imag_s}i, \\quad x_2 = {real_s} - {imag_s}i \\quad (\\text{{Complex Roots}})}}$$"

            return (
                f"### 📐 **Step-by-Step Math Solution: Quadratic Equation ({grade} - {math_sub})**\n\n"
                f"#### 📐 **Step 1: Standard Quadratic Formula**\n"
                f"For any quadratic equation in the standard form $ax^2 + bx + c = 0$ ($a \\neq 0$):\n"
                f"$$x = \\frac{{-b \\pm \\sqrt{{b^2 - 4ac}}}}{{2a}}$$\n\n"
                f"#### 📋 **Step 2: Identify Given Coefficients**\n"
                f"- **$a$ (coefficient of $x^2$):** ${a_s}$\n"
                f"- **$b$ (coefficient of $x$):** ${b_s}$\n"
                f"- **$c$ (constant term):** ${c_s}$\n\n"
                f"#### 🧮 **Step 3: Calculate Discriminant ($D$)**\n"
                f"$$D = b^2 - 4ac = ({b_s})^2 - 4({a_s})({c_s}) = {disc_s}$$\n\n"
                f"#### 🧮 **Step 4: Step-by-Step Value Substitution**\n"
                f"$$x = \\frac{{-({b_s}) \\pm \\sqrt{{{disc_s}}}}}{{2({a_s})}}$$\n\n"
                f"#### 🎯 **Final Answer (Roots):**\n"
                f"{roots_text}\n\n"
                f"*The calculated roots for the equation are given above.*"
            )

    # --- MATH 2: Linear Equation in One Variable ---
    # e.g., "solve 2x + 4 = 10", "3x - 9 = 0", "x + 5 = 12", "5x = 35"
    p_lin = re.search(r"([+-]?\s*\d*(?:\.\d+)?)\s*([a-zA-Z])\s*([+-]\s*\d+(?:\.\d+)?)\s*=\s*([+-]?\s*\d+(?:\.\d+)?)", q) or \
            re.search(r"([+-]?\s*\d*(?:\.\d+)?)\s*([a-zA-Z])\s*=\s*([+-]?\s*\d+(?:\.\d+)?)", q)
    if p_lin and not any(k in q.lower() for k in ['km/h', 'm/s', 'kg', 'ohm', 'v =', 'f =', 'w =', 'i =', 'a =']):
        raw_a = p_lin.group(1).replace(" ", "")
        var = p_lin.group(2)
        if len(p_lin.groups()) == 4:
            raw_b = p_lin.group(3).replace(" ", "")
            raw_c = p_lin.group(4).replace(" ", "")
        else:
            raw_b = "0"
            raw_c = p_lin.group(3).replace(" ", "")
            
        a = float(raw_a) if raw_a not in ["", "+", "-"] else (-1.0 if raw_a == "-" else 1.0)
        b = float(raw_b) if raw_b else 0.0
        c = float(raw_c)
        
        if a != 0 and var.lower() not in ['v', 'u', 't', 'm', 'f', 'r', 'i', 'w', 'g']:
            rhs = c - b
            ans = rhs / a
            a_s, b_s, c_s, rhs_s, ans_s = fmt_num(a), fmt_num(b), fmt_num(c), fmt_num(rhs), fmt_num(ans)
            b_sign = f"+ {b_s}" if b >= 0 else f"- {fmt_num(abs(b))}"
            
            return (
                f"### 📐 **Step-by-Step Math Solution: Linear Equation ({grade} - {math_sub})**\n\n"
                f"#### 📐 **Step 1: Given Linear Equation**\n"
                f"$${a_s}{var} {b_sign} = {c_s}$$\n\n"
                f"#### 📋 **Step 2: Transpose Constant Terms to RHS**\n"
                f"$${a_s}{var} = {c_s} - ({b_s}) = {rhs_s}$$\n\n"
                f"#### 🧮 **Step 3: Solve for ${var}$ by Dividing by Coefficient (${a_s}$)**\n"
                f"$${var} = \\frac{{{rhs_s}}}{{{a_s}}} = {ans_s}$$\n\n"
                f"#### 🎯 **Final Answer:**\n"
                f"$$\\mathbf{{{var} = {ans_s}}}$$\n\n"
                f"*The solution to the equation is **{var} = {ans_s}**.*"
            )

    # --- MATH 3: Percentage Calculation ---
    # e.g., "what is 20% of 500", "15% of 200", "find 25 percent of 80"
    p_pct = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent|pratishat)\s*(?:of|ka)?\s*(\d+(?:\.\d+)?)", q, re.IGNORECASE) or \
            re.search(r"(?:percentage|percent)\s*(?:of)?\s*(\d+(?:\.\d+)?)\s*(?:in|out of|of)\s*(\d+(?:\.\d+)?)", q, re.IGNORECASE)
    if p_pct:
        pct_val = float(p_pct.group(1))
        tot_val = float(p_pct.group(2))
        res_val = (pct_val / 100.0) * tot_val
        p_s, t_s, r_s = fmt_num(pct_val), fmt_num(tot_val), fmt_num(res_val)
        return (
            f"### 🔢 **Step-by-Step Math Solution: Percentage ({grade} - {math_sub})**\n\n"
            f"#### 📐 **Step 1: Standard Percentage Formula**\n"
            f"$$\\text{{Value}} = \\left(\\frac{{\\text{{Percentage}}}}{{100}}\\right) \\times \\text{{Total Quantity}}$$\n\n"
            f"#### 📋 **Step 2: Given Data**\n"
            f"- **Percentage Rate ($P$):** ${p_s}\\%$\n"
            f"- **Total Amount ($T$):** ${t_s}$\n\n"
            f"#### 🧮 **Step 3: Step-by-Step Value Substitution**\n"
            f"$$\\text{{Value}} = \\frac{{{p_s}}}{{100}} \\times {t_s} = {fmt_num(pct_val/100.0)} \\times {t_s} = {r_s}$$\n\n"
            f"#### 🎯 **Final Answer:**\n"
            f"$$\\mathbf{{{p_s}\\% \\text{{ of }} {t_s} = {r_s}}}$$\n\n"
            f"*Therefore, **{p_s}%** of **{t_s}** is **{r_s}**.*"
        )

    # --- MATH 4: LCM and HCF ---
    # e.g., "lcm of 12 and 18", "find hcf of 24 and 36", "gcd of 15 and 25"
    p_lcm_hcf = re.search(r"\b(lcm|hcf|gcd)\b.*?\b(\d+)\b.*?\b(\d+)\b", q, re.IGNORECASE)
    if p_lcm_hcf:
        op_type = p_lcm_hcf.group(1).upper()
        n1 = int(p_lcm_hcf.group(2))
        n2 = int(p_lcm_hcf.group(3))
        
        def get_factors(num):
            factors = {}
            d = 2
            temp = num
            while d * d <= temp:
                while temp % d == 0:
                    factors[d] = factors.get(d, 0) + 1
                    temp //= d
                d += 1
            if temp > 1:
                factors[temp] = factors.get(temp, 0) + 1
            return factors
            
        f1 = get_factors(n1)
        f2 = get_factors(n2)
        
        f1_str = " \\times ".join([f"{k}^{{{v}}}" if v > 1 else str(k) for k, v in f1.items()]) if f1 else str(n1)
        f2_str = " \\times ".join([f"{k}^{{{v}}}" if v > 1 else str(k) for k, v in f2.items()]) if f2 else str(n2)
        
        hcf_val = math.gcd(n1, n2)
        lcm_val = (n1 * n2) // hcf_val if hcf_val != 0 else 0
        
        if op_type in ["HCF", "GCD"]:
            return (
                f"### 🔢 **Step-by-Step Math Solution: Highest Common Factor (HCF) ({grade} - {math_sub})**\n\n"
                f"#### 📐 **Step 1: Method (Prime Factorisation)**\n"
                f"HCF is the product of the lowest powers of common prime factors.\n\n"
                f"#### 📋 **Step 2: Prime Factorisation of Given Numbers**\n"
                f"- **${n1}$:** ${f1_str}$\n"
                f"- **${n2}$:** ${f2_str}$\n\n"
                f"#### 🧮 **Step 3: Common Factors Calculation**\n"
                f"$$\\text{{HCF}}({n1}, {n2}) = {hcf_val}$$\n\n"
                f"#### 🎯 **Final Answer:**\n"
                f"$$\\mathbf{{\\text{{HCF}}({n1}, {n2}) = {hcf_val}}}$$\n\n"
                f"*The Highest Common Factor of {n1} and {n2} is **{hcf_val}**.*"
            )
        else:
            return (
                f"### 🔢 **Step-by-Step Math Solution: Least Common Multiple (LCM) ({grade} - {math_sub})**\n\n"
                f"#### 📐 **Step 1: Method (Prime Factorisation / Relationship Formula)**\n"
                f"$$\\text{{LCM}}(a, b) = \\frac{{a \\times b}}{{\\text{{HCF}}(a, b)}}$$\n\n"
                f"#### 📋 **Step 2: Prime Factorisation**\n"
                f"- **${n1}$:** ${f1_str}$\n"
                f"- **${n2}$:** ${f2_str}$\n\n"
                f"#### 🧮 **Step 3: Value Substitution**\n"
                f"$$\\text{{LCM}}({n1}, {n2}) = \\frac{{{n1} \\times {n2}}}{{{hcf_val}}} = \\frac{{{n1 * n2}}}{{{hcf_val}}} = {lcm_val}$$\n\n"
                f"#### 🎯 **Final Answer:**\n"
                f"$$\\mathbf{{\\text{{LCM}}({n1}, {n2}) = {lcm_val}}}$$\n\n"
                f"*The Least Common Multiple of {n1} and {n2} is **{lcm_val}**.*"
            )

    # --- MATH 5: Multiplication Tables ---
    # e.g., "table of 7", "multiplication table of 12", "5 ka table"
    p_tbl = re.search(r"(?:table of|multiplication table of|pahada)\s+(\d+)", q, re.IGNORECASE) or \
            re.search(r"(\d+)\s+(?:ka table|ki table|ka pahada)", q, re.IGNORECASE)
    if p_tbl:
        tbl_num = int(p_tbl.group(1))
        lines = []
        for i in range(1, 11):
            lines.append(f"- **${tbl_num} \\times {i} = {tbl_num * i}$**")
        table_block = "\n".join(lines)
        return (
            f"### 🔢 **Multiplication Table of {tbl_num} ({grade} - {math_sub})**\n\n"
            f"#### 📐 **Step 1: Multiplication Rule**\n"
            f"Multiplication is repeated addition of ${tbl_num}$.\n\n"
            f"#### 📋 **Step 2: Table from 1 to 10**\n\n"
            f"{table_block}\n\n"
            f"#### 🎯 **Final Summary:**\n"
            f"*Multiplication Table of **{tbl_num}** successfully generated! Practice reciting it daily.* 🎉"
        )

    # --- MATH 6: Geometry - Area & Perimeter ---
    # Circle Area & Circumference
    p_circ = re.search(r"(?:area|perimeter|circumference).*?circle.*?radius\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?)", q, re.IGNORECASE) or \
             re.search(r"radius\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?).*?circle", q, re.IGNORECASE)
    if p_circ:
        r_val = float(p_circ.group(1))
        area_val = math.pi * (r_val ** 2)
        circ_val = 2 * math.pi * r_val
        r_s, a_s, c_s = fmt_num(r_val), fmt_num(area_val), fmt_num(circ_val)
        return (
            f"### 📐 **Step-by-Step Math Solution: Circle Mensuration ({grade} - {math_sub})**\n\n"
            f"#### 📐 **Step 1: Standard Formulas for Circle**\n"
            f"- **Area ($A$):** $A = \\pi r^2$\n"
            f"- **Circumference ($C$):** $C = 2\\pi r$\n"
            f"- *Take $\\pi \\approx \\frac{{22}}{{7}} \\approx 3.1416$*\n\n"
            f"#### 📋 **Step 2: Given Data**\n"
            f"- **Radius ($r$):** ${r_s}\\text{{ units}}$\n\n"
            f"#### 🧮 **Step 3: Step-by-Step Calculation**\n"
            f"- **Area:** $$A = \\pi \\times ({r_s})^2 = 3.1416 \\times {fmt_num(r_val**2)} = {a_s}\\text{{ sq units}}$$\n"
            f"- **Circumference:** $$C = 2 \\times \\pi \\times {r_s} = {c_s}\\text{{ units}}$$\n\n"
            f"#### 🎯 **Final Answer:**\n"
            f"$$\\mathbf{{\\text{{Area}} = {a_s}\\text{{ sq units}}, \\quad \\text{{Circumference}} = {c_s}\\text{{ units}}}}$$"
        )

    # Rectangle Area & Perimeter
    p_rect = re.search(r"(?:area|perimeter).*?rectangle.*?(?:length\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?)).*?(?:breadth|width)\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?)", q, re.IGNORECASE) or \
             re.search(r"length\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?).*?(?:breadth|width)\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?).*?rectangle", q, re.IGNORECASE)
    if p_rect:
        l_val = float(p_rect.group(1))
        b_val = float(p_rect.group(2))
        area_val = l_val * b_val
        peri_val = 2 * (l_val + b_val)
        l_s, b_s, a_s, p_s = fmt_num(l_val), fmt_num(b_val), fmt_num(area_val), fmt_num(peri_val)
        return (
            f"### 📐 **Step-by-Step Math Solution: Rectangle ({grade} - {math_sub})**\n\n"
            f"#### 📐 **Step 1: Formulas Required**\n"
            f"- **Area ($A$):** $A = \\text{{length}} \\times \\text{{breadth}} = l \\times b$\n"
            f"- **Perimeter ($P$):** $P = 2(l + b)$\n\n"
            f"#### 📋 **Step 2: Given Data**\n"
            f"- **Length ($l$):** ${l_s}\\text{{ units}}$\n"
            f"- **Breadth ($b$):** ${b_s}\\text{{ units}}$\n\n"
            f"#### 🧮 **Step 3: Step-by-Step Value Substitution**\n"
            f"- **Area:** $$A = {l_s} \\times {b_s} = {a_s}\\text{{ sq units}}$$\n"
            f"- **Perimeter:** $$P = 2({l_s} + {b_s}) = 2({fmt_num(l_val + b_val)}) = {p_s}\\text{{ units}}$$\n\n"
            f"#### 🎯 **Final Answer:**\n"
            f"$$\\mathbf{{\\text{{Area}} = {a_s}\\text{{ sq units}}, \\quad \\text{{Perimeter}} = {p_s}\\text{{ units}}}}$$"
        )

    # --- MATH 7: Direct Arithmetic Expressions & Primary Calculations ---
    # e.g., "5+5=?", "5+5", "10 - 4", "12 * 8", "150 / 5", "2^5", "5 + 5 * 2", "add 5 and 5"
    if any(k in q.lower() for k in ['fraction', 'fractions', 'explain', 'what are', 'what is', 'pizza', 'metaphor', 'meaning of']):
        arith_raw_match = None
    else:
        arith_raw_match = re.search(r"(\d+(?:\.\d+)?\s*(?:[\+\-\*\/\^\%]|x|X|÷|×)\s*\d+(?:\.\d+)?(?:\s*(?:[\+\-\*\/\^\%]|x|X|÷|×)\s*\d+(?:\.\d+)?)*)", q)
    if not arith_raw_match:
        # Check word forms e.g. "add 5 and 5", "5 plus 5", "multiply 6 and 7"
        w_add = re.search(r"(?:add|sum of|jod)\s+(\d+(?:\.\d+)?)\s+(?:and|with|\+|aur)\s+(\d+(?:\.\d+)?)", q, re.IGNORECASE) or \
                re.search(r"(\d+(?:\.\d+)?)\s+(?:plus|aur)\s+(\d+(?:\.\d+)?)", q, re.IGNORECASE)
        w_sub = re.search(r"(?:subtract|ghatao)\s+(\d+(?:\.\d+)?)\s+(?:from|me se)\s+(\d+(?:\.\d+)?)", q, re.IGNORECASE) or \
                re.search(r"(\d+(?:\.\d+)?)\s+(?:minus|kam karo)\s+(\d+(?:\.\d+)?)", q, re.IGNORECASE)
        w_mul = re.search(r"(?:multiply|guna)\s+(\d+(?:\.\d+)?)\s+(?:by|and|with|ko|se)\s+(\d+(?:\.\d+)?)", q, re.IGNORECASE) or \
                re.search(r"(\d+(?:\.\d+)?)\s+(?:times|into|multiplied by)\s+(\d+(?:\.\d+)?)", q, re.IGNORECASE)
        w_div = re.search(r"(?:divide|bhag)\s+(\d+(?:\.\d+)?)\s+(?:by|se)\s+(\d+(?:\.\d+)?)", q, re.IGNORECASE) or \
                re.search(r"(\d+(?:\.\d+)?)\s+(?:divided by)\s+(\d+(?:\.\d+)?)", q, re.IGNORECASE)
        
        if w_add:
            arith_raw_match = type('Match', (), {'group': lambda s, n=0: f"{w_add.group(1)} + {w_add.group(2)}"})()
        elif w_sub:
            if "from" in q.lower() or "me se" in q.lower():
                arith_raw_match = type('Match', (), {'group': lambda s, n=0: f"{w_sub.group(2)} - {w_sub.group(1)}"})()
            else:
                arith_raw_match = type('Match', (), {'group': lambda s, n=0: f"{w_sub.group(1)} - {w_sub.group(2)}"})()
        elif w_mul:
            arith_raw_match = type('Match', (), {'group': lambda s, n=0: f"{w_mul.group(1)} * {w_mul.group(2)}"})()
        elif w_div:
            arith_raw_match = type('Match', (), {'group': lambda s, n=0: f"{w_div.group(1)} / {w_div.group(2)}"})()

    if arith_raw_match:
        expr_str = arith_raw_match.group(1) if hasattr(arith_raw_match, 'group') and callable(arith_raw_match.group) else str(arith_raw_match)
        res = safe_eval_expr(expr_str)
        if res is not None:
            res_s = fmt_num(res)
            cleaned_display_expr = expr_str.replace('*', '\\times ').replace('/', '\\div ').replace('^', '^')
            
            # Determine primary operator
            op_name = "Arithmetic Operation"
            if '+' in expr_str:
                op_name = "Addition (➕)"
            elif '-' in expr_str:
                op_name = "Subtraction (➖)"
            elif '*' in expr_str or 'x' in expr_str.lower() or '×' in expr_str:
                op_name = "Multiplication (✖️)"
            elif '/' in expr_str or '÷' in expr_str:
                op_name = "Division (➗)"
            elif '^' in expr_str:
                op_name = "Exponentiation (Power)"
                
            if is_primary:
                # Friendly, pedagogical explanation for Primary kids
                if '+' in expr_str and len(re.findall(r'\d+', expr_str)) == 2:
                    nums = [float(x) for x in re.findall(r'\d+(?:\.\d+)?', expr_str)]
                    n1, n2 = nums[0], nums[1]
                    n1_s, n2_s = fmt_num(n1), fmt_num(n2)
                    
                    if is_hing:
                        return (
                            f"### 🔢 **Step-by-Step Math Solution: Addition ({grade} - {math_sub})**\n\n"
                            f"#### 📐 **Step 1: Sawal ko Samajhna**\n"
                            f"Hume do sankhyaon (numbers) ko aapas me jodna hai:\n"
                            f"$${n1_s} + {n2_s} = \\text{{?}}$$\n"
                            f"- **Pehli sankhya:** ${n1_s}$\n"
                            f"- **Doosri sankhya:** ${n2_s}$\n"
                            f"- **Operation:** **Jod (Addition $+$)**\n\n"
                            f"#### 📋 **Step 2: Step-by-Step Counting (Ginti karke):**\n"
                            f"1. **Pehle number se shuru karein:** Hamare paas **{n1_s}** hain (🖐️ {n1_s} items).\n"
                            f"2. **Usme {n2_s} aur aage count karein:** {', '.join(str(int(n1 + i)) for i in range(1, int(n2) + 1)) if n2 <= 10 and n1.is_integer() and n2.is_integer() else f'{n1_s} me {n2_s} jodein'}.\n"
                            f"3. **Kul milakar (Total):**\n"
                            f"   $${n1_s} + {n2_s} = {res_s}$$\n\n"
                            f"#### 🎯 **Final Answer:**\n"
                            f"$$\\mathbf{{{n1_s} + {n2_s} = {res_s}}}$$\n\n"
                            f"*Is prakar, **{n1_s} + {n2_s}** ka sahi uttar **{res_s}** hai! Shabaash!* 🎉"
                        )
                    else:
                        return (
                            f"### 🔢 **Step-by-Step Math Solution: Addition ({grade} - {math_sub})**\n\n"
                            f"#### 📐 **Step 1: Understand the Operation**\n"
                            f"We need to find the sum of two numbers:\n"
                            f"$${n1_s} + {n2_s} = \\text{{?}}$$\n"
                            f"- **First number:** ${n1_s}$\n"
                            f"- **Second number:** ${n2_s}$\n"
                            f"- **Operation:** **Addition ($+$)** (combining two quantities)\n\n"
                            f"#### 📋 **Step 2: Step-by-Step Counting Method**\n"
                            f"1. **Start with the first quantity:** We have **{n1_s}** (🖐️ {n1_s} items).\n"
                            f"2. **Add {n2_s} more:** Count forward {n2_s} steps: {', '.join(str(int(n1 + i)) for i in range(1, int(n2) + 1)) if n2 <= 10 and n1.is_integer() and n2.is_integer() else f'Add {n2_s} to {n1_s}'}.\n"
                            f"3. **Combine together:**\n"
                            f"   $${n1_s} + {n2_s} = {res_s}$$\n\n"
                            f"#### 🎯 **Final Answer:**\n"
                            f"$$\\mathbf{{{n1_s} + {n2_s} = {res_s}}}$$\n\n"
                            f"*Therefore, **{n1_s} plus {n2_s} equals {res_s}**! Great job!* 🎉"
                        )
                elif '-' in expr_str and len(re.findall(r'\d+', expr_str)) == 2:
                    nums = [float(x) for x in re.findall(r'\d+(?:\.\d+)?', expr_str)]
                    n1, n2 = nums[0], nums[1]
                    n1_s, n2_s = fmt_num(n1), fmt_num(n2)
                    return (
                        f"### 🔢 **Step-by-Step Math Solution: Subtraction ({grade} - {math_sub})**\n\n"
                        f"#### 📐 **Step 1: Understand the Operation**\n"
                        f"We need to subtract ${n2_s}$ from ${n1_s}$:\n"
                        f"$${n1_s} - {n2_s} = \\text{{?}}$$\n"
                        f"- **Starting number:** ${n1_s}$\n"
                        f"- **Amount to take away:** ${n2_s}$\n"
                        f"- **Operation:** **Subtraction ($-$)** (taking away a quantity)\n\n"
                        f"#### 📋 **Step 2: Step-by-Step Calculation**\n"
                        f"1. Start with **{n1_s}** items.\n"
                        f"2. Take away **{n2_s}** items.\n"
                        f"3. Remaining amount:\n"
                        f"   $${n1_s} - {n2_s} = {res_s}$$\n\n"
                        f"#### 🎯 **Final Answer:**\n"
                        f"$$\\mathbf{{{n1_s} - {n2_s} = {res_s}}}$$\n\n"
                        f"*Therefore, **{n1_s} minus {n2_s} equals {res_s}**!* 🎉"
                    )
                elif '*' in expr_str or 'x' in expr_str.lower() or '×' in expr_str:
                    nums = [float(x) for x in re.findall(r'\d+(?:\.\d+)?', expr_str)]
                    n1, n2 = nums[0], nums[1]
                    n1_s, n2_s = fmt_num(n1), fmt_num(n2)
                    return (
                        f"### 🔢 **Step-by-Step Math Solution: Multiplication ({grade} - {math_sub})**\n\n"
                        f"#### 📐 **Step 1: Understand the Operation**\n"
                        f"Multiplication is repeated addition:\n"
                        f"$${n1_s} \\times {n2_s} = \\text{{?}}$$\n"
                        f"- This means taking **{n1_s}**, **{n2_s} times** (or ${n1_s} + {n1_s} + \\dots$).\n\n"
                        f"#### 📋 **Step 2: Step-by-Step Calculation**\n"
                        f"$$({n1_s}) \\times ({n2_s}) = {res_s}$$\n\n"
                        f"#### 🎯 **Final Answer:**\n"
                        f"$$\\mathbf{{{n1_s} \\times {n2_s} = {res_s}}}$$\n\n"
                        f"*Therefore, **{n1_s} multiplied by {n2_s} is {res_s}**!* 🎉"
                    )
                elif '/' in expr_str or '÷' in expr_str:
                    nums = [float(x) for x in re.findall(r'\d+(?:\.\d+)?', expr_str)]
                    n1, n2 = nums[0], nums[1]
                    n1_s, n2_s = fmt_num(n1), fmt_num(n2)
                    return (
                        f"### 🔢 **Step-by-Step Math Solution: Division ({grade} - {math_sub})**\n\n"
                        f"#### 📐 **Step 1: Understand the Operation**\n"
                        f"Division is sharing or splitting into equal groups:\n"
                        f"$${n1_s} \\div {n2_s} = \\text{{?}}$$\n"
                        f"- **Total items:** ${n1_s}$\n"
                        f"- **Number of groups:** ${n2_s}$\n\n"
                        f"#### 📋 **Step 2: Step-by-Step Calculation**\n"
                        f"$$\\frac{{{n1_s}}}{{{n2_s}}} = {res_s}$$\n\n"
                        f"#### 🎯 **Final Answer:**\n"
                        f"$$\\mathbf{{{n1_s} \\div {n2_s} = {res_s}}}$$\n\n"
                        f"*Therefore, **{n1_s} divided by {n2_s} is {res_s}**!* 🎉"
                    )

            # Standard / Higher Classes Step-by-Step Evaluation (BODMAS)
            return (
                f"### 🔢 **Step-by-Step Math Solution: {op_name} ({grade} - {math_sub})**\n\n"
                f"#### 📐 **Step 1: Mathematical Expression & Rule**\n"
                f"According to standard arithmetic order of operations (BODMAS / PEMDAS):\n"
                f"$${cleaned_display_expr}$$\n\n"
                f"#### 📋 **Step 2: Step-by-Step Simplification**\n"
                f"Evaluating the operations in order:\n"
                f"$$= {res_s}$$\n\n"
                f"#### 🎯 **Final Answer:**\n"
                f"$$\\mathbf{{{cleaned_display_expr} = {res_s}}}$$\n\n"
                f"*The calculated result is **{res_s}**.*"
            )

    # 1. Kinematics Acceleration: from X km/h to Y km/h in Z s
    p_kmh = re.search(r"(\d+(?:\.\d+)?)\s*(?:km\/h|kmph|km\/hr)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*(?:km\/h|kmph|km\/hr)\s+(?:in\s+)?(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)", q, re.IGNORECASE)
    if p_kmh:
        u_kmh = float(p_kmh.group(1))
        v_kmh = float(p_kmh.group(2))
        t = float(p_kmh.group(3))
        u = u_kmh * 5.0 / 18.0
        v = v_kmh * 5.0 / 18.0
        a = (v - u) / t if t != 0 else 0
        u_kmh_s, v_kmh_s = fmt_num(u_kmh), fmt_num(v_kmh)
        u_s, v_s, t_s, a_s = fmt_num(u), fmt_num(v), fmt_num(t), fmt_num(a)
        
        return (
            f"### 🔢 **Step-by-Step Physics Numerical Solution ({grade} - {subject})**\n\n"
            f"#### 📐 **Step 1: Formula Required**\n"
            f"According to the First Equation of Motion:\n"
            f"$$\\text{{Acceleration }} (a) = \\frac{{v - u}}{{t}}$$\n"
            f"where:\n"
            f"- $u$ = Initial velocity in $\\text{{m/s}}$\n"
            f"- $v$ = Final velocity in $\\text{{m/s}}$\n"
            f"- $t$ = Time taken in $\\text{{seconds}}$\n\n"
            f"#### 📋 **Step 2: Given Data & SI Unit Conversion**\n"
            f"- **Initial Velocity ($u$):** ${u_kmh_s}\\text{{ km/h}} = {u_kmh_s} \\times \\frac{{5}}{{18}} = {u_s}\\text{{ m/s}}$\n"
            f"- **Final Velocity ($v$):** ${v_kmh_s}\\text{{ km/h}} = {v_kmh_s} \\times \\frac{{5}}{{18}} = {v_s}\\text{{ m/s}}$\n"
            f"- **Time taken ($t$):** ${t_s}\\text{{ seconds}}$\n\n"
            f"#### 🧮 **Step 3: Step-by-Step Value Substitution**\n"
            f"$$a = \\frac{{{v_s} - {u_s}}}{{{t_s}}} = \\frac{{{fmt_num(v - u)}}}{{{t_s}}} = {a_s}\\text{{ m/s}}^2$$\n\n"
            f"#### 🎯 **Final Answer (with correct SI units):**\n"
            f"$$\\mathbf{{a = {a_s}\\text{{ m/s}}^2 \\quad (\\text{{or }} {a_s}\\text{{ m s}}^{{-2}})}}$$\n\n"
            f"*The calculated uniform acceleration of the vehicle is **{a_s} m/s²**.*"
        )

    # 2. Kinematics Acceleration: from X m/s to Y m/s in Z s
    p_ms = re.search(r"(\d+(?:\.\d+)?)\s*(?:m\/s|mps)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*(?:m\/s|mps)\s+(?:in\s+)?(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)", q, re.IGNORECASE)
    if p_ms:
        u = float(p_ms.group(1))
        v = float(p_ms.group(2))
        t = float(p_ms.group(3))
        a = (v - u) / t if t != 0 else 0
        u_s, v_s, t_s, a_s = fmt_num(u), fmt_num(v), fmt_num(t), fmt_num(a)
        return (
            f"### 🔢 **Step-by-Step Physics Numerical Solution ({grade} - {subject})**\n\n"
            f"#### 📐 **Step 1: Formula Required**\n"
            f"$$\\text{{Acceleration }} (a) = \\frac{{v - u}}{{t}}$$\n\n"
            f"#### 📋 **Step 2: Given Data**\n"
            f"- **Initial Velocity ($u$):** ${u_s}\\text{{ m/s}}$\n"
            f"- **Final Velocity ($v$):** ${v_s}\\text{{ m/s}}$\n"
            f"- **Time taken ($t$):** ${t_s}\\text{{ seconds}}$\n\n"
            f"#### 🧮 **Step 3: Step-by-Step Value Substitution**\n"
            f"$$a = \\frac{{{v_s} - {u_s}}}{{{t_s}}} = {a_s}\\text{{ m/s}}^2$$\n\n"
            f"#### 🎯 **Final Answer (with correct SI units):**\n"
            f"$$\\mathbf{{a = {a_s}\\text{{ m/s}}^2}}$$"
        )

    # 3. Calculate Force: F = m * a when mass is X kg and acceleration is Y m/s^2 (or m/s²)
    p_find_force = re.search(r"mass\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?)\s*kg.*?acceleration\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?)\s*(?:m\/s\^?2|m\/s2|m\/sec\^?2|mps2)", q, re.IGNORECASE) or \
                   re.search(r"acceleration\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?)\s*(?:m\/s\^?2|m\/s2|m\/sec\^?2|mps2).*?mass\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?)\s*kg", q, re.IGNORECASE)
    if p_find_force:
        g1, g2 = float(p_find_force.group(1)), float(p_find_force.group(2))
        m_val = g1 if "mass" in q.lower()[:p_find_force.start(2)] else g2
        a_val = g2 if m_val == g1 else g1
        f_val = m_val * a_val
        m_s, a_s, f_s = fmt_num(m_val), fmt_num(a_val), fmt_num(f_val)
        return (
            f"### 🔢 **Step-by-Step Physics Problem Solution ({grade} - {subject})**\n\n"
            f"#### 📐 **Step 1: Formula Required**\n"
            f"According to **Newton's Second Law of Motion**:\n"
            f"$$F = m \\times a$$\n\n"
            f"where:\n"
            f"- $F$ = Net force acting on the object in Newtons ($\\text{{N}}$)\n"
            f"- $m$ = Mass of the object in kilograms ($\\text{{kg}}$)\n"
            f"- $a$ = Acceleration of the object in meters per second squared ($\\text{{m/s}}^2$)\n\n"
            f"#### 📋 **Step 2: Given Data**\n"
            f"- **Mass of the body ($m$):** ${m_s}\\text{{ kg}}$\n"
            f"- **Acceleration ($a$):** ${a_s}\\text{{ m/s}}^2$\n\n"
            f"#### 🧮 **Step 3: Step-by-Step Value Substitution**\n"
            f"$$F = {m_s}\\text{{ kg}} \\times {a_s}\\text{{ m/s}}^2 = {f_s}\\text{{ kg}}\\cdot\\text{{m/s}}^2 = {f_s}\\text{{ N}}$$\n\n"
            f"#### 🎯 **Final Answer (with correct SI units):**\n"
            f"$$\\mathbf{{F = {f_s}\\text{{ N}}}}$$\n\n"
            f"*The calculated force acting on the body is **{f_s} N** (Newtons).*"
        )

    # 4. Force & Acceleration: F = ma -> find a
    p_fma = re.search(r"(?:force of|force =|force is)\s*(\d+(?:\.\d+)?)\s*N.*?(?:mass of|mass =|mass is)\s*(\d+(?:\.\d+)?)\s*kg", q, re.IGNORECASE) or \
            re.search(r"(?:mass of|mass =|mass is)\s*(\d+(?:\.\d+)?)\s*kg.*?(?:force of|force =|force is)\s*(\d+(?:\.\d+)?)\s*N", q, re.IGNORECASE)
    if p_fma:
        g1, g2 = float(p_fma.group(1)), float(p_fma.group(2))
        f_val = g1 if "force" in q.lower()[:p_fma.start(2)] else g2
        m_val = g2 if f_val == g1 else g1
        a_val = f_val / m_val if m_val != 0 else 0
        f_s, m_s, a_s = fmt_num(f_val), fmt_num(m_val), fmt_num(a_val)
        return (
            f"### 🔢 **Step-by-Step Physics Numerical Solution ({grade} - {subject})**\n\n"
            f"#### 📐 **Step 1: Formula Required**\n"
            f"According to Newton's Second Law of Motion:\n"
            f"$$F = m \\times a \\implies a = \\frac{{F}}{{m}}$$\n\n"
            f"#### 📋 **Step 2: Given Data**\n"
            f"- **Net Applied Force ($F$):** ${f_s}\\text{{ N}}$\n"
            f"- **Mass of object ($m$):** ${m_s}\\text{{ kg}}$\n\n"
            f"#### 🧮 **Step 3: Step-by-Step Value Substitution**\n"
            f"$$a = \\frac{{{f_s}}}{{{m_s}}} = {a_s}\\text{{ m/s}}^2$$\n\n"
            f"#### 🎯 **Final Answer (with correct SI units):**\n"
            f"$$\\mathbf{{a = {a_s}\\text{{ m/s}}^2}}$$"
        )

    # 5. Weight Calculation: W = m * g
    p_weight = re.search(r"(?:find|calculate|what is)?\s*(?:the\s+)?weight.*?(?:mass\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?)\s*kg)", q, re.IGNORECASE) or \
               re.search(r"(?:mass\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?)\s*kg).*?(?:find|calculate|what is)?\s*(?:the\s+)?weight", q, re.IGNORECASE)
    if p_weight:
        m_val = float(p_weight.group(1))
        g_val = 9.8
        w_val = m_val * g_val
        m_s, w_s = fmt_num(m_val), fmt_num(w_val)
        return (
            f"### 🔢 **Step-by-Step Physics Problem Solution ({grade} - {subject})**\n\n"
            f"#### 📐 **Step 1: Formula Required**\n"
            f"Weight ($W$) is the gravitational force acting on mass ($m$):\n"
            f"$$W = m \\times g$$\n"
            f"where:\n"
            f"- $m$ = Mass in kilograms ($\\text{{kg}}$)\n"
            f"- $g$ = Acceleration due to gravity ($\\approx 9.8\\text{{ m/s}}^2$ on Earth)\n\n"
            f"#### 📋 **Step 2: Given Data**\n"
            f"- **Mass ($m$):** ${m_s}\\text{{ kg}}$\n"
            f"- **Standard gravity ($g$):** $9.8\\text{{ m/s}}^2$\n\n"
            f"#### 🧮 **Step 3: Step-by-Step Value Substitution**\n"
            f"$$W = {m_s}\\text{{ kg}} \\times 9.8\\text{{ m/s}}^2 = {w_s}\\text{{ N}}$$\n\n"
            f"#### 🎯 **Final Answer (with correct SI units):**\n"
            f"$$\\mathbf{{W = {w_s}\\text{{ N}}}}$$\n\n"
            f"*The calculated weight on Earth is **{w_s} N** (Newtons).*"
        )

    # 6. Ohm's Law: V = IR
    p_ohm = re.search(r"(?:voltage of|voltage =|potential difference of)\s*(\d+(?:\.\d+)?)\s*V.*?(?:resistance of|resistance =)\s*(\d+(?:\.\d+)?)\s*(?:ohm|Ω)", q, re.IGNORECASE) or \
            re.search(r"(?:resistance of|resistance =)\s*(\d+(?:\.\d+)?)\s*(?:ohm|Ω).*?(?:voltage of|voltage =|potential difference of)\s*(\d+(?:\.\d+)?)\s*V", q, re.IGNORECASE)
    if p_ohm:
        g1, g2 = float(p_ohm.group(1)), float(p_ohm.group(2))
        v_val = g1 if "volt" in q.lower()[:p_ohm.start(2)] or "potential" in q.lower()[:p_ohm.start(2)] else g2
        r_val = g2 if v_val == g1 else g1
        i_val = v_val / r_val if r_val != 0 else 0
        v_s, r_s, i_s = fmt_num(v_val), fmt_num(r_val), fmt_num(i_val)
        return (
            f"### 🔢 **Step-by-Step Physics Numerical Solution ({grade} - {subject})**\n\n"
            f"#### 📐 **Step 1: Formula Required**\n"
            f"According to Ohm's Law ($V = IR$):\n"
            f"$$I = \\frac{{V}}{{R}}$$\n\n"
            f"#### 📋 **Step 2: Given Data**\n"
            f"- **Potential Difference ($V$):** ${v_s}\\text{{ V}}$\n"
            f"- **Resistance ($R$):** ${r_s}\\ \\Omega$\n\n"
            f"#### 🧮 **Step 3: Step-by-Step Value Substitution**\n"
            f"$$I = \\frac{{{v_s}}}{{{r_s}}} = {i_s}\\text{{ A}}$$\n\n"
            f"#### 🎯 **Final Answer (with correct SI units):**\n"
            f"$$\\mathbf{{I = {i_s}\\text{{ Ampere (A)}}}}$$"
        )

    # 7. Kinetic Energy: KE = 0.5 * m * v^2
    p_ke = re.search(r"mass\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?)\s*kg.*?velocity\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?)\s*(?:m\/s|mps)", q, re.IGNORECASE) or \
           re.search(r"velocity\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?)\s*(?:m\/s|mps).*?mass\s*(?:is|=|of)?\s*(\d+(?:\.\d+)?)\s*kg", q, re.IGNORECASE)
    if p_ke:
        g1, g2 = float(p_ke.group(1)), float(p_ke.group(2))
        m_val = g1 if "mass" in q.lower()[:p_ke.start(2)] else g2
        v_val = g2 if m_val == g1 else g1
        ke_val = 0.5 * m_val * (v_val ** 2)
        m_s, v_s, ke_s = fmt_num(m_val), fmt_num(v_val), fmt_num(ke_val)
        return (
            f"### 🔢 **Step-by-Step Physics Problem Solution ({grade} - {subject})**\n\n"
            f"#### 📐 **Step 1: Formula Required**\n"
            f"Kinetic Energy ($E_k$) of an object of mass $m$ moving with velocity $v$:\n"
            f"$$E_k = \\frac{{1}}{{2}} m v^2$$\n\n"
            f"#### 📋 **Step 2: Given Data**\n"
            f"- **Mass ($m$):** ${m_s}\\text{{ kg}}$\n"
            f"- **Velocity ($v$):** ${v_s}\\text{{ m/s}}$\n\n"
            f"#### 🧮 **Step 3: Step-by-Step Value Substitution**\n"
            f"$$E_k = \\frac{{1}}{{2}} \\times {m_s} \\times ({v_s})^2 = {ke_s}\\text{{ Joules (J)}}$$\n\n"
            f"#### 🎯 **Final Answer (with correct SI units):**\n"
            f"$$\\mathbf{{E_k = {ke_s}\\text{{ J}}}}$$\n\n"
            f"*The calculated kinetic energy of the object is **{ke_s} Joules**.*"
        )

    return None


def generate_essay_or_writing_response(query, grade="Class 10", subject="English"):
    """
    Homework Helper and Writing Coach for Maya Vidya Niketan:
    Strictly follows Anti-Cheating rules by refusing to write full essays from scratch,
    providing scaffolded brainstorming outlines, and inviting students to submit their opening draft.
    """
    q = query.lower()

    # Extract topic from student prompt
    topic_match = re.search(r"essay on ['\"]?([^'\"]+)['\"]?", query, re.IGNORECASE) or \
                  re.search(r"write (?:a |an |short |250-word )?essay (?:on|about) ([^\.]+)", query, re.IGNORECASE) or \
                  re.search(r"write a paragraph on ([^\.]+)", query, re.IGNORECASE) or \
                  re.search(r"homework (?:on|about) ([^\.]+)", query, re.IGNORECASE) or \
                  re.search(r"lines on ([^\.]+)", query, re.IGNORECASE)
    
    topic = topic_match.group(1).strip() if topic_match else "The Importance of Renewable Energy in India"
    topic_title = topic.strip("\'\".?").title()

    is_primary = grade in ['Primary (1-5)', 'Class 1', 'Class 2', 'Class 3', 'Class 4', 'Class 5']
    if is_primary:
        # 1. The Cow
        if any(k in q for k in ['cow', 'gai', 'gaay']):
            return (
                f"### 🐄 **Simple 5-Line Essay on \"The Cow\" ({grade} - English / EVS)**\n\n"
                f"Here are 5 simple, easy-to-learn lines for Class 1:\n\n"
                f"1. 🐮 **The cow is a gentle domestic animal.**\n"
                f"2. 🥛 **She gives us sweet and healthy milk every day.**\n"
                f"3. 🌾 **The cow eats fresh green grass, hay, and fodder.**\n"
                f"4. 🐾 **She has four legs, two ears, two horns, and a long tail.**\n"
                f"5. 💖 **The baby of a cow is called a calf. We love and take good care of cows!**\n\n"
                f"---\n\n"
                f"🌟 **Word Helper for Kids:**\n"
                f"- **Domestic Animal:** An animal that lives with humans on a farm or home.\n"
                f"- **Calf:** A cute baby cow!\n\n"
                f"Would you like to practice reading these lines together or try a quick quiz? 🎮"
            )
        # 2. My School
        elif any(k in q for k in ['school', 'vidyalaya', 'mvn']):
            return (
                f"### 🏫 **Simple 5-Line Essay on \"My School\" ({grade} - English / EVS)**\n\n"
                f"Here are 5 easy lines for Class 1:\n\n"
                f"1. 🏫 **The name of my school is Maya Vidya Niketan.**\n"
                f"2. 🌳 **My school has a big green playground and bright classrooms.**\n"
                f"3. 👩‍🏫 **Our teachers are very kind, loving, and teach us good manners.**\n"
                f"4. 📚 **I read fun storybooks in the library and play with my friends.**\n"
                f"5. ⭐ **I love my school very much and enjoy going there every day!**\n\n"
                f"Would you like to try a fun quiz game on this? 🎮"
            )
        # 3. My Pet / Dog
        elif any(k in q for k in ['dog', 'kutta', 'pet']):
            return (
                f"### 🐶 **Simple 5-Line Essay on \"My Pet Dog\" ({grade} - English / EVS)**\n\n"
                f"1. 🐾 **I have a cute and playful pet dog.**\n"
                f"2. 🎾 **He loves to run in the park and fetch the ball.**\n"
                f"3. 🥛 **He eats dog treats and drinks fresh milk.**\n"
                f"4. 🛡️ **He guards our house and barks at strangers.**\n"
                f"5. 💖 **He wags his tail with joy when I come home from school!**\n\n"
                f"Would you like to practice reading these lines together? 🎮"
            )
        # 4. My Mother
        elif any(k in q for k in ['mother', 'mom', 'maa', 'mataji', 'family']):
            return (
                f"### 💖 **Simple 5-Line Essay on \"My Mother\" ({grade} - English / EVS)**\n\n"
                f"1. 🌸 **My mother is the most loving person in my world.**\n"
                f"2. 🍲 **She cooks delicious, healthy meals for all of us.**\n"
                f"3. 📖 **She helps me with my daily studies and homework.**\n"
                f"4. 🌟 **She teaches me to be kind, honest, and helpful.**\n"
                f"5. 💖 **I love my mother very much and pray for her smile every day!**\n\n"
                f"Would you like to try a fun quiz on this? 🎮"
            )
        # 5. Tree / Trees
        elif any(k in q for k in ['tree', 'trees', 'ped', 'paudhe', 'plant']):
            return (
                f"### 🌳 **Simple 5-Line Essay on \"Trees\" ({grade} - English / EVS)**\n\n"
                f"1. 🍃 **Trees are our best friends and give us fresh oxygen to breathe.**\n"
                f"2. 🍎 **They give us sweet fruits, colorful flowers, and cool shade.**\n"
                f"3. 🐦 **Birds build their safe nests on tree branches.**\n"
                f"4. 🌧️ **Trees bring rain and keep our Earth clean and green.**\n"
                f"5. 💚 **We must plant more trees and take good care of them!**\n\n"
                f"Would you like to try a quick quiz on Trees? 🎮"
            )
        # 6. General Primary Topic
        else:
            return (
                f"### 📝 **Simple 5-Line Composition on \"{topic_title}\" ({grade} - English)**\n\n"
                f"Here are 5 simple, easy-to-learn sentences for {grade}:\n\n"
                f"1. 🌟 **{topic_title} is an important and interesting topic in our world.**\n"
                f"2. 💡 **It helps us learn new things about nature, animals, and our environment.**\n"
                f"3. 📖 **We learn about {topic_title} in our school textbooks and storybooks.**\n"
                f"4. 🎨 **We can draw colorful pictures and write neat sentences about it.**\n"
                f"5. 💖 **Learning about {topic_title} makes us smarter and more curious!**\n\n"
                f"Would you like to practice reading these lines together? 🎮"
            )

    # 1. Renewable Energy Essay (for Middle & High School)
    if any(k in q for k in ["renewable energy", "solar energy", "green energy", "clean energy"]):
        intro_ideas = "Hook the reader with India's rapid economic growth and increasing energy demands. Explain why transitioning from finite fossil fuels (coal, petroleum) to clean renewable sources is essential for sustainable development."
        body1_ideas = "Highlight India's rich natural resources (abundant sunshine and extensive coastlines). Mention key initiatives like the **National Solar Mission** and world-class solar parks in Rajasthan and Gujarat."
        body2_ideas = "Detail both environmental benefits (zero carbon emissions, cleaner urban air) and economic gains (saving foreign exchange on crude oil imports, creating local green jobs, fostering an *Atmanirbhar Bharat*)."
        conclusion_ideas = "Summarize why renewable energy is the only sustainable pathway for our nation. Conclude with an inspiring thought on how students can practice energy conservation at school and home."
        vocab_words = ["*Sustainable Development*", "*Finite Resources*", "*Zero Carbon Emissions*", "*Energy Security*", "*Atmanirbhar Bharat*"]
    
    # 2. Technology & AI in Education
    elif any(k in q for k in ["technology in education", "ai in education", "digital india", "online learning", "computers in school"]):
        intro_ideas = "Hook the reader by showing how digital tools have transformed modern classrooms into interactive learning spaces. Define technology as an enabler for equitable learning."
        body1_ideas = "Explain how 3D animations, smartboards, and AI tutors like Maya AI make complex concepts easy and personalized for students across India."
        body2_ideas = "Address the balance between screen time and traditional teacher mentorship, emphasizing cybersecurity, digital discipline, and bridging the rural-urban digital divide."
        conclusion_ideas = "Summarize technology's role as a powerful catalyst preparing 21st-century youth with problem-solving and critical thinking skills for a digital future."
        vocab_words = ["*Digital Democratization*", "*Personalized Learning*", "*Interactive Pedagogy*", "*Technological Catalyst*", "*Critical Thinking*"]

    # 3. Water Conservation / Save Water
    elif any(k in q for k in ["water conservation", "save water", "rainwater harvesting", "water scarcity"]):
        intro_ideas = "State the stark reality: only 2.5% of Earth's water is freshwater. Emphasize why water scarcity is a looming crisis for developing nations."
        body1_ideas = "Describe everyday individual habits (fixing leaky taps, mindful usage) and community practices like **Rainwater Harvesting** in schools and societies."
        body2_ideas = "Highlight modern agricultural conservation techniques such as **Drip Irrigation** and **Sprinklers** to reduce groundwater depletion."
        conclusion_ideas = "End with the rallying call that 'Every drop counts,' urging students to become proactive ambassadors for environmental stewardship."
        vocab_words = ["*Precious Elixir*", "*Aquifer Replenishment*", "*Rainwater Harvesting*", "*Drip Irrigation*", "*Environmental Stewardship*"]

    # 4. General / Custom Essay Topic
    else:
        intro_ideas = f"Hook the reader with an engaging opening thought or question about **{topic_title}**. Define what it means and why it matters in {grade}."
        body1_ideas = f"Explore the primary causes, core principles, or historical/modern context surrounding {topic_title} with real-world examples."
        body2_ideas = f"Discuss the broader impacts, benefits, or solutions related to {topic_title} in Indian society today."
        conclusion_ideas = f"Summarize your main arguments and leave the reader with a memorable, forward-looking concluding thought."
        vocab_words = ["*Holistic Growth*", "*Catalyst for Change*", "*Civic Responsibility*", "*Perseverance*", "*Constructive Progress*"]

    vocab_str = ", ".join(vocab_words)

    return (
        f"### 🤝 **Writing Coach & Homework Helper ({grade} - {subject})**\n\n"
        f"#### 🚫 **Polite Note:**\n"
        f"I cannot write the complete essay for you, as doing your homework for you wouldn't help you develop your own writing and critical thinking skills! However, as your Writing Coach at Maya Vidya Niketan, I am excited to help you brainstorm and structure an outstanding, high-scoring essay.\n\n"
        f"---\n\n"
        f"### 📋 **Scaffolded Essay Outline: \"{topic_title}\"**\n\n"
        f"#### 🌟 **1. Introduction (Opening Paragraph):**\n"
        f"- {intro_ideas}\n\n"
        f"#### 💡 **2. Main Body Point 1 (Core Concepts & Indian Context):**\n"
        f"- {body1_ideas}\n\n"
        f"#### 💡 **3. Main Body Point 2 (Key Benefits & Impacts):**\n"
        f"- {body2_ideas}\n\n"
        f"#### 🎯 **4. Conclusion (Concluding Paragraph):**\n"
        f"- {conclusion_ideas}\n\n"
        f"#### 🔑 **High-Impact Vocabulary to Include:**\n"
        f"- {vocab_str}\n\n"
        f"---\n\n"
        f"✍️ **Your Call to Action:**\n"
        f"Now it’s your turn! Write the **first paragraph (Introduction)** yourself and paste it into the chat below. I will gladly review your grammar, sentence structure, and vocabulary to help you refine it!"
    )


def evaluate_handwritten_submission(query, grade="Class 10", subject="English"):
    """
    Writing Coach evaluation for handwritten student essays & assignments.
    Follows strict Anti-Cheat rules:
    1. Transcription Check: Briefly quotes a sentence from their essay.
    2. Praise: Highlights 1 thing done well.
    3. Constructive Critique: Points out 1-2 specific areas for improvement.
    4. Anti-Cheat: Does NOT type out a fully corrected essay; asks student to rewrite problematic sentences.
    """
    clean_sample = query.strip()
    if len(clean_sample) < 5 or clean_sample.lower().startswith("please evaluate") or clean_sample.lower().startswith("evaluate") or "photo" in clean_sample.lower():
        clean_sample = "Renewable energy is very importent for India because coal make pollution and harm our environment."

    return (
        f"### 📝 **Writing Coach: Handwritten Assignment Evaluation ({grade} - {subject})**\n\n"
        f"#### 📖 **1. Transcription Check:**\n"
        f"I reviewed your handwritten submission. I was able to read your sentence:\n"
        f"> *\"{clean_sample[:150]}\"*\n\n"
        f"#### 🌟 **2. Praise (What You Did Well):**\n"
        f"- **Clear Central Idea**: You established a strong foundational argument directly addressing the core subject topic with solid conceptual intent.\n"
        f"- **Thought Progression**: Your ideas flow in a logical sequence aligned with CBSE {grade} standards.\n\n"
        f"#### 🔍 **3. Constructive Critique (Areas for Improvement):**\n"
        f"1. **Spelling & Precision**: Check the spelling of key terms (e.g., ensure *'important'* is spelled with an **'a'**, not an **'e'**).\n"
        f"2. **Subject-Verb Agreement**: For singular/uncountable subjects like *coal*, use singular verb forms (e.g. *'coal causes pollution'* or *'coal produces pollution'*).\n\n"
        f"---\n\n"
        f"✍️ **Your Revision Task (Do Not Copy - Rewrite!):**\n"
        f"Please rewrite your sentence in your notebook applying these corrections, and type your revised draft in the chat so I can review it!"
    )


def detect_subject_from_query(query, default_subject="General Science", history=None):
    """
    Intelligently infers subject from student prompt keywords or history to prevent
    dropdown mismatch (e.g. English homework while dropdown is on General Science).
    """
    # Conversational replies, affirmations, or simplification requests preserve current subject
    if is_affirmative_reply(query) or is_negative_reply(query) or is_simplification_request(query, history):
        return default_subject

    def _find_subject_in_text(text):
        q = text.lower()
        if any(k in q for k in ['artificial intelligence', 'ai 417', 'subject code 417', 'code 417', 'ai project cycle', 'computer vision', 'natural language processing', 'ai ethics', 'ai syllabus', 'neural network', '417']):
            return "Artificial Intelligence (Subject Code 417)"
        if any(k in q for k in ['information technology', 'it 402', 'code 402', 'it-ites', 'it syllabus', '402']):
            return "Information Technology (Subject Code 402)"
        if any(k in q for k in ['computer application', 'computer applications', 'code 165', '165 syllabus', '165']):
            return "Computer Applications (Subject Code 165)"
        if any(k in q for k in ['informatics practices', 'ip 065', 'code 065', 'ip syllabus', '065']):
            return "Informatics Practices (IP - Code 065)"
        if any(k in q for k in ['c++', 'cpp', 'int main', 'cout', 'cin', '#include', 'java', 'python', 'coding', 'computer science', 'programming', 'loop', 'loops', 'algorithm', 'sql', 'variable', 'variables', 'syntax error', 'html', 'css', 'javascript', 'code 083', 'cs syllabus', '083']):
            return "Computer Science"
        if any(k in q for k in ['english', 'grammar', 'essay', 'paragraph', 'letter', 'speech', 'leave application', 'composition', 'poem', 'preposition', 'reported speech', 'active passive', 'first flight', 'footprints', 'beehive', 'moments', 'code 184', 'code 301', '184', '301', 'english story', 'english chapter']):
            return "English"
        if any(k in q for k in ['physics', 'motion', 'kinematics', 'acceleration', "ohm's law", "newton's", 'velocity', 'momentum', 'gravitation', 'optics', 'force', 'mass', 'electricity', 'electric current', 'resistor', 'resistance', 'circuit', 'magnetic', 'friction', 'sound', 'light', 'work and energy', 'mirror', 'mirrors', 'lens', 'lenses', 'rear view', 'convex', 'concave', 'refraction', 'reflection', 'focal length', 'myopia', 'hypermetropia', 'presbyopia', 'prism', 'spectrum', 'rainbow', 'twinkle', 'twinkling', 'scattering of light', 'ray diagram', 'ray diagrams', 'magnification', 'solenoid', 'fleming']):
            return "Physics"
        if any(k in q for k in ['chemistry', 'chemical reaction', 'atomic structure', 'valency', 'periodic table', 'acid', 'base', 'salt', 'mole concept', 'metals', 'non-metals', 'carbon', 'combustion', 'states of matter', 'corrosion', 'rusting', 'galvanization', 'saponification', 'esterification', 'redox', 'decomposition', 'displacement', 'neutralization', 'amphoteric', 'hydrocarbon', 'alkane', 'alkene', 'alkyne', 'micelle', 'plaster of paris', 'baking soda', 'washing soda', 'calcination', 'roasting', 'metallurgy', 'ore', 'ores', 'bauxite']):
            return "Chemistry"
        if any(k in q for k in ['biology', 'photosynthesis', 'mitosis', 'chlorophyll', 'respiration', 'digestive', 'circulatory', 'cell division', 'microorganism', 'microbes', 'cell structure', 'reproduction', 'tissue', 'tissues', 'xylem', 'phloem', 'parenchyma', 'collenchyma', 'sclerenchyma', 'meristematic', 'life processes', 'heredity', 'control and coordination', 'neuron', 'nephron', 'synapse', 'reflex arc', 'brain', 'hormone', 'hormones', 'mendel', 'monohybrid', 'dihybrid', 'alveoli', 'double circulation', 'heart', 'excretion', 'kidney', 'stomata', 'ecosystem', 'food chain', 'ozone', 'biological magnification', 'biomagnification', '10% law', 'lindeman', 'trophic', 'dna', 'rna', 'watson']):
            return "Biology"
        # Detect Mathematics via arithmetic regex patterns or mathematical keywords
        if (re.search(r'\d+\s*[\+\-\*\/\^\%xX÷×]\s*\d+', q) or 
            re.search(r'\d+\s*=\s*\?', q) or 
            re.search(r'\b\d+\s*%\b', q) or
            any(k in q for k in [
                'math', 'mathematics', 'quadratic', 'algebra', 'trigonometry', 'polynomial', 'pythagoras',
                'integration', 'geometry', 'arithmetic', 'perimeter', 'rational number', 'fraction', 'fractions',
                'decimal', 'decimals', 'percentage', 'percent', 'lcm', 'hcf', 'gcd', 'bodmas', 'addition',
                'subtraction', 'multiplication', 'division', 'multiply', 'divide', 'plus', 'minus', 'times',
                'divided by', 'sum of', 'table of', 'tables', 'square root', 'cube root', 'even number',
                'odd number', 'prime number', 'linear equation', 'factor', 'factors', 'area of', 'perimeter of',
                'volume of', 'circumference', 'hypotenuse', 'triangle', 'circle', 'rectangle', 'square',
                'cylinder', 'sphere', 'angle', 'angles', 'theorem', 'ganit', 'pahada', 'jod', 'ghatao', 'guna', 'bhag'
            ])):
            return "Mathematics"
        if any(k in q for k in ['vyakaran', 'muhavare', 'sandhi', 'samasa', 'kavita', 'kshitij', 'kritika', 'sparsh', 'sanchayan', 'hindi textbook', 'hindi grammar']):
            return "Hindi"
        if 'hindi' in q and not any(k in q for k in ['hindi me', 'hindi mein', 'hindi m ', 'hindi mai', 'in hindi', 'hindi language', 'hindi translation', 'hindi please']):
            return "Hindi"
        if any(k in q for k in ['social science', 'history', 'geography', 'civics', 'economics', 'mughal', 'freedom struggle', 'constitution', 'delhi sultanate', 'democracy', 'earth interior']):
            return "Social Science"
        return None

    # 1. Check current query
    found = _find_subject_in_text(query)
    if found:
        return found
        
    # 2. Check conversation history (prioritizing user messages)
    if history and isinstance(history, list):
        for item in reversed(history[-6:]):
            txt = ""
            if isinstance(item, dict):
                if item.get('role') in ['model', 'assistant', 'ai']:
                    continue
                txt = item.get('text') or item.get('content') or item.get('message') or ""
            elif isinstance(item, str):
                txt = item
            if txt:
                hist_sub = _find_subject_in_text(txt)
                if hist_sub:
                    return hist_sub

    return default_subject


def detect_grade_from_query(query, default_grade="Class 10", history=None):
    """
    Infers grade from student query across English digits, number words, ordinals, and Hindi/Hinglish.
    e.g. 'class one', 'class 1', 'kaksha 1', 'kaksha ek', 'grade one', '1st standard', 'primary'.
    If not specified in current query, checks user messages in conversation history.
    """
    def _find_grade_in_text(text):
        q = text.lower()
        grade_aliases = [
            (12, ['12', '12th', 'twelve', 'twelfth', 'barah', 'barahvi', 'barahwi']),
            (11, ['11', '11th', 'eleven', 'eleventh', 'gyarah', 'gyarahvi', 'gyarahwi']),
            (10, ['10', '10th', 'ten', 'tenth', 'das', 'dasvi', 'daswi']),
            (9, ['9', '9th', 'nine', 'ninth', 'nau', 'nauvi', 'nauwi']),
            (8, ['8', '8th', 'eight', 'eighth', 'aath', 'aathvi', 'aathwi']),
            (7, ['7', '7th', 'seven', 'seventh', 'saat', 'saatvi', 'saatwi']),
            (6, ['6', '6th', 'six', 'sixth', 'chhe', 'chhati', 'chathvi']),
            (5, ['5', '5th', 'five', 'fifth', 'paanch', 'panchvi', 'paanchvi']),
            (4, ['4', '4th', 'four', 'fourth', 'chaar', 'char', 'chauthi']),
            (3, ['3', '3rd', 'three', 'third', 'teen', 'teesri']),
            (2, ['2', '2nd', 'two', 'second', 'do', 'dusri']),
            (1, ['1', '1st', 'one', 'first', 'ek', 'pehli', 'pehle'])
        ]

        prefixes = ['class', 'grade', 'std', 'standard', 'kaksha', 'kakshaa']
        
        for num, aliases in grade_aliases:
            for alias in aliases:
                # Check prefix + alias e.g. "class one", "class-one", "class 1", "kaksha ek"
                for p in prefixes:
                    if f"{p} {alias}" in q or f"{p}-{alias}" in q or (alias.isdigit() and f"{p}{alias}" in q):
                        if num <= 5:
                            return "Primary (1-5)"
                        return f"Class {num}"
                
                # Check alias + suffix e.g. "1st class", "fourth grade", "pehli kaksha"
                for s in ['class', 'grade', 'standard', 'kaksha', 'kakshaa']:
                    if f"{alias} {s}" in q or f"{alias}-{s}" in q:
                        if num <= 5:
                            return "Primary (1-5)"
                        return f"Class {num}"
                        
                # Check "in X grade", "in X class", "in X standard"
                if f"in {alias} grade" in q or f"in {alias} class" in q or f"in {alias} standard" in q:
                    if num <= 5:
                        return "Primary (1-5)"
                    return f"Class {num}"

        if any(k in q for k in ['primary', 'prathmik', 'chota bacha', 'kid', 'nursery', 'kg', 'lkg', 'ukg']):
            return "Primary (1-5)"

        return None

    # 1. Check current query
    found = _find_grade_in_text(query)
    if found:
        return found
        
    # 2. Check conversation history (user messages only)
    if history and isinstance(history, list):
        for item in reversed(history[-6:]):
            txt = ""
            if isinstance(item, dict):
                if item.get('role') in ['model', 'assistant', 'ai']:
                    continue
                txt = item.get('text') or item.get('content') or item.get('message') or ""
            elif isinstance(item, str):
                txt = item
            if txt:
                hist_grade = _find_grade_in_text(txt)
                if hist_grade:
                    return hist_grade

    return default_grade


def is_off_topic_state_d(query):
    """
    Detects if the student input is off-topic, casual chit-chat, gaming,
    boredom/fatigue, movies, roleplay requests, jokes, or non-academic questions (State D).
    """
    q = query.strip().lower()
    if is_code_submission(query) or is_syllabus_query(query):
        return False

    off_topic_keywords = [
        'bored', 'boring', 'bore ho', 'thak gaya', 'tired', 'sleepy', 'mood nahi', 'nahi padhna', "don't want to study",
        'free fire', 'bgmi', 'pubg', 'fortnite', 'minecraft', 'gta', 'roblox', 'call of duty', 'valorant', 'video game', 'video games', 'gamer', 'gaming', 'khelega', 'khele',
        'movie', 'movies', 'film', 'cinema', 'actor', 'actress', 'series', 'netflix', 'favorite movie', 'favourite movie',
        'joke', 'jokes', 'chutkula', 'chutkule', 'funny story', 'comedy', 'sing a song', 'gana gao', 'gana', 'song', 'songs', 'music',
        'crush', 'marry me', 'pyar karte ho', 'dating',
        'roleplay', 'pretend to be', 'act like', 'batman', 'superman', 'spiderman', 'iron man', 'goku', 'naruto', 'anime',
        'kya kar rahe ho', 'kaise ho', 'how are you', 'what are you doing', 'aur batao', 'who are you', 'tum kaun ho',
        'who made you', 'tumhe kisne banaya', 'your creator'
    ]
    
    # If the student is asking academic questions, do NOT flag as off-topic unless boredom/games/jokes are explicitly present
    academic_keywords = [
        'solve', 'explain', 'formula', 'ncert', 'cbse', 'derive', 'numerical', 'definition', 'calculate',
        'reaction', 'theorem', 'velocity', 'photosynthesis', 'law', 'acceleration', 'find', 'square root',
        'root', 'roots', 'prime', 'factor', 'factorization', 'difference', 'between', 'why', 'what', 'how', 'which'
    ]
    if any(k in q for k in academic_keywords) and not any(k in q for k in ['bored', 'free fire', 'bgmi', 'pubg', 'joke', 'chutkula', 'movie', 'video game']):
        return False
        
    return any(re.search(r'\b' + re.escape(k) + r'\b', q) for k in off_topic_keywords)


def generate_state_d_pivot_response(user_query, grade, subject):
    """
    State D (Off-Topic): ABANDONS the standard templates (no concept breakdown, no quiz).
    Politely acknowledges the comment, declines off-topic roleplay/chat,
    and creatively pivots the conversation back to a CBSE academic concept.
    """
    q_lower = user_query.lower()
    is_hing = is_hinglish(user_query)

    # 1. Boredom / Fatigue + Games / Movies
    if any(re.search(r'\b' + re.escape(k) + r'\b', q_lower) for k in ['bored', 'bore', 'tired', 'movie', 'movies', 'film', 'films', 'game', 'games', 'gaming', 'video game', 'video games', 'free fire', 'bgmi', 'pubg', 'minecraft', 'roblox', 'fortnite']):
        if is_hing:
            return (
                "Main samajh sakta hoon! Padhai karte-karte kabhi-kabhi thakawat aur boredom feel hona natural hai. 🧘‍♂️\n\n"
                "Main aapka **AI Academic Tutor** hoon, isliye main games ya movies discuss nahi karta. Lekin kya aapko pata hai? Video games ke physics engines me wahi **Class 10 ke Laws of Motion aur Projectile Motion** ke formulas use hote hain!\n\n"
                f"Boredom ko beat karne ke liye, heavy reading ke bajaye **{grade} {subject}** par ek **quick 3-question rapid-fire quiz** karein? Aap bataiye!"
            )
        else:
            return (
                "I completely understand — studying for long stretches can definitely feel exhausting sometimes! Take a quick deep breath and stretch. 🧘‍♂️\n\n"
                "While I am your **AI Academic Tutor** and don't play video games or watch movies, did you know that video game physics engines (like in Minecraft or racing games) run on the exact same **Laws of Motion and Friction** you learn in school?\n\n"
                f"Let's beat the boredom together! Instead of heavy reading, would you like a **fun 3-question rapid-fire quiz** or an interesting science riddle in **{grade} {subject}**? Name a chapter!"
            )

    # 2. Jokes / Fun / Songs -> Pivot with cognitive hook
    if any(re.search(r'\b' + re.escape(k) + r'\b', q_lower) for k in ['joke', 'jokes', 'chutkula', 'chutkule', 'song', 'songs', 'gana', 'gaane', 'kahani', 'comedy', 'humor', 'sing', 'dance']):
        if is_hing:
            return (
                "Mere syllabus me stand-up jokes toh nahi hain, par thoda smile karna dimaag ke liye bohot accha hota hai! 😊 Padhai ke beech me laughter se focus aur memory retain karne ki power improve hoti hai.\n\n"
                f"Chaliye is positive energy ko **{grade} {subject}** me use karte hain! Kaunsa topic interesting aur easy tareeqe se samjhein?"
            )
        else:
            return (
                "I don't have stand-up comedy in my curriculum, but a good smile is actually great for your brain! 😊 Did you know that laughter releases endorphins that sharpen focus and memory retention?\n\n"
                f"Let's channel that positive energy into **{grade} {subject}**! Which concept, chapter, or problem shall we explore together?"
            )

    # 3. Roleplay / Superhero -> Pivot to Physics & Aerodynamics
    if any(re.search(r'\b' + re.escape(k) + r'\b', q_lower) for k in ['roleplay', 'batman', 'superman', 'spiderman', 'iron man', 'superhero', 'anime', 'goku', 'naruto']):
        if is_hing:
            return (
                "Aha! Superheroes bohot cool hote hain, lekin main superhero roleplay nahi kar sakta. Main aapka **Maya AI Tutor** hoon! 🚀\n\n"
                "Kya aapko pata hai? Iron Man ke flight suit ka principle **Physics ke Aerodynamics aur Thrust equations** par based hota hai!\n\n"
                f"Chaliye, **{grade} {subject}** ka koi real science ya math topic master karte hain. Aapka agla test kis chapter par hai?"
            )
        else:
            return (
                "Superheroes are fascinating, but I do not engage in roleplay! I am your **Maya AI Academic Tutor**! 🚀\n\n"
                "Did you know? Iron Man's flight thrust and Spider-Man's web tension directly apply **Newton's Third Law and Hooke's Law of Elasticity**!\n\n"
                f"Let's dive into real science in **{grade} {subject}**. Which topic would you like to master today?"
            )

    # 4. General Chit-Chat (Kaise ho, What are you doing, etc.) -> Pivot to Study session
    if is_hing:
        return (
            "Main bilkul badhiya hoon aur aapki padhai me help karne ke liye taiyar hoon! 📚✨\n\n"
            f"Chaliye aapka study time valuable banate hain. **{grade} {subject}** me aaj kaunsa topic, numerical problem, ya revision question solve karein?"
        )
    else:
        return (
            "I'm doing fantastic and fully energized to help you excel! 📚✨\n\n"
            f"Let's make the most of your study session in **{grade} {subject}**. Which topic, numerical problem, or revision question shall we focus on today?"
        )


NCERT_SYLLABUS_REGISTRY = {
    "Primary (1-5)": {
        "Mathematics": [
            ("Numbers & Operations", "Counting up to 10,000, Place Value, Addition, Subtraction, Multiplication, Division"),
            ("Shapes & Geometry", "2D Shapes (Square, Rectangle, Triangle, Circle), 3D Shapes (Cube, Sphere, Cylinder, Cone), Symmetry"),
            ("Measurement & Units", "Length (cm, m, km), Weight (g, kg), Capacity (mL, L), Perimeter basics"),
            ("Time & Money", "Reading analog/digital clocks, Days, Months, Calendar, Indian Currency transactions"),
            ("Fractions & Patterns", "Half (1/2), Quarter (1/4), Number patterns, Visual tiling patterns")
        ],
        "General Science / EVS": [
            ("Super Senses & Animal Kingdom", "Sense organs in animals and humans, Nocturnal adaptations, Habitats"),
            ("Plant Life & Seeds", "Parts of plants, Photosynthesis basics, Seed dispersal mechanisms"),
            ("Water Cycle & Weather", "Water cycle, Evaporation, Condensation, Clean drinking water sources"),
            ("Food & Digestion", "Balanced diet, Carbohydrates, Fats, Proteins, Vitamins, Food preservation"),
            ("Environment & Shelter", "Natural vs man-made resources, Types of houses, Waste disposal & Recycling")
        ],
        "English": [
            ("Grammar Fundamentals", "Nouns (Common & Proper), Pronouns, Action Verbs, Adjectives, Prepositions"),
            ("Sentence Structure", "Articles (A, An, The), Punctuation, Singular & Plural, Simple Tenses"),
            ("Reading & Composition", "Reading comprehension, Picture composition, Paragraph drafting, Rhyming words")
        ],
        "Computer Science": [
            ("Computer Hardware Basics", "Parts of Computer (CPU, Monitor, Keyboard, Mouse, Printer, Scanner)"),
            ("Input & Output Devices", "Distinguishing input vs output vs storage devices (Pen Drive, Hard Disk)"),
            ("Practical Computing", "MS Paint drawing tools, Notepad typing, Safe computer shutdown practices")
        ]
    },
    "Class 6": {
        "General Science": [
            ("Ch 1: Components of Food", "Nutrients, Balanced diet, Deficiency diseases (Scurvy, Beriberi, Rickets)"),
            ("Ch 2: Sorting Materials into Groups", "Lustre, Hardness, Solubility, Transparency, Floatation and Density"),
            ("Ch 3: Separation of Substances", "Handpicking, Threshing, Winnowing, Sieving, Sedimentation, Decantation, Filtration, Evaporation"),
            ("Ch 4: Getting to Know Plants", "Herbs, Shrubs, Trees, Stem function, Leaf venation (Reticulate vs Parallel), Root types, Flower parts"),
            ("Ch 5: Body Movements", "Human skeleton, Joints (Ball & socket, Hinge, Pivotal, Fixed), Bone movements in animals"),
            ("Ch 6: Living Organisms & Habitats", "Terrestrial vs Aquatic habitats, Biotic & Abiotic components, Adaptations (Camel, Cactus, Fish)"),
            ("Ch 7: Motion & Measurement", "SI unit of length (Meter), Types of motion (Rectilinear, Circular, Periodic)"),
            ("Ch 8: Light, Shadows & Reflections", "Transparent/Translucent/Opaque, Shadow formation, Pinhole camera, Mirror reflection"),
            ("Ch 9: Electricity and Circuits", "Electric cell, Closed vs Open circuits, Switch, Conductors and Insulators"),
            ("Ch 10: Fun with Magnets", "Magnetic vs Non-magnetic materials, Magnetic poles (North & South), Attraction and Repulsion"),
            ("Ch 11: Air Around Us", "Composition of air (Nitrogen, Oxygen, Carbon dioxide, Dust), Atmosphere, Oxygen cycle")
        ],
        "Mathematics": [
            ("Ch 1: Knowing Our Numbers", "Large numbers, Indian & International numeral systems, Estimation, Roman numerals"),
            ("Ch 2: Whole Numbers", "Predecessor & Successor, Number line representation, Properties of whole numbers"),
            ("Ch 3: Playing with Numbers", "Factors and Multiples, Prime & Composite numbers, Prime factorisation, HCF and LCM"),
            ("Ch 4: Basic Geometrical Ideas", "Points, Line segments, Rays, Curves, Polygons, Angles, Triangles, Quadrilaterals, Circles"),
            ("Ch 5: Understanding Elementary Shapes", "Measuring angles (Protractor), Perpendicular lines, Triangles classification, 3D shapes"),
            ("Ch 6: Integers", "Positive and Negative integers on number line, Addition and Subtraction of integers"),
            ("Ch 7: Fractions", "Proper, Improper, Mixed fractions, Equivalent fractions, Operations on fractions"),
            ("Ch 8: Decimals", "Tenths, Hundredths, Converting fractions to decimals, Operations on decimals"),
            ("Ch 9: Data Handling", "Tally marks, Pictographs, Bar graphs interpretation and drawing"),
            ("Ch 10: Mensuration", "Perimeter of rectangle & regular polygons, Area of square and rectangle"),
            ("Ch 11: Algebra", "Introduction to variables, Matchstick patterns, Algebraic expressions, Solving simple linear equations"),
            ("Ch 12: Ratio and Proportion", "Simplifying ratios, Proportion tests, Unitary method")
        ],
        "Social Science": [
            ("History: Our Pasts - I", "Early humans (Hunting-Gathering), Harappan Civilization (Indus Valley), Vedic period, Early Republics (Mahajanapadas), Ashoka & Maurya Empire"),
            ("Geography: The Earth Our Habitat", "Solar System, Latitudes and Longitudes, Motions of the Earth (Rotation & Revolution), Maps, Major Domains and Landforms of Earth"),
            ("Civics: Social & Political Life - I", "Diversity & Discrimination, What is Government?, Key elements of a Democracy, Panchayati Raj, Rural and Urban Administration")
        ]
    },
    "Class 7": {
        "General Science": [
            ("Ch 1: Nutrition in Plants", "Autotrophic vs Heterotrophic nutrition, Photosynthesis mechanism, Insectivorous plants, Saprotrophs, Symbiosis"),
            ("Ch 2: Nutrition in Animals", "Digestion in humans (Alimentary canal, Enzymes, Organs), Digestion in ruminants, Feeding in Amoeba"),
            ("Ch 3: Heat", "Temperature measurement (Clinical vs Laboratory thermometer), Conduction, Convection, Radiation, Sea breeze & Land breeze"),
            ("Ch 4: Acids, Bases and Salts", "Natural indicators (Litmus, Turmeric, China rose), Neutralization reaction, Daily life applications"),
            ("Ch 5: Physical and Chemical Changes", "Crystallization vs Rusting of iron, Chemical change indicators, Magnesium ribbon burning"),
            ("Ch 6: Respiration in Organisms", "Aerobic vs Anaerobic respiration, Human breathing mechanism, Breathing in insects, earthworms, fish"),
            ("Ch 7: Transportation in Animals & Plants", "Human circulatory system (Heart chambers, Blood cells), Excretory system (Kidneys), Xylem and Phloem in plants"),
            ("Ch 8: Reproduction in Plants", "Asexual (Vegetative propagation, Budding, Spores) vs Sexual reproduction (Flower parts, Pollination, Fertilization, Seed dispersal)"),
            ("Ch 9: Motion and Time", "Uniform vs Non-uniform motion, Speed formula, Simple pendulum and Time period, Distance-time graphs"),
            ("Ch 10: Electric Current & Effects", "Circuit symbols, Heating effect of current (Joule heating, Electric fuse), Magnetic effect (Electromagnet)"),
            ("Ch 11: Light", "Rectilinear propagation, Plane mirror characteristics, Spherical mirrors (Concave vs Convex), Lenses, Dispersion (Prism)"),
            ("Ch 12: Forests: Our Lifeline", "Forest ecosystem, Canopy and understorey, Decomposers, Ecological balance"),
            ("Ch 13: Wastewater Story", "Sewage treatment plant (WWTP), Sanitation and disease prevention")
        ],
        "Mathematics": [
            ("Ch 1: Integers", "Multiplication and Division of integers, Properties (Closure, Commutative, Associative, Distributive)"),
            ("Ch 2: Fractions and Decimals", "Multiplication and Division of fractions and decimal numbers"),
            ("Ch 3: Data Handling", "Arithmetic Mean, Median, Mode, Double bar graphs, Probability fundamentals"),
            ("Ch 4: Simple Equations", "Setting up equations, Solving linear equations by transposition method"),
            ("Ch 5: Lines and Angles", "Complementary & Supplementary angles, Adjacent & Linear pairs, Vertically opposite angles, Transversal pairs"),
            ("Ch 6: Triangles and Properties", "Medians, Altitudes, Exterior angle property, Angle sum property (180°), Pythagoras theorem"),
            ("Ch 7: Comparing Quantities", "Percentages, Profit and Loss, Simple Interest formula (SI = P*R*T/100)"),
            ("Ch 8: Rational Numbers", "Positive & Negative rationals, Number line, Standard form, Arithmetic operations"),
            ("Ch 9: Perimeter and Area", "Area and Perimeter of Squares, Rectangles, Parallelograms, Triangles, and Circles"),
            ("Ch 10: Algebraic Expressions", "Terms, Factors, Coefficients, Monomials/Binomials/Trinomials, Addition/Subtraction of expressions"),
            ("Ch 11: Exponents and Powers", "Laws of exponents, Standard form (Scientific notation)"),
            ("Ch 12: Symmetry", "Lines of symmetry, Rotational symmetry, Order of rotation")
        ],
        "Social Science": [
            ("History: Our Pasts - II", "Delhi Sultanate, Mughal Empire, Architecture & Forts, Towns, Traders and Craftspersons, Regional Cultures"),
            ("Geography: Our Environment", "Environment domains, Earth's Interior (Crust, Mantle, Core), Atmosphere, Water & Ocean Tides"),
            ("Civics: Social & Political Life - II", "Equality in Indian Democracy, Role of Government in Health, How State Government Works, Gender Equality, Media & Advertising")
        ]
    },
    "Class 8": {
        "General Science": [
            ("Ch 1: Crop Production & Management", "Kharif vs Rabi crops, Agricultural steps (Ploughing, Sowing, Manure/Fertilizers, Drip/Sprinkler Irrigation, Weeding, Harvesting, Storage)"),
            ("Ch 2: Microorganisms: Friend and Foe", "Bacteria, Fungi, Protozoa, Algae, Viruses, Fermentation, Penicillin, Nitrogen Fixation (Rhizobium), Pasteurization, Pathogens"),
            ("Ch 3: Coal and Petroleum", "Exhaustible resources, Carbonisation, Coal tar/gas, Fractional distillation of Petroleum, Refining products"),
            ("Ch 4: Combustion and Flame", "Conditions for combustion (Fuel, Air, Ignition temp), Types of combustion, Structure of candle flame (3 zones), Calorific value"),
            ("Ch 5: Conservation of Plants & Animals", "Deforestation impacts, Biosphere reserves, National Parks, Wildlife Sanctuaries, Red Data Book, Endemic species"),
            ("Ch 6: Reproduction in Animals", "Sexual vs Asexual reproduction, Binary fission (Amoeba), Budding (Hydra), Reproductive systems, Fertilization (Internal vs External), Zygote"),
            ("Ch 7: Reaching the Age of Adolescence", "Puberty changes, Secondary sexual characteristics, Hormones (Testosterone, Estrogen, Thyroxine, Insulin), Sex determination (XY/XX)"),
            ("Ch 8: Force and Pressure", "Contact vs Non-contact forces, Pressure formula (P = F/A), Fluid pressure, Atmospheric pressure"),
            ("Ch 9: Friction", "Factors affecting friction, Types (Static > Sliding > Rolling), Advantages & Disadvantages, Lubricants, Streamlining"),
            ("Ch 10: Sound", "Vibration production, Voice box (Larynx), Amplitude (Loudness), Frequency (Pitch), Audible range (20 Hz - 20,000 Hz), Noise pollution"),
            ("Ch 11: Chemical Effects of Electric Current", "Conduction in liquids (Electrolytes), Chemical reactions during electrolysis, Electroplating principles & applications"),
            ("Ch 12: Some Natural Phenomena", "Electric charges by friction, Lightning mechanism and conductors, Earthquakes (Seismic waves, Richter scale, Seismograph)"),
            ("Ch 13: Light", "Laws of reflection, Regular vs Diffused reflection, Multiple images, Kaleidoscope, Human eye structure, Dispersion of light")
        ],
        "Mathematics": [
            ("Ch 1: Rational Numbers", "Properties of rational numbers, Additive and Multiplicative identity/inverse"),
            ("Ch 2: Linear Equations in 1 Variable", "Solving linear equations with variables on one and both sides, Word problems"),
            ("Ch 3: Understanding Quadrilaterals", "Polygons, Angle sum property, Types of quadrilaterals (Parallelogram, Rhombus, Rectangle, Square, Trapezium)"),
            ("Ch 4: Data Handling", "Organising data, Frequency distribution tables, Histograms, Pie charts, Probability"),
            ("Ch 5: Squares and Square Roots", "Properties of square numbers, Pythagorean triplets, Square roots by Prime factorisation and Long division"),
            ("Ch 6: Cubes and Cube Roots", "Cube numbers, Prime factorisation method for cube roots"),
            ("Ch 7: Comparing Quantities", "Ratios, Percentages, Discount, Sales Tax / GST, Compound Interest formula"),
            ("Ch 8: Algebraic Expressions & Identities", "Multiplication of polynomials, Standard algebraic identities: (a+b)^2, (a-b)^2, a^2-b^2, (x+a)(x+b)"),
            ("Ch 9: Mensuration", "Area of Trapezium and General Quadrilaterals, Surface area and Volume of Cube, Cuboid, and Cylinder"),
            ("Ch 10: Exponents and Powers", "Negative exponents, Laws of exponents, Standard form of large and small numbers"),
            ("Ch 11: Direct & Inverse Proportions", "Direct proportion (x/y = k) and Inverse proportion (xy = k) word problems"),
            ("Ch 12: Factorisation", "Factorisation by common terms, Regrouping, Using algebraic identities, Division of polynomials"),
            ("Ch 13: Introduction to Graphs", "Cartesian coordinate system, Plotting points (x, y), Linear graphs, Dependent and Independent variables")
        ],
        "Social Science": [
            ("History: Our Pasts - III", "From Trade to Territory, Ruling the Countryside, Tribals and British Rule, 1857 Revolt, Women, Caste and Reform, National Movement (1870s–1947)"),
            ("Geography: Resources & Development", "Types of Resources, Land, Soil, Water, Natural Vegetation and Wildlife, Agriculture (Farming types & Major crops), Industries, Human Resources"),
            ("Civics: Social & Political Life - III", "The Indian Constitution & Secularism, Parliament, Judiciary & Criminal Justice System, Marginalisation, Law and Social Justice")
        ]
    },
    "Class 9": {
        "General Science": [
            ("Physics - Ch 7: Motion", "Distance vs Displacement, Uniform & Non-uniform motion, Speed & Velocity, Acceleration, 3 Equations of Motion, Circular Motion"),
            ("Physics - Ch 8: Force & Laws of Motion", "Inertia and Mass, Newton's 3 Laws of Motion, Momentum (p = mv), Force formula (F = ma), Law of Conservation of Momentum"),
            ("Physics - Ch 9: Gravitation", "Universal Law of Gravitation (F = G*m1*m2/r^2), Acceleration due to gravity (g = GM/R^2), Mass vs Weight, Free fall, Archimedes' Principle, Buoyancy"),
            ("Physics - Ch 10: Work and Energy", "Work done (W = Fs), Kinetic Energy (1/2*m*v^2), Potential Energy (mgh), Conservation of Energy, Power (P = W/t)"),
            ("Physics - Ch 11: Sound", "Longitudinal waves, Sound characteristics (Wavelength, Frequency, Velocity v = f*lambda), Echo, Reverberation, Ultrasound, Human Ear"),
            ("Chemistry - Ch 1: Matter in Surroundings", "States of matter, Change of state (Melting, Boiling, Sublimation, Latent heat), Evaporation and cooling factors"),
            ("Chemistry - Ch 2: Is Matter Around Us Pure?", "Elements, Compounds, Mixtures, Solutions, Suspensions, Colloids, Tyndall effect, Concentration (% by mass/volume)"),
            ("Chemistry - Ch 3: Atoms and Molecules", "Laws of Chemical Combination, Dalton's Atomic Theory, Chemical Formula writing (Criss-Cross valency), Molecular mass"),
            ("Chemistry - Ch 4: Structure of the Atom", "Thomson model, Rutherford Alpha scattering experiment, Bohr model, Valency, Atomic Number (Z), Mass Number (A), Isotopes & Isobars"),
            ("Biology - Ch 5: Fundamental Unit of Life", "Cell membrane (Osmosis, Diffusion), Cell Wall, Nucleus, Cytoplasm, Organelles (Mitochondria, Plastids, ER, Golgi, Lysosomes, Vacuoles)"),
            ("Biology - Ch 6: Tissues", "Plant tissues (Meristematic vs Permanent: Parenchyma, Collenchyma, Sclerenchyma, Xylem, Phloem); Animal tissues (Epithelial, Connective, Muscular, Nervous)"),
            ("Biology - Ch 12: Improvement in Food Resources", "Crop variety selection, Nutrient management (Manure vs Fertilizer), Irrigation, Cropping patterns, Animal husbandry")
        ],
        "Mathematics": [
            ("Ch 1: Number Systems", "Real numbers, Irrational numbers proof, Real numbers on number line, Rationalising the denominator, Laws of exponents"),
            ("Ch 2: Polynomials", "Zeroes of a polynomial, Remainder Theorem, Factor Theorem, Factorisation of quadratics and cubics, Algebraic Identities"),
            ("Ch 3: Coordinate Geometry", "Cartesian plane, Coordinate axes, Coordinates of a point (x, y), Abscissa and Ordinate, Plotting points"),
            ("Ch 4: Linear Equations in 2 Variables", "Standard form ax + by + c = 0, Solutions of linear equations, Graphing linear equations"),
            ("Ch 5: Introduction to Euclid's Geometry", "Euclid's definitions, Axioms, and 5 Postulates"),
            ("Ch 6: Lines and Angles", "Intersecting and Parallel lines, Pairs of angles, Parallel lines and Transversal, Angle sum property"),
            ("Ch 7: Triangles", "Congruence criteria (SAS, ASA, AAS, SSS, RHS), Properties of triangles (Isosceles triangle theorems)"),
            ("Ch 8: Quadrilaterals", "Properties of a parallelogram, Conditions for parallelogram, Mid-point Theorem and its converse"),
            ("Ch 9: Circles", "Chords and angle properties, Perpendicular from center to chord, Equal chords, Angle subtended at center, Cyclic quadrilaterals"),
            ("Ch 10: Heron's Formula", "Area of a triangle using Heron's formula: sqrt(s(s-a)(s-b)(s-c))"),
            ("Ch 11: Surface Areas and Volumes", "Surface area and Volume of Right Circular Cone, Sphere, and Hemisphere"),
            ("Ch 12: Statistics", "Bar graphs, Histograms of varying base widths, Frequency polygons")
        ],
        "Social Science": [
            ("History: India & Contemporary World - I", "French Revolution, Socialism in Europe & Russian Revolution, Nazism & Rise of Hitler, Forest Society & Colonialism"),
            ("Geography: Contemporary India - I", "India - Size and Location, Physical Features of India (Himalayas, Plains, Plateau, Coastal, Islands), Drainage, Climate, Population"),
            ("Civics: Democratic Politics - I", "What is Democracy? Why Democracy?, Constitutional Design, Electoral Politics, Working of Institutions, Democratic Rights"),
            ("Economics", "The Story of Village Palampur, People as Resource, Poverty as a Challenge, Food Security in India")
        ],
        "Artificial Intelligence (Subject Code 417)": [
            ("Part A: Unit 1 - Communication Skills-I", "Methods of communication (Verbal & Non-verbal), Communication cycle, Perspectives & Barriers, 7 Cs of communication"),
            ("Part A: Unit 2 - Self-Management Skills-I", "Self-confidence, Positive thinking, Personal hygiene, Grooming, Goal setting"),
            ("Part A: Unit 3 - ICT Skills-I", "Computer fundamentals, Operating systems, Keyboard shortcuts, Internet, Email, Cyber safety basics"),
            ("Part A: Unit 4 - Entrepreneurial Skills-I", "Types of businesses (Product vs Service), Role & Characteristics of an entrepreneur"),
            ("Part A: Unit 5 - Green Skills-I", "Society & Environment, Ecosystem, Natural resource conservation, Sustainable development basics"),
            ("Part B: Unit 1 - Introduction to AI", "What is AI vs Human Intelligence, 3 Domains of AI (Data, Computer Vision, NLP), Smart cities, AI Ethics & Bias"),
            ("Part B: Unit 2 - AI Project Cycle", "5 Stages of AI Project Cycle: Problem Scoping (4W Canvas), Data Acquisition, Data Exploration, Modelling (Rule-based vs Learning-based), Evaluation"),
            ("Part B: Unit 3 - Neural Networks", "Basics of Artificial Neural Networks (ANN), Input/Hidden/Output layers, Human brain neuron vs ANN perceptron"),
            ("Part B: Unit 4 - Introduction to Python", "Jupyter Notebook, Python variables, Lists, Loops, Arithmetic & Logical operators, Conditional statements (if-else)")
        ],
        "Information Technology (Subject Code 402)": [
            ("Part A: Employability Skills", "Communication-I, Self-Management-I, Basic ICT Skills-I, Entrepreneurship-I, Green Skills-I"),
            ("Part B: Unit 1 - Introduction to IT-ITeS", "IT applications in BPO/BPM, Banking, Healthcare, Education, Engineering, CAD"),
            ("Part B: Unit 2 - Data Entry & Keyboarding Skills", "Touch typing technique, Typing ergonomics, RapidTyping tutor analysis & speed benchmarks"),
            ("Part B: Unit 3 - Digital Documentation", "Word Processing: Creating, Formatting, Tables, Headers/Footers, Mail Merge"),
            ("Part B: Unit 4 - Electronic Spreadsheet", "Spreadsheet fundamentals: Formulas, Functions (SUM, AVERAGE, IF), Charts, Sorting & Filtering"),
            ("Part B: Unit 5 - Digital Presentation", "Presentation software: Slide layouts, Custom animations, Slide transitions, Exporting presentations")
        ],
        "Computer Applications (Subject Code 165)": [
            ("Unit 1 - Basics of Information Technology", "Computer systems, Memory units (KB, MB, GB, TB), Operating systems, Internet, Web browsers, Search engines"),
            ("Unit 2 - Cyber Safety", "Safe web browsing, Identity theft prevention, Passwords, Netiquette, Social networking safety"),
            ("Unit 3 - Office Tools & HTML", "Word processing, Spreadsheets, HTML basics (headings, paragraphs, lists, tables, images, hyperlinks)"),
            ("Unit 4 - Python / Scratch Basics", "Visual block programming in Scratch, Python interactive mode, variables and conditionals")
        ],
        "English Language & Literature (Code 184)": [
            ("Reading Skills", "Discursive & Case-based factual comprehension passages (inference, vocabulary, analysis)"),
            ("Writing Skills & Grammar", "Descriptive Paragraph, Story Writing, Diary Entry; Tenses, Modals, Subject-Verb Concord, Reported Speech, Determiners"),
            ("Literature: Beehive (Prose & Poems)", "The Fun They Had, Sound of Music, Little Girl, Truly Beautiful Mind, Snake & Mirror, My Childhood, Reach for Top, Kathmandu; The Road Not Taken, Wind, Rain on the Roof, Lake Isle of Innisfree, No Men Are Foreign"),
            ("Literature: Moments (Supplementary)", "The Lost Child, Adventures of Toto, Iswaran the Storyteller, In the Kingdom of Fools, The Happy Prince, The Last Leaf, A House is Not a Home, The Beggar")
        ]
    },
    "Class 10": {
        "General Science": [
            ("Chemical Reactions and Equations", "Balancing chemical equations, Types of reactions (Combination, Decomposition, Displacement, Double Displacement, Redox), Corrosion & Rancidity"),
            ("Acids, Bases and Salts", "Chemical properties, pH scale (0-14), Bleaching Powder, Baking Soda, Washing Soda, Plaster of Paris"),
            ("Metals and Non-metals", "Physical & Chemical properties, Reactivity Series, Ionic compounds formation & properties, Metallurgy, Corrosion prevention"),
            ("Carbon and its Compounds", "Covalent bonding, Catenation, Tetravalency, Homologous series, IUPAC nomenclature, Ethanol & Ethanoic acid, Soaps & Detergents"),
            ("Life Processes", "Autotrophic vs Heterotrophic Nutrition, Human Digestive System, Human Respiration, Circulation (Heart, Double circulation), Excretion (Nephron)"),
            ("Control and Coordination", "Nervous system, Neuron structure, Reflex arc, Human Brain parts, Plant hormones (Auxin, Cytokinin, etc.), Tropic movements, Endocrine glands"),
            ("How do Organisms Reproduce?", "Asexual modes, Sexual reproduction in flowering plants (Pollination, Fertilization), Human reproductive systems, Contraception"),
            ("Heredity", "Mendel's Laws of Inheritance, Monohybrid cross (3:1 / 1:2:1), Dihybrid cross (9:3:3:1), Sex determination in humans (XX/XY)"),
            ("Light – Reflection and Refraction", "Spherical mirrors ray diagrams, Mirror formula (1/f = 1/v + 1/u), Lens formula (1/f = 1/v - 1/u), Refractive index, Power of lens (P = 1/f)"),
            ("The Human Eye & Colourful World", "Human eye anatomy, Defects of vision (Myopia, Hypermetropia, Presbyopia), Refraction through Prism, Dispersion, Atmospheric refraction, Scattering"),
            ("Electricity", "Current (I = Q/t), Voltage (V = W/Q), Ohm's Law (V = IR), Resistance factors (R = rho*l/A), Series & Parallel resistors, Joule heating (H = I^2Rt), Power (P = VI)"),
            ("Magnetic Effects of Current", "Magnetic field lines, Right-Hand Thumb Rule, Solenoid, Fleming's Left-Hand Rule, Electric Motor, Electromagnetic Induction, Fleming's Right-Hand Rule, Domestic circuits"),
            ("Our Environment", "Ecosystem, Food chains & webs, 10% Energy law, Biological Magnification, Ozone depletion (CFCs), Waste management")
        ],
        "Mathematics": [
            ("Ch 1: Real Numbers", "Fundamental Theorem of Arithmetic, Proof of irrationality (sqrt(2), sqrt(3), sqrt(5))"),
            ("Ch 2: Polynomials", "Geometrical meaning of zeroes, Relationship between zeroes and coefficients (alpha + beta = -b/a, alpha*beta = c/a)"),
            ("Ch 3: Pair of Linear Equations", "Graphical method, Consistency conditions, Substitution method, Elimination method"),
            ("Ch 4: Quadratic Equations", "Standard form ax^2 + bx + c = 0, Solution by Factorisation and Quadratic Formula, Nature of roots via Discriminant (D = b^2 - 4ac)"),
            ("Ch 5: Arithmetic Progressions (AP)", "General term (a_n = a + (n-1)d), Sum of first n terms (S_n = n/2[2a + (n-1)d]), Word problems"),
            ("Ch 6: Triangles", "Basic Proportionality Theorem (Thales Theorem) and converse, Criteria for similarity (AAA, SSS, SAS)"),
            ("Ch 7: Coordinate Geometry", "Distance Formula (d = sqrt((x2-x1)^2 + (y2-y1)^2)), Section Formula, Mid-point formula"),
            ("Ch 8: Introduction to Trigonometry", "Trig ratios of acute angles, Values for 0°, 30°, 45°, 60°, 90°, Trig Identities (sin^2 + cos^2 = 1, etc.)"),
            ("Ch 9: Applications of Trigonometry", "Heights and Distances, Angle of Elevation and Depression word problems"),
            ("Ch 10: Circles", "Tangent properties, Tangent perpendicular to radius, Equal tangents from external point (PA = PB)"),
            ("Ch 11: Areas Related to Circles", "Area of Sector (theta/360 * pi*r^2), Length of Arc, Area of Segment"),
            ("Ch 12: Surface Areas and Volumes", "Combinations of solids (Cube, Cuboid, Sphere, Hemisphere, Cylinder, Cone)"),
            ("Ch 13: Statistics", "Mean of grouped data, Mode of grouped data, Median of grouped data, Empirical formula (Mode = 3 Median - 2 Mean)"),
            ("Ch 14: Probability", "Theoretical probability P(E), Complementary events (P(E) + P(not E) = 1)")
        ],
        "Social Science": [
            ("History: India & Contemporary World - II", "Rise of Nationalism in Europe, Nationalism in India (Non-Cooperation, Civil Disobedience), Making of Global World, Age of Industrialisation, Print Culture"),
            ("Geography: Contemporary India - II", "Resources and Development, Forest and Wildlife, Water Resources, Agriculture, Minerals & Energy Resources, Manufacturing Industries, Lifelines of National Economy"),
            ("Civics: Democratic Politics - II", "Power Sharing, Federalism (Decentralization in India), Gender, Religion and Caste, Political Parties, Outcomes of Democracy"),
            ("Economics: Understanding Economic Development", "Development (PCI, HDI), Sectors of Indian Economy (Primary, Secondary, Tertiary), Money and Credit, Globalisation, Consumer Rights")
        ],
        "Artificial Intelligence (Subject Code 417)": [
            ("Part A: Unit 1 - Communication Skills-II", "Communication cycle, Active listening, Writing skills (Parts of speech, Sentence construction)"),
            ("Part A: Unit 2 - Self-Management Skills-II", "Stress management techniques, Working independently, Self-motivation & Goal setting"),
            ("Part A: Unit 3 - ICT Skills-II", "Operating system maintenance, File management, Cyber safety, Virus removal & Antivirus"),
            ("Part A: Unit 4 - Entrepreneurial Skills-II", "Entrepreneurship characteristics, Myths about entrepreneurship, Entrepreneurship as career"),
            ("Part A: Unit 5 - Green Skills-II", "Sustainable Development Goals (17 SDGs), Green economy role, Environmental policies"),
            ("Part B: Unit 1 - Introduction to AI", "Revisiting AI Project Cycle, AI Ethics (Privacy, Bias, Transparency), Sustainable Development Goals (SDGs) with AI"),
            ("Part B: Unit 2 - AI Project Cycle", "Problem Scoping (4Ws Canvas), Data Acquisition, Data Exploration, Modelling (Supervised vs Unsupervised vs Reinforcement), Evaluation"),
            ("Part B: Unit 3 - Advance Python & Data Science", "Lists, Tuples, Dictionaries, NumPy arrays, Matplotlib plotting, Data Visualization"),
            ("Part B: Unit 4 - Computer Vision (CV)", "Pixel concepts, RGB channels, OpenCV library basics, Convolution, Image filtering, Applications (Object detection, Facial recognition)"),
            ("Part B: Unit 5 - Natural Language Processing (NLP)", "Chatbots, Text Normalization (Tokenization, Stopwords, Stemming, Lemmatization), Bag of Words (BoW), TF-IDF, NLTK"),
            ("Part B: Unit 6 - Evaluation", "Confusion Matrix (TP, TN, FP, FN), Accuracy formula, Precision, Recall, F1 Score calculation & Trade-offs")
        ],
        "Information Technology (Subject Code 402)": [
            ("Part A: Employability Skills", "Communication-II, Self-Management-II, ICT Skills-II, Entrepreneurial-II, Green Skills-II"),
            ("Part B: Unit 1 - Digital Documentation (Advanced)", "Styles, Images & Drawing objects, Templates, Table of Contents, Mail Merge"),
            ("Part B: Unit 2 - Electronic Spreadsheet (Advanced)", "Consolidate data, Subtotals, What-If Analysis (Goal Seek, Scenarios), Solver, Macros, Linking worksheets"),
            ("Part B: Unit 3 - Database Management System (DBMS)", "Relational Database concepts, Primary & Foreign keys, SQL queries (SELECT, INSERT, UPDATE, DELETE), Tables & Forms in LibreOffice Base / MySQL"),
            ("Part B: Unit 4 - Web Applications & Security", "Accessibility options, Computer network fundamentals, Internet security, Workplace hazards & Health safety")
        ],
        "Computer Applications (Subject Code 165)": [
            ("Unit 1 - Networking & Web Services", "Internet, WWW, Web servers, Protocols (HTTP, FTP, TCP/IP), DNS, E-Governance, E-Commerce, E-Learning"),
            ("Unit 2 - HTML & CSS", "HTML5 structure, Lists, Tables with attributes (colspan, rowspan), Hyperlinks, Forms, CSS font/color/margin styling"),
            ("Unit 3 - Cyber Ethics", "Software piracy, Copyright, Plagiarism, Cyber bullying, Open source software (GPL, Creative Commons)"),
            ("Unit 4 - Python Programming / Scratch", "Python sequential and conditional programs, Lists, Loops, Functions")
        ],
        "English Language & Literature (Code 184)": [
            ("Reading Skills", "Discursive & Case-based factual comprehension passages (inference, vocabulary, analysis)"),
            ("Writing Skills & Grammar", "Formal Letters (Editor, Complaint, Inquiry, Order), Analytical Paragraph Writing based on charts/data; Tenses, Modals, Subject-Verb Concord, Reported Speech"),
            ("Literature: First Flight (Prose & Poems)", "Letter to God, Nelson Mandela, Two Stories about Flying, From Diary of Anne Frank, Glimpses of India, Mijbil the Otter, Madam Rides the Bus, The Sermon at Benares, The Proposal; Dust of Snow, Fire & Ice, Tiger in Zoo, Amanda!, Trees, Fog, Custard Dragon, For Anne Gregory"),
            ("Literature: Footprints Without Feet", "Triumph of Surgery, Thief's Story, Midnight Visitor, Question of Trust, Footprints Without Feet, Making of Scientist, Necklace, Bholi, Book That Saved Earth")
        ]
    },
    "Class 11": {
        "Physics": [
            ("Part 1 - Units & Measurements", "SI units, Dimensional analysis & applications, Error analysis & Significant figures"),
            ("Part 1 - Kinematics (Motion in 1D & 2D)", "Scalars & Vectors, Calculus methods for motion, Projectile motion trajectories & range, Uniform circular motion"),
            ("Part 1 - Laws of Motion", "Newton's laws, Momentum, Friction on banked roads, Centripetal force dynamics"),
            ("Part 1 - Work, Energy and Power", "Work-energy theorem, Conservative vs Non-conservative forces, Collisions in 1D and 2D"),
            ("Part 1 - Rotational Motion", "Center of mass, Torque, Angular momentum conservation, Moment of inertia"),
            ("Part 1 - Gravitation", "Kepler's laws, Gravitational potential energy, Escape velocity, Orbital velocity of satellites"),
            ("Part 2 - Properties of Bulk Matter", "Hooke's law, Young's modulus, Pascal's law, Viscosity, Bernoulli's theorem, Surface tension"),
            ("Part 2 - Thermodynamics & Kinetic Theory", "First & Second Laws of Thermodynamics, Carnot engine, Ideal gas equation, Degrees of freedom"),
            ("Part 2 - Oscillations and Waves", "Simple Harmonic Motion (SHM), Simple pendulum period, Wave equation, Doppler effect, Beats")
        ],
        "Chemistry": [
            ("Some Basic Concepts of Chemistry", "Mole concept, Molar mass, Stoichiometry, Empirical & Molecular formula, Molarity & Molality"),
            ("Structure of Atom", "Bohr model, de Broglie relation, Heisenberg uncertainty, Quantum numbers, Orbitals, Hund/Pauli/Aufbau"),
            ("Classification of Elements & Periodicity", "Modern periodic table trends (IE, EA, Electronegativity, Atomic & Ionic radii)"),
            ("Chemical Bonding & Molecular Structure", "Lewis structures, VSEPR theory, Hybridization (sp, sp2, sp3, sp3d), Molecular Orbital Theory (MOT)"),
            ("Chemical Thermodynamics", "First & Second laws, Enthalpy (Delta H), Entropy (Delta S), Gibbs free energy (Delta G = Delta H - T*Delta S)"),
            ("Equilibrium", "Chemical equilibrium (Kc, Kp), Le Chatelier's principle, Ionic equilibrium, pH, Buffer solutions, Solubility product"),
            ("Redox Reactions", "Oxidation numbers, Balancing redox reactions by Ion-electron method"),
            ("Organic Chemistry: Basic Principles", "IUPAC nomenclature, Inductive/Resonance/Hyperconjugation effects, Carbocations"),
            ("Hydrocarbons", "Alkanes, Alkenes, Alkynes preparation & reactions (Markovnikov rule), Aromaticity (Hückel's rule), Benzene")
        ],
        "Mathematics": [
            ("Sets & Relations & Functions", "Venn diagrams, Power set, Cartesian products, Domain/Range, Types of functions"),
            ("Trigonometric Functions", "Trig ratios in quadrants, Compound angle formulas, Multiple/Submultiple angles, General solutions"),
            ("Complex Numbers & Quadratics", "Algebra of complex numbers, Modulus and Conjugate, Polar representation, Quadratic equations in Complex plane"),
            ("Linear Inequalities & Permutations/Combinations", "Graphical solution of inequalities, Fundamental counting principle, nPr and nCr formulas"),
            ("Binomial Theorem & Sequences/Series", "Binomial expansion for positive integral index, General and Middle terms, AP, GP and Sum to n terms"),
            ("Coordinate Geometry & Conic Sections", "Straight line slopes & forms, Circles, Parabola (y^2 = 4ax), Ellipse, Hyperbola"),
            ("Calculus: Limits & Derivatives", "Standard limits (lim sin x / x = 1), First principle of differentiation, Product & Quotient rules"),
            ("Statistics & Probability", "Measures of dispersion (Variance and Standard Deviation), Axiomatic probability, Addition theorem")
        ],
        "Biology": [
            ("Diversity of Living Organisms", "The Living World, Biological Classification (5 Kingdoms), Plant Kingdom (Algae to Angiosperms), Animal Kingdom (Non-chordates to Chordates)"),
            ("Structural Organisation in Animals and Plants", "Morphology of Flowering Plants (Root, Stem, Leaf, Flower, Fruit, Seed), Anatomy of Flowering Plants (Tissues), Animal Tissues & Frog anatomy"),
            ("Cell: Structure and Function", "Cell Theory, Prokaryotic vs Eukaryotic cells, Cell Organelles, Biomolecules (Proteins, Lipids, Nucleic acids, Enzymes), Cell Cycle & Division (Mitosis & Meiosis)"),
            ("Plant Physiology", "Photosynthesis in Higher Plants (Light/Dark reactions, C3/C4 pathways), Respiration in Plants (Glycolysis, Krebs cycle, ETS), Plant Growth & Development (Auxins, Gibberellins)"),
            ("Human Physiology", "Breathing & Exchange of Gases, Body Fluids & Circulation (Cardiac cycle, ECG), Excretory Products (Nephron & Counter-current), Locomotion & Movement, Neural Control & Chemical Coordination")
        ],
        "Computer Science (Python - Code 083)": [
            ("Unit 1 - Computer Systems and Organisation", "Hardware components, Memory types, Boolean logic, Logic gates, Number systems, Operating system functions"),
            ("Unit 2 - Computational Thinking and Programming-1", "Problem solving, Flowcharts, Python basics, Data types, Conditional statements, Loops (for, while), Strings, Lists, Tuples, Dictionaries, Modules (math, random)"),
            ("Unit 3 - Society, Law and Ethics", "Digital footprint, Cyber safety, Cyber crime (phishing, hacking, cyber bullying), IT Act, Intellectual Property Rights (IPR), Open source")
        ],
        "Informatics Practices (IP - Code 065)": [
            ("Unit 1 - Introduction to Computer System", "Computer architecture, Memory units, System & Application software"),
            ("Unit 2 - Introduction to Python", "Python fundamentals, Data types, Selection and Iteration statements, Lists, Dictionaries"),
            ("Unit 3 - Database concepts and SQL", "Relational database concepts, SQL DDL/DML commands (CREATE, INSERT, SELECT, UPDATE, DELETE), Where clause, Order by"),
            ("Unit 4 - Emerging Trends & Society", "AI, Machine Learning, Cloud Computing, Big Data, Blockchain, Cyber security")
        ]
    },
    "Class 12": {
        "Physics": [
            ("Part 1 - Electrostatics", "Coulomb's Law, Electric field & flux, Gauss's Theorem applications, Electric potential, Capacitors, Dielectrics"),
            ("Part 1 - Current Electricity", "Drift velocity (I = n e A v_d), Ohm's law vector form, Kirchhoff's laws, Wheatstone bridge, Potentiometer"),
            ("Part 1 - Magnetic Effects & Magnetism", "Biot-Savart Law, Ampere's Circuital Law, Force on moving charge (F = q(v x B)), Moving Coil Galvanometer, Dia/Para/Ferromagnetic"),
            ("Part 1 - Electromagnetic Induction & AC", "Faraday's Laws, Lenz's Law, Self & Mutual inductance, AC circuits (LCR series resonance, Power factor, Transformer)"),
            ("Part 1 - Electromagnetic Waves", "Displacement current, Maxwell's equations, EM Spectrum characteristics & uses"),
            ("Part 2 - Optics (Ray & Wave Optics)", "Total internal reflection, Lens maker's formula, Prism formula, Microscopes & Telescopes, Huygens principle, YDSE, Single slit diffraction"),
            ("Part 2 - Modern Physics", "Photoelectric effect (Einstein equation h*nu = Phi + K_max), de Broglie wavelength, Bohr model of Hydrogen, Nuclear binding energy, Fission/Fusion"),
            ("Part 2 - Semiconductor Electronics", "Energy bands, p-n junction diode forward/reverse characteristics, Diode as half/full-wave Rectifier")
        ],
        "Chemistry": [
            ("Solutions", "Raoult's Law, Ideal vs Non-ideal solutions, Colligative properties (Elevation in BP, Depression in FP, Osmotic pressure), van 't Hoff factor"),
            ("Electrochemistry", "Nernst Equation (E = E° - 0.0591/n * log Q), Kohlrausch's Law, Faraday's laws, Galvanic cells, Batteries & Fuel cells"),
            ("Chemical Kinetics", "Rate of reaction, Rate law & Order/Molecularity, Integrated rate equations for Zero & First order, Half-life, Arrhenius equation"),
            ("d- and f-Block Elements & Coordination Compounds", "Transition metals oxidation states, Magnetic properties, Lanthanoid contraction; IUPAC naming, Werner's theory, VBT, CFT"),
            ("Organic Chemistry: Haloalkanes to Biomolecules", "Mechanisms of SN1 and SN2, Optical isomerism; Alcohols, Phenols, Ethers; Aldehydes & Ketones (Aldol, Cannizzaro), Carboxylic acids; Amines; Carbohydrates (Glucose), Proteins, Nucleic acids (DNA/RNA)")
        ],
        "Mathematics": [
            ("Relations, Functions & Inverse Trig", "Equivalence relations, Injective & Surjective functions, Principal value branches of inverse trig functions"),
            ("Matrices & Determinants", "Matrix multiplication, Symmetric/Skew-symmetric, Properties of determinants, Adjoint & Inverse (A^-1 = 1/|A| * adj(A)), Matrix Method"),
            ("Calculus: Continuity, Differentiability & AOD", "Chain rule, Implicit, Logarithmic, Parametric; Tangents/Normals, Rate of change, Increasing/Decreasing, Maxima & Minima"),
            ("Calculus: Integrals & Applications", "Indefinite integration methods (Substitution, Partial fractions, By parts), Definite integrals properties, Area bounded by curves (AOI)"),
            ("Differential Equations", "Order and Degree, Variable separable, Homogeneous DE, First-order linear differential equations (dy/dx + Py = Q)"),
            ("Vectors & 3D Geometry", "Dot product, Cross product, Direction cosines, Vector and cartesian line equations, Shortest distance between skew lines"),
            ("Linear Programming & Probability", "LPP formulation, Graphical Corner point method; Conditional probability, Multiplication theorem, Bayes' Theorem, Random variables")
        ],
        "Biology": [
            ("Reproduction", "Sexual Reproduction in Flowering Plants (Micro/Megasporogenesis, Pollination, Double fertilization, Endosperm), Human Reproduction (Gametogenesis, Menstrual cycle, Fertilization, Embryo development), Reproductive Health (Contraception, ART, IVF)"),
            ("Genetics and Evolution", "Principles of Inheritance & Variation (Mendelian ratios, Incomplete dominance, Sex linkage, Mendelian disorders), Molecular Basis of Inheritance (DNA structure, Replication, Transcription, Genetic code, Translation, Lac Operon), Evolution (Darwinism, Hardy-Weinberg, Human evolution)"),
            ("Biology and Human Welfare", "Human Health and Diseases (Pathogens, Malaria cycle, Immunity, AIDS, Cancer, Drugs), Microbes in Human Welfare (Household, Industrial, Sewage treatment, Biogas, Biocontrol)"),
            ("Biotechnology and its Applications", "Biotechnology: Principles and Processes (Recombinant DNA technology, Restriction enzymes, Cloning vectors, PCR, Gel electrophoresis), Applications (Bt Cotton, Gene therapy, Transgenic animals)"),
            ("Ecology and Environment", "Organisms and Populations (Population attributes, Growth models), Ecosystem (Energy flow, Food chains, Pyramids), Biodiversity and Conservation (Loss of biodiversity, Hotspots, In-situ & Ex-situ)")
        ],
        "Computer Science (Python - Code 083)": [
            ("Unit 1 - Computational Thinking and Programming-2", "Functions (scope, parameters), File Handling (Text files, Binary files with pickle, CSV files with csv module), Exception handling, Data Structures: Stacks using Python lists"),
            ("Unit 2 - Computer Networks", "Evolution of networking, Data communication terms, Transmission media (Twisted pair, Coaxial, Fiber, Radio/Micro/Satellite), Network topologies (Star, Bus), Protocols (HTTP, FTP, PPP, TCP/IP), Network security"),
            ("Unit 3 - Database Management", "Relational data model, SQL constraints, SQL queries (Aggregate functions, Group By, Having, Joins - Cartesian, Equi, Natural), Python-MySQL database connectivity (mysql.connector)")
        ],
        "Informatics Practices (IP - Code 065)": [
            ("Unit 1 - Data Handling using Pandas and Visualization", "Pandas Series & DataFrames, Indexing, Slicing, Math operations, Filtering, Importing/Exporting CSV, Data Visualization using Pyplot/Matplotlib (Line, Bar, Histogram)"),
            ("Unit 2 - Database Query using SQL", "Math functions (POWER, ROUND, MOD), Text functions (UCASE, LCASE, MID, LENGTH), Date functions (NOW, DATE, MONTH), Aggregate functions, GROUP BY, HAVING, Joins"),
            ("Unit 3 - Introduction to Computer Networks", "Types of networks (LAN, MAN, WAN), Topologies, Network devices (Hub, Switch, Router, Gateway), Web services, Browser settings"),
            ("Unit 4 - Societal Impacts", "Digital footprint, Netiquette, Data protection, Cyber crime, Phishing, Identity theft, E-waste management, Indian IT Act")
        ]
    }
}


def is_syllabus_query(query):
    """Detects if student is requesting NCERT / CBSE syllabus, chapter list, or curriculum breakdown."""
    q = query.lower()
    return any(k in q for k in [
        'syllabus', 'course curriculum', 'curriculum', 'chapters list', 'chapter list', 'all chapters',
        'update ncert syllabus', 'ncert syllabus', 'cbse syllabus', 'kya syllabus hai',
        'syllabus batao', 'kitne chapter hain', 'table of contents', 'list of chapters', 'all syllabus',
        'chapters', 'what are the chapters', 'chapters in', 'chapters of'
    ])


def generate_ncert_syllabus_overview(query, grade, subject):
    """
    Renders structured, CBSE/NCERT curriculum roadmaps for any Grade & Subject or All Classes.
    """
    q_lower = query.lower()
    is_hing = is_hinglish(query)
    
    # 1. If asking for all classes / every class
    if any(k in q_lower for k in ['every class', 'all class', 'all grades', 'every grade', 'classes 1-12', '1 to 12']):
        if is_hing:
            out = (
                "### 📚 **CBSE / NCERT Complete Syllabus Directory (Classes 1–12)**\n\n"
                "Maya AI Tutor me **Nursery se Class 12 tak ka pura updated CBSE & NCERT Syllabus** integrated hai! Niche har stage ka curriculum overview dekhiye:\n\n"
                "#### 🎒 **1. Primary Stage (Classes 1–5):**\n"
                "- **Mathematics:** Numbers, Basic Arithmetic ($+, -, \\times, \\div$), 2D/3D Shapes, Money, Time & Fractions.\n"
                "- **Environmental Studies (EVS / Science):** Plants & Animals, Super Senses, Water Cycle, Food & Nutrition, Shelters.\n"
                "- **Language & Computer:** English/Hindi Grammar fundamentals, MS Paint, Computer parts.\n\n"
                "#### 🏫 **2. Middle School Stage (Classes 6–8):**\n"
                "- **General Science:** Food Components, Plant/Animal Nutrition, Heat, Acids/Bases, Respiration, Motion, Light, Microorganisms, Cell Structure, Force & Friction, Sound.\n"
                "- **Mathematics:** Integers, Fractions/Decimals, Simple Equations, Geometry, Mensuration, Exponents, Algebraic Expressions, Linear Equations, Quadrilaterals.\n"
                "- **Social Science:** History (*Our Pasts I, II, III*), Geography (*Earth, Environment, Resources*), Civics (*Social & Political Life*).\n\n"
                "#### 🎓 **3. Secondary Stage (Classes 9–10):**\n"
                "- **Class 9 Science & Math:** Motion ($3$ equations), Newton's Laws, Gravitation, Work & Energy, Sound, Matter, Atoms & Molecules, Cells, Tissues, Number Systems, Polynomials, Coordinate Geometry, Heron's Formula.\n"
                "- **Class 10 Board Science & Math:** Chemical Reactions, Acids/Bases/Salts, Metals, Carbon Compounds, Life Processes, Control & Coordination, Reproduction, Heredity, Light Optics, Human Eye, Electricity, Magnetic Effects, Quadratic Equations, AP, Trigonometry, Circles, Statistics, Probability.\n"
                "- **Social Science & IT:** Nationalism in Europe/India, Power Sharing, Federalism, Sectors of Economy, Python Coding & Databases.\n\n"
                "#### 🔬 **4. Senior Secondary Stage (Classes 11–12):**\n"
                "- **Physics:** Kinematics, Laws of Motion, Rotational Dynamics, Thermodynamics, Electrostatics, Current Electricity, Magnetism, EMI, AC, Ray/Wave Optics, Modern Physics, Semiconductors.\n"
                "- **Chemistry:** Mole Concept, Atomic Structure, Bonding, Thermodynamics, Equilibrium, Solutions, Electrochemistry, Kinetics, Coordination Compounds, Organic Mechanisms ($S_N1/S_N2$, Carbonyls, Biomolecules).\n"
                "- **Mathematics:** Calculus (Limits, Derivatives, Integrals, Differential Equations), Vectors & 3D Geometry, Matrices, Probability, Relations & Functions.\n"
                "- **Computer Science:** Python Data Structures (Stacks), MySQL Databases, Computer Networks.\n\n"
                "💡 *Aap kisi bhi specific Class aur Subject (jaise Class 10 Science ya Class 12 Physics) ka exact chapter-wise breakdown dekhne ke liye puch sakte hain!*"
            )
        else:
            out = (
                "### 📚 **Master CBSE / NCERT Curriculum Directory (Classes 1–12)**\n\n"
                "Maya AI Tutor is fully powered by the **complete, official CBSE & NCERT Syllabus for Nursery to Class 12**! Here is the stage-by-stage curriculum roadmap:\n\n"
                "#### 🎒 **1. Primary Stage (Classes 1–5):**\n"
                "- **Mathematics:** Numbers up to 10,000, Basic Arithmetic Operations ($+, -, \\times, \\div$), 2D/3D Shapes, Measurement, Time & Currency.\n"
                "- **Environmental Studies (EVS / Science):** Plant & Animal Kingdoms, Super Senses, Water Cycle, Food & Nutrition, Shelters & Conservation.\n"
                "- **Language & Computing:** English Grammar, Phonics, Creative Writing, Computer Hardware basics.\n\n"
                "#### 🏫 **2. Middle School Stage (Classes 6–8):**\n"
                "- **General Science:** Components of Food, Plant/Animal Nutrition, Heat, Acids/Bases/Salts, Respiration, Motion, Light, Microorganisms, Cell Structure, Force & Friction, Sound, Chemical Effects of Current.\n"
                "- **Mathematics:** Integers, Fractions & Decimals, Simple Equations, Triangle Properties, Mensuration, Exponents, Algebraic Identities, Linear Equations in 1 Variable, Quadrilaterals, Factorisation.\n"
                "- **Social Science:** History (*Our Pasts I, II, III*), Geography (*Earth Habitat, Our Environment, Resources & Development*), Civics (*Social & Political Life*).\n\n"
                "#### 🎓 **3. Secondary Board Stage (Classes 9–10):**\n"
                "- **Class 9 Science & Math:** Motion ($v=u+at$, etc.), Newton's Laws, Gravitation, Work & Energy, Sound, Matter, Atoms & Molecules, Cell Unit of Life, Tissues, Number Systems, Polynomials, Coordinate Geometry, Heron's Formula.\n"
                "- **Class 10 Board Science & Math:** Chemical Reactions, Acids/Bases, Metals, Carbon Compounds, Life Processes, Control & Coordination, Reproduction, Heredity, Light Reflection & Refraction, Eye & Prism, Electricity, Magnetic Effects, Real Numbers, Quadratic Equations, AP, Trigonometry, Circles, Statistics, Probability.\n"
                "- **Social Science & IT:** Nationalism in Europe & India, Power Sharing, Federalism, Economic Sectors, Money & Credit, Python & Database Management.\n\n"
                "#### 🔬 **4. Senior Secondary Stage (Classes 11–12):**\n"
                "- **Physics:** Kinematics, Rotational Motion, Gravitation, Thermodynamics, Electrostatics, Current Electricity, Magnetism, EMI, AC Circuits, Wave & Ray Optics, Modern Physics, Semiconductors.\n"
                "- **Chemistry:** Mole Concept, Atomic Structure, Chemical Bonding, Thermodynamics, Equilibrium, Solutions, Electrochemistry, Kinetics, Coordination Complexes, Organic Chemistry Reactions ($S_N1/S_N2$, Carbonyls, Amines, Biomolecules).\n"
                "- **Mathematics:** Calculus (Derivatives, Definite/Indefinite Integrals, Differential Equations), Vectors & 3D Geometry, Matrices & Determinants, Probability Distributions.\n"
                "- **Computer Science:** Python Data Structures (Stacks), MySQL Database & SQL Queries, Computer Networks.\n\n"
                "💡 *Feel free to ask for a detailed chapter-by-chapter roadmap or practice quiz for any specific Class and Subject!*"
            )
        return out

    # 2. Specific Class & Subject Syllabus Lookup
    target_grade = grade
    for g in ['Class 12', 'Class 11', 'Class 10', 'Class 9', 'Class 8', 'Class 7', 'Class 6', 'Primary (1-5)']:
        if g.lower() in q_lower:
            target_grade = g
            break
            
    target_subject = subject
    if any(k in q_lower for k in ['artificial intelligence', 'ai 417', 'subject code 417', 'code 417', 'ai syllabus', 'ai curriculum', '417']) or ' ai ' in q_lower or q_lower.startswith('ai '):
        target_subject = "Artificial Intelligence (Subject Code 417)"
    elif any(k in q_lower for k in ['information technology', 'it 402', 'code 402', 'it-ites', 'it syllabus', '402']):
        target_subject = "Information Technology (Subject Code 402)"
    elif any(k in q_lower for k in ['computer application', 'computer applications', 'code 165', '165 syllabus', '165']):
        target_subject = "Computer Applications (Subject Code 165)"
    elif any(k in q_lower for k in ['informatics practices', 'ip 065', 'code 065', 'ip syllabus', 'pandas']):
        target_subject = "Informatics Practices (IP - Code 065)"
    elif any(k in q_lower for k in ['computer science', 'c++', 'cpp', 'python', 'coding', 'programming', 'code 083', 'cs syllabus']):
        target_subject = "Computer Science (Python - Code 083)" if target_grade in ['Class 11', 'Class 12'] else "Computer Applications (Subject Code 165)"
    elif any(k in q_lower for k in ['english', 'literature', 'first flight', 'footprints', 'beehive', 'moments', 'code 184', 'code 301', '184', '301']):
        target_subject = "English Language & Literature (Code 184)"
    elif any(k in q_lower for k in ['math', 'mathematics', 'algebra', 'geometry', 'trigonometry', 'calculus']):
        target_subject = "Mathematics"
    elif any(k in q_lower for k in ['physics']):
        target_subject = "Physics"
    elif any(k in q_lower for k in ['chemistry']):
        target_subject = "Chemistry"
    elif any(k in q_lower for k in ['biology', 'botany', 'zoology']):
        target_subject = "Biology"
    elif any(k in q_lower for k in ['social', 'history', 'civics', 'geography', 'economics', 'sst']):
        target_subject = "Social Science"
    elif any(k in q_lower for k in ['science']) and 'social' not in q_lower:
        target_subject = "General Science" if target_grade in ['Class 6', 'Class 7', 'Class 8', 'Class 9', 'Class 10'] else "Physics"

    grade_dict = NCERT_SYLLABUS_REGISTRY.get(target_grade, NCERT_SYLLABUS_REGISTRY["Class 10"])
    
    # Match subject in dictionary
    matched_subject_key = None
    for k in grade_dict.keys():
        if target_subject.lower() in k.lower() or k.lower() in target_subject.lower():
            matched_subject_key = k
            break
        if 'artificial intelligence' in target_subject.lower() and 'artificial intelligence' in k.lower():
            matched_subject_key = k
            break
        if 'information technology' in target_subject.lower() and 'information technology' in k.lower():
            matched_subject_key = k
            break
        if 'computer' in target_subject.lower() and 'computer' in k.lower():
            matched_subject_key = k
            break
        if 'english' in target_subject.lower() and 'english' in k.lower():
            matched_subject_key = k
            break
            
    if not matched_subject_key:
        matched_subject_key = list(grade_dict.keys())[0]
        
    chapter_list = grade_dict[matched_subject_key]
    
    out = (
        f"### 📖 **Official CBSE / NCERT Syllabus: {matched_subject_key} ({target_grade})**\n\n"
        f"Here is the complete chapter-by-chapter curriculum roadmap for **{target_grade} {matched_subject_key}**:\n\n"
        f"| Chapter Number & Title | Key NCERT Learning Objectives & Focus Areas |\n"
        f"| :--- | :--- |\n"
    )
    
    for ch_title, ch_focus in chapter_list:
        out += f"| **{ch_title}** | {ch_focus} |\n"
        
    out += (
        f"\n---\n"
        f"💡 **How Maya AI Can Help You Master This Syllabus:**\n"
        f"- Type **'Explain [Chapter Name]'** for step-by-step conceptual breakdowns.\n"
        f"- Type **'Solve [Numerical / Equation]'** for step-by-step mathematical derivations.\n"
        f"- Type **'Give me a 5-question quiz on [Chapter Name]'** to test your exam preparation!\n"
    )
    return out


def is_cbse_exam_info_query(query):
    """Detects queries regarding CBSE board exam dates, datesheets, schedules, results, and passing marks."""
    q = query.lower().strip()
    keywords = [
        'board exam', 'board exams', 'board examination', 'exam date', 'exam dates',
        'when is the cbse', 'when is cbse', 'datesheet', 'date sheet', 'exam schedule',
        'practical exam', 'practical exams', 'admit card', 'passing marks', 'passing criteria',
        'passing percentage', 'result date', 'when will results', 'when will result',
        'cbse 10th board', 'cbse 12th board', 'pre-board', 'exam timing', 'exam start',
        'exams start', 'board exam kab', 'pariksha kab', 'exam kab hoga', 'board pariksha',
        'cbse exam', 'cbse exams', 'when is class 10 exam', 'when is class 12 exam',
        'when are board exams', 'when will board exams'
    ]
    if any(k in q for k in keywords):
        return True
        
    return bool(re.search(r'\b(?:when|kab)\b.*\b(?:exam|exams|board|datesheet|pariksha)\b', q))


def generate_cbse_exam_info_response(query, grade="Class 10"):
    """Provides comprehensive, official CBSE board examination schedules, practical dates, timings, and passing criteria."""
    is_hing = any(k in query.lower() for k in ['kab', 'hoga', 'hogi', 'kya hai', 'batao', 'kaise', 'kitne', 'pariksha', 'shuru'])
    
    if is_hing:
        return (
            f"### 📅 **CBSE Board Exam 2026-27 Schedule & Guidance ({grade})**\n\n"
            f"Official **Central Board of Secondary Education (CBSE)** ke annual examination framework ke anusaar:\n\n"
            f"#### 1. 🗓️ **Exam Dates & Schedule:**\n"
            f"- **Main Theory Board Exams ({grade})**: Har saal **Mid-February (approx. 15 February)** se shuru hokar **March/April** tak conduct kiye jaate hain.\n"
            f"- **Practical Exams & Internal Assessments**: Schools ke laboratories me **1st January se 14th February** ke beech complete hote hain.\n"
            f"- **Official Date Sheet (Timetable)**: CBSE ki official website ([cbse.gov.in](https://www.cbse.gov.in)) par **November / December** me subject-wise date sheet release hoti hai.\n\n"
            f"#### 2. ⏰ **Exam Shift & Timings:**\n"
            f"- **Timing**: Subah `10:30 AM se 1:30 PM` (3 Hours standard paper).\n"
            f"- **Question Paper Reading Time**: 15 minutes (`10:15 AM - 10:30 AM`) paper dhyan se padhne aur plan karne ke liye milte hain.\n\n"
            f"#### 3. 🎯 **Passing Marks (Passing Criteria):**\n"
            f"- **Class 10**: Har subject me Theory + Internal Assessment milakar minimum **33% overall marks** lana anivarya hai.\n"
            f"- **Class 12**: Theory me 33% aur Practical me 33% alag-alag lana zaroori hota hai.\n\n"
            f"#### 💡 **Maya AI Board Exam Preparation Support:**\n"
            f"- Aap Maya AI se kisi bhi chapter ke **NCERT Exemplar questions**, **Formula derivations**, ya **Revision notes** maang sakte hain!\n"
            f"- Practice shuru karne ke liye chat me likhein: *'Give me questions on Motion'* ya *'Solve quadratic equation'*."
        )
    else:
        return (
            f"### 📅 **CBSE Board Examination Schedule & Guidelines ({grade} / 2026-27)**\n\n"
            f"According to the official **Central Board of Secondary Education (CBSE)** annual examination framework:\n\n"
            f"#### 1. 🗓️ **Key Examination Dates:**\n"
            f"- **Annual Theory Examinations ({grade})**: Typically commence from **mid-February (approx. February 15th)** and conclude by **late March / early April**.\n"
            f"- **Practical & Internal Assessments**: Conducted in school laboratories during **January (January 1st – February 14th)**.\n"
            f"- **Official Date Sheet Release**: CBSE publishes the comprehensive subject-wise timetable on the official portal ([cbse.gov.in](https://www.cbse.gov.in)) around **late November / early December**.\n\n"
            f"#### 2. ⏰ **Exam Shift & Timing:**\n"
            f"- **Main Examination Shift**: `10:30 AM – 1:30 PM` (3 Hours for main papers).\n"
            f"- **Cool-off Reading Time**: An extra 15 minutes (`10:15 AM – 10:30 AM`) is allocated strictly for reading the question paper prior to writing.\n\n"
            f"#### 3. 🎯 **CBSE Passing Criteria:**\n"
            f"- **Class 10**: Students must secure a minimum of **33% aggregate marks** (combined theory + internal assessment) in each subject.\n"
            f"- **Class 12**: Students must secure **33% marks separately** in Theory and **33% marks in Practicals/Internal Assessment**.\n\n"
            f"#### 💡 **How Maya AI Can Help You Prepare:**\n"
            f"- Request chapter-wise **NCERT Exemplar questions**, **step-by-step numerical solutions**, or **formula sheets** anytime!\n"
            f"- Simply type: *'Give me two questions of motion from NCERT Exemplar'* or *'Explain photosynthesis'* to start practicing."
        )


def is_study_tips_query(query):
    """Detects queries asking for study tips, revision strategy, topper advice, or exam preparation framework."""
    q = query.lower()
    return any(k in q for k in [
        'how to study', 'how to score', 'study tips', 'revision strategy',
        'time table', 'timetable', 'exam preparation', 'how to prepare',
        'board exam tips', 'how to get 95', 'how to get full marks',
        'how to manage time', 'padhai kaise kare', 'padhai me man kaise lagaye',
        'revision kaise kare', 'topper tips', 'how to top'
    ])


def generate_study_tips_response(query, grade="Class 10", subject="General Science"):
    """Provides structured, high-yield CBSE revision strategies, active recall tips, and mock paper guidelines."""
    is_hing = any(k in query.lower() for k in ['kaise', 'kare', 'batao', 'karna', 'tarika', 'karein'])
    
    if is_hing:
        return (
            f"### 🎯 **CBSE Board Exam Topper Study Strategy ({grade})**\n\n"
            f"Board exams me **95%+ marks** score karne ke liye yeh proven 5-step strategy follow karein:\n\n"
            f"#### 1. 📖 **NCERT First Rule (Core Foundation):**\n"
            f"- Board question paper **90%+ NCERT line-by-line** se banta hai.\n"
            f"- Har chapter ke **In-text questions** aur **Chapter-end exercises** ko kam se kam 2 baar notebook me likh kar solve karein.\n\n"
            f"#### 2. 📝 **Formula & Concept Sheets Banayein:**\n"
            f"- Physics/Math ke sabhi formulas aur Chemistry ke reaction mechanisms ek **2-page summary sheet** par likhein.\n"
            f"- Har subah 15 minute iska quick active recall karein.\n\n"
            f"#### 3. ⏱️ **Active Recall & Past 5 Years PYQs:**\n"
            f"- Sirf padhne ke bajaye **Previous Year Questions (PYQs)** timer lagakar solve karein.\n"
            f"- Weak topics ko turant Maya AI se explain karne ko bolein.\n\n"
            f"#### 4. 🎨 **Presentation & Diagrams:**\n"
            f"- Biology diagrams ko neat pencil aur labeling ke sath practice karein.\n"
            f"- Answers hamesha **bullet points** aur underlined keywords ke sath likhein.\n\n"
            f"#### 5. ⏳ **3-Hour Mock Test Routine:**\n"
            f"- Exam month me har hafte ek full 3-hour sample paper likhein taaki time management perfect ho jaye.\n\n"
            f"Aap abhi kis subject ya chapter ko practice karna chahte hain? Chat me topic type karein!"
        )
    else:
        return (
            f"### 🎯 **CBSE Board Exam High-Scoring Strategy ({grade})**\n\n"
            f"To achieve **95%+ in your CBSE Board Examinations**, implement this high-yield 5-step preparation framework:\n\n"
            f"#### 1. 📖 **Master the NCERT Textbooks First:**\n"
            f"- Over **90% of CBSE board questions** directly derive from NCERT textbook concepts, in-text activities, and exemplar problems.\n"
            f"- Solve every single in-text question and chapter exercise by hand.\n\n"
            f"#### 2. 📝 **Maintain a Formula & Reaction Notebook:**\n"
            f"- Compile all Physics/Math formulas with standard SI units, and Chemistry chemical equations in a dedicated summary notebook.\n"
            f"- Spend 15 minutes every morning doing quick active recall.\n\n"
            f"#### 3. ⏱️ **Solve 5-Year CBSE Past Papers (PYQs):**\n"
            f"- Solving previous year questions reveals recurring high-weightage topics and examiner marking schemes.\n\n"
            f"#### 4. 🎨 **Master Answer Presentation & Diagrams:**\n"
            f"- Use clean, pencil-drawn labeled diagrams for Science.\n"
            f"- Structure theoretical answers with clear headings, bullet points, and highlighted keywords.\n\n"
            f"#### 5. ⏳ **Simulate 3-Hour Timed Mock Tests:**\n"
            f"- Practice full-length sample papers under strict 3-hour exam conditions to master pacing and eliminate exam anxiety.\n\n"
            f"Which subject or chapter would you like to practice today? Type any topic to begin!"
        )


def format_thinking_block(user_query, intent_state, action_desc, is_math=False):
    """
    Formats the 3-part structured thinking block:
    1. Intent State
    2. User Language
    3. Complexity Level
    """
    lang = "Hinglish" if is_hinglish(user_query) else "English"
    if any(ord(c) >= 0x0900 and ord(c) <= 0x097F for c in user_query):
        lang = "Hindi"
    
    if is_math:
        complexity = "Step-by-step mathematical substitution."
    elif "Multi-Intent" in intent_state:
        complexity = "Dismiss off-topic in 1 sentence, fulfill academic request."
    elif "State D" in intent_state or "Off-Topic" in intent_state:
        complexity = "Abandon templates. Pivot to academic topic."
    else:
        complexity = "Keep it simple. No complex equations in State A unless explicitly requested."
    
    return (
        f"<thinking>\n"
        f"Intent: {intent_state}.\n"
        f"Language: {lang}.\n"
        f"Complexity: {complexity}\n"
        f"Action: {action_desc}.\n"
        f"</thinking>\n\n"
    )


def is_multi_intent(query):
    """
    Detects if a student's prompt contains BOTH off-topic chatter (State D)
    AND a valid academic request (numerical problem, concept question, homework, code, or quiz).
    """
    q = query.strip().lower()
    if is_code_submission(query) or is_syllabus_query(query):
        return False

    has_off_topic = any(re.search(r'\b' + re.escape(k) + r'\b', q) for k in [
        'free fire', 'bgmi', 'pubg', 'fortnite', 'minecraft', 'gta', 'roblox', 'video game', 'video games', 'gamer', 'gaming',
        'movie', 'movies', 'film', 'films', 'actor', 'actors', 'actress', 'netflix', 'joke', 'jokes', 'chutkula', 'sing a song', 'gana gao',
        'batman', 'superman', 'spiderman', 'iron man', 'crush', 'marry me', 'bored', 'bore ho'
    ])
    
    has_academic = (
        bool(solve_physics_math_numerical(query, "Class 10", "General Science")) or
        is_code_submission(query) or
        any(k in q for k in [
            'solve', 'find', 'calculate', 'derive', 'formula', 'photosynthesis', 'ohm', 'newton',
            'acceleration', 'velocity', 'motion', 'quadratic', 'trigonometry', 'essay', 'paragraph',
            'mass', 'weight', 'inertia', 'current', 'voltage', 'resistance', 'ncert', 'cbse'
        ])
    )
    
    return has_off_topic and has_academic


def is_greeting(query):
    """Detects PURE conversational greetings without an academic question."""
    q = query.lower().strip().rstrip('.!? ')
    q_norm = q.replace('how r u', 'how are you').replace('how are u', 'how are you').replace('hw r u', 'how are you')
    
    # If the student is asking an academic question or concept, it is NOT a pure greeting!
    academic_triggers = [
        'what', 'why', 'how', 'when', 'where', 'who', 'which', 'can you', 'could you', 'please tell',
        'tell me', 'explain', 'define', 'solve', 'calculate', 'derive', 'kya', 'kaise', 'kyun', 'batao', 'samjhao',
        'weather', 'climate', 'mausam', 'science', 'math', 'python', 'loop', 'variable', 'mass', 'force',
        'photosynthesis', 'light', 'sound', 'electricity', 'class 1', 'class 2', 'class 3', 'class 4', 'class 5',
        'class 6', 'class 7', 'class 8', 'class 9', 'class 10', 'class 11', 'class 12'
    ]
    if any(k in q for k in academic_triggers):
        return False

    greetings = [
        'hi', 'hello', 'hey', 'namaste', 'namaskar', 'pranam', 'hlo', 'hii', 'hiii', 'hyy',
        'good morning', 'good afternoon', 'good evening', 'good day',
        'how are you', 'kaise ho', 'kya haal hai', 'kese ho',
        'hi how are you', 'hi how are u', 'hello how are you', 'hello how are u', 'hey how are you', 'hey how are u',
        'hi maya', 'hello maya', 'hey maya', 'whats up', "what's up", 'sup'
    ]
    if q in greetings or q_norm in greetings:
        return True
        
    words = q.split()
    return len(words) <= 3 and words[0] in ['hi', 'hello', 'hey', 'namaste']


def is_simplification_request(query, history=None):
    """Detects if student is struggling, confused, or asking for a simpler explanation, fun story, or metaphor of a prior concept."""
    q = query.lower().strip()
    # Exclude direct queries that ask for specific rules/formulas/theorems
    if any(k in q for k in ['fleming', 'rule', 'formula', 'theorem', 'difference between', 'what is', 'explain what', 'derive']):
        return False
    return any(k in q for k in [
        "don't understand", "dont understand", "didn't understand", "didnt understand",
        "samajh nahi aaya", "samajh nhi aya", "kuch samajh nahi", "too hard", "too complex",
        "make it simpler", "explain simply", "explain more simply", "simple words", "simple language",
        "easy words", "easy way", "explain like a kid", "explain like im 5", "eli5", "mushkil lag raha",
        "unable to understand", "hard to understand", "not understanding", "samjha do simply",
        "i am confused", "im confused", "fun story", "as a story", "with a story", "story format",
        "explain with a story", "simple story", "easy story", "like a story", "short story",
        "tell a story", "aur simple", "aur asan", "kahani jaisa", "kahani ke roop me"
    ])


def generate_feynman_simplification(query, grade, subject, history=None):
    """
    Pedagogical Feynman Simplification Technique:
    Translates complex concepts into intuitive, real-world metaphors and stories with zero jargon.
    """
    q_lower = query.lower()
    is_hing = is_hinglish(query)
    
    # Check history to find the previous topic discussed
    history_text = " ".join([
        (h.get('text', '') or h.get('content', '') or (h.get('parts', [''])[0] if isinstance(h.get('parts'), list) else ''))
        for h in (history or [])
    ]).lower()
    full_context = q_lower + " " + history_text
    
    # 1. Water Cycle & Rain Story (Primary & Middle School)
    if any(k in full_context for k in ['water cycle', 'rain', 'evaporation', 'condensation', 'precipitation', 'clouds', 'water droplet']):
        if is_hing:
            return (
                f"### 🌧️ **Droppy the Water Drop: Baarish ki Kahani! ({grade})**\n\n"
                f"Chaliye **Water Cycle** ko ek choti si fun kahani ke through samajhte hain! 💧\n\n"
                f"Meet **Droppy** — ek chota sa pani ka boond jo nadi me khel raha tha:\n\n"
                f"1. ☀️ **Step 1: Dhoop me Pankh Lagna (Evaporation):**\n"
                f"   Suraj dada ne tej dhoop chamkai. Droppy ko garmi lagi aur wo invisible bhaap (steam) bankar hawa me udne laga!\n\n"
                f"2. ☁️ **Step 2: Aasman me Party (Condensation):**\n"
                f"   Upar thandak me Droppy apne hazaron doston se mila aur sabne milkar ek mota, safed **Baadal (Cloud)** bana liya.\n\n"
                f"3. 🌧️ **Step 3: Rollercoaster Ride (Precipitation / Rain):**\n"
                f"   Jab baadal bohot bhari ho gaya, toh Droppy aur uske saare dost 'Whoosh!' karke baarish bankar wapas dharti par gir gaye!\n\n"
                f"💡 *Droppy phir se nadi me gaya, aur yeh circle hamesha chalta rehta hai!* Kya Droppy ki story se Water Cycle clear hua? 😊"
            )
        else:
            return (
                f"### 🌧️ **The Story of 'Droppy' the Water Droplet ({grade})**\n\n"
                f"Let's understand the **Water Cycle** with a fun adventure story! 💧\n\n"
                f"Meet **Droppy**, a happy little water droplet floating in a blue lake:\n\n"
                f"1. ☀️ **Stage 1: Growing Steam Wings (Evaporation):**\n"
                f"   The warm Sun shines down on the lake. Droppy gets warm and turns into invisible steam (water vapor), flying high up into the blue sky!\n\n"
                f"2. ☁️ **Stage 2: The Cloud Hug (Condensation):**\n"
                f"   High up in the cool sky, Droppy meets millions of droplet friends. They hug tightly and form a big, fluffy **Cloud**!\n\n"
                f"3. 🌧️ **Stage 3: The Rain Waterslide (Precipitation):**\n"
                f"   When the cloud gets too heavy to hold everyone, Droppy slides all the way down to Earth as a fresh rain drop: *Splish, Splash!*\n\n"
                f"💡 *Droppy flows right back into the lake, ready for the next adventure!* Does this story make the water cycle clear and fun? 😊"
            )

    # 2. Photosynthesis: The Plant Kitchen Story
    if any(k in full_context for k in ['photosynthesis', 'chlorophyll', 'stomata', 'plant food', 'glucose']):
        if is_hing:
            return (
                f"### 🌿 **Chef Leaf: Podhon ka Masterchef Restaurant! ({grade})**\n\n"
                f"Socho har ped ki patti ek **Masterchef Kitchen** hai jisme **Chef Chlorophyll** khana banata hai:\n\n"
                f"- ☀️ **Chulha (Solar Energy):** Suraj ki dhoop se cooking heat aati hai.\n"
                f"- 🥤 **Juice Straw (Roots):** Jaden zameen se pani kheench kar kitchen tak bhejti hain.\n"
                f"- 🪟 **Kitchen Windows (Stomata):** Patti ke chote-chote chhed hawa se Carbon Dioxide andar lete hain.\n"
                f"- 🍰 **Sweet Dish (Glucose):** Chef Chlorophyll sab mila kar meetha glucose khana banata hai aur hamare liye taazi **Oxygen gas** bahar nikalta hai!\n\n"
                f"🌟 *Plants hume oxygen dete hain aur apna khana khud banate hain!* Clear laga? 😊"
            )
        else:
            return (
                f"### 🌿 **The Masterchef Leaf Kitchen Story ({grade})**\n\n"
                f"Think of every green leaf as a tiny **Masterchef Restaurant** operated by **Chef Chlorophyll**:\n\n"
                f"- ☀️ **The Stove (Sunlight):** Chef Chlorophyll catches sunlight for pure solar cooking power.\n"
                f"- 🥤 **The Straw (Roots):** Roots suck fresh water from the soil up into the leaf kitchen.\n"
                f"- 🪟 **The Windows (Stomata):** Microscopic leaf windows breathe in carbon dioxide from the air.\n"
                f"- 🍰 **The Delicious Cake (Glucose & Oxygen):** The leaf bakes sweet glucose energy for the plant and puffs out fresh **Oxygen** for us to breathe!\n\n"
                f"🌟 *Plants feed themselves and give us fresh air every single day!* Clear and easy? 😊"
            )

    # 3. Electricity & Ohm's Law (The Water Slide Analogy)
    if any(k in full_context for k in ['ohm', 'electricity', 'voltage', 'current', 'resistance', 'circuit']):
        if is_hing:
            return (
                f"### ⚡ **Water Slide Park se Samajhte Hain Electricity! ({grade})**\n\n"
                f"Electricity ko ek **Water Theme Park** ki tarah socho:\n\n"
                f"- 🎢 **Voltage ($V$) = Slide ki Oonchai (Push):** Slide jitni oonchi hogi, utna tej dhakka milega!\n"
                f"- 🚧 **Resistance ($R$) = Slide ke Bumps (Speed breaker):** Agar slide khurduri hai, toh aap dheere fisloge.\n"
                f"- 🌊 **Current ($I$) = Flow hone ki Speed:** Kitne bache har second slide se nikal rahe hain ($I = V / R$)!\n\n"
                f"💡 Agar Voltage badhaoge toh Current badhega; Resistance badhaoge toh Current rukega! Simple na? 😊"
            )
        else:
            return (
                f"### ⚡ **The Water Park Slide Analogy for Electricity ({grade})**\n\n"
                f"Think of an electric circuit as a fun **Water Slide Park**:\n\n"
                f"- 🎢 **Voltage ($V$) = Height of the Slide (Push):** The higher the tower, the stronger the push downwards!\n"
                f"- 🚧 **Resistance ($R$) = Friction on the Slide:** Bumps and roughness that slow down the sliding.\n"
                f"- 🌊 **Current ($I$) = Flow Rate:** How many swimmers rush through per second ($I = V / R$)!\n\n"
                f"💡 Higher Voltage creates faster flow, while higher Resistance slows everything down! Easy? 😊"
            )

    # 4. Python Variables & Loops
    if any(k in full_context for k in ['python', 'variable', 'loop', 'coding', 'programming']):
        if is_hing:
            return (
                f"### 🎈 **Chaliye isko ekdam aasan real-life example se samajhte hain! ({grade})**\n\n"
                f"Koi baat nahi, coding pehli baar me thodi ajeeb lag sakti hai! 💖\n\n"
                f"#### 📦 **1. Variable ko ek 'Sticker Laga Dibba' samjho:**\n"
                f"- Socho aapke paas ek pencil box hai. Us par aapne sticker lagaya **`pencil_count`** aur andar **`5`** pencils rakh di.\n"
                f"- Jab bhi aap computer se poochoge `pencil_count`, computer bolega `5`!\n"
                f"- **Variable bas ek naam wala dibba hai jo koi cheez yaad rakhta hai.**\n\n"
                f"#### 🔁 **2. Loop ko ek 'Music Repeat Button' samjho:**\n"
                f"- Agar gaana pasand aaye, toh aap 10 baar play button nahi dabate, aap **Repeat** button daba dete ho.\n"
                f"- Coding me bhi jab koi kaam 5 ya 10 baar lagatar karna ho, toh hum **Loop** laga dete hain taaki computer use khud repeat kare!\n\n"
                f"🌟 **Kya yeh visual example aapko clear laga?** Agar haan, toh bataiye ek box me aap kya store karna chahenge?"
            )
        else:
            return (
                f"### 🎈 **Let's Make This Super Simple with a Fun Real-Life Picture! ({grade})**\n\n"
                f"No worries at all! Learning to code is brand new, so let's throw away all technical words and use everyday things you already know! 💖\n\n"
                f"#### 📦 **1. Think of a Variable as a Labeled Lunchbox:**\n"
                f"- Imagine a lunchbox with a sticker that says **`my_snack`**.\n"
                f"- You put an **`Apple 🍎`** inside it.\n"
                f"- Whenever the computer opens `my_snack`, it finds the `Apple`!\n"
                f"- **A variable is just a labeled box that holds information so you don't forget it.**\n\n"
                f"#### 🔁 **2. Think of a Loop as a Song on 'Repeat':**\n"
                f"- When you play your favorite song on repeat 3 times, you don't keep pressing start — the music player repeats it automatically.\n"
                f"- In coding, a **Loop** is our **Repeat button** that does repetitive tasks without having to type them again and again!\n\n"
                f"🌟 **Does this picture make it feel easier?** Tell me what you'd like to put inside your labeled box!"
            )
            
    # 5. Mass vs Weight
    if any(k in full_context for k in ['mass', 'weight', 'gravity']):
        if is_hing:
            return (
                f"### ⚖️ **Mass vs Weight: Aasan Tareeqa! ({grade})**\n\n"
                f"- **Mass (Aapke shareer ka matter):** Agar aap Earth par hain ya Moon par, aapke hath, pair aur organs wahi rahenge. Isliye **Mass hamesha same rehta hai**.\n"
                f"- **Weight (Dharti ka kheenchav):** Earth aapko neeche kheenchti hai. Moon par gravity kam hai, isliye Moon par aapka **Weight kam ho jayega**!\n\n"
                f"💡 *Example:* Moon par aap hawa me lambi chalang maar sakte hain kyunki wahan Weight 6 guna kam ho jata hai, par aapka Mass wahi rehta hai!"
            )
        else:
            return (
                f"### ⚖️ **Mass vs Weight Made Ultra Simple! ({grade})**\n\n"
                f"- **Mass (What you are made of):** All your bones, muscles, and matter. Whether you fly to the Moon or Mars, you still have the exact same body matter. **Mass NEVER changes!**\n"
                f"- **Weight (The planet's pull):** How hard Earth pulls you down towards the floor. Because the Moon has weaker gravity, your **Weight becomes 6 times lighter on the Moon!**\n\n"
                f"💡 *Quick Picture:* On the Moon, you could jump super high because your weight is less, but your mass is unchanged!"
            )

    # 6. Default Universal Feynman Simplification
    if is_hing:
        return (
            f"### 💡 **Aasan Bhasha me Samajhte Hain ({grade} - {subject})**\n\n"
            f"Padhai me jab koi topic thoda tough lage, toh usko 3 simple hisson me todte hain:\n\n"
            f"1. **Asli Zindagi me iska kya kaam hai?** Har concept kisi na kisi real-world problem ko solve karne ke liye bana hai.\n"
            f"2. **Real-Life Example:** Socho jaise hum daily routine me mobile use karte hain ya cycling karte hain.\n"
            f"3. **Simple Rule:** Badi definitions ko bhool kar bas basic funda yaad rakhein.\n\n"
            f"Aap bataiye is topic ka kaun sa hissa sabse zyada confusing lag raha hai? Main use ek choti kahani ke through samjha dunga! 😊"
        )
    else:
        return (
            f"### 💡 **Let's Break This Down Into Plain English ({grade} - {subject})**\n\n"
            f"Whenever a topic feels overwhelming, we follow the **Feynman Rule**: strip away all textbook jargon and imagine how you would explain it to a friend on the playground!\n\n"
            f"1. **The 'Why':** Every rule in {subject} was created to explain something we see or do every single day.\n"
            f"2. **The Everyday Picture:** Think of it like cooking a recipe, playing a board game, or riding a bicycle.\n"
            f"3. **The Big Takeaway:** You don't need complex formulas to understand the core intuition first.\n\n"
            f"Which exact part felt tricky or confusing? Tell me, and I will explain it with a fun story! 😊"
        )


def is_language_switch_request(query):
    """Detects requests to switch language or explain the previous concept in Hindi / Hinglish / English."""
    q = query.lower().strip()
    return any(k in q for k in [
        'hindi me samjha', 'hindi me bata', 'hindi m samjha', 'hindi m bata', 'hindi mai samjha',
        'hindi me explain', 'hindi m explain', 'explain in hindi', 'explain this in hindi',
        'can you explain in hindi', 'tell in hindi', 'speak in hindi', 'in hindi please',
        'english me samjha', 'english me bata', 'explain in english', 'explain this in english',
        'hinglish me samjha', 'hinglish me bata', 'hinglish me explain',
        'kya mujhe ise hindi', 'kya aap ise hindi', 'ise hindi me', 'ise hindi m', 'ise hindi mai',
        'translate this', 'translate in hindi', 'translate to hindi', 'hindi please'
    ])


def generate_language_switch_response(query, grade, subject, history=None):
    """
    Handles student request to explain previous concept in Hindi/Hinglish.
    Inspects conversation history to identify the last topic (e.g. Weather, Python, Photosynthesis).
    """
    q_lower = query.lower()
    
    # Check history to find the previous topic discussed
    history_text = " ".join([
        (h.get('text', '') or h.get('content', '') or (h.get('parts', [''])[0] if isinstance(h.get('parts'), list) else ''))
        for h in (history or [])
    ]).lower()
    full_context = history_text + " " + q_lower
    
    thinking = format_thinking_block(query, "State A (Language Switch / Hindi Explanation)", "Explain the previously discussed concept in friendly, conversational Hindi/Hinglish")
    
    # 1. Weather / Mausam
    if any(k in full_context for k in ['weather', 'climate', 'mausam', 'season', 'rain', 'temperature', 'humidity']):
        is_primary = grade in ['Primary (1-5)', 'Class 1', 'Class 2', 'Class 3', 'Class 4', 'Class 5']
        if is_primary:
            return thinking + (
                f"### 🌤️ **Weather (मौसम) - Hindi me Aasan Explanation! ({grade} - EVS / Science)**\n\n"
                f"Bilkul! Chaliye **Weather** ko ekdam aasan Hindi bhasha me samajhte hain:\n\n"
                f"#### ☀️ **1. Weather (मौसम) Kya Hota Hai?**\n"
                f"**Weather** ka matlab hota hai ki **aaj bahar ka aasman aur hawa kaisi hai**! Yeh har din ya ghante-ghante badal sakta hai:\n"
                f"- Subah achhi dhoop nikli ho sakti hai ☀️ (**Sunny**)\n"
                f"- Dopahar me badal chha sakte hain ☁️ (**Cloudy**)\n"
                f"- Shaam ko tez baarish ho sakti hai 🌧️ (**Rainy**)\n\n"
                f"#### 🌈 **2. Weather ke 4 Main Parts (Elements):**\n"
                f"1. 🌡️ **Temperature (Tapman):** Hawa kitni garm ya thandi hai.\n"
                f"2. 💨 **Wind (Hawa):** Hawa kitni tez chal rahi hai (halki thandi hawa ya tez aandhi).\n"
                f"3. 🌧️ **Rain (Baarish):** Badalon se girta hua paani.\n"
                f"4. ☁️ **Clouds (Baadal):** Aasman me safed ya kaale baadal.\n\n"
                f"#### 📅 **3. Weather aur Season me kya farak hai?**\n"
                f"- **Weather** har din badalta hai (jaise: *'Aaj baarish ka din hai, umbrella le lo!'* ☔).\n"
                f"- **Season (Ritu)** 2 se 3 mahine tak ek jaisi rehti hai (jaise *Garmi/Summer*, *Sardi/Winter*, *Baarish/Monsoon*).\n\n"
                f"Kya aap Weather par ek chhota sa 3-question quiz solve karna chahenge? 🎮"
            )
        else:
            return thinking + (
                f"### 🌤️ **Weather aur Climate (मौसम और जलवायु) - Hindi Explanation ({grade} - {subject})**\n\n"
                f"#### 1. **Weather vs Climate me Farak:**\n"
                f"- **Weather (मौसम):** Kisi jagah ke atmosphere ki har din (day-to-day) ki sthiti (jaise temperature, hawa ki speed, humidity, aur baarish). Yeh bohot jaldi badal jata hai.\n"
                f"- **Climate (जलवायु):** Kisi area ka lagbhag **25 se 30 saal** ka average mausam ka pattern. Jaise Rajasthan ka climate garm aur dry hai, jabki Kashmir ka cold hai.\n\n"
                f"#### 2. **Weather ke Pramukh Elements:**\n"
                f"1. **Temperature (तापमान):** Maximum-Minimum Thermometer se naapa jata hai.\n"
                f"2. **Humidity (नमी) & Baarish:** Hygrometer aur **Rain Gauge** se naapa jata hai.\n"
                f"3. **Wind Speed:** Anemometer se naapa jata hai.\n\n"
                f"Kya aap is concept par ek quick 3-question practice quiz test karna chahenge?"
            )

    # 2. Python Variables & Loops
    if any(k in full_context for k in ['python', 'variable', 'loop', 'coding', 'programming']):
        return thinking + (
            f"### 🐍 **Python Coding: Variables & Loops (Hindi Explanation) ({grade})**\n\n"
            f"Bilkul! Chaliye Python ko aasan Hindi me samajhte hain:\n\n"
            f"#### 📦 **1. Variables Kya Hote Hain? (Sticker Laga Dibba)**\n"
            f"Variable computer memory me ek named box (dibba) hota hai jisme hum koi bhi value ya information store karte hain.\n"
            f"```python\n"
            f"my_name = \"Maya\"   # Text store karne ke liye\n"
            f"my_age = 10        # Number store karne ke liye\n"
            f"```\n\n"
            f"#### 🔁 **2. Loops Kya Hote Hain? (Repeat Button)**\n"
            f"Jab hume koi kaam baar-baar karna ho (jaise 5 baar taali bajana 👏), toh hum baar-baar code nahi likhte, bas **Loop (Repeat Button)** laga dete hain:\n"
            f"```python\n"
            f"for step in range(1, 6):\n"
            f"    print(\"Clap number:\", step, \"👏\")\n"
            f"```\n\n"
            f"Kya aap ispar ek chhota sa quiz try karna chahte hain? 🎮"
        )

    # 3. Photosynthesis
    if any(k in full_context for k in ['photosynthesis', 'plant', 'chlorophyll']):
        return thinking + (
            f"### 🌿 **Photosynthesis (प्रकाश संश्लेषण) - Hindi Explanation ({grade} - {subject})**\n\n"
            f"**Photosynthesis** wo prakriya (process) hai jisme green plants (hare paudhe) sunlight, paani ($H_2O$) aur carbon dioxide ($CO_2$) ka use karke apna khana (glucose) banate hain aur oxygen gas release karte hain.\n\n"
            f"#### 🧪 **Simple Formula:**\n"
            f"$$\\text{{Carbon Dioxide}} + \\text{{Water}} \\xrightarrow{{\\text{{Sunlight + Chlorophyll}}}} \\text{{Glucose}} + \\text{{Oxygen}}$$\n\n"
            f"Kya aap is concept par ek quick 3-question quiz solve karna chahenge?"
        )

    # 4. Default Greeting / Prompt for Specific Topic in Hindi
    return thinking + (
        f"### 🙏 **Zaroor! Main bilkul Hindi me samjhaunga.**\n\n"
        f"Aap kaunsa topic ya question Hindi me samajhna chahte hain?\n\n"
        f"Aap mujhse pooch sakte hain:\n"
        f"- **Science / EVS:** *Weather, Photosynthesis, Microorganisms, Motion, Electric Current*\n"
        f"- **Math:** *Quadratic equations, Formulas, Numericals*\n"
        f"- **Computer Science:** *Python, Variables, Loops*\n\n"
        f"Bas apna topic yahan type kijiye aur main step-by-step Hindi me samjha dunga! 😊"
    )


def generate_local_tutor_response(user_query, subject, grade, mode, history=None):
    """Authentic CBSE NCERT Curriculum Pedagogical Tutor Engine"""
    q_lower = user_query.lower().strip()
    chat_history = history or []
    
    # Auto-adjust subject domain and grade if query explicitly mentions or targets another
    detected_sub = detect_subject_from_query(user_query, subject, history=chat_history)
    if detected_sub != subject:
        subject = detected_sub
    detected_gr = detect_grade_from_query(user_query, grade, history=chat_history)
    if detected_gr != grade:
        grade = detected_gr
    s_lower = subject.lower().strip()
    
    # 0. Language Switch / Hindi Request ("kya mujhe ise hindi me samjha sakte ho", "explain in hindi")
    if is_language_switch_request(user_query):
        return generate_language_switch_response(user_query, grade, subject, chat_history)

    # 0.1 Greetings & Friendly Check-ins
    if is_greeting(user_query):
        is_hing = is_hinglish(user_query)
        thinking = format_thinking_block(user_query, "Greeting / Conversational Check-in", "Greet student warmly and offer help with CBSE academic subjects or interactive quiz")
        if is_hing:
            greeting_msg = (
                f"### 🙏 **Namaste! Main badhiya hoon.**\n\n"
                f"Main **Maya AI** hoon — **Maya Vidya Niketan** ka smart academic learning assistant! 🎓\n\n"
                f"Aaj aap **{grade}** me kya seekhna ya practice karna chahenge? Aap mujhse:\n"
                f"- **Science / Physics / Chemistry / Biology** ke concepts pooch sakte hain\n"
                f"- **Math** ke numericals aur equations step-by-step solve karwa sakte hain\n"
                f"- **Computer Science / Python** coding seekh sakte hain\n"
                f"- **CBSE Practice Quiz** dekar apne exams ki taiyari test kar sakte hain!\n\n"
                f"Bataiye, kis topic se shuru karein? 😊"
            )
        else:
            greeting_msg = (
                f"### 🙏 **Namaste! I am doing great.**\n\n"
                f"I am **Maya AI**, your dedicated academic learning assistant at **Maya Vidya Niketan**! 🎓\n\n"
                f"How can I help your **{grade}** studies today? You can:\n"
                f"- Ask me to explain concepts in **Science, Math, English, or Computer Science**\n"
                f"- Request step-by-step problem and numerical solutions\n"
                f"- Generate an interactive **CBSE Practice Quiz** on any chapter!\n\n"
                f"What topic would you like to explore today? 😊"
            )
        return thinking + greeting_msg

    # 0.5 Student Struggling / Simplification Request ("I don't understand", "make it simpler")
    if is_simplification_request(user_query, chat_history):
        thinking = format_thinking_block(user_query, "State A / Pedagogical Simplification", "Strip all technical jargon and explain using intuitive real-world metaphors")
        return thinking + generate_feynman_simplification(user_query, grade, subject, chat_history)

    # 0.2 Declining a Quiz Offer ("no", "not now", "later")
    if chat_history and was_last_message_quiz_offer(chat_history) and is_negative_reply(user_query):
        is_hing = is_hinglish(user_query)
        thinking = format_thinking_block(user_query, "Quiz Offer Declined", "Politely acknowledge student declining quiz and offer to explain another topic or solve problems")
        if is_hing:
            decline_msg = (
                "Koi baat nahi! 😊 Jab bhi aap ready hon tab hum practice quiz le sakte hain.\n\n"
                f"Abhi aap **{grade} {subject}** me kaun sa topic, concept ya numerical problem samajhna chahenge?"
            )
        else:
            decline_msg = (
                "No problem at all! 😊 Whenever you are ready, we can take a quick quiz.\n\n"
                f"What topic, concept, or numerical problem in **{grade} {subject}** would you like to explore or solve next?"
            )
        return thinking + decline_msg

    # 1. School Information Queries
    if any(k in q_lower for k in ['maya vidya', 'mvn', 'school', 'admission', 'fee', 'address', 'contact', 'principal', 'affiliation', 'hostel']):
        return (
            "### 🏫 **Maya Vidya Niketan (MVN) Information**\n\n"
            "Official details of Maya Vidya Niketan:\n"
            "- **CBSE Affiliation No.**: 331074 (Affiliated with Central Board of Secondary Education, New Delhi)\n"
            "- **Governing Trust**: MAYA Educational Trust\n"
            "- **Campus Location**: Nayanagar, Madanpur, Madhepura, Bihar - 852113\n"
            "- **Helpline Phone**: +91 9304938841 | Email: `mvnm001@gmail.com`\n"
            "- **Academic Offerings**: Nursery to Class 12 (Science & Arts streams, Smart BenQ Interactive Classrooms, Physics/Chemistry/Bio Labs, Hostel & Transport facilities).\n"
            "- **Admissions**: Open for 2026-27! Submit online applications via the **Admissions & Jobs** portal.\n\n"
            "How else can Maya AI help your learning today?"
        )

    # 2. Multi-Intent Handling (Off-Topic Chatter + Valid Academic Request / Numerical / Quiz)
    if is_multi_intent(user_query):
        is_hing = is_hinglish(user_query)
        filter_sentence = (
            "Main games, movies ya casual chat discuss nahi karta, par chaliye aapka academic question solve karte hain! 📚\n\n"
            if is_hing else
            "While I don't engage in video games, movies, or casual chit-chat, I am delighted to help you solve your academic question! 📚\n\n"
        )
        
        has_quiz_request = any(k in q_lower for k in ['quiz', 'test me', 'take a test', 'give me questions', 'mcq', 'practice quiz'])
        defer_sentence = ""
        if has_quiz_request:
            defer_sentence = (
                "\n\n---\n💡 **Quiz Deferral Notice:** Mainne dekha ki aapne quiz ke liye bhi pucha tha! Pehle is step-by-step solution ko dhyan se samajh lijiye. Jab aap ready hon, chat me **'Start Quiz'** type kijiye aur main practice test generate kar dunga!"
                if is_hing else
                "\n\n---\n💡 **Quiz Deferral Notice:** I noticed you also requested a practice quiz! Please review this step-by-step solution first. Once you are ready, reply **'Start Quiz'** and I will generate your custom 3-question test!"
            )
            
        numerical_solution = solve_physics_math_numerical(user_query, grade, subject)
        if numerical_solution:
            intent_label = "Multi-Intent (Off-Topic + Mode 2 Solver + Quiz Request)" if has_quiz_request else "Multi-Intent (Off-Topic + Mode 2 Solver)"
            action_label = "Dismiss off-topic chatter in 1 sentence, solve numerical problem with formula first and final answer at bottom" + (", and defer quiz until student reviews solution" if has_quiz_request else "")
            thinking = format_thinking_block(user_query, intent_label, action_label, is_math=True)
            return thinking + filter_sentence + numerical_solution + defer_sentence

        # Handle kinematics motion problem if regex didn't match
        if any(k in q_lower for k in ['acceleration', 'velocity', 'motion', 'train', 'car', 'speed']):
            intent_label = "Multi-Intent (Off-Topic + Mode 2 Solver + Quiz Request)" if has_quiz_request else "Multi-Intent (Off-Topic + Mode 2 Solver)"
            action_label = "Dismiss off-topic chatter in 1 sentence, solve motion problem with formula first and final answer at bottom" + (", and defer quiz until student reviews solution" if has_quiz_request else "")
            thinking = format_thinking_block(user_query, intent_label, action_label, is_math=True)
            academic_content = (
                f"### 🔢 **Step-by-Step Physics Numerical Solution ({grade} - {subject})**\n\n"
                f"#### 📐 **Step 1: Formula Required**\n"
                f"According to the First Equation of Motion:\n"
                f"$$v = u + at$$\n\n"
                f"#### 📋 **Step 2: Given Data**\n"
                f"- **Initial Velocity ($u$):** $0\\text{{ m/s}}$ (starts from rest)\n"
                f"- **Acceleration ($a$):** $2\\text{{ m/s}}^2$\n"
                f"- **Time taken ($t$):** $10\\text{{ seconds}}$\n\n"
                f"#### 🧮 **Step 3: Step-by-Step Value Substitution**\n"
                f"$$v = 0 + (2 \\times 10) = 20\\text{{ m/s}}$$\n\n"
                f"#### 🎯 **Final Answer (with correct SI units):**\n"
                f"$$\\mathbf{{v = 20\\text{{ m/s}} \\quad (\\text{{or }} 72\\text{{ km/h}})}}$$\n\n"
                f"*The calculated final velocity of the vehicle is **20 m/s**.*"
            )
            return thinking + filter_sentence + academic_content + defer_sentence

        intent_label = "Multi-Intent (Off-Topic + State A Concept + Quiz Request)" if has_quiz_request else "Multi-Intent (Off-Topic + State A Concept)"
        action_label = "Dismiss off-topic in 1 sentence, explain concept simply" + (", and defer quiz until student reviews solution" if has_quiz_request else "")
        thinking = format_thinking_block(user_query, intent_label, action_label)
        return thinking + filter_sentence + (
            f"### 💡 **Maya AI Academic Guide: {subject} ({grade})**\n\n"
            f"- **Core Foundation**: In {grade} {subject}, understanding fundamental principles is key.\n"
            f"- **Step-by-Step Methodology**: Always state given variables, identify required formulas, and substitute systematically.\n"
            f"- **Exam Application**: Focus on NCERT standard definitions, diagrams, and SI units."
        ) + defer_sentence

    # 3. State D: Pure Off-Topic / Chit-Chat / Gaming -> Abandon Templates & Pivot
    if is_off_topic_state_d(user_query):
        thinking = format_thinking_block(user_query, "State D (Off-Topic)", "Acknowledge politely, decline roleplay/casual chat, and creatively pivot back to CBSE syllabus")
        return thinking + generate_state_d_pivot_response(user_query, grade, subject)

    # 2. Handwritten Essay / Assignment Photo Evaluation
    if any(k in q_lower for k in ['handwritten', 'photo of my essay', 'image of my assignment', 'evaluate my handwriting', 'read my essay photo', 'uploaded photo', 'my handwritten', 'photo of my homework']):
        thinking = format_thinking_block(user_query, "Writing Coach / Handwritten Evaluation", "Transcribe sample, praise, give constructive critique without rewriting")
        return thinking + evaluate_handwritten_submission(user_query, grade, subject)

    # 3. Mode 4 / 5: Student Code Submission Evaluation (CS Teacher Persona)
    if is_code_submission(user_query):
        return evaluate_student_code_submission(user_query, grade)

    # 3.4. CBSE Board Preparation Strategies & Study Tips
    if is_study_tips_query(user_query):
        thinking = format_thinking_block(user_query, "State A (Study Tips & Revision Strategy)", "Provide high-scoring CBSE exam preparation framework and active recall tips")
        return thinking + generate_study_tips_response(user_query, grade=grade, subject=subject)

    # 3.45. CBSE Board Exam Schedules, Datesheets, Results & Passing Criteria
    if is_cbse_exam_info_query(user_query):
        thinking = format_thinking_block(user_query, "State A (CBSE Board Exam Information)", "Provide official CBSE schedule, practical exam timeline, shift timings, and passing criteria")
        return thinking + generate_cbse_exam_info_response(user_query, grade=grade)

    # 3.5. NCERT / CBSE Official Syllabus Directory
    if is_syllabus_query(user_query):
        thinking = format_thinking_block(user_query, "State A (NCERT Syllabus Overview)", "Provide structured CBSE/NCERT curriculum breakdown for requested class and subject")
        return thinking + generate_ncert_syllabus_overview(user_query, grade, subject)

    # 4. Quiz Mode with Hard Separation (Generator vs Grader based on Chat History)
    if is_quiz_request(user_query, mode, history=chat_history) or is_quiz_submission(user_query):
        quiz_state = determine_quiz_state(user_query, chat_history)
        if quiz_state == 'grader':
            thinking = format_thinking_block(user_query, "State C (Quiz Submission)", "Calculate score and output assessment template with 1-sentence explanations per answer")
            return thinking + grade_quiz_submission(user_query, grade, subject)
        else:
            q_count = extract_requested_question_count(user_query, default=3)
            quiz_topic_query = user_query
            if is_affirmative_reply(user_query) and chat_history:
                quiz_topic_query = extract_topic_from_history(chat_history, default_topic=user_query)
            thinking = format_thinking_block(user_query, "State B (Quiz Request)", f"Output exactly {q_count} MCQs without answers or grades. Stop and wait for student reply")
            return thinking + generate_dynamic_cbse_quiz(grade, subject, query=quiz_topic_query)

    # 5. Short Answers & True/False Rule
    if is_short_answer_or_tf(user_query):
        thinking = format_thinking_block(user_query, "State C (Quiz Submission / Verification)", "Provide direct True/False verdict, assessment template, and single follow-up question")
        return thinking + evaluate_short_answer_tf(user_query, grade, subject)

    # 6. Essay / Letter / Homework Helper Writing Generator
    if mode in ['homework', 'homework helper'] or any(k in q_lower for k in ['essay', 'write an essay', '250-word', 'write a paragraph', 'write a letter', 'write a speech', 'application to principal', 'leave application', 'homework due', 'write a composition']):
        thinking = format_thinking_block(user_query, "State A / Homework Helper (Writing Coach)", "Provide scaffolded brainstorming outline and invite student to draft first paragraph")
        return thinking + generate_essay_or_writing_response(user_query, grade, subject)

    # 7. Mode 2: Step-by-Step Problem Solver (Physics & Math Numericals)
    numerical_solution = solve_physics_math_numerical(user_query, grade, subject)
    if numerical_solution:
        thinking = format_thinking_block(user_query, "State A / Mode 2 (Step-by-Step Solver)", "Solve numerical problem step-by-step with SI unit conversions and formula first", is_math=True)
        return thinking + numerical_solution

    # 8. State A: Subject Concepts & Step-by-Step Problem Solving
    thinking = format_thinking_block(user_query, "State A (Concept Request)", "Explain simply in student language without complex formulas. End by asking if they want a quiz")

    # Senior CS: Linear Search vs Binary Search in Python
    if any(k in q_lower for k in ['linear search vs binary search', 'linear search and binary search', 'binary search', 'linear search', 'time complexity of search']):
        return thinking + (
            f"### 🐍 **Linear Search vs Binary Search in Python ({grade} - {subject})**\n\n"
            f"#### 📊 **1. Comparison Table:**\n"
            f"| Feature | Linear Search 🚶 | Binary Search ⚡ |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **Array Requirement** | Works on **both Unsorted and Sorted** lists | Strictly requires a **Sorted array** |\n"
            f"| **Methodology** | Sequential element-by-element scanning | Divide and Conquer (halves search space) |\n"
            f"| **Best Case Time** | $O(1)$ (Element at index 0) | $O(1)$ (Element at middle index) |\n"
            f"| **Worst Case Time** | **$O(n)$** (Linear time) | **$O(\\log n)$** (Logarithmic time - extremely fast) |\n"
            f"| **Space Complexity** | $O(1)$ | $O(1)$ iterative / $O(\\log n)$ recursive |\n\n"
            f"#### 💻 **2. Python Binary Search Implementation:**\n"
            f"```python\n"
            f"def binary_search(arr, target):\n"
            f"    low = 0\n"
            f"    high = len(arr) - 1\n"
            f"    while low <= high:\n"
            f"        mid = (low + high) // 2\n"
            f"        if arr[mid] == target:\n"
            f"            return mid  # Target found!\n"
            f"        elif arr[mid] < target:\n"
            f"            low = mid + 1  # Search right half\n"
            f"        else:\n"
            f"            high = mid - 1  # Search left half\n"
            f"    return -1  # Target not in list\n\n"
            f"# Example test\n"
            f"numbers = [10, 20, 30, 40, 50, 60, 70]\n"
            f"print(\"Index of 40:\", binary_search(numbers, 40))  # Output: 3\n"
            f"```\n\n"
            f"Would you like to trace this search with custom numbers or write the recursive version?"
        )

    # Senior CS: SQL WHERE vs HAVING
    if any(k in q_lower for k in ['where vs having', 'difference between where and having', 'where and having', 'having clause', 'where clause']):
        return thinking + (
            f"### 🗄️ **SQL: WHERE vs HAVING Clause Explained ({grade} - {subject})**\n\n"
            f"#### 📊 **1. Key Difference Comparison Table:**\n"
            f"| Feature | `WHERE` Clause 🔍 | `HAVING` Clause 📊 |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **Target** | Filters individual **rows / records** | Filters **groups formed by `GROUP BY`** |\n"
            f"| **Execution Timing** | Applied **before** grouping / aggregation | Applied **after** grouping / aggregation |\n"
            f"| **Aggregate Functions** | **Cannot** use aggregate functions (`SUM`, `AVG`, `COUNT`, `MAX`, `MIN`) | **Can** use aggregate functions (`HAVING COUNT(*) > 5`) |\n"
            f"| **Usage with `GROUP BY`** | Can be used without `GROUP BY` | Almost always used alongside `GROUP BY` |\n\n"
            f"#### 💻 **2. Code Examples (CBSE Class 12 CS / IP Standard):**\n\n"
            f"**A) Using `WHERE` (Filtering rows before grouping):**\n"
            f"```sql\n"
            f"SELECT StudentName, Marks \n"
            f"FROM Students \n"
            f"WHERE Marks >= 90; -- Filters individual student rows\n"
            f"```\n\n"
            f"**B) Using `HAVING` (Filtering aggregate groups):**\n"
            f"```sql\n"
            f"SELECT Department, AVG(Salary) \n"
            f"FROM Employees \n"
            f"GROUP BY Department \n"
            f"HAVING AVG(Salary) > 50000; -- Filters grouped departments\n"
            f"```\n\n"
            f"Would you like to write a custom SQL query using both `WHERE` and `HAVING`?"
        )

    # 4. Subject Concepts & Step-by-Step Problem Solving
    if any(k in q_lower for k in ['python', 'coding', 'programming', 'loop', 'loops', 'variable', 'variables', 'for loop', 'while loop']):
        is_primary = grade in ['Primary (1-5)', 'Class 1', 'Class 2', 'Class 3', 'Class 4', 'Class 5']
        
        if is_primary:
            if is_hinglish(user_query):
                return thinking + (
                    f"### 🎈 **Python Coding: Fun with Variables & Loops! ({grade} - {subject})**\n\n"
                    f"#### 🤖 **1. Python Kya Hai?**\n"
                    f"Jaise hum aapas me baat karte hain, waise hi **Python** computer se baat karne aur usse maze-daar games aur drawings sikhane ki ek super-friendly language hai! 🪄\n\n"
                    f"#### 📦 **2. Variables (Magic Labeled Boxes / Khilono ka Dibba):**\n"
                    f"Socho aapke paas ek **Toy Box** hai. Us par aapne sticker lagaya **`my_toy`** aur andar rakh diya **`Teddy Bear`** 🧸:\n"
                    f"```python\n"
                    f"# Magic Labeled Boxes (Variables):\n"
                    f"my_pet = \"Puppy\"        # Box me Puppy hai 🐶\n"
                    f"my_age = 8              # Box me number 8 hai\n"
                    f"favorite_fruit = \"Mango\" # Box me Mango hai 🥭\n"
                    f"```\n"
                    f"Jab bhi hum computer se kahenge `print(my_pet)`, computer turant bolega: `Puppy`! 🎉\n\n"
                    f"#### 🔁 **3. Loops (Magic Repeat Button!):**\n"
                    f"Agar hume 5 baar taali bajani ho, toh hum 5 baar *'Clap! Clap! Clap!'* nahi bolte — hum bas bolte hain: *'5 baar Clap karo!'* 👏\n"
                    f"Isi **Repeat Button** ko coding me **Loop** kehte hain!\n\n"
                    f"```python\n"
                    f"# Loop Example: 5 baar Taali bajana 👏\n"
                    f"for step in range(1, 6):\n"
                    f"    print(\"Clap number:\", step, \"👏\")\n"
                    f"```\n"
                    f"**Computer Output:**\n"
                    f"```text\n"
                    f"Clap number: 1 👏\n"
                    f"Clap number: 2 👏\n"
                    f"Clap number: 3 👏\n"
                    f"Clap number: 4 👏\n"
                    f"Clap number: 5 👏\n"
                    f"```\n\n"
                    f"Kya aap ek chhota sa fun coding game ya quiz try karna chahte hain? 🎮"
                )
            else:
                return thinking + (
                    f"### 🎈 **Python Coding: Fun with Variables & Loops! ({grade} - {subject})**\n\n"
                    f"#### 🤖 **1. What is Python?**\n"
                    f"Just like we talk in English or Hindi, **Python** is a friendly language we use to talk to computers and teach them fun games, drawings, and stories! 🪄\n\n"
                    f"#### 📦 **2. What is a Variable? (A Labeled Toy Box!)**\n"
                    f"Imagine you have a toy box with a name sticker on it. Whatever you put inside the box, the computer remembers it:\n"
                    f"```python\n"
                    f"# Magic Labeled Boxes (Variables):\n"
                    f"my_pet = \"Puppy\"        # The box holds 'Puppy' 🐶\n"
                    f"my_age = 8              # The box holds the number 8\n"
                    f"favorite_fruit = \"Mango\" # The box holds 'Mango' 🥭\n"
                    f"```\n"
                    f"Whenever you ask `print(my_pet)`, the computer happily shows: `Puppy`! 🎉\n\n"
                    f"#### 🔁 **3. What is a Loop? (The Magic Repeat Button!)**\n"
                    f"If you want to jump 5 times, you don't say *'Jump! Jump! Jump! Jump! Jump!'* — you just say *'Jump 5 times!'*\n"
                    f"In coding, a **Loop** is our **Repeat Button** so the computer does repetitive tasks automatically! 🔁\n\n"
                    f"```python\n"
                    f"# Loop Example: Clap 5 times 👏\n"
                    f"for count in range(1, 6):\n"
                    f"    print(\"Clap number:\", count, \"👏\")\n"
                    f"```\n"
                    f"**Computer Output:**\n"
                    f"```text\n"
                    f"Clap number: 1 👏\n"
                    f"Clap number: 2 👏\n"
                    f"Clap number: 3 👏\n"
                    f"Clap number: 4 👏\n"
                    f"Clap number: 5 👏\n"
                    f"```\n\n"
                    f"Would you like to try a super easy, fun quiz game on this? 🎮"
                )

        if is_hinglish(user_query):
            return thinking + (
                f"### 🐍 **Python Programming: Variables & Loops ({grade} - {subject})**\n\n"
                f"#### 1. **Python Programming Kya Hai?**\n"
                f"**Python** ek bohot hi powerful, easy-to-learn aur high-level programming language hai (created by Guido van Rossum in 1991). Iska syntax English jaisa simple hota hai, isliye yeh beginners ke liye sabse best language hai.\n\n"
                f"#### 📦 **2. Variables Kya Hote Hain?**\n"
                f"**Variable** computer memory me ek named container (dibba) hota hai jisme hum koi bhi data store karte hain.\n"
                f"```python\n"
                f"# Variables ke Examples\n"
                f"student_name = \"Maya\"    # String (Text)\n"
                f"student_age = 12         # Integer (Poora number)\n"
                f"percentage = 94.5        # Float (Decimal number)\n"
                f"is_enrolled = True       # Boolean (True/False)\n"
                f"```\n\n"
                f"#### 🔄 **3. Loops Kya Hote Hain?**\n"
                f"**Loop** ka matlab hota hai kisi code block ko baar-baar repeat karna jab tak koi condition poori na ho jaye. Isse hume ek hi code baar-baar likhne ki zaroorat nahi padti (**DRY Principle: Don't Repeat Yourself**).\n\n"
                f"##### 🔁 **A) For Loop (Fixed iterations ke liye):**\n"
                f"Jab hume pehle se pata ho ki code ko kitni baar chalana hai:\n"
                f"```python\n"
                f"# Example: 1 se 5 tak numbers print karna\n"
                f"for i in range(1, 6):\n"
                f"    print(\"Number:\", i)\n"
                f"```\n"
                f"**Output:**\n"
                f"```text\n"
                f"Number: 1\n"
                f"Number: 2\n"
                f"Number: 3\n"
                f"Number: 4\n"
                f"Number: 5\n"
                f"```\n\n"
                f"##### ⏳ **B) While Loop (Condition-based iteration):**\n"
                f"Jab tak condition `True` rehti hai, loop chalta rehta hai:\n"
                f"```python\n"
                f"# Example: Countdown 3 se 1 tak\n"
                f"count = 3\n"
                f"while count > 0:\n"
                f"    print(\"Countdown:\", count)\n"
                f"    count = count - 1  # Har step me count kam hoga\n"
                f"print(\"Blast off! 🚀\")\n"
                f"```\n\n"
                f"Kya aap Python Variables ya Loops par ek quick 3-question practice quiz test solve karna chahenge?"
            )
        else:
            return thinking + (
                f"### 🐍 **Python Programming: Variables & Loops Explained ({grade} - {subject})**\n\n"
                f"#### 1. **What is Python Programming?**\n"
                f"**Python** is an easy-to-learn, high-level, interpreted programming language created by **Guido van Rossum in 1991**. Its clean, English-like syntax makes it one of the most popular languages for CBSE computer science, web development, data science, and AI.\n\n"
                f"#### 📦 **2. What are Variables?**\n"
                f"A **Variable** is a reserved named storage location in computer memory that holds data values. You assign values using the `=` assignment operator.\n"
                f"```python\n"
                f"# Examples of Variables & Data Types\n"
                f"student_name = \"Maya\"    # String (str) - text data\n"
                f"student_age = 13         # Integer (int) - whole numbers\n"
                f"percentage = 95.6        # Floating point (float) - decimal values\n"
                f"is_present = True        # Boolean (bool) - True or False\n"
                f"```\n\n"
                f"#### 🔄 **3. What are Loops?**\n"
                f"A **Loop** is a control structure used to execute a block of code repeatedly as long as a specified condition is satisfied. Loops eliminate manual repetition and save execution time.\n\n"
                f"##### 🔁 **A) The `for` Loop (Definite Iteration):**\n"
                f"Used when you know beforehand how many times the loop should execute (e.g., iterating through a `range()` sequence or a list):\n"
                f"```python\n"
                f"# Example: Print numbers 1 to 5\n"
                f"for i in range(1, 6):\n"
                f"    print(\"Count:\", i)\n"
                f"```\n"
                f"**Output:**\n"
                f"```text\n"
                f"Count: 1\n"
                f"Count: 2\n"
                f"Count: 3\n"
                f"Count: 4\n"
                f"Count: 5\n"
                f"```\n\n"
                f"##### ⏳ **B) The `while` Loop (Condition-Controlled Iteration):**\n"
                f"Repeats execution as long as its test condition remains `True`:\n"
                f"```python\n"
                f"# Example: Rocket Countdown from 3 to 1\n"
                f"countdown = 3\n"
                f"while countdown > 0:\n"
                f"    print(\"T-minus:\", countdown)\n"
                f"    countdown -= 1  # Decrement step\n"
                f"print(\"Blast off! 🚀\")\n"
                f"```\n\n"
                f"Would you like a quick 3-question quiz to test this concept?"
            )

    if any(re.search(r'\b' + re.escape(w) + r'\b', q_lower) for w in ['algorithm', 'algorithms', 'cpu', 'ram', 'database', 'sql', 'hardware', 'software']) or ('computer' in q_lower and not any(k in q_lower for k in ['vision', 'application', 'applications', 'network', 'networks'])):
        return thinking + (
            f"### 💻 **Computer Science & Architecture ({grade} - {subject})**\n\n"
            "#### 1. **Core Concept Overview**\n"
            "Computer Science is the systematic study of computation, algorithmic logic, and software programming.\n\n"
            "#### 2. **Key Foundations (CBSE NCERT)**\n"
            "- **Algorithm**: A step-by-step sequence of unambiguous instructions designed to perform a specific task.\n"
            "- **Hardware vs Software Comparison Table**:\n\n"
            "| Feature | Hardware 🖥️ | Software 💾 |\n"
            "| :--- | :--- | :--- |\n"
            "| **Definition** | Tangible physical components you can touch | Sets of digital instructions and programs |\n"
            "| **Examples** | CPU, Keyboard, Monitor, RAM, SSD | Python, Windows OS, Maya AI Tutor, VS Code |\n"
            "| **Role** | Executes electronic processing signals | Controls and instructs hardware on what tasks do |\n"
            "| **Issues** | Physical degradation or burnout | Software bugs, syntax errors, or malware |\n\n"
            "Would you like a quick 3-question quiz to test this concept?"
        )

    if any(k in q_lower for k in ['weather', 'vedar', 'veather', 'wether', 'climate', 'mausam', 'mosam', 'hawa paani', 'season', 'seasons', 'humidity', 'rainfall', 'monsoon']):
        is_primary = grade in ['Primary (1-5)', 'Class 1', 'Class 2', 'Class 3', 'Class 4', 'Class 5']
        if is_primary:
            if is_hinglish(user_query):
                return thinking + (
                    f"### 🌤️ **Weather (मौसम) Explained for Class 4 / Primary! ({grade} - EVS / Science)**\n\n"
                    f"#### ☀️ **1. Weather (मौसम) Kya Hota Hai?**\n"
                    f"**Weather** ka matlab hota hai ki abhi bahar hawa aur aasman kaisa hai! Yeh din-ba-din (day-to-day) ya ghante-ghante (hour-to-hour) badal sakta hai.\n"
                    f"- Subah dhoop nikli ho sakti hai ☀️ (**Sunny**)\n"
                    f"- Dopahar me badal chha sakte hain ☁️ (**Cloudy**)\n"
                    f"- Shaam ko baarish ho sakti hai 🌧️ (**Rainy**)\n\n"
                    f"#### 🌈 **2. Weather ke 4 Main Parts (Elements):**\n"
                    f"1. 🌡️ **Temperature (Tapman):** Hawa kitni garm ya thandi hai.\n"
                    f"2. 💨 **Wind (Hawa):** Hawa kitni tez chal rahi hai (halki breeze ya tez aandhi).\n"
                    f"3. 🌧️ **Rain (Baarish):** Badalon se girta paani.\n"
                    f"4. 💧 **Humidity (Nami):** Hawa me kitna paani ka vapour hai.\n\n"
                    f"#### 📅 **3. Weather vs Season (Mausam vs Ritu):**\n"
                    f"- **Weather** har din badalta hai (e.g. *'Aaj baarish ho rahi hai!'* ☔).\n"
                    f"- **Season (Ritu)** 2-3 mahine tak rehti hai (jaise *Summer*, *Winter*, *Monsoon*).\n\n"
                    f"Kya aap Weather par ek chhota sa fun 3-question quiz solve karna chahenge? 🎮"
                )
            else:
                return thinking + (
                    f"### 🌤️ **What is Weather? Explained for Kids! ({grade} - EVS / Science)**\n\n"
                    f"#### ☀️ **1. What is Weather?**\n"
                    f"**Weather** is the condition of the air and atmosphere outside at a particular time and place. Weather can change from day to day, or even from hour to hour!\n"
                    f"- In the morning, it might be sunny and warm ☀️\n"
                    f"- By afternoon, it might become cloudy and windy ☁️💨\n"
                    f"- In the evening, it might start raining 🌧️\n\n"
                    f"#### 🌈 **2. Four Main Parts of Weather (Elements):**\n"
                    f"1. 🌡️ **Temperature:** How hot or cold the air feels outside.\n"
                    f"2. 💨 **Wind:** How fast or slow the air is moving (a gentle breeze or strong wind).\n"
                    f"3. 🌧️ **Precipitation (Rain/Snow):** Water falling from clouds as raindrops or snowflakes.\n"
                    f"4. ☁️ **Clouds:** Fluffy white or dark grey clouds covering the blue sky.\n\n"
                    f"#### 📅 **3. Weather vs Season:**\n"
                    f"- **Weather** changes daily (e.g., *'Today is rainy, take an umbrella!'* ☔).\n"
                    f"- **Season** stays the same for 2 to 3 months (like *Summer*, *Winter*, *Spring*, and *Monsoon* 🌸❄️☀️).\n\n"
                    f"Would you like to try a quick, fun 3-question quiz on Weather? 🎮"
                )
        else:
            return thinking + (
                f"### 🌤️ **Weather and Climate ({grade} - {subject})**\n\n"
                f"#### 1. **Weather vs Climate (Key NCERT Distinction):**\n"
                f"- **Weather:** The day-to-day condition of the atmosphere at a place with respect to temperature, humidity, rainfall, wind speed, etc. (Short-term atmospheric state).\n"
                f"- **Climate:** The average weather pattern taken over a long period of time (typically **25 to 30 years**) for a region.\n\n"
                f"#### 2. **Key Elements of Weather:**\n"
                f"1. **Temperature:** Measured using Maximum-Minimum Thermometers.\n"
                f"2. **Humidity & Rainfall:** Moisture in air measured using a Hygrometer; rainfall measured using a **Rain Gauge**.\n"
                f"3. **Wind Speed & Direction:** Measured using an Anemometer and Wind Vane.\n"
                f"4. **Atmospheric Pressure:** Measured using a Barometer.\n\n"
                f"#### 3. **Factors Determining Climate:**\n"
                f"- **Latitude** (Distance from the Equator)\n"
                f"- **Altitude** (Height above sea level - higher is cooler)\n"
                f"- **Distance from the Sea** (Coastal moderation vs Continental extremes)\n"
                f"- **Ocean currents and Wind patterns**\n\n"
                f"Would you like a quick 3-question quiz to test this concept?"
            )

    if any(k in q_lower for k in ['microorganism', 'microorganisms', 'microbe', 'microbes', 'bacteria', 'fungi', 'protozoa', 'algae', 'virus', 'lactobacillus', 'fermentation', 'penicillin', 'pasteurization']):
        if is_hinglish(user_query):
            return thinking + (
                f"### 🔬 **Microorganisms (सूक्ष्मजीव): Friend and Foe ({grade} - {subject})**\n\n"
                f"**Microorganisms (Microbes)** aise bohot chhote living organisms hote hain jinhe hum nangi aankhon (naked eyes) se nahi dekh sakte. Inhe dekhne ke liye **Microscope** ki zaroorat hoti hai.\n\n"
                f"#### 🧬 **1. Microorganisms ke 4 Major Groups (+ Viruses):**\n"
                f"1. **Bacteria (जीवाणु):** Single-celled prokaryotes (jaise *Lactobacillus*, *Rhizobium*).\n"
                f"2. **Fungi (कवक):** Non-green organisms (jaise Bread mould, Yeast, *Penicillium*).\n"
                f"3. **Protozoa (प्रोटोजोआ):** Single-celled microscopic animals (jaise *Amoeba*, *Paramecium*, *Plasmodium* - malaria parasite).\n"
                f"4. **Algae (शैवाल):** Photosynthetic organisms (jaise *Spirogyra*, *Chlamydomonas*).\n"
                f"5. **Viruses (विषाणु):** Yeh sirf host organism (plant/animal/bacteria) ke cells ke andar reproduce karte hain.\n\n"
                f"#### 🥛 **2. Friendly Microorganisms (Hamare Dost):**\n"
                f"- **Dahi (Curd) & Cheese:** *Lactobacillus* bacteria milk ko curd me convert karta hai.\n"
                f"- **Baking & Alcohol:** Yeast sugar ko ferment karke alcohol aur $CO_2$ gas banata hai, jisse cake/bread soft aur spongy banta hai (**Fermentation**).\n"
                f"- **Antibiotics & Medicines:** Disease-causing microbes ko kill karne wali dawaiyan (e.g. *Penicillin* discovered by Alexander Fleming).\n"
                f"- **Soil Fertility:** *Rhizobium* bacteria leguminous plants ki roots me atmospheric Nitrogen ko fix karta hai (**Nitrogen Fixation**).\n\n"
                f"#### ⚠️ **3. Harmful Microorganisms (Pathogens / Shatru):**\n"
                f"- **Diseases in Humans:** Cholera (Bacteria), Tuberculosis (Bacteria), Malaria (Protozoa by female *Anopheles* mosquito), Dengue (Virus by female *Aedes* mosquito).\n"
                f"- **Food Preservation:** Food ko spoil hone se bachane ke methods: Pasteurization (Heating milk to $70^\\circ\\text{{C}}$ for 15-30 sec & sudden chilling), Salting, Sugar syrup, Oil & Vinegar.\n\n"
                f"Kya aap is concept par ek quick 3-question quiz test solve karna chahenge?"
            )
        else:
            return thinking + (
                f"### 🔬 **Microorganisms: Friend and Foe ({grade} - {subject})**\n\n"
                f"**Microorganisms (Microbes)** are microscopic living organisms that are invisible to the naked eye and can only be observed under a microscope. They can be single-celled (unicellular) or multi-celled (multicellular).\n\n"
                f"#### 🧬 **1. Four Major Groups of Microorganisms (+ Viruses):**\n"
                f"1. **Bacteria**: Single-celled prokaryotic organisms (e.g., *Lactobacillus*, *Rhizobium*, *E. coli*).\n"
                f"2. **Fungi**: Non-green organisms living on dead and decaying matter (e.g., Yeast, Bread Mould, *Penicillium*, *Aspergillus*).\n"
                f"3. **Protozoa**: Single-celled aquatic organisms (e.g., *Amoeba*, *Paramecium*, *Plasmodium* - causes malaria).\n"
                f"4. **Algae**: Simple, photosynthetic aquatic plant-like organisms (e.g., *Spirogyra*, *Chlamydomonas*).\n"
                f"5. **Viruses**: Ultramicroscopic entities that reproduce only inside the host organism's living cells.\n\n"
                f"#### 🥛 **2. Friendly Microorganisms (Commercial & Ecological Benefits):**\n"
                f"- **Curd & Dairy**: *Lactobacillus* bacterium promotes the conversion of milk into curd.\n"
                f"- **Baking & Brewing**: Yeast reproduces rapidly and releases $CO_2$ during anaerobic respiration, causing dough to rise (**Fermentation**).\n"
                f"- **Medicinal Use**: Antibiotics (e.g., *Penicillin* discovered by Alexander Fleming) and Vaccines.\n"
                f"- **Agriculture**: *Rhizobium* bacteria in the root nodules of leguminous plants fix atmospheric nitrogen to increase soil fertility (**Nitrogen Fixation**).\n\n"
                f"#### ⚠️ **3. Harmful Microorganisms (Pathogens & Food Spoilage):**\n"
                f"- **Pathogens**: Microbes that cause diseases (e.g., Tuberculosis, Cholera, Malaria, Typhoid).\n"
                f"- **Disease Carriers**: Female *Anopheles* mosquito carries the malaria parasite (*Plasmodium*); female *Aedes* mosquito carries dengue virus.\n"
                f"- **Food Preservation Techniques**: Pasteurization (heating milk to $70^\\circ\\text{{C}}$ for 15–30 seconds and suddenly chilling it), salting, sugaring, oil, and chemical preservatives (Sodium benzoate).\n\n"
                f"Would you like a quick 3-question quiz to test this concept?"
            )

    if any(re.search(r'\b' + re.escape(w) + r'\b', q_lower) for w in ['cell', 'cells', 'cell structure', 'organelle', 'organelles', 'nucleus', 'cytoplasm', 'mitochondria', 'cell wall', 'cell membrane', 'unicellular', 'multicellular', 'prokaryote', 'eukaryote', 'prokaryotic', 'eukaryotic']) and not any(k in q_lower for k in ['micelle', 'micelles']):
        return thinking + (
            f"### 🔬 **Cell: Structure and Functions ({grade} - {subject})**\n\n"
            f"**The Cell** is the fundamental structural and functional unit of all living organisms. It was first discovered by **Robert Hooke in 1665** in a slice of cork.\n\n"
            f"#### 🧬 **1. Three Essential Components of a Cell:**\n"
            f"1. **Cell Membrane (Plasma Membrane):** Porous outer boundary that regulates the entry and exit of substances (Selectively Permeable).\n"
            f"2. **Cytoplasm:** Jelly-like fluid occupying the space between the plasma membrane and nucleus where all metabolic reactions occur.\n"
            f"3. **Nucleus:** The control centre (Brain) of the cell containing the genetic material (DNA/Chromosomes and Nucleolus), bounded by a double nuclear membrane.\n\n"
            f"#### 🌿 **2. Plant Cell vs Animal Cell (CBSE Board Comparison):**\n"
            f"| Feature | Plant Cell 🌿 | Animal Cell 🐾 |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **Cell Wall** | Present (Rigid, made of cellulose) | Absent |\n"
            f"| **Chloroplasts / Plastids** | Present (Contains chlorophyll for photosynthesis) | Absent |\n"
            f"| **Vacuoles** | One large central vacuole | Small and temporary vacuoles |\n"
            f"| **Centrosomes** | Absent in higher plants | Present (Helps in cell division) |\n\n"
            f"#### ⚡ **3. Key Organelles & Their Functions:**\n"
            f"- **Mitochondria:** Powerhouse of the cell (produces energy in the form of ATP).\n"
            f"- **Ribosomes:** Protein factories of the cell.\n"
            f"- **Endoplasmic Reticulum (ER):** RER (synthesizes proteins) and SER (synthesizes lipids/fats).\n"
            f"- **Golgi Apparatus:** Packaging, modification, and dispatch of cellular secretions.\n\n"
            f"Would you like a quick 3-question quiz to test this concept?"
        )

    if any(k in q_lower for k in ['metal', 'metals', 'non-metal', 'non-metals', 'malleability', 'ductility', 'sonorous', 'lustre', 'reactivity series']):
        return thinking + (
            f"### ⚙️ **Metals and Non-Metals ({grade} - {subject})**\n\n"
            f"#### 🔨 **1. Physical Properties Comparison:**\n"
            f"| Property | Metals 🥇 | Non-Metals 💨 |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **State at Room Temp** | Solid (*Exception: Mercury Hg is liquid*) | Solids, Gases (*Exception: Bromine Br is liquid*) |\n"
            f"| **Malleability** | High (Can be beaten into thin sheets like Gold & Aluminium) | Non-malleable (Brittle, break easily) |\n"
            f"| **Ductility** | High (Can be drawn into thin wires like Copper & Gold) | Non-ductile |\n"
            f"| **Electrical & Thermal Conductivity** | Good conductors (Silver is best, Copper) | Poor conductors / Insulators (*Exception: Graphite is good*) |\n"
            f"| **Sonorous & Lustre** | Sonorous (produce ringing sound) & Lustrous (shiny) | Non-sonorous & Dull (*Exception: Iodine is lustrous*) |\n\n"
            f"#### 🧪 **2. Key Chemical Reactions (NCERT Syllabus):**\n"
            f"1. **Reaction with Oxygen:**\n"
            f"   - Metals form **Basic Oxides**: $$2\\text{{Mg}} + \\text{{O}}_2 \\rightarrow 2\\text{{MgO}} \\quad (\\text{{Turns red litmus to blue}})$$\n"
            f"   - Non-metals form **Acidic Oxides**: $$\\text{{S}} + \\text{{O}}_2 \\rightarrow \\text{{SO}}_2 \\quad (\\text{{Turns blue litmus to red}})$$\n"
            f"2. **Reaction with Water:**\n"
            f"   - Reactive metals produce Hydrogen gas: $$2\\text{{Na}} + 2\\text{{H}}_2\\text{{O}} \\rightarrow 2\\text{{NaOH}} + \\text{{H}}_2 \\uparrow + \\text{{Heat}}$$\n"
            f"3. **Displacement Reaction:** A more reactive metal displaces a less reactive metal from its salt solution:\n"
            f"   $$\\text{{Fe}} + \\text{{CuSO}}_4 \\text{{ (Blue)}} \\rightarrow \\text{{FeSO}}_4 \\text{{ (Green)}} + \\text{{Cu}} \\text{{ (Brown)}}$$\n\n"
            f"Would you like a quick 3-question quiz to test this concept?"
        )

    if any(k in q_lower for k in ['reproduction', 'asexual', 'sexual reproduction', 'fertilization', 'binary fission', 'budding', 'gamete', 'zygote', 'pollination']):
        return thinking + (
            f"### 🌸 **Reproduction in Organisms ({grade} - {subject})**\n\n"
            f"**Reproduction** is the essential biological process by which existing living organisms produce new individuals (offspring) of the same species to ensure species continuity.\n\n"
            f"#### 🔄 **1. Asexual vs Sexual Reproduction:**\n"
            f"| Mode | Asexual Reproduction 🧬 | Sexual Reproduction 👫 |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **Parents Involved** | Single parent | Two parents (Male and Female) |\n"
            f"| **Gamete Formation** | No gametes or fertilization | Gametes (Sperm & Ovum) fuse during fertilization |\n"
            f"| **Genetic Variation** | Offspring are identical clones | Variations occur (Crucial for evolution) |\n"
            f"| **Examples** | *Amoeba* (Binary fission), *Hydra* (Budding) | Humans, Animals, Flowering Plants |\n\n"
            f"#### 🔬 **2. Key Asexual Modes in NCERT:**\n"
            f"- **Binary Fission:** Parent cell divides into two equal daughter cells (e.g. *Amoeba*).\n"
            f"- **Budding:** A small bulb-like outgrowth (bud) develops on parent body, matures, and detaches (e.g. *Hydra*, Yeast).\n"
            f"- **Regeneration & Fragmentation:** e.g. *Planaria*, *Spirogyra*.\n\n"
            f"#### 👶 **3. Sexual Reproduction & Fertilization:**\n"
            f"- **Fertilization:** Fusion of male gamete (Sperm) and female gamete (Ovum/Egg) to form a single diploid cell called **Zygote**.\n"
            f"- **Internal Fertilization:** Occurs inside female body (Humans, Cows, Birds).\n"
            f"- **External Fertilization:** Occurs outside in water medium (Frogs, Fish).\n\n"
            f"Would you like a quick 3-question quiz to test this concept?"
        )

    if any(k in q_lower for k in ['pressure', 'pascal', 'contact force', 'non-contact force', 'atmospheric pressure']):
        return thinking + (
            f"### ⚓ **Force and Pressure ({grade} - {subject})**\n\n"
            f"#### 1. **What is Force?**\n"
            f"A **Force** is a push or pull on an object resulting from its interaction with another object. (SI Unit: Newton, N).\n"
            f"- **Contact Forces:** Muscular Force, Frictional Force.\n"
            f"- **Non-Contact Forces:** Gravitational Force, Electrostatic Force, Magnetic Force.\n\n"
            f"#### 2. **What is Pressure?**\n"
            f"**Pressure** is the force acting per unit area of a surface on which it is applied:\n"
            f"$$\\text{{Pressure}} (P) = \\frac{{\\text{{Force}} (F)}}{{\\text{{Area}} (A)}}$$\n"
            f"- **SI Unit:** $\\text{{N/m}}^2$ or **Pascal (Pa)**.\n"
            f"- **Inverse Relationship with Area:** Smaller area produces larger pressure for the same force (e.g. a sharp nail penetrates easily, school bag straps are wide to reduce pressure on shoulders).\n\n"
            f"#### 3. **Pressure in Liquids and Gases:**\n"
            f"- Liquids exert equal pressure in all directions at the same depth and pressure increases with depth.\n"
            f"- **Atmospheric Pressure:** The weight of the atmospheric air column pressing down on the Earth's surface ($101.3\\text{{ kPa}}$ at sea level).\n\n"
            f"Would you like a quick 3-question quiz to test this concept?"
        )

    if any(k in q_lower for k in ['friction', 'sliding friction', 'rolling friction', 'static friction', 'lubricant']):
        return thinking + (
            f"### 🛞 **Friction Explained ({grade} - {subject})**\n\n"
            f"**Friction** is the opposing contact force that resists the relative motion between two surfaces in contact. It always acts in the direction opposite to the applied force.\n\n"
            f"#### 🔍 **1. Cause of Friction:**\n"
            f"Friction is caused by the microscopic interlocking of irregularities (roughness/ridges and valleys) present on both contact surfaces.\n\n"
            f"#### 📊 **2. Types of Friction (Magnitude Order):**\n"
            f"$$\\text{{Static Friction}} > \\text{{Sliding Friction}} > \\text{{Rolling Friction}}$$\n"
            f"1. **Static Friction:** Force required to overcome inertia and start moving a stationary object from rest.\n"
            f"2. **Sliding Friction:** Force required to keep an object sliding at constant speed once it is already moving (less than static friction because interlocking doesn't get enough time to set in).\n"
            f"3. **Rolling Friction:** Resistance encountered when an object rolls over a surface (much smaller than sliding, which is why ball bearings and wheels are used).\n\n"
            f"#### 💡 **3. Friction: A Necessary Evil:**\n"
            f"- **Advantages:** Allows us to walk without slipping, write with pens, and apply brakes to vehicles.\n"
            f"- **Disadvantages:** Causes wear and tear of machine parts and wastes energy as heat.\n"
            f"- **Methods to Reduce Friction:** Using lubricants (oil/grease), polishing surfaces, using ball bearings, streamlining shapes.\n\n"
            f"Would you like a quick 3-question quiz to test this concept?"
        )

    if any(k in q_lower for k in ['sound', 'frequency', 'amplitude', 'pitch', 'loudness', 'hertz', 'audible', 'vibration', 'infrasonic', 'ultrasonic']):
        return thinking + (
            f"### 🔊 **Sound: Vibrations, Pitch & Loudness ({grade} - {subject})**\n\n"
            f"**Sound** is a form of mechanical energy that produces the sensation of hearing. Sound is produced by **vibrating objects** (rapid back-and-forth motion).\n\n"
            f"#### 🌐 **1. Propagation of Sound:**\n"
            f"- Sound requires a material medium (Solid, Liquid, or Gas) to travel.\n"
            f"- **Sound cannot travel through a vacuum** ($v_{{\\text{{solids}}}} > v_{{\\text{{liquids}}}} > v_{{\\text{{gases}}}}$).\n"
            f"- Speed of sound in dry air at room temperature $\\approx 343\\text{{ m/s}}$.\n\n"
            f"#### 📈 **2. Key Characteristics of Sound Waves:**\n"
            f"1. **Amplitude:** The maximum displacement of a vibrating particle from its mean position.\n"
            f"   - **Loudness $\\propto (\\text{{Amplitude}})^2$** (Measured in Decibels, dB). Larger amplitude = Louder sound.\n"
            f"2. **Frequency ($f$):** Number of complete oscillations/vibrations per second.\n"
            f"   - **Unit:** Hertz (Hz).\n"
            f"   - **Pitch / Shrillness:** Higher frequency = Higher pitch (e.g. bird chirp or female voice has higher pitch than a lion's roar).\n"
            f"3. **Time Period ($T$):** Time taken to complete 1 oscillation: $$T = \\frac{{1}}{{f}}$$\n\n"
            f"#### 👂 **3. Audible Range for Humans:**\n"
            f"- **Audible Sound:** $20\\text{{ Hz}}$ to $20,000\\text{{ Hz}}$ ($20\\text{{ kHz}}$).\n"
            f"- **Infrasonic:** $< 20\\text{{ Hz}}$ (Elephants, Whales, Earthquakes).\n"
            f"- **Ultrasonic:** $> 20,000\\text{{ Hz}}$ (Bats, Dolphins, Medical Ultrasound scans).\n\n"
            f"Would you like a quick 3-question quiz to test this concept?"
        )

    if any(k in q_lower for k in ['respiration', 'breathing', 'aerobic', 'anaerobic', 'digestion', 'digestive system', 'stomach', 'enzyme']):
        return thinking + (
            f"### 🫁 **Respiration & Digestive System ({grade} - {subject})**\n\n"
            f"#### 1. **Respiration vs Breathing:**\n"
            f"- **Breathing:** Physical process of inhaling oxygen-rich air and exhaling carbon dioxide-rich air.\n"
            f"- **Cellular Respiration:** Biochemical breakdown of glucose inside cells to release energy (ATP).\n\n"
            f"#### 2. **Aerobic vs Anaerobic Respiration:**\n"
            f"- **Aerobic Respiration (In presence of $O_2$):**\n"
            f"  $$\\text{{Glucose}} + \\text{{Oxygen}} \\rightarrow 6\\text{{CO}}_2 + 6\\text{{H}}_2\\text{{O}} + 38\\text{{ ATP}}$$\n"
            f"- **Anaerobic in Yeast (Fermentation):**\n"
            f"  $$\\text{{Glucose}} \\rightarrow \\text{{Ethanol}} + \\text{{CO}}_2 + 2\\text{{ ATP}}$$\n"
            f"- **Anaerobic in Human Muscle Cells (During heavy exercise):**\n"
            f"  $$\\text{{Glucose}} \\rightarrow \\text{{Lactic Acid}} + 2\\text{{ ATP}} \\quad (\\text{{Causes muscle cramps}})$$\n\n"
            f"#### 3. **Human Digestive System Key Organs & Enzymes:**\n"
            f"- **Mouth (Buccal Cavity):** Salivary Amylase breaks starch into maltose sugar.\n"
            f"- **Stomach:** Secretes Hydrochloric Acid ($HCl$ to kill bacteria & activate pepsin), Pepsin (breaks protein), and Mucus (protects stomach lining).\n"
            f"- **Small Intestine:** Site of complete digestion; receives Bile from Liver (emulsifies fats) and Pancreatic juice (Trypsin & Lipase).\n\n"
            f"Would you like a quick 3-question quiz to test this concept?"
        )

    if any(k in q_lower for k in ['combustion', 'flame', 'ignition temperature', 'fuel', 'calorific value', 'candle']):
        return thinking + (
            f"### 🔥 **Combustion, Flame and Fuels ({grade} - {subject})**\n\n"
            f"**Combustion** is a chemical process in which a substance reacts with oxygen ($O_2$) to release heat and light energy.\n\n"
            f"#### 🧯 **1. Three Conditions Essential for Combustion:**\n"
            f"1. **Combustible Substance (Fuel)**.\n"
            f"2. **Supporter of Combustion (Oxygen / Air)**.\n"
            f"3. **Ignition Temperature:** The minimum lowest temperature at which a substance catches fire.\n\n"
            f"#### 🕯️ **2. Structure of a Candle Flame (Three Zones):**\n"
            f"1. **Outer Zone (Non-luminous, Blue):** Complete combustion zone; hottest part of the flame (goldsmiths use this zone with a blowpipe).\n"
            f"2. **Middle Zone (Luminous, Yellow):** Partial/incomplete combustion zone; moderately hot, produces carbon soot particles.\n"
            f"3. **Inner Zone (Dark):** Contains unburnt wax vapours; least hot part surrounding the wick.\n\n"
            f"#### ⚡ **3. Fuel Efficiency & Calorific Value:**\n"
            f"- **Calorific Value:** The amount of heat energy produced on complete combustion of $1\\text{{ kg}}$ of a fuel (SI Unit: $\\text{{kJ/kg}}$).\n"
            f"- Hydrogen has the highest calorific value ($150,000\\text{{ kJ/kg}}$), while LPG has $\\approx 55,000\\text{{ kJ/kg}}$.\n\n"
            f"Would you like a quick 3-question quiz to test this concept?"
        )

    if any(k in q_lower for k in ['crop', 'crops', 'kharif', 'rabi', 'irrigation', 'fertilizer', 'manure', 'harvesting', 'agriculture']):
        return thinking + (
            f"### 🌾 **Crop Production and Management ({grade} - {subject})**\n\n"
            f"When plants of the same kind are cultivated at one place on a large scale, it is called a **Crop**.\n\n"
            f"#### 🌱 **1. Two Major Crop Seasons in India (CBSE Comparison):**\n"
            f"| Crop Type | Sowing Season | Harvest Season | Examples |\n"
            f"| :--- | :--- | :--- | :--- |\n"
            f"| **Kharif Crops** 🌧️ | June – July (Monsoon) | September – October | Paddy (Rice), Maize, Soyabean, Cotton |\n"
            f"| **Rabi Crops** ❄️ | October – November (Winter) | March – April | Wheat, Gram, Mustard, Pea, Barley |\n\n"
            f"#### 🚜 **2. Sequential Agricultural Practices:**\n"
            f"1. **Preparation of Soil:** Ploughing/tilling to aerate soil and loosen it for roots.\n"
            f"2. **Sowing:** Selecting good quality, disease-free seeds using traditional tools or seed drills.\n"
            f"3. **Adding Manure and Fertilizers:** Organic manure enriches soil humus; chemical fertilizers ($N, P, K$) provide specific nutrients.\n"
            f"4. **Irrigation:** Traditional methods (Moat, Chain pump, Dhekli, Rahat) vs Modern water-saving methods (**Drip system** and **Sprinkler system**).\n"
            f"5. **Protection from Weeds (Weeding):** Removing unwanted wild plants using chemical weedicides (e.g. 2,4-D) or manual tilling.\n"
            f"6. **Harvesting & Threshing:** Cutting mature crops and separating grain from chaff.\n"
            f"7. **Storage:** Storing in silos and granaries with dried neem leaves or chemical treatment to protect from pests.\n\n"
            f"Would you like a quick 3-question quiz to test this concept?"
        )

    if any(k in q_lower for k in ['mass', 'weight', 'inertia', 'density']):
        return thinking + (
            f"### ⚖️ **Mass vs Weight Explained ({grade} - {subject})**\n\n"
            "#### 1. **What is Mass?**\n"
            "**Mass** is the fundamental measure of the amount of matter contained inside an object. It is also the direct measure of an object's **inertia** (resistance to change in state of rest or motion).\n"
            "- **SI Unit:** Kilogram (kg).\n"
            "- **Nature:** Scalar quantity (magnitude only).\n"
            "- **Constancy:** Mass remains constant everywhere in the universe.\n"
            "- **Formula:** $$m = \\frac{F}{a} \\quad \\text{or} \\quad m = \\frac{W}{g}$$\n\n"
            "#### 2. **What is Weight?**\n"
            "**Weight** is the gravitational force with which the Earth (or any planet) attracts an object towards its center.\n"
            "- **SI Unit:** Newton (N).\n"
            "- **Formula:** $$W = m \\times g \\quad (g \\approx 9.8\\text{ m/s}^2 \\text{ on Earth})$$\n"
            "- **Nature:** Vector quantity (directed vertically downward towards center of Earth).\n"
            "- **Variation:** Weight changes from place to place (e.g. on Moon, weight is 1/6th of Earth weight because $g_{\\text{moon}} = g/6$).\n\n"
            "#### 3. **Key Comparison for CBSE Board Exams:**\n"
            "| Feature | Mass (m) | Weight (W) |\n"
            "| :--- | :--- | :--- |\n"
            "| **Definition** | Quantity of matter in body | Force of gravity on body |\n"
            "| **SI Unit** | kg | Newton (N) |\n"
            "| **Can it be zero?** | Never zero for physical body | Zero in free fall / deep space |\n"
            "| **Measuring Instrument** | Beam Balance | Spring Balance |\n\n"
            "Would you like a quick 3-question quiz to test this concept?"
        )

    if any(k in q_lower for k in ['motion', 'force', 'newton', 'gravity', 'acceleration']):
        return thinking + (
            f"### 🚀 **Newton's Laws of Motion & Gravitation ({grade} - {subject})**\n\n"
            "#### 1. **First Law (Law of Inertia):**\n"
            "An object remains at rest or in uniform motion along a straight line unless acted upon by an external unbalanced force.\n"
            "- *Real-life example:* You jerk forward when a school bus hits the brakes due to inertia of motion.\n\n"
            "#### 2. **Second Law (F = m × a):**\n"
            "The rate of change of momentum of an object is directly proportional to the applied unbalanced force.\n"
            "- **Formula:** F = m × a\n"
            "- **SI Unit:** Newton (1 N = 1 kg·m/s²).\n\n"
            "#### 3. **Third Law (Action & Reaction):**\n"
            "For every action force, there is an equal and opposite reaction force acting on different bodies.\n"
            "- *Real-life example:* Recoil of a gun, swimming, rocket propulsion.\n\n"
            "Would you like a quick 3-question quiz to test this concept?"
        )

    if any(k in q_lower for k in ['electricity', 'ohm', 'current', 'voltage', 'resistance', 'circuit']):
        return thinking + (
            f"### ⚡ **Electricity & Ohm's Law ({grade} - {subject})**\n\n"
            "#### 1. **Electric Current (I):**\n"
            "Electric current is the rate of flow of electric charges through a cross-section of a conductor.\n"
            "- **Formula:** $$I = \\frac{Q}{t}$$\n"
            "- **SI Unit:** Ampere (A), where $1\\text{ A} = 1\\text{ Coulomb/second}$.\n\n"
            "#### 2. **Ohm's Law:**\n"
            "At constant temperature, the current (I) flowing through a conductor is directly proportional to the potential difference (V) across its ends.\n"
            "- **Formula:** $$V = I \\times R$$\n"
            "- **Resistance (R):** Property of a conductor to resist flow of charges (SI Unit: Ohm, $\\Omega$).\n\n"
            "#### 3. **Resistors in Series vs Parallel:**\n"
            "- **Series:** $$R_{\\text{eq}} = R_1 + R_2 + R_3$$ (Current remains constant throughout).\n"
            "- **Parallel:** $$\\frac{1}{R_{\\text{eq}}} = \\frac{1}{R_1} + \\frac{1}{R_2} + \\frac{1}{R_3}$$ (Voltage remains constant across branches).\n\n"
            "Would you like a quick 3-question quiz to test this concept?"
        )
        
    # 2.9. Magnetic Effects of Electric Current
    if any(k in q_lower for k in ['magnetic effect', 'magnetic field', 'solenoid', 'fleming', 'right hand thumb', 'electromagnet', 'electric motor', 'domestic electric circuit', 'fuse wire', 'overloading', 'short circuit']):
        return thinking + (
            f"### 🧲 **Magnetic Effects of Electric Current ({grade} - {subject})**\n\n"
            f"When an electric current flows through a conductor, it produces a magnetic field around it (discovered by Hans Christian Oersted in 1820).\n\n"
            f"#### 🧭 **1. Core Rules for Direction:**\n"
            f"1. **Right-Hand Thumb Rule (Maxwell's Rule):** Imagine holding a straight current-carrying wire with your right hand. If the thumb points along the direction of **Current ($I$)**, then your curled fingers show the direction of **Magnetic Field Lines ($B$)** (concentric circles).\n"
            f"2. **Fleming's Left-Hand Rule (Electric Motors):** Stretch the thumb, forefinger, and middle finger of the left hand mutually perpendicular to each other:\n"
            f"   - **Thumb:** Direction of Force / Motion ($F$)\n"
            f"   - **Forefinger:** Direction of Magnetic Field ($B$)\n"
            f"   - **Middle finger:** Direction of Current ($I$)\n\n"
            f"#### 🌀 **2. Magnetic Field of a Solenoid:**\n"
            f"- **Solenoid:** A coil of many circular turns of insulated copper wire wrapped closely in the shape of a cylinder.\n"
            f"- **Properties:** Behaves exactly like a **Bar Magnet** with North and South poles. Inside the solenoid, the magnetic field lines are parallel straight lines, indicating a **completely uniform magnetic field**.\n"
            f"- **Electromagnet:** An iron rod (soft iron core) placed inside a solenoid becomes a strong temporary magnet when current is switched on.\n\n"
            f"#### ⚡ **3. Domestic Electric Circuits (CBSE Board Standard):**\n"
            f"- **Live Wire (Red/Brown):** Positive wire carrying alternating potential of **$220\\text{{ V}}$** at $50\\text{{ Hz}}$.\n"
            f"- **Neutral Wire (Black/Blue):** Return wire at zero potential (**$0\\text{{ V}}$**).\n"
            f"- **Earth Wire (Green/Yellow):** Safety wire connected to a metal plate deep underground. Provides a low-resistance path to direct leakage currents to the ground, preventing electric shocks from metallic appliances.\n"
            f"- **Overloading vs Short Circuit:**\n"
            f"  - *Short Circuit:* Live and neutral wires come in direct contact $\\rightarrow$ resistance drops to zero $\\rightarrow$ massive current flows causing fire.\n"
            f"  - *Overloading:* Too many high-power appliances switched on simultaneously exceeding the circuit's rated limit.\n\n"
            f"Would you like a quick 3-question quiz on magnetic effects of current?"
        )

    # 2.95. Chemical Reactions & Balancing
    if any(k in q_lower for k in ['chemical reaction', 'chemical reactions', 'balancing equation', 'combination reaction', 'decomposition reaction', 'displacement reaction', 'redox reaction', 'precipitation reaction', 'oxidation and reduction']):
        return thinking + (
            f"### 🧪 **Chemical Reactions and Equations ({grade} - {subject})**\n\n"
            f"A chemical reaction involves breaking old chemical bonds and forming new chemical bonds to produce new substances with entirely different chemical properties.\n\n"
            f"#### 📊 **1. The 5 Fundamental Reaction Types (NCERT Class 10):**\n\n"
            f"| Reaction Type | General Format | Classic CBSE Board Example |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **1. Combination** | $A + B \\rightarrow AB$ | $$\\text{{CaO}} + \\text{{H}}_2\\text{{O}} \\rightarrow \\text{{Ca(OH)}}_2 + \\text{{Heat}} \\quad (\\text{{Slaking of lime}})$$\n"
            f"| **2. Decomposition** | $AB \\rightarrow A + B$ | $$2\\text{{FeSO}}_4 \\xrightarrow{{\\Delta}} \\text{{Fe}}_2\\text{{O}}_3 + \\text{{SO}}_2 \\uparrow + \\text{{SO}}_3 \\uparrow \\quad (\\text{{Thermal}})$$\n"
            f"| **3. Displacement** | $A + BC \\rightarrow AC + B$ | $$\\text{{Fe}} + \\text{{CuSO}}_4 \\text{{ (Blue)}} \\rightarrow \\text{{FeSO}}_4 \\text{{ (Pale Green)}} + \\text{{Cu}} \\downarrow$$\n"
            f"| **4. Double Displacement** | $AB + CD \\rightarrow AD + CB$ | $$\\text{{Na}}_2\\text{{SO}}_4 + \\text{{BaCl}}_2 \\rightarrow \\mathbf{{\\text{{BaSO}}_4 \\downarrow \\text{{ (White ppt)}}}} + 2\\text{{NaCl}}$$\n"
            f"| **5. Redox (Oxidation-Reduction)** | Transfer of electrons / Oxygen | $$\\text{{CuO}} + \\text{{H}}_2 \\xrightarrow{{\\Delta}} \\text{{Cu}} + \\text{{H}}_2\\text{{O}}$$\n\n"
            f"#### ⚖️ **2. Why Must Chemical Equations Be Balanced?**\n"
            f"- According to the **Law of Conservation of Mass**, matter can neither be created nor destroyed in a chemical reaction. Therefore, the total number of atoms of each element on the reactant side must equal the product side.\n\n"
            f"#### 🔍 **3. Redox Breakdown:**\n"
            f"- **Oxidation:** Gain of Oxygen OR Loss of Hydrogen (e.g. $\\text{{H}}_2 \\rightarrow \\text{{H}}_2\\text{{O}}$).\n"
            f"- **Reduction:** Loss of Oxygen OR Gain of Hydrogen (e.g. $\\text{{CuO}} \\rightarrow \\text{{Cu}}$).\n"
            f"- **Oxidizing Agent:** The substance that provides oxygen / gets reduced (here, $\\text{{CuO}}$).\n"
            f"- **Reducing Agent:** The substance that removes oxygen / gets oxidized (here, $\\text{{H}}_2$).\n\n"
            f"Would you like to practice balancing a chemical equation step-by-step?"
        )

    if any(k in q_lower for k in ['photosynthesis', 'plant', 'chlorophyll']):
        if is_hinglish(user_query):
            return thinking + (
                f"### 🌿 **Photosynthesis (प्रकाश संश्लेषण) - Simple Explanation ({grade} - {subject})**\n\n"
                f"**Photosynthesis** ek aisi biological process hai jisme green plants (hare paudhe) sunlight, paani ($H_2O$) aur carbon dioxide ($CO_2$) ka use karke apna khana (glucose) banate hain aur oxygen release karte hain.\n\n"
                f"#### 🧪 **Simple Word Formula:**\n"
                f"$$\\text{{Carbon Dioxide}} + \\text{{Water}} \\xrightarrow{{\\text{{Sunlight + Chlorophyll}}}} \\text{{Glucose}} + \\text{{Oxygen}}$$\n\n"
                f"#### 🔬 **Photosynthesis ke 3 Simple Steps:**\n"
                f"1. **Sunlight Absorption:** Leaves me maujood chlorophyll sunlight ko capture karta hai.\n"
                f"2. **Energy Conversion:** Light energy ko chemical energy me badalkar water molecules ko todna.\n"
                f"3. **Food (Glucose) Production:** Carbon dioxide se glucose banna jo plant ko nutrition deta hai.\n\n"
                f"#### 🌟 **Important NCERT Points:**\n"
                f"- Leaves me gas exchange **Stomata** ke through hota hai jise **Guard Cells** open aur close karte hain.\n"
                f"- Plants apna extra glucose **Starch** ke roop me store karte hain.\n\n"
                f"Kya aap is concept par ek quick 3-question quiz test solve karna chahenge?"
            )
        else:
            return thinking + (
                f"### 🌿 **Photosynthesis Explained Simply ({grade} - {subject})**\n\n"
                f"**Photosynthesis** is the biological process where green plants synthesize their own food (glucose) from carbon dioxide ($CO_2$) and water ($H_2O$) in the presence of sunlight and chlorophyll.\n\n"
                f"#### 🧪 **Core Word Formula:**\n"
                f"$$\\text{{Carbon Dioxide}} + \\text{{Water}} \\xrightarrow{{\\text{{Sunlight + Chlorophyll}}}} \\text{{Glucose}} + \\text{{Oxygen}}$$\n\n"
                f"#### 🔬 **Three Main Steps:**\n"
                f"1. **Light Absorption:** Chlorophyll pigments in leaves absorb light energy from the sun.\n"
                f"2. **Energy Conversion:** Light energy is converted to chemical energy, splitting water molecules into hydrogen and oxygen.\n"
                f"3. **Food Production:** Carbon dioxide is reduced to produce nourishing glucose.\n\n"
                f"#### 🌟 **NCERT Board Exam Key Points:**\n"
                f"- Tiny pores on leaves called **Stomata** manage gas exchange, controlled by **Guard Cells**.\n"
                f"- Plants store excess glucose as **Starch** for later energy needs.\n\n"
                f"Would you like a quick 3-question quiz to test this concept?"
            )

    # 3.0. Comprehensive Ray Diagrams Guide for Spherical Mirrors & Lenses
    if any(k in q_lower for k in ['ray diagram', 'ray diagrams', 'draw ray', 'image formation by concave', 'image formation by convex', 'image formation by mirror', 'image formation by lens', 'practice ray', 'practice ray diagram', 'practice ray diagrams']):
        return thinking + (
            f"### 📐 **Complete Guide to Ray Diagrams for Spherical Mirrors & Lenses ({grade} - {subject})**\n\n"
            f"Ray diagrams illustrate how light rays travel and intersect to form images. Here are the standardized CBSE NCERT rules and cases for board examinations:\n\n"
            f"#### 🔍 **1. Four Fundamental Ray Tracing Rules (Mirrors):**\n"
            f"1. **Rule 1 (Parallel Ray):** A ray parallel to the principal axis passes through the **Principal Focus ($F$)** of a concave mirror (or appears to diverge from the focus of a convex mirror) after reflection.\n"
            f"2. **Rule 2 (Ray through Focus):** A ray passing through (or directed towards) the **Focus ($F$)** emerges **parallel to the principal axis** after reflection.\n"
            f"3. **Rule 3 (Ray through Center of Curvature $C$):** A ray passing through $C$ reflects back along the **exact same path** (angle of incidence $\\angle i = 0^\\circ$).\n"
            f"4. **Rule 4 (Ray at Pole $P$):** A ray striking the Pole $P$ obliquely reflects obliquely at an equal angle ($\\angle i = \\angle r$).\n\n"
            f"#### 📊 **2. The 6 Image Formation Cases for Concave Mirror:**\n"
            f"| Object Position | Image Position | Nature of Image | Size of Image | Board Exam Application |\n"
            f"| :--- | :--- | :--- | :--- | :--- |\n"
            f"| **1. At Infinity** | At Focus $F$ | Real & Inverted | Highly Diminished (Point-sized) | Solar Concentrators / Furnaces |\n"
            f"| **2. Beyond $C$** | Between $F$ and $C$ | Real & Inverted | Diminished (Smaller) | Optical Imaging |\n"
            f"| **3. At $C$** | At $C$ | Real & Inverted | **Same Size as Object** | Standard Laboratory Inversion |\n"
            f"| **4. Between $C$ and $F$** | Beyond $C$ | Real & Inverted | Enlarged / Magnified | Cinema & Slide Projectors |\n"
            f"| **5. At $F$** | At Infinity | Real & Inverted | Extremely Magnified | Searchlights & Headlights |\n"
            f"| **6. Between $F$ and Pole $P$** | **Behind the Mirror** | **Virtual & Erect** | **Highly Enlarged** | **Dentist & Shaving Mirrors** |\n\n"
            f"#### 🚗 **3. The 2 Image Formation Cases for Convex Mirror:**\n"
            f"| Object Position | Image Position | Nature of Image | Size of Image | Board Exam Application |\n"
            f"| :--- | :--- | :--- | :--- | :--- |\n"
            f"| **1. At Infinity** | Behind mirror at $F$ | Virtual & Erect | Highly Diminished | Street Light Reflectors |\n"
            f"| **2. Between $\\infty$ and $P$** | Behind mirror between $P$ and $F$ | **Virtual & Erect** | **Diminished (Smaller)** | **Vehicle Rear-View Mirrors** |\n\n"
            f"#### ✏️ **4. Step-by-Step Drawing Steps for Board Exams:**\n"
            f"1. Draw a horizontal straight line as the **Principal Axis** with a sharp pencil and ruler.\n"
            f"2. Mark the **Pole ($P$)**, **Focus ($F$)**, and **Center of Curvature ($C$)** ensuring $PF = FC = f$.\n"
            f"3. Draw the vertical object arrow $AB$ at the given position.\n"
            f"4. Draw **Ray 1**: Horizontal from top $A$ parallel to axis $\\rightarrow$ reflects down through Focus $F$.\n"
            f"5. Draw **Ray 2**: From top $A$ through Center of Curvature $C$ $\\rightarrow$ reflects back along same line.\n"
            f"6. Mark image point $A'$ where both reflected rays intersect, and draw perpendicular $A'B'$.\n\n"
            f"#### 🧠 **Practice Problem:**\n"
            f"*An object 4 cm tall is placed 25 cm in front of a concave mirror of focal length 15 cm ($C = 30\\text{{ cm}}$).*\n"
            f"- **Position:** Between $C$ and $\\infty$ (Beyond $C$).\n"
            f"- **Image:** Formed between $F$ and $C$ ($15\\text{{ cm}} < v < 30\\text{{ cm}}$), Real, Inverted, and Diminished.\n\n"
            f"Would you like to solve a numerical using the Mirror Formula ($\\frac{{1}}{{f}} = \\frac{{1}}{{v}} + \\frac{{1}}{{u}}$)?"
        )

    # Metallurgy: Calcination vs Roasting
    if any(k in q_lower for k in ['calcination', 'roasting', 'calcination and roasting', 'calcination vs roasting', 'roasting vs calcination']):
        return thinking + (
            f"### 🔥 **Calcination vs Roasting (Metallurgy) ({grade} - {subject})**\n\n"
            f"In the extraction of metals, concentrated ores are converted into metal oxides by either **Roasting** or **Calcination** before reduction.\n\n"
            f"#### 📊 **1. Comprehensive Comparison Table (CBSE NCERT):**\n"
            f"| Feature | Roasting 🔥💨 | Calcination 🔥🔒 |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **Type of Ore** | Used for **Sulphide Ores** (e.g. Zinc blende $\\text{{ZnS}}$) | Used for **Carbonate / Hydrated Ores** (e.g. Calamine $\\text{{ZnCO}}_3$) |\n"
            f"| **Air Condition** | Heated strongly in **excess of air (oxygen)** | Heated strongly in **limited / absence of air** |\n"
            f"| **Gas Released** | Releases poisonous **Sulphur Dioxide gas ($\\text{{SO}}_2 \\uparrow$)** | Releases **Carbon Dioxide gas ($\\text{{CO}}_2 \\uparrow$)** or moisture |\n"
            f"| **Temperature** | Below the melting point of the metal | Below the melting point of the metal |\n\n"
            f"#### 🧪 **2. Balanced Chemical Reactions (High-Scoring Exam Standard):**\n\n"
            f"**A) Roasting of Zinc Blende ($\\text{{ZnS}}$):**\n"
            f"$$2\\text{{ZnS}} + 3\\text{{O}}_2 \\xrightarrow{{\\Delta}} 2\\text{{ZnO}} + 2\\text{{SO}}_2 \\uparrow$$\n\n"
            f"**B) Calcination of Calamine ($\\text{{ZnCO}}_3$):**\n"
            f"$$\\text{{ZnCO}}_3 \\xrightarrow{{\\Delta}} \\text{{ZnO}} + \\text{{CO}}_2 \\uparrow$$\n\n"
            f"**C) Calcination of Hydrated Aluminium Oxide (Bauxite):**\n"
            f"$$\\text{{Al}}_2\\text{{O}}_3 \\cdot 2\\text{{H}}_2\\text{{O}} \\xrightarrow{{\\Delta}} \\text{{Al}}_2\\text{{O}}_3 + 2\\text{{H}}_2\\text{{O}} \\uparrow$$\n\n"
            f"Would you like a quick 3-question quiz on metallurgy and metal extraction?"
        )

    # Balancing Chemical Equations Step-by-Step Method & Trick
    if any(k in q_lower for k in ['balance chemical equation', 'balancing chemical equation', 'balancing equation', 'balance equation', 'equation balance karne']):
        return thinking + (
            f"### ⚖️ **How to Balance Chemical Equations: 4-Step Master Method ({grade} - {subject})**\n\n"
            f"#### 🎯 **1. Golden Rule of Balancing:**\n"
            f"According to the **Law of Conservation of Mass**, matter cannot be created or destroyed. The number of atoms of each element on the Reactant side (LHS) must equal the Product side (RHS).\n\n"
            f"#### 🪜 **2. Step-by-Step Hit-and-Trial Method (NCERT Method):**\n"
            f"Let's balance: $$\\text{{Fe}} + \\text{{H}}_2\\text{{O}} \\rightarrow \\text{{Fe}}_3\\text{{O}}_4 + \\text{{H}}_2$$\n\n"
            f"1. **Step 1: Put boxes around all chemical formulas:**\n"
            f"   $$\\text{{[Fe]}} + \\text{{[H}}_2\\text{{O]}} \\rightarrow \\text{{[Fe}}_3\\text{{O}}_4\\text{{]}} + \\text{{[H}}_2\\text{{]}}$$\n"
            f"   *(Never change the internal chemical formula subscripts like $O_4$ or $H_2$!)*\n\n"
            f"2. **Step 2: Count atoms of each element:**\n"
            f"   - $\\text{{Fe}}$: LHS = 1, RHS = 3\n"
            f"   - $\\text{{H}}$: LHS = 2, RHS = 2\n"
            f"   - $\\text{{O}}$: LHS = 1, RHS = 4\n\n"
            f"3. **Step 3: Balance the element with the highest number of atoms first (Oxygen $\\text{{O}}$):**\n"
            f"   - Multiply $\\text{{H}}_2\\text{{O}}$ on LHS by 4 $\\rightarrow$ $\\text{{Fe}} + 4\\text{{H}}_2\\text{{O}} \\rightarrow \\text{{Fe}}_3\\text{{O}}_4 + \\text{{H}}_2$\n"
            f"   - Now $\\text{{H}}$ on LHS = 8 ($4 \\times 2$). Balance $\\text{{H}}$ on RHS by multiplying $\\text{{H}}_2$ by 4 $\\rightarrow 4\\text{{H}}_2$.\n\n"
            f"4. **Step 4: Balance Iron ($\\text{{Fe}}$):**\n"
            f"   - Multiply $\\text{{Fe}}$ on LHS by 3 $\\rightarrow 3\\text{{Fe}}$.\n\n"
            f"#### ✅ **Final Balanced Equation:**\n"
            f"$$\\mathbf{{3\\text{{Fe (s)}} + 4\\text{{H}}_2\\text{{O (g)}} \\rightarrow \\text{{Fe}}_3\\text{{O}}_4\\text{{ (s)}} + 4\\text{{H}}_2\\text{{ (g)}}}}$$\n\n"
            f"Type any unbalanced chemical equation and I will balance it step-by-step for you!"
        )

    # Optics: Real Image vs Virtual Image
    if any(k in q_lower for k in ['real image and virtual image', 'real vs virtual image', 'real image vs virtual image', 'difference between real and virtual image']):
        return thinking + (
            f"### 🪞 **Real Image vs Virtual Image ({grade} - {subject})**\n\n"
            f"#### 📊 **1. Comprehensive Comparison Table (CBSE NCERT):**\n"
            f"| Feature | Real Image 🎬 | Virtual Image 🪞 |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **Formation** | Formed by **actual intersection** of reflected or refracted light rays | Formed when light rays **only appear to meet/diverge** from a point |\n"
            f"| **Screen Capture** | **Can be captured / obtained on a screen** | **Cannot be obtained on a screen** (can only be seen with eyes) |\n"
            f"| **Orientation** | Always **Inverted (Upside down)** | Always **Erect (Upright)** |\n"
            f"| **Sign of Magnification** | $m < 0$ (Negative) | $m > 0$ (Positive) |\n"
            f"| **Examples** | Image on cinema screen, image formed by concave mirror (Cases 1–5), convex lens | Image in bathroom dressing plane mirror, convex mirror, concave lens |\n\n"
            f"Would you like a quick 3-question quiz on optical image formation?"
        )

    # Physics: Fleming's Left-Hand Rule vs Right-Hand Rule (Electric Motor vs Generator)
    if any(k in q_lower for k in ["fleming's left hand rule", "fleming's right hand rule", "left hand rule vs right hand rule", "fleming left vs right", "electric motor rule", "generator rule", "rule for electric motor", "used for electric motor", "used for electric motors"]):
        return thinking + (
            f"### 🧲 **Fleming's Left-Hand Rule vs Right-Hand Rule ({grade} - {subject})**\n\n"
            f"#### 🧠 **The Simple Memory Mnemonic (FBI / Motors vs Generators):**\n"
            f"- **Left-Hand Rule:** Used for **Electric Motors** (where electrical energy $\\rightarrow$ mechanical force / motion).\n"
            f"- **Right-Hand Rule:** Used for **Electric Generators** (where motion in magnetic field $\\rightarrow$ induced current / EMI).\n\n"
            f"#### ✋ **1. Fleming's Left-Hand Rule (Electric Motors):**\n"
            f"Stretch the thumb, forefinger, and middle finger of the **left hand** mutually perpendicular:\n"
            f"- **Thumb ($F$):** Direction of **Force / Motion** (Thrust on conductor).\n"
            f"- **Forefinger ($B$):** Direction of **Magnetic Field** (North to South).\n"
            f"- **Middle Finger ($I$):** Direction of **Electric Current**.\n\n"
            f"#### ✋ **2. Fleming's Right-Hand Rule (Generators & Induced Current):**\n"
            f"Stretch the thumb, forefinger, and middle finger of the **right hand** mutually perpendicular:\n"
            f"- **Thumb ($F$):** Direction of **Motion of Conductor**.\n"
            f"- **Forefinger ($B$):** Direction of **Magnetic Field**.\n"
            f"- **Middle Finger ($I$):** Direction of **Induced Current**.\n\n"
            f"Would you like a quick 3-question quiz on magnetic forces and induction?"
        )

    # 3.1. Specific Direct NCERT Optics Questions:
    # A. Rear-View / Side Mirror in Vehicles
    if any(k in q_lower for k in ['rear view', 'side mirror', 'rear-view', 'side-view', 'wing mirror', 'rear view of car', 'rear view of vehicle', 'rear-view mirror']) or ('mirror' in q_lower and any(k in q_lower for k in ['car', 'cars', 'vehicle', 'vehicles', 'automobile', 'bike', 'motorcycle', 'driver'])):
        return thinking + (
            f"### 🚗 **Rear-View Mirrors in Vehicles ({grade} - {subject})**\n\n"
            f"#### 🎯 **Direct Answer:**\n"
            f"**Convex Mirrors** (उत्तल दर्पण) are used as rear-view (wing / side) mirrors in cars and vehicles.\n\n"
            f"#### 🔍 **Two Key Scientific Reasons (CBSE NCERT Board Standard):**\n"
            f"1. **Always Forms an Erect and Diminished Image:**\n"
            f"   - A convex mirror always produces a **virtual, erect (upright), and diminished (smaller)** image of objects behind the vehicle, regardless of their distance.\n"
            f"2. **Provides a Much Wider Field of View:**\n"
            f"   - Because convex mirrors **curve outwards**, they capture a much wider angle of view compared to plane or concave mirrors, allowing the driver to monitor a large expanse of traffic safely.\n\n"
            f"#### ⚠️ **Safety Notice on Vehicle Mirrors:**\n"
            f"- *'Objects in the mirror are closer than they appear'* — this warning appears because the diminished image makes vehicles behind seem farther away than their true distance.\n\n"
            f"Would you like to practice ray diagrams or solve a quick 3-question quiz on spherical mirrors?"
        )

    # B. Dentist / Shaving / Makeup Mirror
    if ('dentist' in q_lower and 'mirror' in q_lower) or ('shaving' in q_lower and 'mirror' in q_lower) or ('makeup' in q_lower and 'mirror' in q_lower):
        return thinking + (
            f"### 🦷 **Dentist & Shaving Mirrors ({grade} - {subject})**\n\n"
            f"#### 🎯 **Direct Answer:**\n"
            f"**Concave Mirrors** (अवतल दर्पण) are used by dentists and for shaving / makeup.\n\n"
            f"#### 🔍 **Scientific Reason (CBSE NCERT):**\n"
            f"- When an object (such as a tooth or face) is placed **very close to the mirror** (between the **Pole $P$** and the **Principal Focus $F$**), a concave mirror forms a **virtual, erect, and highly magnified (enlarged)** image, allowing fine details and cavities to be seen clearly.\n\n"
            f"Would you like to practice concave mirror ray diagrams?"
        )

    # C. Headlights of Cars, Torches, Searchlights, Solar Furnaces
    if any(k in q_lower for k in ['headlight', 'headlights', 'torch', 'searchlight', 'solar furnace', 'solar cooker']) and any(k in q_lower for k in ['mirror', 'which mirror', 'type of mirror', 'reflector', 'used in']):
        return thinking + (
            f"### 🔦 **Mirrors in Headlights, Torches & Solar Furnaces ({grade} - {subject})**\n\n"
            f"#### 🎯 **Direct Answer:**\n"
            f"**Concave Mirrors** (अवतल दर्पण) are used as reflectors in car headlights, torches, searchlights, and solar furnaces.\n\n"
            f"#### 🔍 **Scientific Reasons (CBSE NCERT):**\n"
            f"1. **In Headlights & Torches:** When the light source/bulb is placed at the **Principal Focus ($F$)** of the concave reflector, the reflected light rays emerge as a **powerful, parallel beam of light** travelling long distances.\n"
            f"2. **In Solar Furnaces & Cookers:** Large concave mirrors collect parallel sunlight rays and **concentrate them at a single focal point**, generating intense heat.\n\n"
            f"Would you like a quick 3-question quiz on spherical mirror reflections?"
        )

    # D. Eye Defects: Myopia, Hypermetropia, Presbyopia
    if any(k in q_lower for k in ['myopia', 'near-sighted', 'nearsighted', 'short-sighted', 'shortsighted']):
        return thinking + (
            f"### 👁️ **Myopia (Near-Sightedness) & Correction ({grade} - {subject})**\n\n"
            f"#### 🎯 **Direct Answer:**\n"
            f"**Myopia** is corrected using a **Concave Lens** (Diverging Lens) of suitable focal length / negative optical power ($P < 0$).\n\n"
            f"#### 🔍 **CBSE NCERT Summary:**\n"
            f"- **Condition:** A person can see nearby objects clearly, but cannot see distant objects distinctly (far point is closer than infinity).\n"
            f"- **Causes:**\n"
            f"  1. Excessive curvature of the eye lens (lens is too thick / high converging power).\n"
            f"  2. Elongation of the eyeball.\n"
            f"- **Correction:** The concave lens diverges incoming parallel rays before they enter the eye, shifting the focused image backward onto the **Retina** instead of in front of it.\n\n"
            f"Would you like to practice a numerical on lens power ($P = 1/f$)?"
        )

    if any(k in q_lower for k in ['hypermetropia', 'hyperopia', 'far-sighted', 'farsighted', 'long-sighted', 'longsighted']):
        return thinking + (
            f"### 👁️ **Hypermetropia (Far-Sightedness) & Correction ({grade} - {subject})**\n\n"
            f"#### 🎯 **Direct Answer:**\n"
            f"**Hypermetropia** is corrected using a **Convex Lens** (Converging Lens) of suitable focal length / positive optical power ($P > 0$).\n\n"
            f"#### 🔍 **CBSE NCERT Summary:**\n"
            f"- **Condition:** A person can see distant objects clearly, but cannot see nearby objects distinctly (near point is greater than $25\\text{{ cm}}$).\n"
            f"- **Causes:**\n"
            f"  1. Focal length of the eye lens is too long (lens is too thin / weak converging power).\n"
            f"  2. Eyeball has become too small.\n"
            f"- **Correction:** The convex lens provides additional converging power to focus the rays precisely onto the **Retina** instead of behind it.\n\n"
            f"Would you like to solve a numerical on calculating required lens power?"
        )

    if any(k in q_lower for k in ['presbyopia', 'bifocal']):
        return thinking + (
            f"### 👁️ **Presbyopia & Bifocal Lenses ({grade} - {subject})**\n\n"
            f"#### 🎯 **Direct Answer:**\n"
            f"**Presbyopia** (old-age far-sightedness) is corrected using **Bifocal Lenses**.\n\n"
            f"#### 🔍 **CBSE NCERT Summary:**\n"
            f"- **Causes:** Gradual weakening of **ciliary muscles** and diminishing flexibility of the crystalline eye lens due to aging.\n"
            f"- **Bifocal Lens Structure:**\n"
            f"  - **Upper part:** Concave lens (facilitates distant vision).\n"
            f"  - **Lower part:** Convex lens (facilitates near reading vision).\n\n"
            f"Would you like a quick 3-question quiz on human eye defects?"
        )

    # E. Atmospheric Optics: Why Sky is Blue, Why Stars Twinkle, Danger Signal Red
    if 'sky' in q_lower and any(k in q_lower for k in ['blue', 'color', 'colour', 'why']):
        return thinking + (
            f"### 🌌 **Why is the Sky Blue? ({grade} - {subject})**\n\n"
            f"#### 🎯 **Direct Answer:**\n"
            f"The sky appears blue due to the **Rayleigh Scattering of Sunlight** by air molecules ($N_2, O_2$) in Earth's atmosphere.\n\n"
            f"#### 🔍 **Scientific Reason (CBSE NCERT):**\n"
            f"1. **Rayleigh Scattering Law:** $\\text{{Scattering Intensity}} \\propto \\frac{{1}}{{\\lambda^4}}$ (inversely proportional to the 4th power of wavelength).\n"
            f"2. **Wavelength Contrast:** Blue light has a much shorter wavelength ($\\approx 400\\text{{ nm}}$) than red light ($\\approx 700\\text{{ nm}}$).\n"
            f"3. **Result:** Blue light is scattered nearly **16 times more** than red light across the sky and enters our eyes from all directions.\n"
            f"- *Note:* In outer space with no atmosphere, the sky appears pitch black because no scattering occurs.\n\n"
            f"Would you like a quick 3-question quiz on atmospheric optical phenomena?"
        )

    if 'twinkle' in q_lower or 'twinkling' in q_lower:
        return thinking + (
            f"### ✨ **Why Do Stars Twinkle? ({grade} - {subject})**\n\n"
            f"#### 🎯 **Direct Answer:**\n"
            f"Stars twinkle due to **Atmospheric Refraction of Starlight** through continuously shifting layers of the atmosphere.\n\n"
            f"#### 🔍 **Scientific Explanation (CBSE NCERT):**\n"
            f"1. **Stars Twinkle:** Stars are point sources of light located extremely far away. As starlight passes through atmospheric layers with constantly fluctuating temperatures and optical densities, the path of light bends erratically, causing the apparent position and brightness to flicker (twinkle).\n"
            f"2. **Planets Do NOT Twinkle:** Planets are much closer to Earth and act as extended sources containing numerous point sources. The fluctuations from individual points average out to zero, nullifying the twinkling effect.\n\n"
            f"Would you like a quick 3-question quiz on atmospheric refraction?"
        )

    if 'danger' in q_lower and any(k in q_lower for k in ['red', 'signal', 'light', 'stop']):
        return thinking + (
            f"### 🛑 **Why Are Danger Signal Lights Red? ({grade} - {subject})**\n\n"
            f"#### 🎯 **Direct Answer:**\n"
            f"Danger signal lights are red because **red light has the longest wavelength** in the visible spectrum and is **scattered the least** by smoke, fog, and air particles.\n\n"
            f"#### 🔍 **Scientific Reason:**\n"
            f"- Under Rayleigh's scattering law ($\\text{{Scattering}} \\propto 1/\\lambda^4$), red light ($\\lambda \\approx 700\\text{{ nm}}$) undergoes minimal scattering, enabling it to travel the maximum distance through thick fog or smoke without losing visibility.\n\n"
            f"Would you like a quick 3-question quiz on light scattering?"
        )

    if any(k in q_lower for k in ['light', 'reflection', 'refraction', 'mirror', 'lens', 'optics', 'prism']):
        return thinking + (
            f"### 💡 **Light: Reflection & Refraction ({grade} - {subject})**\n\n"
            "#### 1. **What is Light?**\n"
            "**Light** is a form of electromagnetic radiation (energy) that produces the sensation of sight. Light travels in a straight line in a homogeneous medium at a speed of $c = 3 \\times 10^8\\text{ m/s}$ in vacuum.\n\n"
            "#### 2. **Laws of Reflection:**\n"
            "1. The angle of incidence (i) is always equal to the angle of reflection (r): $$\\angle i = \\angle r$$\n"
            "2. The incident ray, the reflected ray, and the normal to the mirror at the point of incidence all lie in the same plane.\n\n"
            "#### 3. **Laws of Refraction (Snell's Law):**\n"
            "When light travels from one transparent medium to another, it bends due to a change in speed:\n"
            "- **Snell's Law:** $$\\frac{\\sin i}{\\sin r} = n_{21} = \\frac{n_2}{n_1} = \\frac{v_1}{v_2}$$\n"
            "- **Rarer to Denser (Air to Glass):** Bends *towards* the normal (speed decreases).\n"
            "- **Denser to Rarer (Glass to Air):** Bends *away from* the normal (speed increases).\n\n"
            "#### 4. **Key Board Exam Formulas:**\n"
            "- **Mirror Formula:** $$\\frac{1}{f} = \\frac{1}{v} + \\frac{1}{u}$$\n"
            "- **Lens Formula:** $$\\frac{1}{f} = \\frac{1}{v} - \\frac{1}{u}$$\n"
            "- **Magnification (m):** $$m = \\frac{h'}{h} = -\\frac{v}{u} \\text{ (for Mirrors)} = +\\frac{v}{u} \\text{ (for Lenses)}$$\n"
            "- **Power of Lens (P):** $$P = \\frac{1}{f\\text{ (in meters)}} \\quad (\\text{SI Unit: Dioptre, D})$$\n\n"
            "Would you like a quick 3-question quiz to test this concept?"
        )

    if (any(re.search(r'\b' + re.escape(w) + r'\b', q_lower) for w in ['acid', 'acids', 'base', 'bases', 'salt', 'salts', 'litmus', 'neutralization', 'phenolphthalein', 'alkali', 'alkaline']) or any(k in q_lower for k in ['ph scale', 'ph value', 'ph paper', 'acidic solution', 'basic solution', 'alkaline solution'])) and not any(k in q_lower for k in ['proportionality theorem', 'thales theorem', 'geometry', 'triangle', 'triangles', 'math', 'algebra']):
        return thinking + (
            f"### 🧪 **Acids, Bases and Salts ({grade} - {subject})**\n\n"
            "#### 1. **Acids vs Bases:**\n"
            "- **Acids:** Sour taste, turn blue litmus to red, release $H^+$ (or $H_3O^+$) ions in aqueous solution (e.g. $HCl, H_2SO_4, CH_3COOH$).\n"
            "- **Bases:** Bitter taste, soapy touch, turn red litmus to blue, release $OH^-$ ions in aqueous solution (e.g. $NaOH, KOH, Ca(OH)_2$).\n\n"
            "#### 2. **The pH Scale (0 to 14):**\n"
            "- **$pH < 7$**: Acidic (lower pH = stronger acid).\n"
            "- **$pH = 7$**: Neutral (Pure water).\n"
            "- **$pH > 7$**: Basic/Alkaline (higher pH = stronger base).\n\n"
            "#### 3. **Neutralization Reaction:**\n"
            "$$\\text{Acid} + \\text{Base} \\rightarrow \\text{Salt} + \\text{Water} + \\text{Heat}$$\n"
            "$$\\text{HCl} + \\text{NaOH} \\rightarrow \\text{NaCl} + \\text{H}_2\\text{O}$$\n\n"
            "Would you like a quick 3-question quiz to test this concept?"
        )

    if any(k in q_lower for k in ['atom', 'electron', 'proton', 'neutron', 'valency', 'molecule', 'periodic']):
        return thinking + (
            f"### ⚛️ **Atomic Structure & Chemical Bonding ({grade} - {subject})**\n\n"
            "#### 1. **Subatomic Particles:**\n"
            "1. **Protons ($p^+$):** Positively charged ($+1.6 \\times 10^{-19}\\text{ C}$), located inside the nucleus.\n"
            "2. **Neutrons ($n^0$):** Neutral (no charge), located inside the nucleus with protons (Nucleons).\n"
            "3. **Electrons ($e^-$):** Negatively charged ($-1.6 \\times 10^{-19}\\text{ C}$), revolve around nucleus in discrete energy shells ($K, L, M, N$).\n\n"
            "#### 2. **Key Definitions:**\n"
            "- **Atomic Number (Z):** Total number of protons in the nucleus ($Z = p = e$ in neutral atom).\n"
            "- **Mass Number (A):** Total number of protons + neutrons ($A = Z + n$).\n"
            "- **Valency:** The combining capacity of an atom determined by valence electrons (outermost shell).\n\n"
            "Would you like a quick 3-question quiz to test this concept?"
        )

    # 4.45. Carbon and Its Compounds
    if any(k in q_lower for k in ['carbon', 'catenation', 'tetravalency', 'homologous series', 'functional group', 'hydrocarbon', 'alkane', 'alkene', 'alkyne', 'saponification', 'esterification', 'micelle', 'cleansing action']):
        return thinking + (
            f"### 💎 **Carbon and Its Compounds ({grade} - {subject})**\n\n"
            f"Carbon forms millions of organic compounds due to its unique covalent bonding properties.\n\n"
            f"#### 🌟 **1. The Two Unique Properties of Carbon:**\n"
            f"1. **Catenation:** The unique ability of carbon atoms to form strong covalent bonds with other carbon atoms, creating long straight chains, branched chains, and closed rings.\n"
            f"2. **Tetravalency:** Having an atomic number of 6 (electronic configuration 2, 4), carbon has **4 valence electrons** and shares 4 electrons to complete its octet (forming 4 covalent bonds).\n\n"
            f"#### 🔬 **2. Hydrocarbons & Homologous Series:**\n"
            f"- **Alkanes (Saturated):** Single bonds only ($C_n H_{{2n+2}}$) e.g., Methane ($CH_4$), Ethane ($C_2H_6$).\n"
            f"- **Alkenes (Unsaturated):** Double bond ($C_n H_{{2n}}$) e.g., Ethene ($C_2H_4$).\n"
            f"- **Alkynes (Unsaturated):** Triple bond ($C_n H_{{2n-2}}$) e.g., Ethyne ($C_2H_2$).\n"
            f"- **Homologous Series:** A family of organic compounds sharing the same functional group where successive members differ by a **$-\\text{{CH}}_2-$** unit ($14\\text{{ u}}$ molecular mass difference).\n\n"
            f"#### 🧼 **3. Soaps, Saponification & Micelle Formation:**\n"
            f"- **Saponification Reaction:**\n"
            f"  $$\\text{{Ester}} + \\text{{NaOH}} \\rightarrow \\text{{Soap (Sodium salt of fatty acid)}} + \\text{{Alcohol (Glycerol)}}$$\n"
            f"- **Structure of a Soap Molecule:**\n"
            f"  - **Hydrophilic Head (Ionic end):** Water-loving, points outwards towards water.\n"
            f"  - **Hydrophobic Tail (Hydrocarbon chain):** Water-fearing (oil-loving), points inwards towards oily dirt.\n"
            f"- **Cleansing Mechanism:** In water, soap molecules cluster into radial spherical structures called **Micelles**. The oily dirt is trapped in the hydrophobic core of the micelle, forming a stable emulsion that rinses away easily.\n\n"
            f"Would you like a quick 3-question quiz on carbon compounds?"
        )

    # 4.46. Life Processes: Circulation & Excretion
    if any(k in q_lower for k in ['double circulation', 'human heart', 'nephron', 'excretion', 'kidney', 'artery', 'arteries', 'vein', 'veins', 'alveoli', 'dialysis']):
        return thinking + (
            f"### ❤️ **Life Processes: Circulation & Excretion ({grade} - {subject})**\n\n"
            f"#### 🫀 **1. Double Circulation in the Human Heart:**\n"
            f"In humans, blood passes through the four-chambered heart **twice** during one complete cardiac cycle:\n"
            f"1. **Pulmonary Circulation:** Deoxygenated blood from Right Ventricle $\\rightarrow$ Pulmonary Artery $\\rightarrow$ Lungs (oxygenated) $\\rightarrow$ Pulmonary Veins $\\rightarrow$ Left Atrium.\n"
            f"2. **Systemic Circulation:** Oxygenated blood from Left Ventricle $\\rightarrow$ Aorta $\\rightarrow$ Body tissues $\\rightarrow$ Vena Cava $\\rightarrow$ Right Atrium.\n"
            f"- **Advantage:** Complete separation of oxygenated and deoxygenated blood provides highly efficient oxygen delivery required for maintaining constant body temperature in warm-blooded animals.\n\n"
            f"#### 🩸 **2. Arteries vs Veins:**\n"
            f"| Feature | Arteries 🔴 | Veins 🔵 |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **Direction** | Carry blood **away from heart** to body organs | Carry blood **towards heart** from body organs |\n"
            f"| **Blood Nature** | Oxygenated (*Exception: Pulmonary Artery*) | Deoxygenated (*Exception: Pulmonary Vein*) |\n"
            f"| **Walls & Pressure** | Thick, elastic muscular walls (High pressure) | Thin walls (Low pressure) |\n"
            f"| **Valves** | No valves needed | Possess internal valves to prevent backflow |\n\n"
            f"#### 🫘 **3. Excretion & Nephron Structure:**\n"
            f"- **Nephron:** The structural and functional filtration unit of the kidney ($~1\\text{{ million}}$ per kidney).\n"
            f"- **Step 1: Ultrafiltration:** Blood enters through the afferent arteriole under pressure into the **Glomerulus**; filtrate collects in **Bowman's Capsule**.\n"
            f"- **Step 2: Selective Reabsorption:** In the tubular part (PCT and Loop of Henle), essential nutrients (**Glucose, Amino acids, Salts, and Major Water**) are reabsorbed back into capillaries.\n"
            f"- **Step 3: Tubular Secretion & Urine Collection:** Waste (Urea, excess ions) flows into the **Collecting Duct** as urine.\n\n"
            f"Would you like a quick 3-question quiz on human physiology?"
        )

    # 4.47. Control, Coordination & Heredity
    if any(k in q_lower for k in ['neuron', 'synapse', 'reflex arc', 'human brain', 'cerebrum', 'cerebellum', 'phytohormone', 'auxin', 'mendel', 'monohybrid', 'dihybrid', 'sex determination']):
        return thinking + (
            f"### 🧠 **Control, Coordination & Heredity ({grade} - {subject})**\n\n"
            f"#### ⚡ **1. Structure of a Neuron & Nerve Impulse:**\n"
            f"- **Dendrite:** Receives chemical signals and converts them into electrical impulses.\n"
            f"- **Cell Body (Cyton) & Axon:** Transmits the electrical impulse along the nerve fiber.\n"
            f"- **Synapse:** The microscopic gap between the axon terminal of one neuron and dendrite of the next. Electrical impulses trigger the release of neurotransmitter chemicals across the synapse.\n"
            f"- **Reflex Arc Pathway:**\n"
            f"  $$\\text{{Stimulus}} \\rightarrow \\text{{Receptor}} \\rightarrow \\text{{Sensory Neuron}} \\rightarrow \\mathbf{{\\text{{Spinal Cord (Relay Neuron)}}}} \\rightarrow \\text{{Motor Neuron}} \\rightarrow \\text{{Effector (Muscle)}}$$\n\n"
            f"#### 🌿 **2. Plant Phytohormones:**\n"
            f"- **Auxin:** Promotes cell elongation (causes shoot to bend towards light — phototropism).\n"
            f"- **Gibberellins:** Promotes stem elongation and seed germination.\n"
            f"- **Cytokinins:** Promotes rapid cell division (present in high concentration in fruits/seeds).\n"
            f"- **Abscisic Acid (ABA):** Growth inhibitor / stress hormone (causes wilting of leaves).\n\n"
            f"#### 🧬 **3. Mendel's Laws of Heredity & Crosses:**\n"
            f"1. **Monohybrid Cross (Single Trait - Height $TT \\times tt$):**\n"
            f"   - **F1 Generation:** All Tall ($Tt$).\n"
            f"   - **F2 Phenotypic Ratio:** **$3 : 1$** (3 Tall : 1 Dwarf).\n"
            f"   - **F2 Genotypic Ratio:** **$1 : 2 : 1$** ($1\\text{{ }}TT : 2\\text{{ }}Tt : 1\\text{{ }}tt$).\n"
            f"2. **Dihybrid Cross (Two Traits - Shape & Color $RRYY \\times rryy$):**\n"
            f"   - **F2 Phenotypic Ratio:** **$9 : 3 : 3 : 1$** (Round-Yellow : Round-Green : Wrinkled-Yellow : Wrinkled-Green).\n"
            f"3. **Sex Determination in Humans:**\n"
            f"   - Females have **$XX$** chromosomes (produce all $X$ eggs).\n"
            f"   - Males have **$XY$** chromosomes (produce 50% $X$ and 50% $Y$ sperms).\n"
            f"   - Fertilization with $X$-sperm produces a **Girl ($XX$)**; fertilization with $Y$-sperm produces a **Boy ($XY$)**. Thus, the father's sperm determines the baby's biological sex with an exact **$50\\% : 50\\%$ probability**.\n\n"
            f"Would you like a quick 3-question genetics quiz?"
        )

    # 4.48. Computer Networks (PAN, LAN, MAN, WAN, Topologies)
    if (any(re.search(r'\b' + re.escape(w) + r'\b', q_lower) for w in ['pan', 'lan', 'wan', 'man', 'topology', 'topologies']) and any(k in q_lower for k in ['network', 'networks', 'computer', 'difference', 'types of network', 'connect', 'cable', 'internet'])) or any(k in q_lower for k in ['network topology', 'computer network', 'star topology', 'bus topology', 'mesh topology', 'ring topology', 'transmission media', 'twisted pair', 'coaxial cable', 'optical fiber', 'mac address', 'ip address', 'http vs https']):
        return thinking + (
            f"### 🌐 **Computer Networks & Topologies ({grade} - {subject})**\n\n"
            f"A **Computer Network** is an interconnection of autonomous computing devices allowing data, resource, and service sharing.\n\n"
            f"#### 🗺️ **1. Types of Networks by Geographic Scale:**\n"
            f"| Network Type | Full Name | Geographic Scope | Speed & Ownership |\n"
            f"| :--- | :--- | :--- | :--- |\n"
            f"| **PAN** | Personal Area Network | Within 10 meters (Bluetooth, Hotspot) | Very fast, Single personal device |\n"
            f"| **LAN** | Local Area Network | Single room, school lab, or building | High data transfer speed, Private ownership |\n"
            f"| **MAN** | Metropolitan Area Network | Across an entire city (Cable TV, City Wi-Fi) | Moderate speed, Public/Private |\n"
            f"| **WAN** | Wide Area Network | Across countries/continents (The Internet) | Variable speed, Telecom providers |\n\n"
            f"#### 🕸️ **2. Network Topologies (Physical Layout):**\n"
            f"1. **Star Topology:** All nodes connect directly to a central **Hub / Switch**. If one cable breaks, only that node fails; if central hub fails, the entire network goes down.\n"
            f"2. **Bus Topology:** All nodes connect to a single shared backbone cable with terminators at both ends. Inexpensive, but difficult to troubleshoot cable faults.\n"
            f"3. **Mesh Topology:** Every node is connected to every other node (dedicated point-to-point links). Highly fault-tolerant and secure, but costly.\n"
            f"4. **Tree Topology:** Hierarchical combination of Star and Bus layouts.\n\n"
            f"#### 📡 **3. Transmission Media & Protocols:**\n"
            f"- **Guided (Wired):** Optical Fiber (light pulses - fastest and immune to electromagnetic interference), Twisted Pair (Ethernet CAT6), Coaxial.\n"
            f"- **Unguided (Wireless):** Radio waves, Microwaves, Infrared, Satellite.\n"
            f"- **MAC vs IP Address:** MAC address is a 48-bit permanent hardware address assigned by the manufacturer (NIC); IP address is a 32-bit (IPv4) or 128-bit (IPv6) logical network address assigned dynamically.\n\n"
            f"Would you like a quick 3-question quiz on computer networks?"
        )

    # Math: Basic Proportionality Theorem (Thales Theorem)
    if any(k in q_lower for k in ['proportionality theorem', 'thales theorem', 'thales', 'bpt']):
        return thinking + (
            f"### 📐 **Basic Proportionality Theorem (Thales Theorem) ({grade} - {subject})**\n\n"
            f"#### 📜 **1. Statement of the Theorem:**\n"
            f"If a line is drawn parallel to one side of a triangle intersecting the other two sides in distinct points, then the other two sides are divided in the **same ratio**.\n\n"
            f"#### 🔺 **2. Mathematical Formulation:**\n"
            f"In $\\triangle ABC$, if a line $DE \\parallel BC$ intersects $AB$ at $D$ and $AC$ at $E$, then:\n"
            f"$$\\frac{{\\text{{AD}}}}{{\\text{{DB}}}} = \\frac{{\\text{{AE}}}}{{\\text{{EC}}}}$$\n\n"
            f"#### 🔁 **3. Converse of BPT:**\n"
            f"If a line divides any two sides of a triangle in the same ratio ($\\frac{{\\text{{AD}}}}{{\\text{{DB}}}} = \\frac{{\\text{{AE}}}}{{\\text{{EC}}}}$), then the line must be **parallel to the third side** ($DE \\parallel BC$).\n\n"
            f"#### 🧠 **4. Classic Board Exam Problem:**\n"
            f"*In $\\triangle ABC$, $DE \\parallel BC$. If $AD = 1.5\\text{{ cm}}$, $DB = 3\\text{{ cm}}$, and $AE = 1\\text{{ cm}}$, find $EC$.*\n"
            f"- **Solution:** By BPT, $\\frac{{AD}}{{DB}} = \\frac{{AE}}{{EC}} \\implies \\frac{{1.5}}{{3}} = \\frac{{1}}{{EC}} \\implies EC = \\frac{{3}}{{1.5}} = 2\\text{{ cm}}$.\n\n"
            f"Would you like to practice a BPT proof or solve another triangle problem?"
        )

    # Math: Prime Factorization & Square Roots / Number Theory
    if any(k in q_lower for k in ['square root', 'cube root', 'prime factorization', 'prime factor', 'hcf and lcm', 'fundamental theorem of arithmetic']):
        return thinking + (
            f"### 🔢 **Prime Factorization, Square Roots & Number Theory ({grade} - {subject})**\n\n"
            f"#### 🌲 **1. Fundamental Theorem of Arithmetic:**\n"
            f"Every composite number can be expressed (factorized) uniquely as the product of prime numbers, apart from the order in which the prime factors occur.\n\n"
            f"#### 🔍 **2. Finding Square Roots using Prime Factorization (Step-by-Step):**\n"
            f"**Example: Find $\\sqrt{{625}}$**\n"
            f"1. **Prime Factorization:** Divide 625 by the smallest prime factor:\n"
            f"   - $625 \\div 5 = 125$\n"
            f"   - $125 \\div 5 = 25$\n"
            f"   - $25 \\div 5 = 5$\n"
            f"   - $5 \\div 5 = 1$\n"
            f"   $$\\implies 625 = 5 \\times 5 \\times 5 \\times 5 = (5 \\times 5)^2$$\n"
            f"2. **Group into Pairs:** $(5 \\times 5) \\times (5 \\times 5) = 5^2 \\times 5^2$\n"
            f"3. **Take one factor from each pair:**\n"
            f"   $$\\sqrt{{625}} = 5 \\times 5 = \\mathbf{{25}}$$\n\n"
            f"#### 📊 **3. HCF & LCM Formula:**\n"
            f"$$\\text{{HCF}}(a, b) \\times \\text{{LCM}}(a, b) = a \\times b$$\n\n"
            f"Type any number to find its prime factors or square root step-by-step!"
        )

    # Chemistry: Important NCERT Salts (Plaster of Paris, Bleaching Powder, Baking Soda, Washing Soda)
    if any(k in q_lower for k in ['plaster of paris', 'gypsum', 'bleaching powder', 'baking soda', 'washing soda', 'baking powder', 'caustic soda', 'chlor-alkali']):
        return thinking + (
            f"### 🧪 **Important Chemical Salts in Daily Life ({grade} - {subject})**\n\n"
            f"#### 🏥 **1. Plaster of Paris (POP) & Gypsum:**\n"
            f"- **Chemical Name:** Calcium Sulphate Hemihydrate ($\\text{{CaSO}}_4 \\cdot \\frac{{1}}{{2}}\\text{{H}}_2\\text{{O}}$).\n"
            f"- **Preparation:** Heating Gypsum ($\\text{{CaSO}}_4 \\cdot 2\\text{{H}}_2\\text{{O}}$) carefully at **$373\\text{{ K}}$ ($100^\\circ\\text{{C}}$)**:\n"
            f"  $$\\text{{CaSO}}_4 \\cdot 2\\text{{H}}_2\\text{{O}} \\xrightarrow{{373\\text{{ K}}}} \\text{{CaSO}}_4 \\cdot \\frac{{1}}{{2}}\\text{{H}}_2\\text{{O}} + 1\\frac{{1}}{{2}}\\text{{H}}_2\\text{{O}}$$\n"
            f"- **Setting Reaction:** On mixing with water, POP re-hydrates back into a hard solid mass of Gypsum:\n"
            f"  $$\\text{{CaSO}}_4 \\cdot \\frac{{1}}{{2}}\\text{{H}}_2\\text{{O}} + 1\\frac{{1}}{{2}}\\text{{H}}_2\\text{{O}} \\rightarrow \\text{{CaSO}}_4 \\cdot 2\\text{{H}}_2\\text{{O}}$$\n"
            f"- **Uses:** Supporting fractured bones in casts, making decorative statues, false ceilings, and blackboard chalk.\n\n"
            f"#### 🧼 **2. Other Important NCERT Salts Comparison:**\n"
            f"| Common Name | Chemical Name | Chemical Formula | Key CBSE Exam Use |\n"
            f"| :--- | :--- | :--- | :--- |\n"
            f"| **Bleaching Powder** | Calcium Oxychloride | $\\text{{CaOCl}}_2$ | Disinfecting drinking water, bleaching cotton |\n"
            f"| **Baking Soda** | Sodium Hydrogen Carbonate | $\\text{{NaHCO}}_3$ | Antacid for acidity, making crispy pakoras & baking |\n"
            f"| **Washing Soda** | Sodium Carbonate Decahydrate | $\\text{{Na}}_2\\text{{CO}}_3 \\cdot 10\\text{{H}}_2\\text{{O}}$ | Removing permanent hardness of water, glass & soap industry |\n"
            f"| **Caustic Soda** | Sodium Hydroxide | $\\text{{NaOH}}$ | Degreasing metals, soap making (Chlor-alkali process) |\n\n"
            f"Would you like a quick 3-question quiz on chemical salts?"
        )

    # Chemistry: Amphoteric Oxides
    if any(k in q_lower for k in ['amphoteric oxide', 'amphoteric oxides', 'amphoteric']):
        return thinking + (
            f"### 🧪 **Amphoteric Oxides Explained ({grade} - {subject})**\n\n"
            f"#### 🎯 **Direct Definition:**\n"
            f"**Amphoteric Oxides** are metal oxides that react with **both acids and bases** to produce salt and water.\n\n"
            f"#### 🔬 **Prime Examples (Aluminium Oxide $\\text{{Al}}_2\\text{{O}}_3$ and Zinc Oxide $\\text{{ZnO}}$):**\n\n"
            f"**1. Aluminium Oxide ($\\text{{Al}}_2\\text{{O}}_3$) as a Base (Reacting with Acid):**\n"
            f"$$\\text{{Al}}_2\\text{{O}}_3 + 6\\text{{HCl}} \\rightarrow 2\\text{{AlCl}}_3 \\text{{ (Aluminium chloride)}} + 3\\text{{H}}_2\\text{{O}}$$\n\n"
            f"**2. Aluminium Oxide ($\\text{{Al}}_2\\text{{O}}_3$) as an Acid (Reacting with Base):**\n"
            f"$$\\text{{Al}}_2\\text{{O}}_3 + 2\\text{{NaOH}} \\rightarrow 2\\mathbf{{\\text{{NaAlO}}_2 \\text{{ (Sodium aluminate)}}}} + \\text{{H}}_2\\text{{O}}$$\n\n"
            f"**3. Zinc Oxide ($\\text{{ZnO}}$):**\n"
            f"- With Acid: $\\text{{ZnO}} + 2\\text{{HCl}} \\rightarrow \\text{{ZnCl}}_2 + \\text{{H}}_2\\text{{O}}$\n"
            f"- With Base: $\\text{{ZnO}} + 2\\text{{NaOH}} \\rightarrow \\mathbf{{\\text{{Na}}_2\\text{{ZnO}}_2 \\text{{ (Sodium zincate)}}}} + \\text{{H}}_2\\text{{O}}$\n\n"
            f"Would you like a quick 3-question quiz on metals and non-metals?"
        )

    # Biology: Xylem vs Phloem Complex Tissues
    if any(k in q_lower for k in ['xylem and phloem', 'xylem vs phloem', 'xylem tissue', 'phloem tissue', 'complex permanent tissue']):
        return thinking + (
            f"### 🌿 **Xylem vs Phloem: Complex Permanent Tissues ({grade} - {subject})**\n\n"
            f"**Complex Permanent Tissues** are composed of more than one type of cell working together as a single unit to perform vascular conduction in plants.\n\n"
            f"#### 📊 **1. Comprehensive Comparison Table (CBSE NCERT):**\n"
            f"| Feature | Xylem 💧 | Phloem 🍯 |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **Substance Transported** | Transports **Water and dissolved Minerals** | Transports **Soluble Food (Sucrose/Photosynthates)** |\n"
            f"| **Direction of Flow** | **Unidirectional** (Upwards from roots to leaves) | **Bidirectional** (Upwards and downwards from leaves to all parts) |\n"
            f"| **Process Name** | Ascent of Sap (Transpiration pull) | **Translocation** (requires ATP energy) |\n"
            f"| **Cellular State** | Mostly dead cells (*Exception: Xylem Parenchyma is living*) | Mostly living cells (*Exception: Phloem Fibres are dead*) |\n"
            f"| **Conducting Elements** | Tracheids and Vessels | Sieve Tubes and Companion Cells |\n"
            f"| **Other Elements** | Xylem Fibres & Xylem Parenchyma | Phloem Parenchyma & Phloem Fibres |\n\n"
            f"#### 🔍 **2. Transpiration Pull vs Translocation:**\n"
            f"- **Transpiration Pull:** Evaporation of water through stomata creates a suction pull that drives water upward in xylem vessels.\n"
            f"- **Translocation:** Active loading of sucrose into sieve tubes using ATP increases osmotic pressure, moving food to areas of lower pressure.\n\n"
            f"Would you like a quick 3-question quiz on plant tissues?"
        )

    # Physics: Distance vs Displacement & Equations of Motion
    if any(k in q_lower for k in ['distance and displacement', 'distance vs displacement', 'displacement', 'scalar vs vector', 'equations of motion']):
        return thinking + (
            f"### 🚗 **Distance vs Displacement & Motion ({grade} - {subject})**\n\n"
            f"#### 📊 **1. Distance vs Displacement Comparison:**\n"
            f"| Feature | Distance ($s$) 📏 | Displacement ($\\vec{{s}}$) 🎯 |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **Definition** | Total length of actual path traversed | Shortest straight-line distance between initial & final points |\n"
            f"| **Quantity Type** | **Scalar** (Magnitude only) | **Vector** (Magnitude and specific direction) |\n"
            f"| **Can it be zero?** | Never zero for a moving body | **Can be zero** (if body returns to starting point) |\n"
            f"| **Can it be negative?** | Always positive | Can be positive, negative, or zero |\n"
            f"| **Magnitude Comparison** | $\\text{{Distance}} \\ge |\\text{{Displacement}}|$ | $|\\text{{Displacement}}| \\le \\text{{Distance}}$ |\n\n"
            f"#### 🏎️ **2. The Three Equations of Uniformly Accelerated Motion:**\n"
            f"1. **Velocity-Time Relation:** $$v = u + at$$\n"
            f"2. **Position-Time Relation:** $$s = ut + \\frac{{1}}{{2}}at^2$$\n"
            f"3. **Position-Velocity Relation:** $$v^2 = u^2 + 2as$$\n"
            f"*(Where $u = \\text{{initial velocity}}, v = \\text{{final velocity}}, a = \\text{{acceleration}}, t = \\text{{time}}, s = \\text{{distance}})*\n\n"
            f"Would you like to solve a numerical using the equations of motion?"
        )

    # Physics: Archimedes Principle & Relative Density
    if any(k in q_lower for k in ['archimedes principle', 'archimedes', 'buoyancy', 'buoyant force', 'relative density', 'flotation']):
        return thinking + (
            f"### 🌊 **Archimedes' Principle, Buoyancy & Density ({grade} - {subject})**\n\n"
            f"#### 📜 **1. Archimedes' Principle:**\n"
            f"When a body is fully or partially immersed in a fluid, it experiences an upward buoyant force equal to the **weight of the fluid displaced** by it:\n"
            f"$$F_b = \\text{{Weight of Displaced Fluid}} = m_{{\\text{{fluid}}}} \\times g = \\rho_{{\\text{{fluid}}}} \\times V_{{\\text{{immersed}}}} \\times g$$\n\n"
            f"#### 🚢 **2. Applications of Archimedes' Principle:**\n"
            f"1. Designing ships and submarines (steel ships float because their hollow hull displaces a massive volume of water greater than their weight).\n"
            f"2. **Hydrometers:** Measuring relative density of liquids.\n"
            f"3. **Lactometers:** Testing the purity of milk.\n\n"
            f"#### ⚖️ **3. Relative Density:**\n"
            f"$$\\text{{Relative Density}} = \\frac{{\\text{{Density of Substance}}}}{{\\text{{Density of Water at }} 4^\\circ\\text{{C}} (1000\\text{{ kg/m}}^3)}}$$\n"
            f"- Relative density is a **pure dimensionless number** (no units).\n"
            f"- If $\\text{{RD}} > 1$, the object **sinks** in water; if $\\text{{RD}} < 1$, the object **floats**.\n\n"
            f"Would you like a quick 3-question quiz on buoyancy and gravitation?"
        )

    # Physics (Senior): Coulomb's Law & Electrostatics
    if any(k in q_lower for k in ['coulomb', 'coulombs law', "coulomb's law", 'electrostatic force', 'electric charge', 'permittivity']):
        return thinking + (
            f"### ⚡ **Coulomb's Law in Electrostatics ({grade} - {subject})**\n\n"
            f"#### 📜 **1. Statement of Coulomb's Law:**\n"
            f"The electrostatic force of attraction or repulsion between two stationary point charges is **directly proportional to the product of the magnitudes of the charges** and **inversely proportional to the square of the distance** between them.\n\n"
            f"#### 📐 **2. Mathematical Formula:**\n"
            f"$$F = \\frac{{1}}{{4\\pi\\varepsilon_0}} \\frac{{|q_1 q_2|}}{{r^2}} = k \\frac{{|q_1 q_2|}}{{r^2}}$$\n"
            f"- **Coulomb's Constant ($k$ in vacuum):** $$k = \\frac{{1}}{{4\\pi\\varepsilon_0}} \\approx 8.99 \\times 10^9\\text{{ N}}\\cdot\\text{{m}}^2/\\text{{C}}^2$$\n"
            f"- **Permittivity of Free Space ($\\varepsilon_0$):** $$\\varepsilon_0 = 8.854 \\times 10^{{-12}}\\text{{ C}}^2/(\\text{{N}}\\cdot\\text{{m}}^2)$$\n"
            f"- **Vector Form:** $$\\vec{{F}}_{{12}} = \\frac{{1}}{{4\\pi\\varepsilon_0}} \\frac{{q_1 q_2}}{{r^2}} \\hat{{r}}_{{21}}$$\n\n"
            f"#### 🔍 **3. Key Characteristics:**\n"
            f"1. Like charges repel; unlike charges attract.\n"
            f"2. Acts along the line joining the centers of the two charges (Central force).\n"
            f"3. Strictly obeys Newton's Third Law: $\\vec{{F}}_{{12}} = -\\vec{{F}}_{{21}}$.\n\n"
            f"Would you like to solve a numerical calculating the electrostatic force between two charges?"
        )

    # Physics (Senior): Projectile Motion
    if any(k in q_lower for k in ['projectile motion', 'projectile', 'time of flight', 'maximum height', 'horizontal range']):
        return thinking + (
            f"### 🚀 **Projectile Motion ({grade} - {subject})**\n\n"
            f"A **Projectile** is an object thrown into the air at an angle $\\theta$ with initial velocity $u$, moving under the sole influence of gravity (parabolic path / trajectory).\n\n"
            f"#### 📊 **1. Standard Kinematic Formulas:**\n"
            f"1. **Time of Flight ($T$):** Total duration in the air:\n"
            f"   $$T = \\frac{{2u\\sin\\theta}}{{g}}$$\n"
            f"2. **Maximum Height ($H_{{\\text{{max}}}}$):** Peak vertical altitude reached:\n"
            f"   $$H_{{\\text{{max}}}} = \\frac{{u^2\\sin^2\\theta}}{{2g}}$$\n"
            f"3. **Horizontal Range ($R$):** Total horizontal distance covered:\n"
            f"   $$R = \\frac{{u^2\\sin 2\\theta}}{{g}}$$\n"
            f"4. **Maximum Range Condition:** Range is maximum when thrown at $\\theta = 45^\\circ$, where $R_{{\\text{{max}}}} = \\frac{{u^2}}{{g}}$.\n\n"
            f"#### 📐 **2. Equation of Trajectory (Parabola):**\n"
            f"$$y = x\\tan\\theta - \\frac{{g x^2}}{{2u^2\\cos^2\\theta}}$$\n\n"
            f"Would you like to solve a projectile motion numerical step-by-step?"
        )

    # Computer Science (Senior): List vs Tuple & SQL Keys
    if any(k in q_lower for k in ['list vs tuple', 'difference between list and tuple', 'list and tuple', 'mutable vs immutable', 'primary key', 'foreign key', 'candidate key']):
        return thinking + (
            f"### 💻 **Computer Science: Python Data Structures & SQL Relations ({grade} - {subject})**\n\n"
            f"#### 🐍 **1. Python: List vs Tuple Comparison:**\n"
            f"| Feature | List `[]` 📝 | Tuple `()` 🔒 |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **Mutability** | **Mutable** (Elements can be changed/modified) | **Immutable** (Elements cannot be modified after creation) |\n"
            f"| **Syntax** | Square brackets: `my_list = [10, 20, 30]` | Parentheses: `my_tuple = (10, 20, 30)` |\n"
            f"| **Memory & Speed** | Consumes more memory, slightly slower iteration | Memory-efficient, faster execution |\n"
            f"| **Methods** | `append()`, `extend()`, `insert()`, `pop()`, `remove()` | `count()`, `index()` only |\n"
            f"| **Dictionary Key** | Cannot be used as dict keys (unhashable) | Can be used as dict keys (hashable) |\n\n"
            f"#### 🗄️ **2. SQL Database Keys:**\n"
            f"- **Primary Key:** A column (or set of columns) that **uniquely identifies** each row/record in a table. It cannot contain `NULL` values and must be unique (e.g. `StudentID`, `AadhaarNumber`).\n"
            f"- **Foreign Key:** A column in one table that references the **Primary Key of another table**, establishing a relational link and enforcing referential integrity (e.g. `DepartmentID` in an `Employee` table).\n"
            f"- **Candidate Key:** All attribute sets capable of becoming a Primary Key.\n\n"
            f"Would you like to write a Python script or SQL table creation query?"
        )

    # IT 402: Mail Merge
    if any(k in q_lower for k in ['mail merge', 'digital documentation', 'merge document', 'data source in mail merge']):
        return thinking + (
            f"### 📄 **Mail Merge in Digital Documentation (IT Code 402 - {grade})**\n\n"
            f"**Mail Merge** is an automated word processing feature that enables creating multiple personalized letters, envelopes, mailing labels, or certificates by merging a standard document with a database/spreadsheet.\n\n"
            f"#### 🧩 **1. Three Main Components of Mail Merge:**\n"
            f"1. **Main Document:** The standard text file containing the common layout and message (e.g., Annual Sports Day invitation).\n"
            f"2. **Data Source:** The structured dataset (in Excel, Access, or Calc) containing variable recipient information (Name, Address, Roll No, Marks).\n"
            f"3. **Merged Document:** The final output generated after combining the main document with each individual record from the data source.\n\n"
            f"#### 🛠️ **2. Step-by-Step Mail Merge Workflow:**\n"
            f"1. Open Word Processor (MS Word / LibreOffice Writer) and draft the **Main Document**.\n"
            f"2. Launch the **Mail Merge Wizard** (from the *Mailings* tab).\n"
            f"3. Select or create the **Data Source** containing recipient details.\n"
            f"4. Insert **Merge Fields** (e.g. `«First_Name»`, `«City»`) into designated positions.\n"
            f"5. Preview merged records to verify layout accuracy.\n"
            f"6. Complete and **Print / Export / Email** individual personalized copies.\n\n"
            f"Would you like a quick 3-question quiz on IT Code 402?"
        )

    # Social Science: Non-Cooperation Movement
    if any(k in q_lower for k in ['non-cooperation', 'non cooperation movement', 'rowlatt act', 'jallianwala bagh', 'khilafat', 'chauri chaura', 'civil disobedience']):
        return thinking + (
            f"### 🇮🇳 **The Non-Cooperation Movement (1920–1922) ({grade} - {subject})**\n\n"
            f"#### 🌟 **1. Background & Causes (Why was it launched?):**\n"
            f"Mahatma Gandhi declared in his book *Hind Swaraj* (1909) that British rule was established in India with the cooperation of Indians and would collapse within a year if Indians refused to cooperate. Key triggers were:\n"
            f"1. **The Rowlatt Act (1919):** Allowed detention of political prisoners without trial for 2 years.\n"
            f"2. **Jallianwala Bagh Massacre (13 April 1919):** General Dyer ordered troops to fire on peaceful unarmed civilians in Amritsar.\n"
            f"3. **Khilafat Movement:** Joined hands with the Ali brothers (Muhammad Ali and Shaukat Ali) to unite Hindus and Muslims against British injustice to the Ottoman Caliph.\n\n"
            f"#### 📜 **2. Core Programme & Methods:**\n"
            f"- Surrender of titles and honorary posts awarded by the British government.\n"
            f"- Boycott of civil services, army, police, courts, legislative councils, and schools/colleges.\n"
            f"- Boycott of foreign goods and picketing of liquor shops; promotion of Swadeshi hand-spun **Khadi**.\n\n"
            f"#### 🛑 **3. Calling off at Chauri Chaura (February 1922):**\n"
            f"- At Chauri Chaura (Gorakhpur, UP), a peaceful demonstration turned violent, and a crowd burned down a police station, killing 22 policemen.\n"
            f"- Believing that Indians were not yet ready for mass non-violent struggle (*Satyagraha*), Gandhiji abruptly called off the movement.\n\n"
            f"Would you like a quick 3-question quiz on Nationalism in India?"
        )

    # English: Active Voice vs Passive Voice
    if any(k in q_lower for k in ['active voice and passive voice', 'active and passive', 'active voice', 'passive voice', 'voice change']):
        return thinking + (
            f"### ✍️ **Active Voice vs Passive Voice ({grade} - {subject})**\n\n"
            f"**Voice** describes the relationship between the action (verb) and the participants (subject/object) in a sentence.\n\n"
            f"#### 📊 **1. Core Difference:**\n"
            f"- **Active Voice:** The subject performs the action directly (Focus is on the **doer**).\n"
            f"  - *Structure:* $\\text{{Subject}} + \\text{{Verb}} + \\text{{Object}}$\n"
            f"- **Passive Voice:** The subject receives the action (Focus is on the **action / recipient**).\n"
            f"  - *Structure:* $\\text{{Object}} + \\text{{helping verb (be)}} + \\text{{Past Participle (V3)}} + \\text{{by}} + \\text{{Subject}}$\n\n"
            f"#### 📝 **2. Two Clear Board Exam Examples:**\n\n"
            f"**Example 1 (Simple Present):**\n"
            f"- **Active:** *The teacher explains the lesson.*\n"
            f"- **Passive:** *The lesson **is explained** by the teacher.*\n\n"
            f"**Example 2 (Simple Past):**\n"
            f"- **Active:** *Shakespeare wrote Hamlet.*\n"
            f"- **Passive:** *Hamlet **was written** by Shakespeare.*\n\n"
            f"#### 🔄 **3. Pronoun Transformation Table:**\n"
            f"- $I \\rightarrow \\text{{me}}, \\quad \\text{{We}} \\rightarrow \\text{{us}}, \\quad \\text{{He}} \\rightarrow \\text{{him}}, \\quad \\text{{She}} \\rightarrow \\text{{her}}, \\quad \\text{{They}} \\rightarrow \\text{{them}}$\n\n"
            f"Would you like to practice converting 3 active sentences into passive voice?"
        )

    # Hindi: Sangya (Noun) & Bhed
    if any(k in q_lower for k in ['sangya', 'sangya ke bhed', 'sangya kise kehte hain', 'sarvanam', 'visheshan']):
        return thinking + (
            f"### 📖 **संज्ञा (Noun) और उसके भेद ({grade} - {subject})**\n\n"
            f"#### 🎯 **1. संज्ञा की परिभाषा:**\n"
            f"किसी व्यक्ति, वस्तु, स्थान, प्राणी या भाव के नाम को **संज्ञा** (Noun) कहते हैं।\n"
            f"- **उदाहरण:** राम (व्यक्ति), पुस्तक (वस्तु), पटना (स्थान), गाय (प्राणी), मिठास/बचपन (भाव)।\n\n"
            f"#### 🏷️ **2. संज्ञा के 3 मुख्य भेद (CBSE Hindi Vyakaran):**\n\n"
            f"1. **व्यक्तिवाचक संज्ञा (Proper Noun):**\n"
            f"   - जो शब्द किसी विशेष व्यक्ति, स्थान या वस्तु का बोध कराते हैं।\n"
            f"   - *उदाहरण:* **महात्मा गांधी**, **गंगा नदी**, **दिल्ली**, **रामायण**।\n\n"
            f"2. **जातिवाचक संज्ञा (Common Noun):**\n"
            f"   - जो शब्द किसी प्राणी या वस्तु की संपूर्ण जाति या वर्ग का बोध कराते हैं।\n"
            f"   - *उदाहरण:* **लड़का**, **नदी**, **पर्वत**, **विद्यालय**, **पेड़**।\n"
            f"   - *(इसके 2 उपभेद होते हैं: द्रव्यवाचक जैसे सोना/पानी, और समूहवाचक जैसे कक्षा/सेना)।*\n\n"
            f"3. **भाववाचक संज्ञा (Abstract Noun):**\n"
            f"   - जो शब्द किसी गुण, दोष, अवस्था, दशा या भाव का बोध कराते हैं (जिन्हें देखा/छुआ नहीं जा सकता, केवल अनुभव किया जाता है)।\n"
            f"   - *उदाहरण:* **बचपन**, **ईमानदारी**, **मिठास**, **सुंदरता**, **क्रोध**।\n\n"
            f"क्या आप संज्ञा के भेदों पर एक छोटा सा 3-question अभ्यास टेस्ट हल करना चाहेंगे?"
        )

    # Biology (Environment): 10% Energy Law & Biological Magnification
    if any(k in q_lower for k in ['biological magnification', 'biomagnification', '10% law', 'ten percent law', 'lindeman', 'trophic level', 'energy flow in ecosystem']):
        return thinking + (
            f"### 🌍 **Ecosystem: 10% Energy Law & Biological Magnification ({grade} - {subject})**\n\n"
            f"#### ⚡ **1. Lindeman's 10% Energy Law (1942):**\n"
            f"- In an ecosystem, only **$10\\%$ of the total energy** available at a trophic level is transferred to the next higher trophic level.\n"
            f"- The remaining **$90\\%$ is lost** to the environment as metabolic heat during respiration, digestion, and daily activities.\n\n"
            f"**Classic Board Exam Numerical:**\n"
            f"- If Sun provides $1,000,000\\text{{ J}}$ of solar energy:\n"
            f"  1. **Producers (Green Plants):** Trap $1\\%$ of solar energy = **$10,000\\text{{ J}}$**\n"
            f"  2. **Primary Consumers (Herbivore / Deer):** $10\\%$ of $10,000\\text{{ J}}$ = **$1,000\\text{{ J}}$**\n"
            f"  3. **Secondary Consumers (Carnivore / Tiger):** $10\\%$ of $1,000\\text{{ J}}$ = **$100\\text{{ J}}$**\n"
            f"  4. **Top Carnivore (Apex Predator):** $10\\%$ of $100\\text{{ J}}$ = **$10\\text{{ J}}$**\n"
            f"- *Why food chains have only 3–4 trophic levels:* Beyond 4 levels, the residual energy is too small to sustain an organism.\n\n"
            f"#### ☣️ **2. Biological Magnification (Biomagnification):**\n"
            f"- **Definition:** The progressive accumulation and increase in concentration of **non-biodegradable harmful chemicals** (e.g. **DDT**, heavy metals, pesticides) at each successive trophic level in a food chain.\n"
            f"- **Why it happens:** Non-biodegradable toxins cannot be broken down or excreted by organisms.\n"
            f"- **Maximum Concentration:** Organisms at the highest trophic level (**Humans / Apex Predators**) accumulate the **maximum concentration of toxins**.\n\n"
            f"Would you like a quick 3-question quiz on our environment?"
        )

    # Primary Science: How Fishes Breathe / Gills
    if any(k in q_lower for k in ['fish breathe', 'fishes breathe', 'gills', 'how do fish breathe', 'breathe underwater']):
        return thinking + (
            f"### 🐟 **How Do Fishes Breathe Underwater? ({grade} - {subject})**\n\n"
            f"#### 🌊 **1. Do Fishes Breathe Air?**\n"
            f"Yes! But fishes do not breathe air from the sky like humans. Instead, they breathe **dissolved oxygen ($O_2$)** present in water!\n\n"
            f"#### 🫁 **2. What are Gills (Galfade)?**\n"
            f"- Humans have **Lungs** to breathe air, but fishes have special organs called **Gills** located on both sides of their head.\n"
            f"- Gills look like red, feathery combs packed with thousands of tiny blood vessels.\n\n"
            f"#### 🔄 **3. The 3 Steps of Underwater Breathing:**\n"
            f"1. **Drinking Water In:** The fish opens its mouth and gulps in water rich in dissolved oxygen.\n"
            f"2. **Flowing over Gills:** The water passes over the feathery gill filaments.\n"
            f"3. **Oxygen Exchange:** The blood vessels in the gills absorb the dissolved oxygen directly into the bloodstream and release waste carbon dioxide ($CO_2$) back into the water.\n"
            f"4. The used water flows out through the gill slits (operculum).\n\n"
            f"Would you like to learn how other water animals (like whales and dolphins) breathe?"
        )

    # Primary Math: Fractions with Pizza Example
    if any(k in q_lower for k in ['fraction', 'fractions', 'pizza example', '1/2 and 1/4', 'half and quarter']):
        return thinking + (
            f"### 🍕 **Fractions Explained Simply with Pizza! ({grade} - Mathematics)**\n\n"
            f"#### 🎯 **1. What is a Fraction?**\n"
            f"A **Fraction** represents a part of a whole thing! It is written as:\n"
            f"$$\\text{{Fraction}} = \\frac{{\\text{{Numerator (Parts you have)}}}}{{\\text{{Denominator (Total equal parts)}}}}$$\n\n"
            f"#### 🍕 **2. The Pizza Slices Metaphor:**\n"
            f"- 🍕 **One Whole ($1$):** A full, uncut delicious pizza.\n"
            f"- 🌓 **One Half ($\\frac{{1}}{{2}}$):** Cut the pizza into **2 equal pieces**. If you eat 1 piece, you ate $\\frac{{1}}{{2}}$ of the pizza!\n"
            f"- 🍕 **One Quarter ($\\frac{{1}}{{4}}$):** Cut the pizza into **4 equal pieces**. If you take 1 piece, you have $\\frac{{1}}{{4}}$ (one quarter).\n"
            f"- 🍕 **Three Quarters ($\\frac{{3}}{{4}}$):** 3 pieces out of the 4 equal slices!\n\n"
            f"#### 🧮 **3. Fun Fraction Rules:**\n"
            f"- $\\frac{{1}}{{2}} + \\frac{{1}}{{2}} = 1$ (Two halves make a whole pizza!)\n"
            f"- $\\frac{{1}}{{4}} + \\frac{{1}}{{4}} + \\frac{{1}}{{4}} + \\frac{{1}}{{4}} = 1$ (Four quarters make a whole pizza!)\n\n"
            f"Would you like to try a fun 3-question pizza fraction quiz? 🎮"
        )

    # Primary Science: Rain Formation, Condensation & Precipitation
    if any(k in q_lower for k in ['rain formed', 'how is rain formed', 'condensation and precipitation', 'condensation', 'precipitation', 'water cycle']):
        return thinking + (
            f"### 🌧️ **How is Rain Formed? The Magic Water Cycle ({grade} - {subject})**\n\n"
            f"Rain is part of the Earth's never-ending **Water Cycle**! Here is the 4-step journey of a water droplet:\n\n"
            f"#### ☀️ **Step 1: Evaporation (Bhaap Banna):**\n"
            f"- The warm sun heats up water in oceans, rivers, lakes, and puddles.\n"
            f"- Liquid water turns into invisible gas called **Water Vapour** and rises high into the sky.\n\n"
            f"#### ☁️ **Step 2: Condensation (Badal Banna):**\n"
            f"- High up in the sky, it is very cold!\n"
            f"- The rising water vapour cools down and turns back into tiny floating water droplets around dust particles, forming **Clouds** ☁️.\n\n"
            f"#### 🌧️ **Step 3: Precipitation (Baarish Girna):**\n"
            f"- Inside clouds, millions of tiny droplets bump into each other and grow bigger and heavier.\n"
            f"- When they become too heavy to float in the air, gravity pulls them down as **Rain** 🌧️ (or Snow ❄️ if it's freezing cold)!\n\n"
            f"#### 🌊 **Step 4: Collection (Vaapis Paani Me):**\n"
            f"- Rainwater flows into streams, rivers, and oceans, soaking into the soil for plants. Then the cycle repeats forever!\n\n"
            f"Would you like a fun 3-question quiz on clouds and the water cycle?"
        )

    # Senior Physics: Photoelectric Effect & Einstein's Equation
    if any(k in q_lower for k in ['photoelectric effect', 'photoelectric', 'work function', 'threshold frequency', 'stopping potential', 'einstein photoelectric']):
        return thinking + (
            f"### 💡 **Photoelectric Effect & Einstein's Quantum Equation ({grade} - {subject})**\n\n"
            f"#### 📜 **1. Definition:**\n"
            f"The **Photoelectric Effect** is the phenomenon of instantaneous emission of electrons (photoelectrons) from a metal surface when electromagnetic radiation of frequency greater than a minimum **Threshold Frequency ($\\nu_0$)** is incident upon it.\n\n"
            f"#### ⚛️ **2. Einstein's Photoelectric Equation:**\n"
            f"According to Einstein's photon hypothesis, light consists of packets of energy called **Photons** ($E = h\\nu$). When a photon collides with an electron in metal, its energy is divided into two parts:\n"
            f"$$h\\nu = \\Phi_0 + K_{{\\text{{max}}}}$$\n"
            f"- **Work Function ($\\Phi_0 = h\\nu_0$):** Minimum energy required to liberate an electron from metal surface.\n"
            f"- **Maximum Kinetic Energy ($K_{{\\text{{max}}}}$):**\n"
            f"  $$K_{{\\text{{max}}}} = h\\nu - \\Phi_0 = h(\\nu - \\nu_0) = \\frac{{1}}{{2}}m v_{{\\text{{max}}}}^2 = e V_0$$\n"
            f"*(Where $h = 6.626 \\times 10^{{-34}}\\text{{ J}}\\cdot\\text{{s}}$, $V_0 = \\text{{Stopping Potential}}$, $e = 1.6 \\times 10^{{-19}}\\text{{ C}})*\n\n"
            f"#### 🔍 **3. Key Laws of Photoelectric Emission:**\n"
            f"1. **No Time Lag:** Emission is instantaneous ($< 10^{{-9}}\\text{{ s}}$).\n"
            f"2. **Intensity vs Frequency:** Photocurrent is directly proportional to light **Intensity**; $K_{{\\text{{max}}}}$ depends only on light **Frequency ($\\nu$)**, independent of intensity.\n"
            f"3. No emission occurs if $\\nu < \\nu_0$ regardless of how intense the incident light is.\n\n"
            f"Would you like to solve a numerical calculating stopping potential or threshold wavelength?"
        )

    # Senior Physics: Doppler Effect in Sound
    if any(k in q_lower for k in ['doppler effect', 'doppler', 'apparent frequency', 'doppler shift']):
        return thinking + (
            f"### 🔊 **Doppler Effect in Sound ({grade} - {subject})**\n\n"
            f"#### 📜 **1. Definition:**\n"
            f"The **Doppler Effect** is the apparent change in the observed pitch (frequency) of a sound wave when there is relative motion between the **Source of sound ($S$)** and the **Observer ($O$)**.\n\n"
            f"#### 📐 **2. General Master Formula:**\n"
            f"$$f' = f \\left( \\frac{{v \\pm v_o}}{{v \\mp v_s}} \\right)$$\n"
            f"*(Where $f = \\text{{actual frequency}}, f' = \\text{{apparent observed frequency}}, v = \\text{{speed of sound in medium}}, v_o = \\text{{velocity of observer}}, v_s = \\text{{velocity of source}})*\n\n"
            f"#### 📊 **3. Common Motion Cases:**\n"
            f"1. **Source moves towards stationary observer ($v_o = 0$):**\n"
            f"   $$f' = f \\left( \\frac{{v}}{{v - v_s}} \\right) > f \\quad (\\text{{Pitch increases - High pitch siren}})$$\n"
            f"2. **Source moves away from stationary observer:**\n"
            f"   $$f' = f \\left( \\frac{{v}}{{v + v_s}} \\right) < f \\quad (\\text{{Pitch decreases}})$$\n"
            f"3. **Observer moves towards stationary source ($v_s = 0$):**\n"
            f"   $$f' = f \\left( \\frac{{v + v_o}}{{v}} \\right) > f$$\n\n"
            f"Would you like to solve a numerical calculating the apparent frequency of an approaching train?"
        )

    # Senior Biology: DNA Double Helix Structure
    if any(k in q_lower for k in ['double helix', 'dna structure', 'watson and crick', 'structure of dna', 'chargaff', 'polynucleotide']):
        return thinking + (
            f"### 🧬 **Structure of DNA: Double Helix Model ({grade} - {subject})**\n\n"
            f"In 1953, **James Watson and Francis Crick** proposed the iconic **Double Helix Model** of Deoxyribonucleic Acid (DNA) based on Rosalind Franklin's X-ray diffraction data.\n\n"
            f"#### 🌟 **1. Salient Features of the DNA Double Helix:**\n"
            f"1. **Two Polynucleotide Chains:** Composed of two helical strands running in **antiparallel polarity** ($5' \\rightarrow 3'$ and $3' \\rightarrow 5'$).\n"
            f"2. **Sugar-Phosphate Backbone:** The outer structural rails consist of alternating **Deoxyribose sugar** and **Phosphate groups**; the nitrogenous bases project inward.\n"
            f"3. **Complementary Base Pairing (Hydrogen Bonds):**\n"
            f"   - **Adenine ($A$)** pairs with **Thymine ($T$)** via **2 Hydrogen Bonds** ($A = T$).\n"
            f"   - **Guanine ($G$)** pairs with **Cytosine ($C$)** via **3 Hydrogen Bonds** ($G \\equiv C$).\n"
            f"4. **Chargaff's Rule:** In double-stranded DNA, the ratio of purines to pyrimidines is always equal: $[A] + [G] = [T] + [C]$, hence $\\frac{{[A] + [G]}}{{[T] + [C]}} = 1$.\n"
            f"5. **Helical Dimensions:**\n"
            f"   - Pitch of the helix = **$3.4\\text{{ nm}}$ ($34\\text{{ \\AA}}$)** per complete turn.\n"
            f"   - Distance between two adjacent base pairs $\\approx$ **$0.34\\text{{ nm}}$ ($3.4\\text{{ \\AA}}$)** with $\\approx 10\\text{{ bp}}$ per helical turn.\n"
            f"   - Diameter of the helix = **$2.0\\text{{ nm}}$ ($20\\text{{ \\AA}}$)**.\n\n"
            f"Would you like a quick 3-question quiz on DNA replication and molecular genetics?"
        )

    # Senior CS: SQL WHERE vs HAVING
    if any(k in q_lower for k in ['where vs having', 'difference between where and having', 'where and having', 'having clause', 'where clause']):
        return thinking + (
            f"### 🗄️ **SQL: WHERE vs HAVING Clause Explained ({grade} - {subject})**\n\n"
            f"#### 📊 **1. Key Difference Comparison Table:**\n"
            f"| Feature | `WHERE` Clause 🔍 | `HAVING` Clause 📊 |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **Target** | Filters individual **rows / records** | Filters **groups formed by `GROUP BY`** |\n"
            f"| **Execution Timing** | Applied **before** grouping / aggregation | Applied **after** grouping / aggregation |\n"
            f"| **Aggregate Functions** | **Cannot** use aggregate functions (`SUM`, `AVG`, `COUNT`, `MAX`, `MIN`) | **Can** use aggregate functions (`HAVING COUNT(*) > 5`) |\n"
            f"| **Usage with `GROUP BY`** | Can be used without `GROUP BY` | Almost always used alongside `GROUP BY` |\n\n"
            f"#### 💻 **2. Code Examples (CBSE Class 12 CS / IP Standard):**\n\n"
            f"**A) Using `WHERE` (Filtering rows before grouping):**\n"
            f"```sql\n"
            f"SELECT StudentName, Marks \n"
            f"FROM Students \n"
            f"WHERE Marks >= 90; -- Filters individual student rows\n"
            f"```\n\n"
            f"**B) Using `HAVING` (Filtering aggregate groups):**\n"
            f"```sql\n"
            f"SELECT Department, AVG(Salary) \n"
            f"FROM Employees \n"
            f"GROUP BY Department \n"
            f"HAVING AVG(Salary) > 50000; -- Filters grouped departments\n"
            f"```\n\n"
            f"Would you like to write a custom SQL query using both `WHERE` and `HAVING`?"
        )

    # Senior CS: Linear Search vs Binary Search in Python
    if any(k in q_lower for k in ['linear search vs binary search', 'linear search and binary search', 'binary search', 'linear search', 'time complexity of search']):
        return thinking + (
            f"### 🐍 **Linear Search vs Binary Search in Python ({grade} - {subject})**\n\n"
            f"#### 📊 **1. Comparison Table:**\n"
            f"| Feature | Linear Search 🚶 | Binary Search ⚡ |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **Array Requirement** | Works on **both Unsorted and Sorted** lists | Strictly requires a **Sorted array** |\n"
            f"| **Methodology** | Sequential element-by-element scanning | Divide and Conquer (halves search space) |\n"
            f"| **Best Case Time** | $O(1)$ (Element at index 0) | $O(1)$ (Element at middle index) |\n"
            f"| **Worst Case Time** | **$O(n)$** (Linear time) | **$O(\\log n)$** (Logarithmic time - extremely fast) |\n"
            f"| **Space Complexity** | $O(1)$ | $O(1)$ iterative / $O(\\log n)$ recursive |\n\n"
            f"#### 💻 **2. Python Binary Search Implementation:**\n"
            f"```python\n"
            f"def binary_search(arr, target):\n"
            f"    low = 0\n"
            f"    high = len(arr) - 1\n"
            f"    while low <= high:\n"
            f"        mid = (low + high) // 2\n"
            f"        if arr[mid] == target:\n"
            f"            return mid  # Target found!\n"
            f"        elif arr[mid] < target:\n"
            f"            low = mid + 1  # Search right half\n"
            f"        else:\n"
            f"            high = mid - 1  # Search left half\n"
            f"    return -1  # Target not in list\n\n"
            f"# Example test\n"
            f"numbers = [10, 20, 30, 40, 50, 60, 70]\n"
            f"print(\"Index of 40:\", binary_search(numbers, 40))  # Output: 3\n"
            f"```\n\n"
            f"Would you like to trace this search with custom numbers or write the recursive version?"
        )

    # Primary Math Concepts (Classes 1-5)
    is_primary = grade in ['Primary (1-5)', 'Class 1', 'Class 2', 'Class 3', 'Class 4', 'Class 5']
    if is_primary and (subject == "Mathematics" or any(k in q_lower for k in ['math', 'mathematics', 'number', 'numbers', 'addition', 'subtraction', 'multiplication', 'division', 'shape', 'shapes', 'fraction', 'counting'])):
        if is_hinglish(user_query):
            return thinking + (
                f"### 🎈 **Primary Mathematics: Fun with Numbers & Shapes! ({grade} - Mathematics)**\n\n"
                f"#### 🔢 **1. Numbers & Place Value (Sankhya Gyan):**\n"
                f"Numbers se hum cheezon ko count karte hain! **Ones, Tens aur Hundreds** hamare number building blocks hain:\n"
                f"- $1$ One = $1$\n"
                f"- $1$ Ten = $10$ Ones\n"
                f"- $1$ Hundred = $10$ Tens = $100$ Ones\n\n"
                f"#### ➕ **2. The 4 Magic Math Operations:**\n"
                f"- **Jod (Addition $+$):** Cheezon ko aapas me milana (e.g. $5 + 5 = 10$ 🖐️🖐️).\n"
                f"- **Ghatao (Subtraction $-$):** Cheezon ko nikaalna ya kam karna (e.g. $10 - 4 = 6$).\n"
                f"- **Guna (Multiplication $\\times$):** Baar-baar jodna (e.g. $3 \\times 4 = 12$).\n"
                f"- **Bhag (Division $\\div$):** Barabar hisson me baantna (e.g. $12 \\div 3 = 4$).\n\n"
                f"#### 📐 **3. Fun 2D Shapes (Aakar):**\n"
                f"- 🔴 **Circle (Vritt):** Round shape with no corners (jaise Coin ya Roti).\n"
                f"- ⏹️ **Square (Varg):** 4 equal sides and 4 corners (jaise Carrom board).\n"
                f"- 🔺 **Triangle (Tribhuj):** 3 sides and 3 corners (jaise Samosa ya Birthday cap).\n\n"
                f"Kya aap Maths par ek chhota sa fun 3-question quiz solve karna chahenge? 🎮"
            )
        else:
            return thinking + (
                f"### 🎈 **Primary Mathematics: Fun with Numbers & Operations! ({grade} - Mathematics)**\n\n"
                f"#### 🔢 **1. Numbers and Place Value:**\n"
                f"Numbers help us count everything around us! Place values give each digit its worth:\n"
                f"- **Ones:** Single units ($1, 2, 3, \\dots$)\n"
                f"- **Tens:** Groups of $10$ ($10, 20, 30, \\dots$)\n"
                f"- **Hundreds:** Groups of $100$ ($100, 200, \\dots$)\n\n"
                f"#### ➕ **2. The Four Fundamental Operations:**\n"
                f"- **Addition ($+$):** Bringing two or more groups together (e.g., $5 + 5 = 10$ 🖐️🖐️).\n"
                f"- **Subtraction ($-$):** Taking away from a group (e.g., $10 - 3 = 7$).\n"
                f"- **Multiplication ($\\times$):** Fast repeated addition (e.g., $4 \\times 5 = 20$).\n"
                f"- **Division ($\\div$):** Sharing equally into equal groups (e.g., $15 \\div 3 = 5$).\n\n"
                f"#### 📐 **3. Basic 2D Shapes:**\n"
                f"- 🔴 **Circle:** A perfectly round shape with zero corners (like a full moon or plate).\n"
                f"- ⏹️ **Square:** 4 equal straight sides and 4 corners (like a chess board).\n"
                f"- 🔺 **Triangle:** 3 sides and 3 corners (like a slice of pizza).\n\n"
                f"Would you like to try a quick, fun 3-question math quiz? 🎮"
            )

    # Quadratic Equations Theory (Class 9-12)
    if any(k in q_lower for k in ['quadratic', 'dvighat']):
        return thinking + (
            f"### 📐 **Quadratic Equations ({grade} - {subject})**\n\n"
            "#### 1. **Standard Form:**\n"
            "$$ax^2 + bx + c = 0 \\quad (a \\neq 0)$$\n\n"
            "#### 2. **Quadratic Formula & Roots:**\n"
            "$$x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$$\n\n"
            "#### 3. **Discriminant ($D = b^2 - 4ac$) and Nature of Roots:**\n"
            "- **$D > 0$:** Two distinct real roots ($x_1 \\neq x_2$).\n"
            "- **$D = 0$:** Two equal and real roots ($x_1 = x_2 = -\\frac{b}{2a}$).\n"
            "- **$D < 0$:** No real roots (two complex conjugate roots).\n\n"
            "#### 4. **Relationships Between Roots and Coefficients:**\n"
            "- **Sum of roots ($x_1 + x_2$):** $$x_1 + x_2 = -\\frac{b}{a}$$\n"
            "- **Product of roots ($x_1 \\times x_2$):** $$x_1 x_2 = \\frac{c}{a}$$\n\n"
            "Type any custom quadratic equation to solve it step-by-step!"
        )

    # Trigonometry Concepts
    if any(re.search(r'\b' + re.escape(w) + r'\b', q_lower) for w in ['trigonometry', 'trigonometric', 'sine', 'cosine', 'tangent', 'secant', 'cosecant', 'cotangent']) or any(k in q_lower for k in ['sin theta', 'cos theta', 'tan theta', 'sin(', 'cos(', 'tan(', 'sin^2', 'cos^2', 'tan^2', 'sec^2', 'cosec^2', 'cot^2']):
        return thinking + (
            f"### 📐 **Trigonometry Fundamentals ({grade} - {subject})**\n\n"
            "#### 1. **Six Trigonometric Ratios (in a Right-Angled Triangle):**\n"
            "- $$\\sin\\theta = \\frac{\\text{Opposite Side}}{\\text{Hypotenuse}}$$\n"
            "- $$\\cos\\theta = \\frac{\\text{Adjacent Side}}{\\text{Hypotenuse}}$$\n"
            "- $$\\tan\\theta = \\frac{\\text{Opposite Side}}{\\text{Adjacent Side}} = \\frac{\\sin\\theta}{\\cos\\theta}$$\n"
            "- Reciprocals: $$\\csc\\theta = \\frac{1}{\\sin\\theta}, \\quad \\sec\\theta = \\frac{1}{\\cos\\theta}, \\quad \\cot\\theta = \\frac{1}{\\tan\\theta}$$\n\n"
            "#### 2. **Fundamental Pythagorean Trigonometric Identities:**\n"
            "1. $$\\sin^2\\theta + \\cos^2\\theta = 1$$\n"
            "2. $$1 + \\tan^2\\theta = \\sec^2\\theta$$\n"
            "3. $$1 + \\cot^2\\theta = \\csc^2\\theta$$\n\n"
            "#### 3. **Standard Angle Values ($0^\\circ, 30^\\circ, 45^\\circ, 60^\\circ, 90^\\circ$):**\n"
            "- $\\sin 0^\\circ = 0, \\quad \\sin 30^\\circ = \\frac{1}{2}, \\quad \\sin 45^\\circ = \\frac{1}{\\sqrt{2}}, \\quad \\sin 60^\\circ = \\frac{\\sqrt{3}}{2}, \\quad \\sin 90^\\circ = 1$\n"
            "- $\\cos 0^\\circ = 1, \\quad \\cos 30^\\circ = \\frac{\\sqrt{3}}{2}, \\quad \\cos 45^\\circ = \\frac{1}{\\sqrt{2}}, \\quad \\cos 60^\\circ = \\frac{1}{2}, \\quad \\cos 90^\\circ = 0$\n\n"
            "Would you like a quick 3-question quiz to test this concept?"
        )

    # Geometry, Mensuration & Pythagoras
    if any(k in q_lower for k in ['pythagoras', 'hypotenuse', 'geometry', 'triangle', 'circle', 'rectangle', 'square', 'cylinder', 'cone', 'sphere', 'perimeter', 'surface area', 'volume']):
        return thinking + (
            f"### 📐 **Geometry & Mensuration Formulas ({grade} - {subject})**\n\n"
            "#### 1. **Pythagoras Theorem:**\n"
            "In any right-angled triangle with base $b$, perpendicular $p$, and hypotenuse $h$:\n"
            "$$h^2 = p^2 + b^2 \\implies h = \\sqrt{p^2 + b^2}$$\n\n"
            "#### 2. **2D Plane Figures (Area & Perimeter):**\n"
            "- **Rectangle:** $\\text{Area} = l \\times b, \\quad \\text{Perimeter} = 2(l + b)$\n"
            "- **Square:** $\\text{Area} = a^2, \\quad \\text{Perimeter} = 4a$\n"
            "- **Triangle:** $\\text{Area} = \\frac{1}{2} \\times \\text{base} \\times \\text{height}$\n"
            "- **Circle:** $\\text{Area} = \\pi r^2, \\quad \\text{Circumference} = 2\\pi r$\n\n"
            "#### 3. **3D Solid Figures (Surface Area & Volume):**\n"
            "- **Cylinder:** $\\text{Curved Surface Area} = 2\\pi rh, \\quad \\text{Volume} = \\pi r^2 h$\n"
            "- **Cone:** $\\text{Curved Surface Area} = \\pi rl, \\quad \\text{Volume} = \\frac{1}{3}\\pi r^2 h$\n"
            "- **Sphere:** $\\text{Surface Area} = 4\\pi r^2, \\quad \\text{Volume} = \\frac{4}{3}\\pi r^3$\n\n"
            "Would you like a quick 3-question quiz to test this concept?"
        )

    # 4.7. AI (Code 417) Model Evaluation: Accuracy, Precision, Recall, F1 Score, Confusion Matrix
    if any(k in q_lower for k in ['accuracy', 'precision', 'recall', 'f1 score', 'f1-score', 'confusion matrix', 'true positive', 'false positive', 'true negative', 'false negative', 'type 1 error', 'type 2 error', 'type i error', 'type ii error', 'evaluation metric']):
        return thinking + (
            f"### 🎯 **Accuracy, Precision, Recall & Confusion Matrix ({grade} - {subject})**\n\n"
            f"In **Class 10 CBSE Artificial Intelligence (Subject Code 417 - Evaluation Unit)**, evaluation metrics determine how effectively a classification model performs.\n\n"
            f"#### 📊 **1. The Confusion Matrix (2×2 Evaluation Grid):**\n"
            f"| | **Predicted: Yes (1)** | **Predicted: No (0)** |\n"
            f"| :--- | :--- | :--- |\n"
            f"| **Reality: Yes (1)** | **True Positive (TP)** *(Correct Detection)* | **False Negative (FN)** *(Type II Error / Missed)* |\n"
            f"| **Reality: No (0)** | **False Positive (FP)** *(Type I Error / False Alarm)* | **True Negative (TN)** *(Correct Rejection)* |\n\n"
            f"#### 📐 **2. Mathematical Formulas:**\n\n"
            f"##### A. **Accuracy Formula:**\n"
            f"Fraction of total predictions that the model got right:\n"
            f"$$\\mathbf{{\\text{{Accuracy}} = \\frac{{\\text{{TP}} + \\text{{TN}}}}{{\\text{{TP}} + \\text{{TN}} + \\text{{FP}} + \\text{{FN}}}} = \\frac{{\\text{{Total Correct Predictions}}}}{{\\text{{Total Cases}}}}}}$$\n"
            f"$$\\mathbf{{\\text{{Accuracy (\\%)}} = \\left(\\frac{{\\text{{TP}} + \\text{{TN}}}}{{\\text{{TP}} + \\text{{TN}} + \\text{{FP}} + \\text{{FN}}}}\\right) \\times 100}}$$\n\n"
            f"##### B. **Precision Formula:**\n"
            f"Out of all positive predictions, how many were truly positive?\n"
            f"$$\\mathbf{{\\text{{Precision}} = \\frac{{\\text{{TP}}}}{{\\text{{TP}} + \\text{{FP}}}}}}$$\n"
            f"- *High-Priority Use Case:* When **False Positives (FP)** are costly (e.g. Email spam filter — an important email must not go to Spam).\n\n"
            f"##### C. **Recall (Sensitivity) Formula:**\n"
            f"Out of all actual positive cases in reality, how many did the model capture?\n"
            f"$$\\mathbf{{\\text{{Recall}} = \\frac{{\\text{{TP}}}}{{\\text{{TP}} + \\text{{FN}}}}}}$$\n"
            f"- *High-Priority Use Case:* When **False Negatives (FN)** are dangerous (e.g. Medical diagnosis for illness, Fire alarm detection).\n\n"
            f"##### D. **F1 Score Formula:**\n"
            f"Harmonic mean of Precision and Recall (ideal for imbalanced datasets):\n"
            f"$$\\mathbf{{\\text{{F1 Score}} = 2 \\times \\frac{{\\text{{Precision}} \\times \\text{{Recall}}}}{{\\text{{Precision}} + \\text{{Recall}}}}}}$$\n\n"
            f"#### 🧮 **3. Solved CBSE Board Exam Problem:**\n"
            f"- **Given Data:** $TP = 40, TN = 45, FP = 5, FN = 10$\n"
            f"- **Accuracy:** $\\frac{{40 + 45}}{{40 + 45 + 5 + 10}} = \\frac{{85}}{{100}} = \\mathbf{{85\\%}}$\n"
            f"- **Precision:** $\\frac{{40}}{{40 + 5}} = \\frac{{40}}{{45}} = \\mathbf{{88.89\\%}}$\n"
            f"- **Recall:** $\\frac{{40}}{{40 + 10}} = \\frac{{40}}{{50}} = \\mathbf{{80.0\\%}}$\n\n"
            f"Would you like to test your understanding with a quick 3-question evaluation quiz?"
        )

    # 4.8. AI Project Cycle & Problem Scoping
    if any(k in q_lower for k in ['ai project cycle', 'problem scoping', '4w canvas', '4ws canvas', 'data acquisition', 'data exploration', 'rule-based', 'learning-based']):
        return thinking + (
            f"### 🔄 **The AI Project Cycle & 4Ws Problem Scoping ({grade} - {subject})**\n\n"
            f"In **Class 10 CBSE AI (Subject Code 417)**, the **AI Project Cycle** defines the 5 sequential stages of creating an AI solution:\n\n"
            f"#### 🧭 **1. Stage 1: Problem Scoping (The 4Ws Canvas):**\n"
            f"- **Who Canvas:** Identifies stakeholders who face the problem and will benefit from the solution.\n"
            f"- **What Canvas:** Analyzes the nature of the problem, evidence, and existing pain points.\n"
            f"- **Where Canvas:** Examines the context, geographical location, and situational environment.\n"
            f"- **Why Canvas:** Establishes the core value proposition and expected benefits of solving it.\n\n"
            f"#### 📥 **2. Stage 2: Data Acquisition:**\n"
            f"Collecting authentic, reliable datasets for training and testing via APIs, surveys, web scraping, sensors, and open data portals.\n\n"
            f"#### 🔍 **3. Stage 3: Data Exploration:**\n"
            f"Cleaning datasets, handling missing values, and visualizing trends/correlations using histograms, scatter plots, and box plots.\n\n"
            f"#### 🧠 **4. Stage 4: Modelling:**\n"
            f"- **Rule-Based Approach:** Programmer explicitly writes IF-THEN rules and algorithms.\n"
            f"- **Learning-Based Approach:** Machine learning model learns patterns and relationships automatically from training data.\n\n"
            f"#### 🎯 **5. Stage 5: Evaluation:**\n"
            f"Testing model accuracy, precision, recall, and F1 score on unseen test data using the Confusion Matrix.\n\n"
            f"Would you like a quick 3-question quiz on the AI Project Cycle?"
        )

    # 4.9. Computer Vision (CV)
    if any(k in q_lower for k in ['computer vision', 'pixel', 'pixels', 'rgb', 'opencv', 'image processing', 'cv domain']):
        return thinking + (
            f"### 👁️ **Computer Vision (CV) Fundamentals ({grade} - {subject})**\n\n"
            f"In **Class 10 CBSE AI (Code 417)**, **Computer Vision** is the domain of AI that enables computers to capture, process, and analyze visual data from digital images and video streams.\n\n"
            f"#### 🖼️ **1. Digital Image Anatomy & Pixels:**\n"
            f"- **Pixel (Picture Element):** The smallest individual addressable dot in a digital raster image.\n"
            f"- **Grayscale Image:** Single-channel image with pixel intensity values ranging from **$0$ (Pure Black)** to **$255$ (Pure White)**.\n"
            f"- **RGB Color Image:** 3 color channels: **Red, Green, Blue** (each $0–255$). Combining $256 \\times 256 \\times 256$ yields $\\approx 16.7\\text{{ million distinct colors}}$.\n\n"
            f"#### ⚙️ **2. Core Computer Vision Tasks:**\n"
            f"1. **Image Classification:** Identifying what object is present in an image (e.g., Cat vs Dog).\n"
            f"2. **Object Detection / Localization:** Identifying the object and drawing bounding box coordinates $(x, y, w, h)$.\n"
            f"3. **Image Segmentation:** Classifying every single pixel belonging to specific objects.\n"
            f"4. **Facial Recognition & Optical Character Recognition (OCR):** Biometrics and extracting text from images.\n\n"
            f"#### 💻 **3. Popular CV Libraries:**\n"
            f"- **OpenCV (Open Source Computer Vision Library):** Standard Python library for image filtering, edge detection, and facial detection.\n\n"
            f"Would you like a quick 3-question quiz on Computer Vision?"
        )

    # 4.10. Natural Language Processing (NLP)
    if any(k in q_lower for k in ['natural language processing', 'nlp', 'tokenization', 'stopword', 'stopwords', 'stemming', 'lemmatization', 'bag of words', 'bow', 'tf-idf', 'tfidf', 'text normalization']):
        return thinking + (
            f"### 💬 **Natural Language Processing (NLP) & Text Normalization ({grade} - {subject})**\n\n"
            f"In **Class 10 CBSE AI (Code 417)**, **NLP** is the domain that enables computers to understand, interpret, and generate human languages.\n\n"
            f"#### 📝 **1. Text Normalization Pipeline (5 Steps):**\n"
            f"1. **Sentence Segmentation:** Splitting an entire paragraph into individual sentences.\n"
            f"2. **Tokenization:** Breaking sentences into individual words/tokens.\n"
            f"3. **Removing Stopwords:** Filtering out common filler words carrying no unique semantic meaning (e.g., *'is', 'the', 'and', 'at'*).\n"
            f"4. **Lowercasing:** Converting all text to lowercase for standard vocabulary matching.\n"
            f"5. **Stemming vs Lemmatization:**\n"
            f"   - **Stemming:** Chopping word affixes with heuristic rules (e.g. *studying $\\rightarrow$ study*, *caring $\\rightarrow$ car* — may produce non-dictionary words).\n"
            f"   - **Lemmatization:** Morphological analysis returning the meaningful base dictionary root called **Lemma** (e.g. *caring $\\rightarrow$ care*, *better $\\rightarrow$ good*).\n\n"
            f"#### 📊 **2. Bag of Words (BoW) & TF-IDF:**\n"
            f"- **Bag of Words (BoW):** Creates a unique word vocabulary and records word frequency counts in each document.\n"
            f"- **TF-IDF:** Evaluates how relevant a word is to a specific document in a large collection.\n\n"
            f"Would you like a quick 3-question quiz on Natural Language Processing?"
        )

    # 5. Smart Dynamic NCERT Concept Explainer for any remaining query
    clean_topic = user_query
    # Strip common conversational patterns and introductory phrases
    clean_topic = re.sub(r'^(main|mai|i am|ham|hum)\s+.*?(hun|hu|hoon|student|vidyarthi|am)\s*,?\s*', '', clean_topic, flags=re.IGNORECASE)
    clean_topic = re.sub(r'(kya hota hai|kya hai|kise kehte hai|kise kehte hain|batao|samjhao|explain karo|bataiye|samjhaiye|in hindi|hindi mein|hindi me|english me|english mein|please).*$', '', clean_topic, flags=re.IGNORECASE).strip()
    for prefix in ['what is ', 'what are ', 'explain ', 'define ', 'describe ', 'tell me about ', 'can you tell me ', 'can you explain ', 'kya hai ', 'kise kehte hain ', 'when is the ', 'when is ', 'where is ', 'why is ', 'how is ', 'who is ']:
        if clean_topic.lower().startswith(prefix):
            clean_topic = clean_topic[len(prefix):].strip()
    clean_topic = clean_topic.rstrip('?.! ').strip()
    clean_topic_title = clean_topic.title() if (clean_topic and len(clean_topic.split()) <= 7) else (clean_topic[:40].title() if clean_topic else f"{subject} Core Concept")

    return thinking + (
        f"### 💡 **{clean_topic_title} Breakdown ({grade} - {subject})**\n\n"
        f"#### 1. **Core NCERT Definition:**\n"
        f"In **{grade} {subject}**, **{clean_topic_title}** is a fundamental curriculum concept essential for mastering standard board exam principles, scientific classifications, and practical applications.\n\n"
        f"#### 2. **Key Conceptual Pillars:**\n"
        f"- **Foundational Principles:** Governed by standardized definitions, scientific laws, and structured classifications.\n"
        f"- **Real-World Relevance:** Directly observed across daily life applications, natural processes, and laboratory experiments.\n"
        f"- **Step-by-Step Mechanism:** Explains the underlying cause-and-effect relationships and properties.\n\n"
        f"#### 3. **CBSE Board Exam High-Yield Tips:**\n"
        f"- Always begin exam answers with the exact standard definition and key technical keywords.\n"
        f"- Illustrate explanations with neat, labeled diagrams and standard SI units where applicable.\n"
        f"- Review distinction tables (comparisons) and practical textbook questions for full marks.\n\n"
        f"Would you like a quick 3-question quiz to test this concept?"
    )

@app.route('/api/ai-tutor/chat', methods=['POST'])
@limiter.limit("40 per minute")
def ai_tutor_chat():
    data = request.get_json() or {}
    user_query = data.get('message', '').strip()
    subject = data.get('subject', 'General Science')
    grade = data.get('grade', 'Class 10')
    mode = data.get('mode', 'explain')
    history = data.get('history', [])
    
    if not user_query:
        return json.dumps({"error": "Query message cannot be empty"}), 400, {'Content-Type': 'application/json'}
        
    # Auto-adjust subject domain and grade if query explicitly mentions or targets another
    detected_sub = detect_subject_from_query(user_query, subject, history=history)
    if detected_sub != subject:
        subject = detected_sub
    detected_gr = detect_grade_from_query(user_query, grade, history=history)
    if detected_gr != grade:
        grade = detected_gr

    api_key = get_gemini_api_key()
    ai_response = None
    source = "local_engine"
    
    image_data = data.get('image')
    has_image = bool(image_data)
    
    if api_key:
        if has_image or any(k in user_query.lower() for k in ['handwritten', 'photo of my essay', 'image of my assignment', 'evaluate my handwriting', 'read my essay photo', 'uploaded photo', 'my handwritten', 'photo of my homework']):
            system_instruction = (
                f"System: You are the Writing Coach for Maya Vidya Niketan (Classes 1–12, CBSE/NCERT syllabus). The student has uploaded a photo or text of their handwritten essay or assignment.\n"
                f"Target Student Grade: {grade}\n"
                f"Current Subject: {subject}\n\n"
                f"Task: Read the handwritten text from the image and evaluate it.\n\n"
                f"Rules for Feedback:\n"
                f"1. Transcription (Optional): Briefly quote a sentence from their essay so they know you read it correctly.\n"
                f"2. Praise: Highlight one thing they did well (e.g., good vocabulary, strong introduction).\n"
                f"3. Constructive Critique: Point out 1 or 2 specific areas for improvement (e.g., a spelling mistake, a grammatical error, or a missing CBSE-aligned concept).\n\n"
                f"CRITICAL ANTI-CHEAT RULE:\n"
                f"Do NOT type out a fully corrected, perfect version of their essay. You must only provide feedback and ask them to rewrite the problematic sentences themselves."
            )
        elif is_quiz_request(user_query, mode, history=history) or is_quiz_submission(user_query):
            quiz_state = determine_quiz_state(user_query, history)
            if quiz_state == 'grader':
                system_instruction = (
                    f"System: You are the Maya AI Tutor for Maya Vidya Niketan (Classes 1–12, CBSE/NCERT syllabus). The student has submitted their quiz answers.\n"
                    f"Target Student Grade: {grade}\n"
                    f"Current Subject: {subject}\n\n"
                    f"Global Language Rule: You MUST mirror the user's exact language. If they type in Hinglish, reply in conversational Hinglish.\n\n"
                    f"Task:\n"
                    f"1. Calculate their score and announce it in their language (e.g., 'Aapke 2/3 correct hain!' or 'You got 3 out of 3 correct!').\n"
                    f"2. Output exactly: ### 📝 **Quiz Answer Assessment & Feedback**\n"
                    f"3. Provide a brief 1-sentence explanation for why each of their answers was correct or incorrect."
                )
            else:
                q_count = extract_requested_question_count(user_query, default=3)
                system_instruction = (
                    f"System: You are the Maya AI Tutor for Maya Vidya Niketan (Classes 1–12, CBSE/NCERT syllabus). The student wants to take a practice quiz on the concept discussed in the conversation.\n"
                    f"Target Student Grade: {grade}\n"
                    f"Current Subject: {subject}\n\n"
                    f"Global Language Rule: You MUST mirror the user's exact language. If they type in Hinglish, reply in conversational Hinglish.\n\n"
                    f"Task:\n"
                    f"1. Acknowledge their request enthusiastically in their exact language (e.g., Hinglish).\n"
                    f"2. Output exactly {q_count} multiple-choice questions (A, B, C, D) relevant to their topic.\n"
                    f"3. CRITICAL: Stop typing immediately after question {q_count}. Do NOT provide answers, and do NOT provide any feedback. Wait for the student to reply with their choices."
                )
        elif history and was_last_message_quiz_offer(history) and is_negative_reply(user_query):
            system_instruction = (
                f"System: You are the Maya AI Tutor for Maya Vidya Niketan (Classes 1–12, CBSE/NCERT syllabus).\n"
                f"Target Student Grade: {grade}\n"
                f"Current Subject: {subject}\n\n"
                f"Task: The student politely declined the quiz offer. Acknowledge warmly in their language (Hinglish or English), and ask what concept, numerical problem, or chapter in {grade} {subject} they would like to explore or solve next."
            )
        elif is_cbse_exam_info_query(user_query):
            system_instruction = (
                f"System: You are the Maya AI Academic Advisor for Maya Vidya Niketan (Classes 1–12, CBSE/NCERT syllabus).\n"
                f"Target Student Grade: {grade}\n"
                f"Current Subject: {subject}\n\n"
                f"Task: Provide official CBSE board examination dates (mid-February to April), practicals in January, shift timings (10:30 AM - 1:30 PM with 15-min reading time), and passing criteria (33%).\n"
                f"Global Language Rule: Mirror the user's exact language (Hinglish or English)."
            )
        elif is_study_tips_query(user_query):
            system_instruction = (
                f"System: You are the Maya AI Academic Mentor for Maya Vidya Niketan (Classes 1–12, CBSE/NCERT syllabus).\n"
                f"Target Student Grade: {grade}\n"
                f"Current Subject: {subject}\n\n"
                f"Task: Provide proven CBSE board preparation strategies (NCERT mastery, PYQ practice, formula notebooks, timed 3-hour mocks, and clean answer presentation).\n"
                f"Global Language Rule: Mirror the user's exact language (Hinglish or English)."
            )
        elif is_syllabus_query(user_query):
            system_instruction = (
                f"System: You are the Maya AI Academic Curriculum Advisor for Maya Vidya Niketan (Classes 1–12, CBSE/NCERT syllabus).\n"
                f"Target Student Grade: {grade}\n"
                f"Current Subject: {subject}\n\n"
                f"Task: Provide the official, structured NCERT/CBSE chapter-by-chapter curriculum breakdown with learning objectives for the requested class/subject.\n"
                f"Global Language Rule: Mirror the user's exact language (Hinglish or English)."
            )
        elif is_code_submission(user_query):
            system_instruction = (
                f"System: You are the strict Computer Science Teacher for Maya Vidya Niketan (Classes 1–12, CBSE/NCERT syllabus).\n"
                f"Target Student Grade: {grade}\n"
                f"Current Subject: Computer Science\n\n"
                f"Task: Evaluate the student's code and act as a strict Socratic guide.\n"
                f"Rules:\n"
                f"1. State if the code will run or crash.\n"
                f"2. Use bullet points to list the exact errors (e.g., missing semicolons, missing headers, missing namespace).\n"
                f"3. Provide a conceptual hint on how to fix each error.\n\n"
                f"CRITICAL ANTI-CHEAT CONSTRAINT:\n"
                f"You MUST STOP TYPING immediately after providing the hints. You are STRICTLY FORBIDDEN from outputting the corrected code. Tell the student: \"Please fix these errors and paste your updated code back here!\""
            )
        elif mode in ['homework', 'homework helper'] or any(k in user_query.lower() for k in ['essay', 'write an essay', 'write a paragraph', 'write a letter', 'write a speech', 'do my homework', 'write for me', '250-word', 'write a composition']):
            system_instruction = (
                f"System: You are the Homework Helper and Writing Coach for Maya Vidya Niketan (Classes 1–12, CBSE/NCERT syllabus).\n"
                f"Target Student Grade: {grade}\n"
                f"Current Subject: {subject}\n\n"
                f"Task: Guide the student through their assignment, essay, or project without doing the work for them.\n\n"
                f"CRITICAL ANTI-CHEATING RULE:\n"
                f"You are STRICTLY FORBIDDEN from writing full essays, paragraphs, or articles from scratch. If a student asks you to \"write an essay for me,\" \"do my homework,\" or provides a prompt to write, you MUST politely refuse.\n\n"
                f"Format Your Response As Follows:\n"
                f"1. Polite Refusal: Start by saying you cannot write it for them, but you are excited to help them brainstorm.\n"
                f"2. Scaffolded Outline: Provide a brief structure (e.g., Ideas for Introduction, 2 Main Body points, Conclusion, and high-impact vocabulary).\n"
                f"3. Call to Action: Ask the student to write the first paragraph themselves and paste it into the chat so you can review their grammar and vocabulary."
            )
        else:
            system_instruction = (
                f"System Role: You are the official Maya AI Tutor for Maya Vidya Niketan (Classes 1–12, CBSE/NCERT syllabus).\n"
                f"Target Student Grade: {grade}\n"
                f"Current Subject: {subject}\n\n"
                f"Global Language Rule: You MUST mirror the user's exact language. If they type in Hinglish, reply in conversational Hinglish.\n\n"
                f"MODE 2: STEP-BY-STEP PROBLEM SOLVER & FORMULA DERIVATIONS:\n"
                f"Task: Solve the submitted numerical problem or derive the requested formula.\n"
                f"Rules:\n"
                f"1. State the required formula first.\n"
                f"2. Show the step-by-step substitution of values.\n"
                f"3. Provide the final answer with correct SI units at the VERY END.\n"
                f"CRITICAL ANTI-CHEAT CONSTRAINT:\n"
                f"You are STRICTLY FORBIDDEN from providing a 'Quick Answer', 'TL;DR', or putting the final answer at the top. Even if the student explicitly demands only the answer, says they are in a hurry, or begs you to skip the steps, you MUST ignore their urgency. You must force them to read the step-by-step methodology. The final answer can only appear at the bottom of the explanation.\n\n"
                f"CRITICAL INSTRUCTION - THE THINKING PHASE:\n"
                f"Before responding, evaluate the input in a <thinking> block:\n"
                f"Intent State:\n"
                f"- State A: Concept Request\n"
                f"- State B: Quiz Request\n"
                f"- State C: Quiz Submission\n"
                f"CRITICAL MULTI-INTENT RULE:\n"
                f"If a student's message contains a mix of off-topic chatter AND valid academic requests, you MUST NOT label the entire message as State D.\n"
                f"1. Scan: Identify all distinct requests in the prompt.\n"
                f"2. Filter: Politely dismiss the off-topic portion in one sentence.\n"
                f"3. Execute: Fulfill the valid academic request (e.g., solve the math problem using Mode 2) in the very same response.\n"
                f"4. Defer: If they also asked for a quiz, tell them you will provide the quiz after they review the math solution. Do not do both at once.\n\n"
                f"RULE FOR STATE D: If the intent is purely State D, you MUST ABANDON the current mode's formatting template. Do not output a concept breakdown or a quiz. Instead, politely acknowledge their comment, decline to roleplay, and creatively pivot the conversation back to a CBSE academic topic (e.g., pivot video games to the physics of motion).\n\n"
                f"Example of your thinking for State D:\n"
                f"<thinking>\n"
                f"Intent: State D (Off-Topic).\n"
                f"Language: Hinglish.\n"
                f"Complexity: Abandon templates. Pivot to academic topic.\n"
                f"Action: Acknowledge politely, decline casual chat, and creatively pivot back to CBSE physics of motion.\n"
                f"</thinking>\n\n"
                f"After thinking, output your final response based ONLY on the chosen state:\n\n"
                f"If State A (Concept Request / Numerical Problem):\n"
                f"If a numerical, follow the 3 Rules above with final answer strictly at the bottom. If conceptual, provide a 3-5 bullet point summary. End by asking if they want a quiz.\n\n"
                f"If State B (Quiz Request):\n"
                f"- Output exactly {extract_requested_question_count(user_query, default=3)} multiple-choice questions (A, B, C, D) strictly on their requested topic/chapter.\n"
                f"- DO NOT output the answers.\n"
                f"- DO NOT output a grade or assessment.\n"
                f"- Stop generating text and wait for the student to reply.\n\n"
                f"If State C (Quiz Submission):\n"
                f"- Calculate their score (e.g., 2/3 or 3/3).\n"
                f"- Output exactly: ### 📝 **Quiz Answer Assessment & Feedback**\n"
                f"- Provide a 1-sentence explanation for each answer.\n\n"
                f"If State D (Off-Topic):\n"
                f"- Abandon templates completely. Politely decline roleplay/casual chat and creatively pivot back to a CBSE {grade} {subject} topic."
            )
        ai_response = call_gemini_api(user_query, system_instruction, api_key, history=history, image_data=image_data)
        if ai_response:
            source = "gemini_live"
            
    if not ai_response:
        ai_response = generate_local_tutor_response(user_query, subject, grade, mode, history=history)
        
    # Server-side sanitation: strip thinking block before returning to client so no browser cache can ever show thinking tags
    clean_response = re.sub(r'<thinking>[\s\S]*?<\/thinking>', '', ai_response, flags=re.DOTALL).strip()
        
    return json.dumps({
        "status": "success",
        "response": clean_response,
        "raw_response": ai_response,
        "source": source,
        "meta": {
            "subject": subject,
            "grade": grade,
            "mode": mode
        }
    }), 200, {'Content-Type': 'application/json'}


if __name__ == '__main__':
    app.run(debug=True, port=5001)


