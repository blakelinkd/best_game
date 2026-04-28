import sqlite3

db_path = r"C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

def print_table(name, limit=5):
    print(f"\n--- {name} ---")
    try:
        cursor.execute(f"SELECT * FROM {name} LIMIT {limit}")
        rows = cursor.fetchall()
        if rows:
            # get column names
            cursor.execute(f"PRAGMA table_info({name})")
            cols = cursor.fetchall()
            col_names = [c[1] for c in cols]
            print("Columns:", col_names)
            for row in rows:
                print(dict(zip(col_names, row)))
        else:
            print("No rows")
    except Exception as e:
        print(f"Error: {e}")

print_table("ProductsToReleaseKeys")
print_table("ReleaseProperties")
print_table("LimitedDetails")
print_table("LibraryReleases")
print_table("LicensedReleases")

conn.close()