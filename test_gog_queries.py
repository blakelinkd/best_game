import sqlite3
import json
import os

db_path = r"C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=== Query owned games (non-DLC) with titles ===")
cursor.execute("""
    SELECT 
        lr.releaseKey,
        rp.gameId,
        rp.isDlc,
        ld.title,
        ld.images,
        ld.links,
        ibp.installationPath,
        ibp.installationDate
    FROM LibraryReleases lr
    JOIN LicensedReleases lic ON lr.id = lic.libraryId
    JOIN ReleaseProperties rp ON lr.releaseKey = rp.releaseKey
    LEFT JOIN LimitedDetails ld ON ld.productId = CAST(SUBSTR(lr.releaseKey, 5) AS INTEGER)  -- 'gog_' prefix
    LEFT JOIN InstalledBaseProducts ibp ON ibp.productId = CAST(SUBSTR(lr.releaseKey, 5) AS INTEGER)
    WHERE lic.isOwned = 1
        AND rp.isDlc = 0
    ORDER BY ld.title
    LIMIT 20
""")
rows = cursor.fetchall()
print(f"Found {len(rows)} owned games")
for row in rows:
    release_key = row['releaseKey']
    game_id = row['gameId']
    title = row['title'] or 'Unknown'
    installed = row['installationPath'] is not None
    print(f"  - {title} (releaseKey: {release_key}, gameId: {game_id}, installed: {installed})")
    if row['images']:
        try:
            images = json.loads(row['images'])
            if isinstance(images, dict):
                # find header image
                pass
        except:
            pass

print("\n=== Query installed products with titles ===")
cursor.execute("""
    SELECT 
        ip.productId,
        ld.title,
        ibp.installationPath
    FROM InstalledProducts ip
    LEFT JOIN InstalledBaseProducts ibp ON ip.productId = ibp.productId
    LEFT JOIN LimitedDetails ld ON ip.productId = ld.productId
    WHERE ibp.installationPath IS NOT NULL
    LIMIT 10
""")
for row in cursor.fetchall():
    print(f"  - {row['productId']}: {row['title']} at {row['installationPath']}")

print("\n=== Check mapping of releaseKey to productId ===")
cursor.execute("SELECT releaseKey FROM LibraryReleases WHERE releaseKey LIKE 'gog_%' LIMIT 5")
for row in cursor.fetchall():
    print(f"  {row['releaseKey']} -> productId {row['releaseKey'][4:]}")

conn.close()