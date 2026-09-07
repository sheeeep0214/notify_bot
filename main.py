import discord
from discord.ext import commands
from fastapi import FastAPI, Request, HTTPException
import uvicorn
import asyncio
import os
import xml.etree.ElementTree as ET
import httpx # 需安裝 httpx: pip install httpx

# --- 環境變數設定 ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
# 你的雲端伺服器公開網址，例如 https://my-bot.onrender.com (請勿加上結尾斜線)
PUBLIC_URL = os.environ.get("PUBLIC_URL") 
YOUTUBE_HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"

# 模擬資料庫：紀錄 "頻道ID" -> ["Discord_Channel_ID_1", "Discord_Channel_ID_2"]
# 實務上請改用 SQLite 儲存，否則伺服器重啟資料會消失
subscriptions = {}

# --- 建立機器人與伺服器 ---
intents = discord.Intents.default()
intents.message_content = True # 允許讀取訊息內容以接收指令
bot = commands.Bot(command_prefix="$", intents=intents)
app = FastAPI()

# ==========================================
# 1. Discord 機器人指令區
# ==========================================
@bot.event
async def on_ready():
    print(f'Bot 已登入為：{bot.user}')

@bot.command(name="sub")
async def subscribe_channel(ctx, platform: str, target_id: str):
    """
    指令用法: !sub yt UC_x5XG1OV2P6uZZ5FSM9Ttw
    """
    if platform.lower() != "yt":
        await ctx.send("目前僅支援 yt (YouTube) 訂閱。\nIG 與 X 建議使用頻道 Webhook 搭配 Make.com。")
        return

    if not PUBLIC_URL:
        await ctx.send("❌ 系統尚未設定 PUBLIC_URL，無法註冊 Webhook。")
        return

    channel_id = str(ctx.channel.id)
    
    # 紀錄訂閱關係
    if target_id not in subscriptions:
        subscriptions[target_id] = []
    
    if channel_id not in subscriptions[target_id]:
        subscriptions[target_id].append(channel_id)
        
        # 核心：向 YouTube Hub 註冊 Webhook
        topic_url = f"https://www.youtube.com/xml/schemas/2015/feeds/videos.xml?channel_id={target_id}"
        callback_url = f"{PUBLIC_URL}/yt-webhook"
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(YOUTUBE_HUB_URL, data={
                    "hub.callback": callback_url,
                    "hub.topic": topic_url,
                    "hub.verify": "async", # 非同步驗證
                    "hub.mode": "subscribe",
                    "hub.lease_seconds": 864000 # 訂閱期限 (10天)，實務上需排程重新訂閱
                })
                
                if response.status_code in [202, 204]:
                    await ctx.send(f"✅ 成功訂閱 YouTube 頻道 ID: {target_id}\n正在向 YouTube 驗證中...")
                else:
                    await ctx.send(f"⚠️ 註冊請求已發送，但 Hub 回應異常 (狀態碼: {response.status_code})")
                    print(f"Hub response: {response.text}")
                    
            except Exception as e:
                await ctx.send(f"❌ 無法連線至 YouTube Hub: {e}")
    else:
        await ctx.send("⚠️ 這個頻道已經在訂閱清單中了。")

# ==========================================
# 2. FastAPI 伺服器路由 (Webhook 接收站)
# ==========================================
@app.get("/")
async def root():
    return {"message": "Discord Bot Webhook Server is running."}

@app.get("/yt-webhook")
async def verify_youtube(request: Request):
    """處理 YouTube Hub 的訂閱驗證要求 (GET)"""
    challenge = request.query_params.get("hub.challenge")
    topic = request.query_params.get("hub.topic")
    mode = request.query_params.get("hub.mode")
    
    if challenge and mode == "subscribe":
        print(f"✅ 成功通過 YouTube 驗證！Topic: {topic}")
        # 必須直接回傳 challenge 的數值
        return int(challenge) 
    raise HTTPException(status_code=400, detail="Missing challenge")

@app.post("/yt-webhook")
async def receive_youtube(request: Request):
    """處理 YouTube 傳送過來的新影片通知 (POST)"""
    body = await request.body()
    try:
        root_xml = ET.fromstring(body)
        ns = {'atom': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
        
        # 檢查是否為新影片 (YouTube 有時會發送已更新或刪除的通知)
        entry = root_xml.find('atom:entry', ns)
        if entry is not None:
            # 提取影片資訊
            title = entry.find('atom:title', ns).text
            link = entry.find('atom:link', ns).attrib['href']
            channel_name = entry.find('atom:author/atom:name', ns).text
            yt_channel_id = entry.find('yt:channelId', ns).text
            
            print(f"收到推播：{channel_name} - {title}")
            
            # 找到有哪些 Discord 頻道訂閱了這個 YouTube 頻道
            if yt_channel_id in subscriptions:
                for dc_channel_id in subscriptions[yt_channel_id]:
                    dc_channel = bot.get_channel(int(dc_channel_id))
                    if dc_channel:
                        # 呼叫 Discord 機器人發訊息
                        asyncio.create_task(dc_channel.send(
                            f"🔔 **{channel_name}** 發布了新影片！\n**{title}**\n{link}"
                        ))
    except Exception as e:
        print(f"解析 XML 失敗: {e}")
        
    # 無論如何都要回傳 200，否則 YouTube 會認為你沒收到而瘋狂重試
    return {"status": "success"}

# ==========================================
# 3. 系統啟動管理
# ==========================================
async def main():
    if not DISCORD_TOKEN:
        print("❌ 錯誤：未設定 DISCORD_TOKEN 環境變數。")
        return
        
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port)
    server = uvicorn.Server(config)
    
    print("啟動伺服器與機器人...")
    await asyncio.gather(
        server.serve(),
        bot.start(DISCORD_TOKEN)
    )

if __name__ == "__main__":
    asyncio.run(main())