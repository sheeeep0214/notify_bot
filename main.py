import discord
from discord.ext import commands, tasks
import asyncio
import os
import feedparser
import traceback
from aiohttp import web

# --- 環境變數設定 ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

# 新版資料結構：{ "YouTube頻道ID": { "Discord頻道ID": "自訂訊息字串 (若無則為 None)" } }
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

# ==========================================
# 1. 訂閱功能 ($sub)
# ==========================================
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
        subscriptions[target_id] = {}
        
    # 預設自訂訊息為 None (使用系統預設)
    subscriptions[target_id][channel_id] = None
    
    await ctx.send(f"✅ 成功訂閱 YouTube 頻道 ID: `{target_id}`！\n💡 提示：可使用 `$msg yt {target_id} 你的自訂訊息` 來更改推播格式。")

# ==========================================
# 2. 取消訂閱功能 ($unsub)
# ==========================================
@bot.command(name="unsub")
async def unsubscribe_channel(ctx, platform: str, target_id: str):
    """
    指令用法: $unsub yt UC_x5XG1OV2P6uZZ5FSM9Ttw
    """
    if platform.lower() != "yt":
        await ctx.send("目前僅支援 yt (YouTube)。")
        return

    channel_id = str(ctx.channel.id)
    
    if target_id in subscriptions and channel_id in subscriptions[target_id]:
        # 刪除該 Discord 頻道的訂閱紀錄
        del subscriptions[target_id][channel_id]
        
        # 如果該 YouTube 頻道沒有任何 Discord 頻道訂閱了，就清空它以節省資源
        if not subscriptions[target_id]:
            del subscriptions[target_id]
            
        await ctx.send(f"✅ 已成功取消訂閱頻道 ID: `{target_id}`")
    else:
        await ctx.send("⚠️ 這個頻道尚未在此文字頻道訂閱，無法取消。")

# ==========================================
# 3. 自訂訊息功能 ($msg)
# ==========================================
@bot.command(name="msg")
async def set_custom_message(ctx, platform: str, target_id: str, *, custom_message: str = None):
    """
    指令用法: $msg yt UC_x5XG1OV2P6uZZ5FSM9Ttw 🔔 快來看 {author} 的新影片：{title} \n {link}
    """
    if platform.lower() != "yt":
        await ctx.send("目前僅支援 yt (YouTube)。")
        return

    channel_id = str(ctx.channel.id)
    
    if target_id not in subscriptions or channel_id not in subscriptions[target_id]:
        await ctx.send("⚠️ 請先使用 `$sub` 訂閱該頻道，才能設定專屬訊息。")
        return

    if custom_message is None:
        # 若用戶未輸入訊息，則恢復預設值
        subscriptions[target_id][channel_id] = None
        await ctx.send(f"✅ 頻道 `{target_id}` 的推播已恢復為**預設訊息格式**。")
    else:
        # 寫入用戶的自訂訊息
        subscriptions[target_id][channel_id] = custom_message
        await ctx.send(f"✅ 頻道 `{target_id}` 的專屬訊息設定成功！\n未來的推播格式預覽：\n{custom_message}")

# ==========================================
# 4. 背景輪詢排程 (RSS 解析與推播)
# ==========================================
@tasks.loop(minutes=3)
async def check_youtube_updates():
    if not subscriptions:
        return
        
    print("正在檢查 YouTube 頻道更新...")
    for yt_channel_id in list(subscriptions.keys()):
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={yt_channel_id}"
        
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
                
                # 針對每一個訂閱該 YT 頻道的 Discord 頻道發送通知
                for dc_channel_id, custom_msg in subscriptions[yt_channel_id].items():
                    dc_channel = bot.get_channel(int(dc_channel_id))
                    if dc_channel:
                        # 決定要使用的模板
                        template = custom_msg if custom_msg else "🔔 **{author}** 發布了新影片！\n**{title}**\n{link}"
                        
                        # 替換字串變數
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
# 5. 假 Web 伺服器 (繞過 Render 檢查)
# ==========================================
async def handle(request):
    return web.Response(text="Discord Bot is alive and running!")

async def start_dummy_server():
    app = web.Application()
    app.add_routes([web.get('/', handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"假伺服器已啟動於 Port {port} 以滿足 Render 需求")

# ==========================================
# 6. 系統啟動管理
# ==========================================
async def main():
    if not DISCORD_TOKEN:
        print("❌ 錯誤：未設定 DISCORD_TOKEN 環境變數。")
        return
    
    await start_dummy_server()
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())