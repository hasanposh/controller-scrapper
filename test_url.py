import aiohttp, asyncio

async def test():
    url = 'https://media.sandhills.com/img.axd?id=11021098188&wid=4326165471&rwl=False&p=&ext=&w=0&h=0&t=&lp=&c=True&wt=False&sz=Max&rt=0&checksum=HFkGt84%2fTQWKr%2bhost2epnJjphD7orZBhZHJwedbr78%3d'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Referer': 'https://www.controller.com/',
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as resp:
            ct = resp.headers.get('Content-Type', 'unknown')
            print(f"Status: {resp.status}")
            print(f"Content-Type: {ct}")
            if resp.status == 200:
                data = await resp.read()
                print(f"Size: {len(data)} bytes")
            else:
                text = await resp.text()
                print(f"Body: {text[:300]}")

asyncio.run(test())
