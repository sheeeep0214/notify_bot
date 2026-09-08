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
IG_API_KEY = os.environ.get("IG_API_KEY") # 新增 IG 的 API Key

# --- 初始化 Discord Bot ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="$", intents=intents)

# --- 資料庫變數 ---
db_client = None
db = None
subscriptions_col = None  
history_col = None       

@bot.event
async def on_ready():
    global db_client, db, subscriptions_col, history_col
    print(f'Bot 已登入為：{bot.user}')
    
    if MONGO_URI:
        try:
            db_client = AsyncIOMotorClient(MONGO_URI)
            db = db_client["youtube_notifier"]
            subscriptions_col = db["subscriptions"]
            history_col = db["history"]
            print("✅ 成功讀取 MongoDB 連線字串！")
        except Exception as e:
            print(f"❌ MongoDB 字串解析失敗: {e}")
            return
            
    check_youtube_updates.start()
    check_ig_updates.start() # 啟動 IG 監控

@bot.event
async def on_command_error(ctx, error):
    await ctx.send(f"❌ [系統報錯] 指令執行失敗！錯誤原因：\n`{error}`")
    print(f"Command Error: {error}")

# ==========================================
# 1. 統一指令區 (支援 YT & IG)
# ==========================================
@bot.command(name="sub")
async def subscribe_channel(ctx, platform: str, target_id: str, ig_type: str = "all"):
    platform = platform.lower()
    dc_id = str(ctx.channel.id)

    if platform == "yt":
        try:
            doc = await asyncio.wait_for(subscriptions_col.find_one({"yt_id": target_id}), timeout=10.0)
        except Exception as e:
            await ctx.send(f"⚠️ 資料庫發生錯誤: {e}")
            return

        if doc and dc_id in doc.get("channels", {}):
            await ctx.send("⚠️ 這個 YouTube 頻道已經在該文字頻道訂閱過了。")
            return

        update_query = {"$set": {f"channels.{dc_id}": {"video": None, "live": None}}}
        await subscriptions_col.update_one({"yt_id": target_id}, update_query, upsert=True)
        await ctx.send(f"✅ 成功訂閱 YouTube 頻道 ID: `{target_id}`！")

    elif platform == "ig":
        ig_type = ig_type.lower()
        if ig_type not in ["all", "photo", "video"]:
            await ctx.send("⚠️ IG 訂閱類型錯誤！請輸入 `all`、`photo` 或 `video`。")
            return
            
        types_to_sub = ["photo", "video"] if ig_type == "all" else [ig_type]
        
        doc = await subscriptions_col.find_one({"ig_id": target_id})
        
        if doc and dc_id in doc.get("channels", {}):
            # 如果已經訂閱過，更新接收的類型
            await subscriptions_col.update_one(
                {"ig_id": target_id}, 
                {"$set": {f"channels.{dc_id}.types": types_to_sub}}
            )
            await ctx.send(f"✅ 已更新 IG 帳號 `{target_id}` 的訂閱類型為：**{ig_type}**")
        else:
            # 建立新的 IG 訂閱
            update_query = {
                "$set": {
                    f"channels.{dc_id}": {
                        "types": types_to_sub,
                        "photo": None,
                        "video": None
                    }
                }
            }
            await subscriptions_col.update_one({"ig_id": target_id}, update_query, upsert=True)
            await ctx.send(f"✅ 成功訂閱 IG 帳號: `{target_id}`！\n(接收類型：{ig_type})")
    else:
        await ctx.send("⚠️ 目前僅支援 `yt` (YouTube) 與 `ig` (Instagram)。")

@bot.command(name="unsub")
async def unsubscribe_channel(ctx, platform: str, target_id: str):
    platform = platform.lower()
    if platform not in ["yt", "ig"]: return
    
    dc_id = str(ctx.channel.id)
    query_key = "yt_id" if platform == "yt" else "ig_id"
    
    doc = await subscriptions_col.find_one({query_key: target_id})
    if doc and dc_id in doc.get("channels", {}):
        await subscriptions_col.update_one({query_key: target_id}, {"$unset": {f"channels.{dc_id}": ""}})
        updated_doc = await subscriptions_col.find_one({query_key: target_id})
        if not updated_doc.get("channels"):
            await subscriptions_col.delete_one({query_key: target_id})
        await ctx.send(f"✅ 已成功取消訂閱 {platform.upper()}: `{target_id}`")
    else:
        await ctx.send("⚠️ 尚未在此文字頻道訂閱該帳號，無法取消。")

@bot.command(name="msg")
async def set_custom_message(ctx, platform: str, target_id: str, msg_type: str, *, custom_message: str = None):
    platform = platform.lower()
    msg_type = msg_type.lower()
    dc_id = str(ctx.channel.id)

    if platform == "yt":
        if msg_type not in ["video", "live"]:
            await ctx.send("⚠️ YT 格式錯誤！請輸入 `video` 或 `live`")
            return
        query_key = "yt_id"
    elif platform == "ig":
        if msg_type not in ["photo", "video"]:
            await ctx.send("⚠️ IG 格式錯誤！請輸入 `photo` 或 `video`")
            return
        query_key = "ig_id"
    else:
        return
        
    doc = await subscriptions_col.find_one({query_key: target_id})
    if not doc or dc_id not in doc.get("channels", {}):
        await ctx.send(f"⚠️ 請先使用 `$sub` 訂閱該帳號，才能設定專屬訊息。")
        return

    if custom_message:
        custom_message = custom_message.replace("\\n", "\n")

    await subscriptions_col.update_one(
        {query_key: target_id}, 
        {"$set": {f"channels.{dc_id}.{msg_type}": custom_message}}
    )
    
    status = "預設訊息" if custom_message is None else "專屬訊息"
    await ctx.send(f"✅ 成功將 {platform.upper()} `{target_id}` 的 **{msg_type}** 設定為{status}！")

# ==========================================
# 2. YouTube 監控輪詢 
# ==========================================
@tasks.loop(minutes=2)
async def check_youtube_updates():
    if not YOUTUBE_API_KEY or subscriptions_col is None: return
    try:
        # 只撈取 YT 的訂閱
        cursor = subscriptions_col.find({"yt_id": {"$exists": True}})
        all_subs = await cursor.to_list(length=None)
    except Exception: return 
    if not all_subs: return

    async with aiohttp.ClientSession() as session:
        for sub_doc in all_subs:
            yt_channel_id = sub_doc["yt_id"]
            dc_channels = sub_doc.get("channels", {})
            if not yt_channel_id.startswith("UC"): continue
                
            playlist_id = "UU" + yt_channel_id[2:]
            api_url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&playlistId={playlist_id}&maxResults=5&key={YOUTUBE_API_KEY}"
            
            try:
                async with session.get(api_url) as response:
                    if response.status != 200: continue
                    data = await response.json()
                    items = data.get("items", [])
                    if not items: continue
                    
                    video_ids = [item["snippet"]["resourceId"]["videoId"] for item in items]
                    vids_str = ",".join(video_ids)
                    vid_api_url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,liveStreamingDetails&id={vids_str}&key={YOUTUBE_API_KEY}"
                    
                    async with session.get(vid_api_url) as v_response:
                        if v_response.status != 200: continue
                        v_data = await v_response.json()
                        
                        for v_item in reversed(v_data.get("items", [])):
                            video_id = v_item["id"]
                            snippet = v_item["snippet"]
                            
                            broadcast_status = snippet.get("liveBroadcastContent", "none")
                            if broadcast_status == "upcoming": continue
                                
                            if await history_col.find_one({"video_id": video_id}): continue
                            await history_col.insert_one({"video_id": video_id})
                            
                            video_title = snippet["title"]
                            author_name = snippet["channelTitle"]
                            video_link = f"https://www.youtube.com/watch?v={video_id}"
                            current_type = "live" if broadcast_status == "live" else "video"
                            
                            for dc_id, custom_msgs in dc_channels.items():
                                dc_channel = bot.get_channel(int(dc_id))
                                if not dc_channel: continue
                                
                                custom_msg = custom_msgs.get(current_type) if isinstance(custom_msgs, dict) else custom_msgs
                                
                                if not custom_msg:
                                    if current_type == "live":
                                        template = "🔴 **{author}** 正在直播或首播！\n**{title}**\n{link}"
                                    else:
                                        template = "🔔 **{author}** 發布了新影片！\n**{title}**\n{link}"
                                else:
                                    template = custom_msg
                                    
                                final_msg = template.replace("{author}", author_name).replace("{title}", video_title).replace("{link}", video_link)
                                await dc_channel.send(final_msg)
            except Exception as e:
                print(f"檢查 YT 失敗: {e}")

# ==========================================
# 3. Instagram 監控輪詢 (使用 instagram-scraper-stable-api)
# ==========================================
@tasks.loop(minutes=15) # IG 抓取頻率建議調低，避免 API 額度耗盡
async def check_ig_updates():
    if not IG_API_KEY or subscriptions_col is None: return
    try:
        # 只撈取 IG 的訂閱
        cursor = subscriptions_col.find({"ig_id": {"$exists": True}})
        all_ig_subs = await cursor.to_list(length=None)
    except Exception: return 
    if not all_ig_subs: return

    async with aiohttp.ClientSession() as session:
        for sub_doc in all_ig_subs:
            ig_username = sub_doc["ig_id"]
            dc_channels = sub_doc.get("channels", {})
            
            # 使用 instagram-scraper-stable-api 的設定
            api_url = "https://instagram-scraper-stable-api.p.rapidapi.com/get_ig_user_posts.php"
            headers = {
                "X-RapidAPI-Key": IG_API_KEY,
                "X-RapidAPI-Host": "instagram-scraper-stable-api.p.rapidapi.com",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            # 將帳號轉換成完整網址格式，符合此 API 的要求
            import urllib.parse
            ig_url = f"https://www.instagram.com/{ig_username}/"
            payload = urllib.parse.urlencode({'username_or_url': ig_url, 'amount': 3})
            
            try:
                # 注意：這支 API 需要使用 POST 請求
                async with session.post(api_url, headers=headers, data=payload) as response:
                    if response.status != 200: continue
                    result = await response.json()
                    
                    # 擷取最新貼文陣列 (來自回傳 JSON 的 data 欄位)
                    items = result.get("data", [])
                    if not items: continue
                    
                    # 反轉陣列，讓舊的貼文先發送
                    for item in reversed(items): 
                        node = item.get("node", {})
                        post_id = node.get("id")
                        
                        if not post_id or await history_col.find_one({"ig_post_id": post_id}):
                            continue
                            
                        await history_col.insert_one({"ig_post_id": post_id})
                        
                        # 判斷是影片還是照片 (IG API 的 media_type: 1=相片, 2=影片/Reels, 8=多圖相簿)
                        media_type = node.get("media_type")
                        
                        # 我們把 8 (多圖相簿) 也歸類為 photo，2 歸類為 video
                        current_type = "video" if media_type == 2 else "photo"
                        
                        # 組裝貼文網址
                        post_url = f"https://www.instagram.com/p/{node.get('code')}/"
                        author_name = ig_username
                        
                        for dc_id, config in dc_channels.items():
                            # 核心過濾：檢查這個頻道有沒有訂閱這個類型的貼文
                            if current_type not in config.get("types", ["photo", "video"]):
                                continue 
                                
                            dc_channel = bot.get_channel(int(dc_id))
                            if not dc_channel: continue
                            
                            custom_msg = config.get(current_type)
                            if not custom_msg:
                                if current_type == "video":
                                    template = "🎬 **{author}** 發布了新影片/Reels！\n{link}"
                                else:
                                    template = "📷 **{author}** 發布了新照片貼文！\n{link}"
                            else:
                                template = custom_msg
                                
                            final_msg = template.replace("{author}", author_name).replace("{link}", post_url)
                            await dc_channel.send(final_msg)
            except Exception as e:
                print(f"檢查 IG 帳號 {ig_username} 失敗: {e}")

# ==========================================
# 4. 假 Web 伺服器
# ==========================================
async def handle(request):
    return web.Response(text="Discord Bot is alive, using YT & IG API with MongoDB!")

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