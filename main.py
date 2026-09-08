import discord
from discord.ext import commands, tasks
import asyncio
import os
from aiohttp import web
import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient

# --- 環境變數 ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
MONGO_URI = os.environ.get("MONGO_URI")

# --- 初始化 Discord Bot ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="$", intents=intents)

# --- 資料庫變數 ---
db_client = None
db = None
subscriptions_col = None  # 儲存訂閱資料: {yt_id: "UC...", channels: {dc_id: "自訂訊息"}}
history_col = None       # 儲存已推播過的影片 ID: {video_id: "..."}

@bot.event
async def on_ready():
    global db_client, db, subscriptions_col, history_col
    print(f'Bot 已登入為：{bot.user}')
    
    # 初始化資料庫連線
    if MONGO_URI:
        try:
            db_client = AsyncIOMotorClient(MONGO_URI)
            db = db_client["youtube_notifier"]
            subscriptions_col = db["subscriptions"]
            history_col = db["history"]
            print("✅ 成功連接 MongoDB 資料庫！")
        except Exception as e:
            print(f"❌ MongoDB 連線失敗: {e}")
            return
            
    check_youtube_updates.start()

# ==========================================
# 1. 指令區：操作 MongoDB
# ==========================================
@bot.command(name="sub")
async def subscribe_channel(ctx, platform: str, target_id: str):
    if platform.lower() != "yt":
        await ctx.send("目前僅支援 yt (YouTube) 訂閱。")
        return

    dc_id = str(ctx.channel.id)
    doc = await subscriptions_col.find_one({"yt_id": target_id})
    
    if doc and dc_id in doc.get("channels", {}):
        await ctx.send("⚠️ 這個頻道已經在該文字頻道訂閱過了。")
        return

    # 使用 $set 來更新或新增頻道
    update_query = {"$set": {f"channels.{dc_id}": None}}
    await subscriptions_col.update_one({"yt_id": target_id}, update_query, upsert=True)
    
    await ctx.send(f"✅ 成功訂閱 YouTube 頻道 ID: `{target_id}`！\n(API 監控已啟動，約 2 分鐘內會進行首次同步)")

@bot.command(name="unsub")
async def unsubscribe_channel(ctx, platform: str, target_id: str):
    if platform.lower() != "yt":
        return
        
    dc_id = str(ctx.channel.id)
    doc = await subscriptions_col.find_one({"yt_id": target_id})
    
    if doc and dc_id in doc.get("channels", {}):
        # 從資料庫中移除該 Discord 頻道
        await subscriptions_col.update_one({"yt_id": target_id}, {"$unset": {f"channels.{dc_id}": ""}})
        
        # 檢查該 YouTube 頻道是否還有其他 Discord 頻道訂閱，若無則整筆刪除
        updated_doc = await subscriptions_col.find_one({"yt_id": target_id})
        if not updated_doc.get("channels"):
            await subscriptions_col.delete_one({"yt_id": target_id})
            
        await ctx.send(f"✅ 已成功取消訂閱頻道 ID: `{target_id}`")
    else:
        await ctx.send("⚠️ 這個頻道尚未在此文字頻道訂閱，無法取消。")

@bot.command(name="msg")
async def set_custom_message(ctx, platform: str, target_id: str, *, custom_message: str = None):
    if platform.lower() != "yt":
        return
        
    dc_id = str(ctx.channel.id)
    doc = await subscriptions_col.find_one({"yt_id": target_id})
    
    if not doc or dc_id not in doc.get("channels", {}):
        await ctx.send("⚠️ 請先使用 `$sub` 訂閱該頻道，才能設定專屬訊息。")
        return

    if custom_message:
        # 將使用者輸入的 \n 轉換為真實換行符號
        custom_message = custom_message.replace("\\n", "\n")

    await subscriptions_col.update_one({"yt_id": target_id}, {"$set": {f"channels.{dc_id}": custom_message}})
    
    if custom_message is None:
        await ctx.send(f"✅ 頻道 `{target_id}` 的推播已恢復為**預設訊息格式**。")
    else:
        await ctx.send(f"✅ 頻道 `{target_id}` 的專屬訊息設定成功！")

# ==========================================
# 2. 防漏抓 API 輪詢排程 (一次檢查前 5 部)
# ==========================================
@tasks.loop(minutes=2)
async def check_youtube_updates():
    if not YOUTUBE_API_KEY or subscriptions_col is None:
        return

    # 取得所有資料庫中的訂閱紀錄
    cursor = subscriptions_col.find({})
    all_subs = await cursor.to_list(length=None)
    
    if not all_subs:
        return

    print("正在透過 API 檢查 YouTube 頻道更新 (防漏抓模式)...")
    async with aiohttp.ClientSession() as session:
        for sub_doc in all_subs:
            yt_channel_id = sub_doc["yt_id"]
            dc_channels = sub_doc.get("channels", {})
            
            if not yt_channel_id.startswith("UC"):
                continue
                
            playlist_id = "UU" + yt_channel_id[2:]
            # maxResults=5：一次抓取最新 5 部影片，防止短時間連發導致漏抓
            api_url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&playlistId={playlist_id}&maxResults=5&key={YOUTUBE_API_KEY}"
            
            try:
                async with session.get(api_url) as response:
                    if response.status != 200:
                        continue
                        
                    data = await response.json()
                    if not data.get("items"):
                        continue
                    
                    # 將抓到的影片清單反轉 (讓最舊的先推播，最新的最後推播)
                    items = reversed(data["items"])
                    
                    for item in items:
                        snippet = item["snippet"]
                        video_id = snippet["resourceId"]["videoId"]
                        
                        # 去 MongoDB 檢查這部影片是否推播過
                        if await history_col.find_one({"video_id": video_id}):
                            continue
                            
                        video_title = snippet["title"]
                        author_name = snippet["channelTitle"]
                        video_link = f"https://www.youtube.com/watch?v={video_id}"
                        
                        # 紀錄到歷史資料庫
                        await history_col.insert_one({"video_id": video_id})
                        
                        # 推播給所有訂閱的頻道
                        for dc_id, custom_msg in dc_channels.items():
                            dc_channel = bot.get_channel(int(dc_id))
                            if dc_channel:
                                template = custom_msg if custom_msg else "🔔 **{author}** 發布了新影片！\n**{title}**\n{link}"
                                final_msg = template.replace("{author}", author_name)\
                                                    .replace("{title}", video_title)\
                                                    .replace("{link}", video_link)
                                await dc_channel.send(final_msg)
            except Exception as e:
                print(f"檢查頻道 {yt_channel_id} 失敗: {e}")

@check_youtube_updates.before_loop
async def before_check():
    await bot.wait_until_ready()

# ==========================================
# 3. 假 Web 伺服器 (保持 Render 存活)
# ==========================================
async def handle(request):
    return web.Response(text="Discord Bot is alive, using YT API & MongoDB!")

async def start_dummy_server():
    app = web.Application()
    app.add_routes([web.get('/', handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def main():
    await start_dummy_server()
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())