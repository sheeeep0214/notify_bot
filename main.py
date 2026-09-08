import discord
from discord.ext import commands, tasks
import asyncio
import os
import traceback
from aiohttp import web
import aiohttp

# --- 環境變數設定 ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

# { "YouTube頻道ID": { "Discord頻道ID": "自訂訊息字串 (若無則為 None)" } }
subscriptions = {}
# 改用 List 確保順序，並限制最多記錄 100 筆，避免記憶體溢出
seen_videos = []

# --- 建立機器人 ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="$", intents=intents)

@bot.event
async def on_ready():
    print(f'Bot 已登入為：{bot.user}')
    check_youtube_updates.start()

# ==========================================
# 1. 訂閱與取消功能
# ==========================================
@bot.command(name="sub")
async def subscribe_channel(ctx, platform: str, target_id: str):
    if platform.lower() != "yt":
        await ctx.send("目前僅支援 yt (YouTube) 訂閱。")
        return

    channel_id = str(ctx.channel.id)
    if target_id in subscriptions and channel_id in subscriptions[target_id]:
         await ctx.send("⚠️ 這個頻道已經在該文字頻道訂閱過了。")
         return
        
    if target_id not in subscriptions:
        subscriptions[target_id] = {}
        
    subscriptions[target_id][channel_id] = None
    await ctx.send(f"✅ 成功訂閱 YouTube 頻道 ID: `{target_id}`！\n(API 監控已啟動，約 2 分鐘內會進行首次同步)")

@bot.command(name="unsub")
async def unsubscribe_channel(ctx, platform: str, target_id: str):
    if platform.lower() != "yt":
        return
    channel_id = str(ctx.channel.id)
    if target_id in subscriptions and channel_id in subscriptions[target_id]:
        del subscriptions[target_id][channel_id]
        if not subscriptions[target_id]:
            del subscriptions[target_id]
        await ctx.send(f"✅ 已成功取消訂閱頻道 ID: `{target_id}`")
    else:
        await ctx.send("⚠️ 這個頻道尚未在此文字頻道訂閱，無法取消。")

@bot.command(name="msg")
async def set_custom_message(ctx, platform: str, target_id: str, *, custom_message: str = None):
    if platform.lower() != "yt":
        return
    channel_id = str(ctx.channel.id)
    if target_id not in subscriptions or channel_id not in subscriptions[target_id]:
        await ctx.send("⚠️ 請先使用 `$sub` 訂閱該頻道，才能設定專屬訊息。")
        return

    if custom_message is None:
        subscriptions[target_id][channel_id] = None
        await ctx.send(f"✅ 頻道 `{target_id}` 的推播已恢復為**預設訊息格式**。")
    else:
        subscriptions[target_id][channel_id] = custom_message
        await ctx.send(f"✅ 頻道 `{target_id}` 的專屬訊息設定成功！")

# ==========================================
# 2. 高速 API 輪詢排程 (每 2 分鐘檢查一次)
# ==========================================
@tasks.loop(minutes=2)
async def check_youtube_updates():
    if not subscriptions:
        return
        
    if not YOUTUBE_API_KEY:
        print("❌ 錯誤：尚未設定 YOUTUBE_API_KEY 環境變數。")
        return

    print("正在透過 API 檢查 YouTube 頻道更新...")
    async with aiohttp.ClientSession() as session:
        for yt_channel_id in list(subscriptions.keys()):
            # 轉換技巧：將 UC 開頭的頻道 ID 轉為 UU 開頭的播放清單 ID
            if yt_channel_id.startswith("UC"):
                playlist_id = "UU" + yt_channel_id[2:]
            else:
                continue

            api_url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&playlistId={playlist_id}&maxResults=1&key={YOUTUBE_API_KEY}"
            
            try:
                async with session.get(api_url) as response:
                    if response.status != 200:
                        print(f"API 請求失敗: {await response.text()}")
                        continue
                        
                    data = await response.json()
                    if not data.get("items"):
                        continue
                        
                    latest_item = data["items"][0]["snippet"]
                    video_id = latest_item["resourceId"]["videoId"]
                    video_title = latest_item["title"]
                    author_name = latest_item["channelTitle"]
                    video_link = f"https://www.youtube.com/watch?v={video_id}"
                    
                    if video_id not in seen_videos:
                        seen_videos.append(video_id)
                        if len(seen_videos) > 100:
                            seen_videos.pop(0) # 移除最舊的紀錄
                        
                        for dc_channel_id, custom_msg in subscriptions[yt_channel_id].items():
                            dc_channel = bot.get_channel(int(dc_channel_id))
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
# 3. 假 Web 伺服器 (繞過 Render 檢查)
# ==========================================
async def handle(request):
    return web.Response(text="Discord Bot is alive and using YouTube API!")

async def start_dummy_server():
    app = web.Application()
    app.add_routes([web.get('/', handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"假伺服器已啟動於 Port {port} 以滿足 Render 需求")

async def main():
    if not DISCORD_TOKEN:
        print("❌ 錯誤：未設定 DISCORD_TOKEN 環境變數。")
        return
    
    await start_dummy_server()
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())