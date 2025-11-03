#!/usr/bin/env python3
"""
Database migration script to add the 'type' column to the instruments table.
"""
import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

def migrate_database():
    # Connect to the database
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/zappppppix")
    
    # Parse the connection URL
    if DATABASE_URL.startswith("postgresql://"):
        # Format: postgresql://user:password@host:port/database
        url_parts = DATABASE_URL.replace("postgresql://", "").split("/")
        db_name = url_parts[1]
        user_pass_host = url_parts[0].split("@")
        user_pass = user_pass_host[0].split(":")
        host_port = user_pass_host[1].split(":")
        
        user = user_pass[0]
        password = user_pass[1]
        host = host_port[0]
        port = int(host_port[1])
    else:
        raise ValueError("Unsupported DATABASE_URL format")
    
    try:
        # Open DB connection
        conn = psycopg2.connect(
            host=host,
            port=port,
            database=db_name,
            user=user,
            password=password
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        
        cursor = conn.cursor()
        
        # Check whether the 'type' column exists
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='instruments' AND column_name='type';
        """)
        
        result = cursor.fetchone()
        
        if not result:
            print("Adding column 'type' to table 'instruments'...")
            cursor.execute("ALTER TABLE instruments ADD COLUMN type VARCHAR DEFAULT 'STOCK';")
            print("Column added successfully!")
        else:
            print("Column 'type' already exists.")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"Migration error: {e}")

if __name__ == "__main__":
    migrate_database()
