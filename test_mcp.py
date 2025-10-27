import asyncio
from fastmcp import Client

async def main():
    async with Client("http://localhost:8000") as client:
        await client.call_tool(
            "configure_credentials",
            {"wiley_tdm_token": "a8f2a035-5f11-4c31-a80c-d9e904e35705", "crossref_mailto": "xiejinxiang@smail.nju.edu.cn"}
        )
        dry_run = await client.call_tool(
            "download_papers",
            {"dois": ["10.1002/anie.202410000"], "dry_run": True}
        )
        print(dry_run)
asyncio.run(main())
