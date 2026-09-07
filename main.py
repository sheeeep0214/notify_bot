import discord
from discord.ext import commands, tasks
import asyncio
import os
import feedparser
import traceback

# --- 環境變數設定 ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

# 儲存使用者的訂閱清單: { "YouTube頻道ID": ["Discord頻道ID_1", "Discord頻道ID_2"] }
subscriptions = {}

# 記錄已經發送過的影片 ID，避免重複推播
seen_videos = set()

# --- 建立機器人 ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="$", intents=intents)

@bot.event
async def on_ready():
    print(f'Bot 已登入為：{bot.user}')
    # 啟動背景輪詢任務（每 3 分鐘檢查一次 YouTube）
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
    
    # 檢查是否重複訂閱
    if target_id in subscriptions and channel_id in subscriptions[target_id]:
         await ctx.send("⚠️ 這個頻道已經在該文字頻道訂閱過了。")
         return
        
    # 加入訂閱清單
    if target_id not in subscriptions:
        subscriptions[target_id] = []
    subscriptions[target_id].append(channel_id)
    
    await ctx.send(f"✅ 成功訂閱 YouTube 頻道 ID: `{target_id}`！未來有新影片將自動推播至本頻道。")

# --- 背景定時任務：每 3 分鐘檢查一次所有訂閱的 YouTube 頻道 ---
@tasks.loop(minutes=3)
async def check_youtube_updates():
    if not subscriptions:
        return
        
    print("正在檢查 YouTube 頻道更新...")
    # 取得所有被訂閱的頻道 ID
    all_channel_ids = list(subscriptions.keys())
    
    for channel_id in all_channel_ids:
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        
        try:
            # 使用 feedparser 解析 RSS
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                continue
                
            # 取得最新的一部影片
            latest_video = feed.entries[0]
            video_id = latest_video.id
            video_title = latest_video.title
            video_link = latest_video.link
            author_name = feed.feed.get.get('title', 'YouTube 頻道') if hasattr(feed, 'feed') else "YouTube 頻道"
            
            # 如果這部影片還沒發送過
            if video_id not in seen_videos:
                seen_videos.add(video_id)
                
                # 限制記憶體中的快取大小，避免過大
                if len(seen_videos) > 500:
                    seen_videos.pop()
                
                # 發送到所有訂閱了該頻道的 Discord 頻道
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

# --- 啟動機器人 ---
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ 錯誤：未設定 DISCORD_TOKEN 環境變數。")
    else:
        bot.run(DISCORD_TOKEN)