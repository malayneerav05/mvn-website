import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()

def init_db():
    db_path = os.path.join(os.path.dirname(__file__), 'database.db')
    
    # Remove existing database to start fresh (equivalent to DROP DATABASE)
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"Removed existing database at {db_path}")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Read the schema file
        with open('schema.sql', 'r') as f:
            sql_script = f.read()
            
        # SQLite doesn't support CREATE DATABASE or USE
        # We need to filter those out or just rely on the connection creating the file
        filtered_statements = []
        for statement in sql_script.split(';'):
            clean_stmt = statement.strip()
            if not clean_stmt:
                continue
            if clean_stmt.upper().startswith('CREATE DATABASE') or clean_stmt.upper().startswith('USE '):
                continue
            # SQLite uses AUTOINCREMENT instead of AUTO_INCREMENT
            clean_stmt = clean_stmt.replace('AUTO_INCREMENT', 'AUTOINCREMENT')
            filtered_statements.append(clean_stmt)
            
        # Execute the filtered script
        for statement in filtered_statements:
            try:
                cursor.execute(statement)
            except sqlite3.Error as e:
                print(f"Error executing statement: {statement}")
                print(f"Error message: {e}")
                
        conn.commit()
        print("Database initialized successfully with SQLite.")
        
    except sqlite3.Error as err:
        print(f"Error: {err}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == '__main__':
    init_db()
