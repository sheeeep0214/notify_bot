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

# ==========================================
# 1. 指令區
# ==========================================
@bot.command(name="sub")
async def subscribe_channel(ctx, platform: str, target_id: str):
    print(f"📍 [Debug] 收到 $sub 指令！準備進入資料庫查詢...")
    if platform.lower() != "yt":
        await ctx.send("目前僅支援 yt (YouTube) 訂閱。")
        return

    dc_id = str(ctx.channel.id)
    
    try:
        doc = await asyncio.wait_for(subscriptions_col.find_one({"yt_id": target_id}), timeout=10.0)
    except asyncio.TimeoutError:
        await ctx.send("⚠️ 機器人無法連線至資料庫！請檢查 MongoDB 白名單與網址。")
        return
    except Exception as e:
        await ctx.send(f"⚠️ 資料庫發生錯誤: {e}")
        return

    if doc and dc_id in doc.get("channels", {}):
        await ctx.send("⚠️ 這個頻道已經在該文字頻道訂閱過了。")
        return

    # 升級資料庫結構，預留 video 與 live 兩種訊息欄位
    update_query = {"$set": {f"channels.{dc_id}": {"video": None, "live": None}}}
    await subscriptions_col.update_one({"yt_id": target_id}, update_query, upsert=True)
    
    await ctx.send(f"✅ 成功訂閱 YouTube 頻道 ID: `{target_id}`！\n(已支援過濾預告片，約 2 分鐘內進行首次同步)")

@bot.command(name="unsub")
async def unsubscribe_channel(ctx, platform: str, target_id: str):
    if platform.lower() != "yt": return
    dc_id = str(ctx.channel.id)
    doc = await subscriptions_col.find_one({"yt_id": target_id})
    if doc and dc_id in doc.get("channels", {}):
        await subscriptions_col.update_one({"yt_id": target_id}, {"$unset": {f"channels.{dc_id}": ""}})
        updated_doc = await subscriptions_col.find_one({"yt_id": target_id})
        if not updated_doc.get("channels"):
            await subscriptions_col.delete_one({"yt_id": target_id})
        await ctx.send(f"✅ 已成功取消訂閱頻道 ID: `{target_id}`")
    else:
        await ctx.send("⚠️ 這個頻道尚未在此文字頻道訂閱，無法取消。")

@bot.command(name="msg")
async def set_custom_message(ctx, platform: str, target_id: str, msg_type: str, *, custom_message: str = None):
    if platform.lower() != "yt": return
    
    # 強制要求使用者區分是設定 video 還是 live
    msg_type = msg_type.lower()
    if msg_type not in ["video", "live"]:
        await ctx.send("⚠️ 格式錯誤！請輸入 `$msg yt <頻道ID> video <訊息>` 或 `$msg yt <頻道ID> live <訊息>`")
        return
        
    dc_id = str(ctx.channel.id)
    doc = await subscriptions_col.find_one({"yt_id": target_id})
    
    if not doc or dc_id not in doc.get("channels", {}):
        await ctx.send("⚠️ 請先使用 `$sub` 訂閱該頻道，才能設定專屬訊息。")
        return

    if custom_message:
        custom_message = custom_message.replace("\\n", "\n")

    # 將訊息寫入對應的分類欄位
    await subscriptions_col.update_one({"yt_id": target_id}, {"$set": {f"channels.{dc_id}.{msg_type}": custom_message}})
    
    if custom_message is None:
        await ctx.send(f"✅ 頻道 `{target_id}` 的 **{msg_type} (發片/直播)** 推播已恢復為預設訊息。")
    else:
        await ctx.send(f"✅ 頻道 `{target_id}` 的 **{msg_type} (發片/直播)** 專屬訊息設定成功！")


# ==========================================
# 2. 防漏抓 API 輪詢排程 (進階狀態過濾版)
# ==========================================
@tasks.loop(minutes=2)
async def check_youtube_updates():
    if not YOUTUBE_API_KEY or subscriptions_col is None: return

    try:
        cursor = subscriptions_col.find({})
        all_subs = await cursor.to_list(length=None)
    except Exception: return 
    
    if not all_subs: return

    async with aiohttp.ClientSession() as session:
        for sub_doc in all_subs:
            yt_channel_id = sub_doc["yt_id"]
            dc_channels = sub_doc.get("channels", {})
            if not yt_channel_id.startswith("UC"): continue
                
            playlist_id = "UU" + yt_channel_id[2:]
            # 步驟一：先抓取最新 5 部影片的 ID
            api_url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&playlistId={playlist_id}&maxResults=5&key={YOUTUBE_API_KEY}"
            
            try:
                async with session.get(api_url) as response:
                    if response.status != 200: continue
                    data = await response.json()
                    items = data.get("items", [])
                    if not items: continue
                    
                    video_ids = [item["snippet"]["resourceId"]["videoId"] for item in items]
                    
                    # 步驟二：拿著這些 ID 去 Videos API 查詢「即時直播狀態」
                    vids_str = ",".join(video_ids)
                    vid_api_url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,liveStreamingDetails&id={vids_str}&key={YOUTUBE_API_KEY}"
                    
                    async with session.get(vid_api_url) as v_response:
                        if v_response.status != 200: continue
                        v_data = await v_response.json()
                        
                        # 翻轉順序，讓舊的先發
                        for v_item in reversed(v_data.get("items", [])):
                            video_id = v_item["id"]
                            snippet = v_item["snippet"]
                            
                            # 取得狀態：none (一般), live (直播中), upcoming (預告)
                            broadcast_status = snippet.get("liveBroadcastContent", "none")
                            
                            # 🌟 核心過濾機制：如果是「預告中」，直接無視跳過！
                            if broadcast_status == "upcoming":
                                continue
                                
                            if await history_col.find_one({"video_id": video_id}):
                                continue
                                
                            # 記錄到歷史，避免重複發送
                            await history_col.insert_one({"video_id": video_id})
                            
                            video_title = snippet["title"]
                            author_name = snippet["channelTitle"]
                            video_link = f"https://www.youtube.com/watch?v={video_id}"
                            
                            # 判斷當下是直播還是發片
                            current_type = "live" if broadcast_status == "live" else "video"
                            
                            for dc_id, custom_msgs in dc_channels.items():
                                dc_channel = bot.get_channel(int(dc_id))
                                if not dc_channel: continue
                                
                                # 相容舊版資料與讀取新版訊息設定
                                custom_msg = None
                                if isinstance(custom_msgs, dict):
                                    custom_msg = custom_msgs.get(current_type)
                                elif isinstance(custom_msgs, str):
                                    custom_msg = custom_msgs # 舊版 fallback
                                
                                if not custom_msg:
                                    if current_type == "live":
                                        template = "🔴 **{author}** 正在直播或首播！\n**{title}**\n{link}"
                                    else:
                                        template = "🔔 **{author}** 發布了新影片！\n**{title}**\n{link}"
                                else:
                                    template = custom_msg
                                    
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
# 3. 假 Web 伺服器
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