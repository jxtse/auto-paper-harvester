import asyncio
from fastmcp import Client

async def main():
    # 使用 stdio transport 连接到 MCP 服务器
    transport_config = {
        "mcpServers": {
            "auto_paper_download": {
                "command": "python",
                "args": ["auto_paper_download/mcp_server.py"]
            }
        }
    }
    async with Client(transport_config, name="auto_paper_download") as client:
        # 配置凭证
        config_result = await client.call_tool(
            "configure_credentials",
            {"wiley_tdm_token": "a8f2a035-5f11-4c31-a80c-d9e904e35705", 
             "crossref_mailto": "xiejinxiang@smail.nju.edu.cn"}
        )
        print("配置凭证结果:")
        print(config_result)
        print()
        
        # 执行 dry run 测试
        dry_run = await client.call_tool(
            "download_papers",
            {"dois": ["10.1002/anie.202410000"], "dry_run": True}
        )
        print("Dry run 结果:")
        print(dry_run)

if __name__ == "__main__":
    asyncio.run(main())

