from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
import os
from functools import wraps
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Configurations for Uploads
UPLOAD_FOLDER = 'static/images/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure upload directory exists
os.makedirs(os.path.join(app.root_path, UPLOAD_FOLDER), exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Admin Credentials
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    db_path = os.path.join(os.path.dirname(__file__), 'database.db')
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    return conn

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
    return render_template('index.html')

@app.route('/campus')
def campus():
    conn = None
    images = {}
    try:
        conn = get_db_connection()
        rows = conn.execute('SELECT section_name, image_path FROM site_images').fetchall()
        images = {row['section_name']: row['image_path'] for row in rows}
    except sqlite3.Error as err:
        print(f"Database Error: {err}")
    finally:
        if conn: conn.close()
    
    return render_template('campus.html', images=images)

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
        filename = secure_filename(file.filename)
        # Add section prefix to avoid name collisions
        filename = f"{section_name}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(os.path.join(app.root_path, file_path))
        
        # Update database with new path (relative to static)
        relative_path = os.path.join('images/uploads', filename)
        
        try:
            conn = get_db_connection()
            conn.execute('UPDATE site_images SET image_path = ? WHERE section_name = ?', 
                         (relative_path, section_name))
            conn.commit()
            flash(f'Image for {section_name} updated successfully!', 'success')
        except sqlite3.Error as err:
            flash(f'Database error: {err}', 'error')
        finally:
            if conn: conn.close()
            
    return redirect(url_for('admin'))

@app.route('/news')
def news():
    conn = None
    news_items = []
    try:
        conn = get_db_connection()
        news_items = conn.execute('SELECT * FROM news ORDER BY date_posted DESC').fetchall()
    except sqlite3.Error as err:
        print(f"Database Error: {err}")
    finally:
        if conn: conn.close()
        
    return render_template('news.html', news=news_items)

@app.route('/admissions', methods=['GET', 'POST'])
def admissions():
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        
        conn = None
        try:
            conn = get_db_connection()
            
            if form_type == 'admission':
                student_name = request.form.get('student_name')
                grade = request.form.get('grade')
                parent_name = request.form.get('parent_name')
                email = request.form.get('email')
                phone = request.form.get('phone')
                
                conn.execute(
                    'INSERT INTO admissions (student_name, grade_applied, parent_name, email, phone) VALUES (?, ?, ?, ?, ?)',
                    (student_name, grade, parent_name, email, phone)
                )
                
                flash('Admission application submitted successfully!', 'success')
                
            elif form_type == 'recruitment':
                applicant_name = request.form.get('applicant_name')
                position = request.form.get('position')
                email = request.form.get('email')
                phone = request.form.get('phone')
                qualifications = request.form.get('qualifications')
                
                conn.execute(
                    'INSERT INTO recruitment (applicant_name, position_applied, email, phone, qualifications) VALUES (?, ?, ?, ?, ?)',
                    (applicant_name, position, email, phone, qualifications)
                )
                
                flash('Recruitment application submitted successfully!', 'success')
            
            conn.commit()
            
        except sqlite3.Error as err:
            flash(f'Database error: {err}', 'error')
        finally:
            if conn: conn.close()
            
        return redirect(url_for('admissions'))
        
    return render_template('admissions.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
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
    
    try:
        conn = get_db_connection()
        
        admissions_data = conn.execute('SELECT * FROM admissions ORDER BY submission_date DESC').fetchall()
        recruitment_data = conn.execute('SELECT * FROM recruitment ORDER BY submission_date DESC').fetchall()
        news_data = conn.execute('SELECT * FROM news ORDER BY date_posted DESC').fetchall()
        
    except sqlite3.Error as err:
        flash(f'Database error: {err}', 'error')
    finally:
        if conn: conn.close()
        
    return render_template('admin.html', admissions=admissions_data, recruitment=recruitment_data, news=news_data)

@app.route('/admin/add_news', methods=['POST'])
@login_required
def add_news():
    title = request.form.get('title')
    content = request.form.get('content')
    
    if not title or not content:
        flash('Title and content are required!', 'error')
        return redirect(url_for('admin'))
    
    try:
        conn = get_db_connection()
        conn.execute('INSERT INTO news (title, content) VALUES (?, ?)', (title, content))
        conn.commit()
        flash('News article posted successfully!', 'success')
    except sqlite3.Error as err:
        flash(f'Database error: {err}', 'error')
    finally:
        if conn: conn.close()
        
    return redirect(url_for('admin'))

@app.route('/admin/delete_news/<int:news_id>', methods=['POST'])
@login_required
def delete_news(news_id):
    try:
        conn = get_db_connection()
        conn.execute('DELETE FROM news WHERE id = ?', (news_id,))
        conn.commit()
        flash('News article deleted successfully!', 'success')
    except sqlite3.Error as err:
        flash(f'Database error: {err}', 'error')
    finally:
        if conn: conn.close()
        
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=False, port=5001)
