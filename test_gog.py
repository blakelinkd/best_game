from gog_client import GogClient

client = GogClient()
print('Testing connection...')
print(client.test_connection())
print('Getting owned games...')
games = client.get_owned_games()
print(f'Found {len(games)} games')
for g in games[:5]:
    print(f'  {g.get("name")} ({g.get("appid")}) installed: {g.get("installed")}')
    print(f'    header image: {g.get("header_image")}')