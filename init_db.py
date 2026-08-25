import os
import psycopg2
import sqlite3
from dotenv import load_dotenv

load_dotenv()

def init_db():
    database_url = os.getenv('DATABASE_URL')
    
    if database_url:
        # PostgreSQL logic
        print("Initializing PostgreSQL database...")
        try:
            conn = psycopg2.connect(database_url)
            cursor = conn.cursor()
            
            with open('schema.sql', 'r') as f:
                sql_script = f.read()
                
            # PostgreSQL can execute the whole script at once
            cursor.execute(sql_script)
            conn.commit()
            print("PostgreSQL database initialized successfully.")
        except Exception as err:
            print(f"Error: {err}")
        finally:
            if 'conn' in locals():
                conn.close()
    else:
        # SQLite logic fallback for local testing
        print("No DATABASE_URL found. Initializing local SQLite database...")
        db_path = os.path.join(os.path.dirname(__file__), 'database.db')
        if os.path.exists(db_path):
            os.remove(db_path)
            
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            with open('schema.sql', 'r') as f:
                sql_script = f.read()
            
            # Filter and convert for SQLite
            filtered_statements = []
            for statement in sql_script.split(';'):
                clean_stmt = statement.strip()
                if not clean_stmt: continue
                # Convert SERIAL to INTEGER PRIMARY KEY AUTOINCREMENT
                clean_stmt = clean_stmt.replace('SERIAL PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT')
                filtered_statements.append(clean_stmt)
            
            for statement in filtered_statements:
                cursor.execute(statement)
                
            conn.commit()
            print("SQLite database initialized successfully.")
        except Exception as err:
            print(f"Error: {err}")
        finally:
            if 'conn' in locals():
                conn.close()

if __name__ == '__main__':
    init_db()
