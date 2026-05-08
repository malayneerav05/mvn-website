DROP TABLE IF EXISTS news;
DROP TABLE IF EXISTS admissions;
DROP TABLE IF EXISTS recruitment;

CREATE TABLE news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    date_posted TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE admissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_name VARCHAR(255) NOT NULL,
    grade_applied VARCHAR(50) NOT NULL,
    parent_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    submission_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE recruitment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    applicant_name VARCHAR(255) NOT NULL,
    position_applied VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    qualifications TEXT NOT NULL,
    submission_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO news (title, content) VALUES
('Celebrating our Foundation Day', 'We proudly celebrated our Foundation Day with a series of cultural events and academic showcases. A big thank you to all the students, parents, and staff who made it a memorable occasion.'),
('Highlights from the Annual Sports Meet', 'Our Annual Sports Meet was a huge success. Students showed exceptional talent and sportsmanship across track and field events. Congratulations to the Blue House for winning the overall championship trophy!'),
('Success at our recent Result Day & Parent-Teacher Meeting', 'The recent Result Day and Parent-Teacher Meeting highlighted the hard work of our students and the dedicated support of parents. We look forward to continued academic excellence.'),
('Table Tennis Tournament Achievements', 'A special congratulation to Naman Gupta and Manan Gupta for their outstanding performance in the regional Table Tennis Tournament. They have brought great pride to Maya Vidya Niketan!');
