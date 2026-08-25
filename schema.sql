CREATE TABLE IF NOT EXISTS news (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    image_path TEXT,
    date_posted TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notices (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    link TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    date_posted TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admissions (
    id SERIAL PRIMARY KEY,
    student_name VARCHAR(255) NOT NULL,
    grade_applied VARCHAR(50) NOT NULL,
    parent_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    submission_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recruitment (
    id SERIAL PRIMARY KEY,
    applicant_name VARCHAR(255) NOT NULL,
    position_applied VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    qualifications TEXT NOT NULL,
    submission_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS site_images (
    id SERIAL PRIMARY KEY,
    section_name VARCHAR(100) UNIQUE,
    image_path TEXT,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    adm_no VARCHAR(20) UNIQUE NOT NULL,
    student_name VARCHAR(255) NOT NULL,
    father_name VARCHAR(255),
    class VARCHAR(50),
    section VARCHAR(10),
    mobile VARCHAR(20),
    category VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS aadhaar_submissions (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    student_aadhaar_encrypted TEXT NOT NULL,
    father_aadhaar_encrypted TEXT NOT NULL,
    dob DATE,
    submission_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT IGNORE INTO site_images (section_name, image_path) VALUES 
('physics_lab', 'images/physics_lab.png'), 
('chemistry_lab', 'images/chemistry_lab.png'), 
('classroom', 'images/classroom_benq.png');

INSERT IGNORE INTO news (title, content, date_posted) VALUES 
('MAYA VIDYA NIKETAN CELEBRATES CLASS 10 TOPPER', 'A huge congratulations to Mrityunjay Singh for his exceptional achievement in the Class 10 examinations. You have made the entire Maya Vidya Niketan family proud!', '2026-04-16 10:00:00'), 
('ADMISSIONS OPEN 2026-2027', 'The wait is over! Admissions for the 2026-2027 academic session are officially open. Secure your childs future with Maya Vidya Niketan.', '2026-04-01 09:00:00'), 
('IIT MADRAS CERTIFICATION COURSES', 'Unlock your future! We are now offering specialized IIT Madras certification courses for Class X, XI, and XII students to give them a head start in their technical careers.', '2025-07-22 11:00:00');
