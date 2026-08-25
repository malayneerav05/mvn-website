import pymysql
import os
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()

def init_mysql():
    host = os.getenv('MYSQL_HOST', 'localhost')
    user = os.getenv('MYSQL_USER')
    password = os.getenv('MYSQL_PASSWORD')
    db_name = os.getenv('MYSQL_DB')

    if not all([user, db_name]):
        raise Exception("MYSQL_USER and MYSQL_DB must be set.")

    print(f"Connecting to MySQL database '{db_name}'...")
    
    # Use the correct connection method for Cloud SQL vs Local
    if host.startswith('/cloudsql/'):
        conn = pymysql.connect(
            unix_socket=host,
            user=user,
            password=password,
            database=db_name,
            autocommit=True
        )
    else:
        conn = pymysql.connect(
            host=host, 
            user=user, 
            password=password, 
            database=db_name,
            autocommit=True
        )
        
    try:
        cursor = conn.cursor()

        # Read the schema file
        schema_path = 'schema.sql'
        with open(schema_path, 'r') as f:
            sql_script = f.read()

        # Execute statements
        statements = sql_script.split(';')
        for statement in statements:
            clean_stmt = statement.strip()
            if not clean_stmt: continue
            
            # Convert PostgreSQL/SQLite specific syntax to MySQL
            clean_stmt = clean_stmt.replace('SERIAL PRIMARY KEY', 'INT AUTO_INCREMENT PRIMARY KEY')
            clean_stmt = clean_stmt.replace('TIMESTAMP DEFAULT CURRENT_TIMESTAMP', 'DATETIME DEFAULT CURRENT_TIMESTAMP')
            
            try:
                cursor.execute(clean_stmt)
            except Exception as e:
                # Only ignore "already exists" errors
                if "already exists" in str(e).lower() or "1050" in str(e) or "1062" in str(e):
                    continue
                raise e

        # Manual migration for DOB column if Init DB didn't add it
        try:
            cursor.execute("ALTER TABLE aadhaar_submissions ADD COLUMN dob DATE AFTER father_aadhaar_encrypted")
            print("Added missing 'dob' column.")
        except Exception as e:
            if "Duplicate column name" not in str(e) and "1060" not in str(e):
                print(f"Notice: Could not add dob column: {e}")

        print("✔ MySQL Database initialized successfully!")
        
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == '__main__':
    init_mysql()
