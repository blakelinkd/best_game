import sqlite3
import sys

db_path = r"C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=== Users ===")
cursor.execute("SELECT * FROM Users")
print(cursor.fetchall())

print("\n=== LibraryReleases ===")
cursor.execute("SELECT * FROM LibraryReleases LIMIT 10")
for row in cursor.fetchall():
    print(row)

print("\n=== LicensedReleases ===")
cursor.execute("SELECT * FROM LicensedReleases LIMIT 10")
for row in cursor.fetchall():
    print(row)

print("\n=== ReleaseProperties ===")
cursor.execute("SELECT * FROM ReleaseProperties LIMIT 10")
for row in cursor.fetchall():
    print(row)

print("\n=== LimitedDetails ===")
cursor.execute("SELECT id, productId, title FROM LimitedDetails LIMIT 10")
for row in cursor.fetchall():
    print(row)

print("\n=== Join to get owned games with titles ===")
cursor.execute("""
    SELECT lr.releaseKey, rp.gameId, ld.title
    FROM LibraryReleases lr
    JOIN LicensedReleases lic ON lr.id = lic.libraryId
    JOIN ReleaseProperties rp ON lr.releaseKey = rp.releaseKey
    LEFT JOIN LimitedDetails ld ON rp.gameId = ld.productId
    WHERE lic.isOwned = 1
    LIMIT 20
""")
for row in cursor.fetchall():
    print(row)

print("\n=== Installed products ===")
cursor.execute("""
    SELECT ip.productId, ibp.installationPath, ld.title
    FROM InstalledProducts ip
    LEFT JOIN InstalledBaseProducts ibp ON ip.productId = ibp.productId
    LEFT JOIN LimitedDetails ld ON ip.productId = ld.productId
    LIMIT 10
""")
for row in cursor.fetchall():
    print(row)

conn.close()