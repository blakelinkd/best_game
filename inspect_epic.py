import sqlite3
import sys

db_path = r"C:\Users\blake\AppData\Local\EpicGamesLauncher\Saved\webcache_4430\databases\Databases.db"
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

print("\n--- Checking ItemCache table ---")
cursor.execute("SELECT * FROM ItemCache LIMIT 5")
rows = cursor.fetchall()
for row in rows:
    print(row)

conn.close()