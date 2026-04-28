import sqlite3
import sys

db_path = r"C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db"
print(f"Opening {db_path}")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()
print("Tables:")
for (t,) in tables:
    print(f"  {t}")
    cursor.execute(f"PRAGMA table_info({t});")
    cols = cursor.fetchall()
    print(f"    Columns: {[c[1] for c in cols]}")

print("\n--- Products table sample ---")
cursor.execute("SELECT * FROM Products LIMIT 5")
rows = cursor.fetchall()
for row in rows:
    print(row)

print("\n--- Checking for installed games ---")
cursor.execute("SELECT productId, title, installationPath FROM InstalledProducts LIMIT 10")
installed = cursor.fetchall()
for row in installed:
    print(row)

conn.close()