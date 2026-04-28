import sqlite3

db_path = r"C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT * FROM Products LIMIT 5")
cols = [desc[0] for desc in cursor.description]
print("Products columns:", cols)
for row in cursor.fetchall():
    print(dict(zip(cols, row)))

# Check if any have non-null name
cursor.execute("SELECT id, name FROM Products WHERE name IS NOT NULL LIMIT 10")
for row in cursor.fetchall():
    print(f"Product {row[0]}: {row[1]}")

conn.close()