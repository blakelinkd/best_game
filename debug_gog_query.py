import sqlite3
import json

db_path = r"C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Test query for a few releaseKeys
cursor.execute("""
    SELECT lr.releaseKey, rp.gameId, ptr.gogId, ld.title, ld.images
    FROM LibraryReleases lr
    JOIN LicensedReleases lic ON lr.id = lic.libraryId
    JOIN ReleaseProperties rp ON lr.releaseKey = rp.releaseKey
    LEFT JOIN ProductsToReleaseKeys ptr ON lr.releaseKey = ptr.releaseKey
    LEFT JOIN LimitedDetails ld ON ptr.gogId = ld.productId
    WHERE lic.isOwned = 1
        AND rp.isDlc = 0
        AND rp.isVisibleInLibrary = 1
        AND lr.releaseKey LIKE 'gog_%'
    LIMIT 10
""")
rows = cursor.fetchall()
print(f"Found {len(rows)} rows")
for row in rows:
    print(f"releaseKey: {row['releaseKey']}, gameId: {row['gameId']}, gogId: {row['gogId']}, title: {row['title']}")
    if row['images']:
        try:
            images = json.loads(row['images'])
            bg = images.get('background')
            print(f"  background: {bg}")
        except:
            pass

# Check if there are any titles at all
cursor.execute("SELECT COUNT(*) as cnt FROM LimitedDetails")
print(f"\nTotal LimitedDetails rows: {cursor.fetchone()['cnt']}")

cursor.execute("SELECT productId, title FROM LimitedDetails WHERE title IS NOT NULL LIMIT 5")
for row in cursor.fetchall():
    print(f"  {row['productId']}: {row['title']}")

conn.close()