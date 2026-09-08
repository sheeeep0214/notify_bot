import discord
from discord.ext import commands, tasks
import asyncio
import os
import feedparser
import traceback
from aiohttp import web  # 用來建立假網頁伺服器

# --- 環境變數設定 ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

# 儲存使用者的訂閱清單: { "YouTube頻道ID": ["Discord頻道ID_1", "Discord頻道ID_2"] }
subscriptions = {}
seen_videos = set()

# --- 建立機器人 ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="$", intents=intents)

@bot.event
async def on_ready():
    print(f'Bot 已登入為：{bot.user}')
    check_youtube_updates.start()

@bot.command(name="sub")
async def subscribe_channel(ctx, platform: str, target_id: str):
    """
    指令用法: $sub yt UC_x5XG1OV2P6uZZ5FSM9Ttw
    """
    if platform.lower() != "yt":
        await ctx.send("目前僅支援 yt (YouTube) 訂閱。")
        return

    channel_id = str(ctx.channel.id)
    
    if target_id in subscriptions and channel_id in subscriptions[target_id]:
         await ctx.send("⚠️ 這個頻道已經在該文字頻道訂閱過了。")
         return
        
    if target_id not in subscriptions:
        subscriptions[target_id] = []
    subscriptions[target_id].append(channel_id)
    
    await ctx.send(f"✅ 成功訂閱 YouTube 頻道 ID: `{target_id}`！未來有新影片將自動推播至本頻道。")

@tasks.loop(minutes=3)
async def check_youtube_updates():
    if not subscriptions:
        return
        
    print("正在檢查 YouTube 頻道更新...")
    for channel_id in list(subscriptions.keys()):
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                continue
                
            latest_video = feed.entries[0]
            video_id = latest_video.id
            video_title = latest_video.title
            video_link = latest_video.link
            author_name = feed.feed.get('title', 'YouTube 頻道') if hasattr(feed, 'feed') else "YouTube 頻道"
            
            if video_id not in seen_videos:
                seen_videos.add(video_id)
                if len(seen_videos) > 500:
                    seen_videos.pop()
                
                for dc_channel_id in subscriptions[channel_id]:
                    dc_channel = bot.get_channel(int(dc_channel_id))
                    if dc_channel:
                        await dc_channel.send(
                            f"🔔 **{author_name}** 發布了新影片！\n**{video_title}**\n{video_link}"
                        )
        except Exception as e:
            print(f"檢查頻道 {channel_id} 失敗: {e}")

@check_youtube_updates.before_loop
async def before_check():
    await bot.wait_until_ready()

# ==========================================
# 建立一個假的 Web 伺服器來應付 Render 的 Port 檢查機制
# ==========================================
async def handle(request):
    return web.Response(text="Discord Bot is alive and running!")

async def start_dummy_server():
    app = web.Application()
    app.add_routes([web.get('/', handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render 會動態分配 PORT 環境變數
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"假伺服器已啟動於 Port {port} 以滿足 Render 需求")

# ==========================================
# 系統啟動管理
# ==========================================
async def main():
    if not DISCORD_TOKEN:
        print("❌ 錯誤：未設定 DISCORD_TOKEN 環境變數。")
        return
    
    # 同時啟動「假伺服器」與「Discord 機器人」
    await start_dummy_server()
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())