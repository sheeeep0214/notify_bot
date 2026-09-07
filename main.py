import discord
from discord.ext import commands
from fastapi import FastAPI, Request, HTTPException
import uvicorn
import asyncio
import os
import xml.etree.ElementTree as ET
import httpx 
import traceback

# --- 環境變數設定 ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
PUBLIC_URL = os.environ.get("PUBLIC_URL") 
YOUTUBE_HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"

# 模擬資料庫：紀錄 "頻道ID" -> ["Discord_Channel_ID_1", "Discord_Channel_ID_2"]
subscriptions = {}

# --- 建立機器人與伺服器 ---
intents = discord.Intents.default()
intents.message_content = True 
# 將指令前綴設定為 $，避免與其他機器人衝突
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
    指令用法: $sub yt UC_x5XG1OV2P6uZZ5FSM9Ttw
    """
    if platform.lower() != "yt":
        await ctx.send("目前僅支援 yt (YouTube) 訂閱。")
        return

    if not PUBLIC_URL:
        await ctx.send("❌ 系統尚未設定 PUBLIC_URL，無法註冊 Webhook。")
        return

    channel_id = str(ctx.channel.id)
    
    # 1. 先檢查是否已經訂閱
    if target_id in subscriptions and channel_id in subscriptions[target_id]:
         await ctx.send("⚠️ 這個頻道已經在該文字頻道訂閱過了。")
         return
        
    # 2. 準備向 YouTube Hub 註冊
    topic_url = f"https://www.youtube.com/xml/schemas/2015/feeds/videos.xml?channel_id={target_id}"
    callback_url = f"{PUBLIC_URL}/yt-webhook"
    
    # 使用 AsyncClient 並設定 timeout=20.0 秒，避免發生 ReadTimeout 崩潰
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            # 3. 發送連線請求
            response = await client.post(YOUTUBE_HUB_URL, data={
                "hub.callback": callback_url,
                "hub.topic": topic_url,
                "hub.verify": "async", 
                "hub.mode": "subscribe",
                "hub.lease_seconds": 864000 
            })
            
            # 4. 判斷 YouTube 的回應
            if response.status_code in [202, 204]:
                # 只有在 YouTube 同意後，才寫入記憶體清單
                if target_id not in subscriptions:
                    subscriptions[target_id] = []
                subscriptions[target_id].append(channel_id)
                
                await ctx.send(f"✅ 成功向 YouTube 申請訂閱: {target_id}\n正在等待 YouTube 最終驗證...")
            else:
                await ctx.send(f"⚠️ 註冊請求已發送，但 YouTube 回應異常 (狀態碼: {response.status_code})")
                print(f"Hub response: {response.text}")
                
        except httpx.ReadTimeout:
            await ctx.send("❌ 連線至 YouTube Hub 逾時 (ReadTimeout)，可能是 YouTube 伺服器目前回應較慢，請稍後再試一次。")
        except Exception as e:
            # 加入詳細追蹤，方便我們在 Render 日誌中除錯
            print(f"詳細連線錯誤：\n{traceback.format_exc()}")
            await ctx.send(f"❌ 連線失敗！已將錯誤記錄在 Render 日誌中，請查看。")

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
        return int(challenge) 
    raise HTTPException(status_code=400, detail="Missing challenge")

@app.post("/yt-webhook")
async def receive_youtube(request: Request):
    """處理 YouTube 傳送過來的新影片通知 (POST)"""
    body = await request.body()
    try:
        root_xml = ET.fromstring(body)
        ns = {'atom': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
        
        entry = root_xml.find('atom:entry', ns)
        if entry is not None:
            title = entry.find('atom:title', ns).text
            link = entry.find('atom:link', ns).attrib['href']
            channel_name = entry.find('atom:author/atom:name', ns).text
            yt_channel_id = entry.find('yt:channelId', ns).text
            
            print(f"收到推播：{channel_name} - {title}")
            
            if yt_channel_id in subscriptions:
                for dc_channel_id in subscriptions[yt_channel_id]:
                    dc_channel = bot.get_channel(int(dc_channel_id))
                    if dc_channel:
                        asyncio.create_task(dc_channel.send(
                            f"🔔 **{channel_name}** 發布了新影片！\n**{title}**\n{link}"
                        ))
    except Exception as e:
        print(f"解析 XML 失敗: {e}")
        
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