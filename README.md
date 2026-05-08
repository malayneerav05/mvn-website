# Maya Vidya Niketan - Full-Stack Website

A full-stack responsive website for Maya Vidya Niketan, built using Python (Flask), MySQL, and Tailwind CSS.

## Features
- **Homepage:** Welcoming landing page with a message from Directoress Chandrika Kumari and an About Us section.
- **Campus & Facilities:** Visual showcase of the Physics Lab, new Chemistry Lab (with mahogany workbenches), and interactive classrooms.
- **News Feed:** Dynamic news articles fetched from a MySQL database.
- **Admissions & Recruitment:** Interactive forms for 2026-2027 admissions and staff recruitment, connected to MySQL and a webhook for n8n integration.

## Prerequisites
- Python 3.8+
- MySQL Server
- `pip` package manager

## Setup Instructions

### 1. Create a Virtual Environment (Optional but recommended)
```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Database Configuration
Ensure your MySQL server is running. Create a `.env` file in the root directory (or simply configure your environment variables) with the following variables:
```env
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_mysql_password
DB_NAME=mvn_db
WEBHOOK_URL=http://your-n8n-webhook-url
```
*(If you don't use a `.env` file, the script defaults to `root` with no password).*

### 4. Initialize the Database
Run the initialization script to create the database, tables, and insert placeholder news articles:
```bash
python init_db.py
```

### 5. Run the Application
Start the Flask development server:
```bash
python app.py
```
Open your browser and navigate to `http://127.0.0.1:5000` to view the website.

## Admin Dashboard
The application includes a protected Admin Dashboard to view all admission and recruitment applications. 

### Accessing the Dashboard
- Navigate to `http://127.0.0.1:5000/admin` or click the "Admin Login" link in the website footer.
- **Default Username:** `admin`
- **Default Password:** `admin123`

*Note: You can override these credentials by setting `ADMIN_USERNAME` and `ADMIN_PASSWORD` in your `.env` file.*
