import sqlite3

db_path = r"C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=== ProductsToReleaseKeys ===")
cursor.execute("SELECT * FROM ProductsToReleaseKeys LIMIT 10")
for row in cursor.fetchall():
    print(row)

print("\n=== ReleaseProperties for a few releaseKeys ===")
cursor.execute("SELECT * FROM ReleaseProperties WHERE releaseKey LIKE 'gog_%' LIMIT 10")
for row in cursor.fetchall():
    print(row)

print("\n=== LimitedDetails for productIds from releaseKeys ===")
cursor.execute("""
    SELECT lr.releaseKey, rp.gameId, ld.productId, ld.title
    FROM LibraryReleases lr
    JOIN ReleaseProperties rp ON lr.releaseKey = rp.releaseKey
    LEFT JOIN LimitedDetails ld ON ld.productId = CAST(SUBSTR(lr.releaseKey, 5) AS INTEGER)
    WHERE lr.releaseKey LIKE 'gog_%'
    LIMIT 15
""")
for row in cursor.fetchall():
    print(row)

print("\n=== Checking if productId matches gameId ===")
cursor.execute("""
    SELECT lr.releaseKey, rp.gameId, ld.productId
    FROM LibraryReleases lr
    JOIN ReleaseProperties rp ON lr.releaseKey = rp.releaseKey
    LEFT JOIN LimitedDetails ld ON ld.productId = rp.gameId
    WHERE lr.releaseKey LIKE 'gog_%'
    LIMIT 10
""")
for row in cursor.fetchall():
    print(row)

conn.close()