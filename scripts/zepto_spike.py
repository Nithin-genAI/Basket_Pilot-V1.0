import asyncio, json, pathlib
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

URL = "https://mcp.zepto.co.in/mcp"

async def main():
    params = StdioServerParameters(command="npx", args=["-y", "mcp-remote", URL])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.list_tools()
            tools = [t.model_dump(mode="json", by_alias=True) for t in res.tools]
    out = pathlib.Path("docs"); out.mkdir(exist_ok=True)
    (out / "zepto_tools.json").write_text(json.dumps(tools, indent=2))
    for t in tools:
        print("-", t["name"])

asyncio.run(main())