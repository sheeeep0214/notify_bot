import discord
from discord.ext import commands, tasks
import asyncio
import os
from aiohttp import web
import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient
import urllib.parse

# --- 環境變數 ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
MONGO_URI = os.environ.get("MONGO_URI")
IG_API_KEY = os.environ.get("IG_API_KEY")
X_API_KEY = os.environ.get("X_API_KEY")

# --- 初始化 Discord Bot ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="$", intents=intents, help_command=None)

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
    check_ig_updates.start()
    check_x_updates.start()

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    await ctx.send(f"❌ [系統報錯] 指令執行失敗！錯誤原因：\n`{error}`")
    print(f"Command Error: {error}")

# ==========================================
# 1. 統一指令區
# ==========================================
@bot.command(name="help")
async def show_help(ctx):
    embed = discord.Embed(
        title="社群推播機器人 指令手冊",
        description="指令前綴為 `$`, 支援 YouTube (`yt`), Instagram (`ig`), X (`x`)",
        color=0x2b2d31 
    )

    embed.add_field(
        name="1. 新增訂閱",
        value=(
            "**格式：** `$sub <平台> <帳號或ID> [接收類型]`\n\n"
            "**YouTube** (無接收類型)\n"
            "▶ `$sub yt UCIU8ha-NHmLjtUwU7dFiXUA`\n\n"
            "**Instagram** (可選: `all`, `photo`, `video`)\n"
            "▶ `$sub ig nmixx_official video`\n\n"
            "**X / Twitter** (可選: `all`, `post`, `video`)\n"
            "▶ `$sub x nmixx_official all`"
        ),
        inline=False
    )

    embed.add_field(
        name="2. 移除訂閱",
        value=(
            "**格式：** `$unsub <平台> <帳號或ID>`\n"
            "▶ `$unsub ig nmixx_official`"
        ),
        inline=False
    )

    embed.add_field(
        name="3. 自訂推播訊息",
        value=(
            "**格式：** `$msg <平台> <帳號或ID> <推播類型> [自訂文字]`\n\n"
            "**支援類型：**\n"
            "• yt: `video`, `live`\n"
            "• ig: `photo`, `video`\n"
            "• x: `post`, `video`\n\n"
            "**可用變數：** `{author}`, `{title}`(限YT), `{link}`\n"
            "▶ `$msg yt UCIU... video 🔔 {author} 發新片啦 {link}`\n"
            "*(不填後方文字即可恢復預設)*"
        ),
        inline=False
    )

    embed.add_field(
        name="4. 訂閱清單管理",
        value=(
            "• **查看清單：** `$list`\n"
            "• **清除全部：** `$clear`"
        ),
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command(name="sub")
async def subscribe_channel(ctx, platform: str, target_id: str, sub_type: str = "all"):
    platform = platform.lower()
    dc_id = str(ctx.channel.id)

    if platform == "yt":
        if not target_id.startswith("UC"):
            await ctx.send("⚠️ YouTube 頻道 ID 通常是 `UC` 開頭，請確認格式！")
            return

        channel_title = target_id
        if YOUTUBE_API_KEY:
            channel_api_url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet&id={target_id}&key={YOUTUBE_API_KEY}"
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(channel_api_url) as resp:
                        if resp.status == 200:
                            ch_data = await resp.json()
                            ch_items = ch_data.get("items", [])
                            if ch_items:
                                channel_title = ch_items[0]["snippet"]["title"]
                            else:
                                await ctx.send("❌ 找不到該 YouTube 頻道，請確認 ID 是否正確！")
                                return
                except Exception as e:
                    print(f"查詢 YT 頻道名稱失敗: {e}")

        try:
            doc = await asyncio.wait_for(subscriptions_col.find_one({"yt_id": target_id}), timeout=10.0)
        except Exception as e:
            await ctx.send(f"⚠️ 資料庫發生錯誤: {e}")
            return

        if doc and dc_id in doc.get("channels", {}):
            await ctx.send(f"⚠️ 頻道 **{channel_title}** 已經在該文字頻道訂閱過了。")
            return

        update_query = {
            "$set": {
                "yt_title": channel_title,
                f"channels.{dc_id}": {"video": None, "live": None}
            }
        }
        await subscriptions_col.update_one({"yt_id": target_id}, update_query, upsert=True)
        await ctx.send(f"✅ 成功訂閱 YouTube 頻道：**{channel_title}** (`{target_id}`)！")

    elif platform in ["ig", "x"]:
        sub_type = sub_type.lower()
        if platform == "ig" and sub_type not in ["all", "photo", "video"]:
            await ctx.send("⚠️ IG 訂閱類型錯誤！請輸入 `all`、`photo` 或 `video`。")
            return
        if platform == "x" and sub_type not in ["all", "post", "video"]:
            await ctx.send("⚠️ X 訂閱類型錯誤！請輸入 `all`、`post` 或 `video`。")
            return
            
        types_to_sub = ["photo", "video"] if (platform == "ig" and sub_type == "all") else \
                       ["post", "video"] if (platform == "x" and sub_type == "all") else [sub_type]
        
        query_key = f"{platform}_id"
        doc = await subscriptions_col.find_one({query_key: target_id})
        
        if doc and dc_id in doc.get("channels", {}):
            await subscriptions_col.update_one(
                {query_key: target_id}, 
                {"$set": {f"channels.{dc_id}.types": types_to_sub}}
            )
            await ctx.send(f"✅ 已更新 {platform.upper()} 帳號 `{target_id}` 的訂閱類型為：**{sub_type}**")
        else:
            if platform == "ig":
                if IG_API_KEY:
                    await ctx.send("🔍 正在驗證 IG 帳號是否存在，請稍候...")
                    api_url = "https://instagram-scraper-stable-api.p.rapidapi.com/get_ig_user_posts.php"
                    headers = {
                        "X-RapidAPI-Key": IG_API_KEY,
                        "X-RapidAPI-Host": "instagram-scraper-stable-api.p.rapidapi.com",
                        "Content-Type": "application/x-www-form-urlencoded"
                    }
                    ig_url = f"https://www.instagram.com/{target_id}/"
                    payload = urllib.parse.urlencode({'username_or_url': ig_url, 'amount': 1})
                    
                    async with aiohttp.ClientSession() as session:
                        try:
                            async with session.post(api_url, headers=headers, data=payload) as response:
                                if response.status != 200:
                                    await ctx.send(f"⚠️ API 暫時遇到速率限制或異常 (HTTP {response.status})，已為您跳過驗證直接訂閱！請確認帳號名稱無誤。")
                                else:
                                    result = await response.json()
                                    if "error" in result:
                                        await ctx.send(f"❌ 找不到該 IG 帳號或帳號無效：`{target_id}`\n({result['error']})")
                                        return
                        except Exception as e:
                            await ctx.send(f"⚠️ 驗證過程發生錯誤: {e}")
                            return
                else:
                    await ctx.send("⚠️ 尚未設定 IG_API_KEY，跳過事前驗證直接訂閱。")
                    
            elif platform == "x":
                if X_API_KEY:
                    await ctx.send("🔍 正在驗證 X (Twitter) 帳號是否存在，請稍候...")
                    api_url = f"https://twitter-api45.p.rapidapi.com/timeline.php?screenname={target_id}"
                    headers = {
                        "X-RapidAPI-Key": X_API_KEY,
                        "X-RapidAPI-Host": "twitter-api45.p.rapidapi.com",
                        "Content-Type": "application/json"
                    }
                    async with aiohttp.ClientSession() as session:
                        try:
                            async with session.get(api_url, headers=headers) as response:
                                if response.status != 200:
                                    await ctx.send(f"⚠️ API 暫時遇到速率限制或異常 (HTTP {response.status})，已為您跳過驗證直接訂閱！請確認帳號名稱無誤。")
                                else:
                                    result = await response.json()
                                    is_error = False
                                    error_msg = "查無此人或無法存取"
                                    
                                    if isinstance(result, dict):
                                        if "error" in result or "message" in result:
                                            is_error = True
                                            error_msg = result.get("error", result.get("message", "帳號不存在或遭到停權"))
                                    
                                    items = result.get("timeline", result) if isinstance(result, dict) else result
                                    if not isinstance(items, list) or len(items) == 0:
                                        is_error = True
                                        if not isinstance(result, dict) or ("error" not in result and "message" not in result):
                                            error_msg = "帳號不存在，或是該帳號從未發佈過任何推文，無法驗證。"
                                    
                                    if is_error:
                                        await ctx.send(f"❌ 找不到該 X (Twitter) 帳號或無效：`{target_id}`\n({error_msg})")
                                        return
                        except Exception as e:
                            await ctx.send(f"⚠️ 驗證過程發生錯誤: {e}")
                            return
                else:
                    await ctx.send("⚠️ 尚未設定 X_API_KEY，跳過事前驗證直接訂閱。")

            db_types_init = {"photo": None, "video": None} if platform == "ig" else {"post": None, "video": None}
            update_query = {
                "$set": {
                    f"channels.{dc_id}": {
                        "types": types_to_sub,
                        **db_types_init
                    }
                }
            }
            await subscriptions_col.update_one({query_key: target_id}, update_query, upsert=True)
            await ctx.send(f"✅ 成功訂閱 {platform.upper()} 帳號: `{target_id}`！\n(接收類型：{sub_type})")
    else:
        await ctx.send("⚠️ 目前僅支援 `yt` (YouTube)、`ig` (Instagram) 與 `x` (Twitter)。")

@bot.command(name="unsub")
async def unsubscribe_channel(ctx, platform: str, target_id: str):
    platform = platform.lower()
    if platform not in ["yt", "ig", "x"]: return
    
    dc_id = str(ctx.channel.id)
    query_key = f"{platform}_id"
    
    doc = await subscriptions_col.find_one({query_key: target_id})
    if doc and dc_id in doc.get("channels", {}):
        await subscriptions_col.update_one({query_key: target_id}, {"$unset": {f"channels.{dc_id}": ""}})
        updated_doc = await subscriptions_col.find_one({query_key: target_id})
        if not updated_doc.get("channels"):
            await subscriptions_col.delete_one({query_key: target_id})
        await ctx.send(f"✅ 已成功取消訂閱 {platform.upper()}: `{target_id}`")
    else:
        await ctx.send("⚠️ 尚未在此文字頻道訂閱該帳號，無法取消。")

@bot.command(name="list")
async def list_subscriptions(ctx):
    dc_id = str(ctx.channel.id)
    cursor = subscriptions_col.find({})
    all_docs = await cursor.to_list(length=None)
    
    yt_list, ig_list, x_list = [], [], []
    
    for doc in all_docs:
        channels = doc.get("channels", {})
        if dc_id in channels:
            if "yt_id" in doc:
                title = doc.get("yt_title", doc["yt_id"])
                yt_list.append(f"{title} (`{doc['yt_id']}`)")
            elif "ig_id" in doc:
                config = channels[dc_id]
                types = config.get("types", ["photo", "video"])
                type_str = "all" if len(types) == 2 else types[0]
                ig_list.append(f"{doc['ig_id']} (類型: {type_str})")
            elif "x_id" in doc:
                config = channels[dc_id]
                types = config.get("types", ["post", "video"])
                type_str = "all" if len(types) == 2 else types[0]
                x_list.append(f"{doc['x_id']} (類型: {type_str})")
                
    if not yt_list and not ig_list and not x_list:
        await ctx.send("📂 此文字頻道目前沒有訂閱任何帳號。")
        return
        
    msg = "📋 **此文字頻道目前的訂閱清單：**\n"
    if yt_list:
        msg += "\n**▶️ YouTube 頻道：**\n" + "\n".join([f"- {yt}" for yt in yt_list])
    if ig_list:
        msg += "\n\n**📸 Instagram 帳號：**\n" + "\n".join([f"- `{ig}`" for ig in ig_list])
    if x_list:
        msg += "\n\n**🐦 X (Twitter) 帳號：**\n" + "\n".join([f"- `{x}`" for x in x_list])
        
    await ctx.send(msg)

@bot.command(name="clear")
async def clear_all_subscriptions(ctx):
    dc_id = str(ctx.channel.id)
    cursor = subscriptions_col.find({})
    all_docs = await cursor.to_list(length=None)
    
    count = 0
    for doc in all_docs:
        channels = doc.get("channels", {})
        if dc_id in channels:
            query_key = "yt_id" if "yt_id" in doc else "ig_id" if "ig_id" in doc else "x_id"
            target_id = doc[query_key]
            
            await subscriptions_col.update_one({query_key: target_id}, {"$unset": {f"channels.{dc_id}": ""}})
            updated_doc = await subscriptions_col.find_one({query_key: target_id})
            if not updated_doc.get("channels"):
                await subscriptions_col.delete_one({query_key: target_id})
            count += 1
            
    if count == 0:
        await ctx.send("⚠️ 此文字頻道本來就沒有任何訂閱。")
    else:
        await ctx.send(f"🗑️ 已成功清除此文字頻道的所有訂閱（共解除 {count} 個項目）！")

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
    elif platform == "x":
        if msg_type not in ["post", "video"]:
            await ctx.send("⚠️ X 格式錯誤！請輸入 `post` 或 `video`")
            return
        query_key = "x_id"
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
                    if response.status != 200: 
                        await asyncio.sleep(2) 
                        continue
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
                
            await asyncio.sleep(2)

# ==========================================
# 3. Instagram 監控輪詢
# ==========================================
@tasks.loop(minutes=15)
async def check_ig_updates():
    if not IG_API_KEY or subscriptions_col is None: return
    try:
        cursor = subscriptions_col.find({"ig_id": {"$exists": True}})
        all_ig_subs = await cursor.to_list(length=None)
    except Exception: return 
    if not all_ig_subs: return

    async with aiohttp.ClientSession() as session:
        for sub_doc in all_ig_subs:
            ig_username = sub_doc["ig_id"]
            dc_channels = sub_doc.get("channels", {})
            
            api_url = "https://instagram-scraper-stable-api.p.rapidapi.com/get_ig_user_posts.php"
            headers = {
                "X-RapidAPI-Key": IG_API_KEY,
                "X-RapidAPI-Host": "instagram-scraper-stable-api.p.rapidapi.com",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            ig_url = f"https://www.instagram.com/{ig_username}/"
            payload = urllib.parse.urlencode({'username_or_url': ig_url, 'amount': 5})
            
            try:
                async with session.post(api_url, headers=headers, data=payload) as response:
                    if response.status != 200: 
                        await asyncio.sleep(3) 
                        continue
                    result = await response.json()
                    
                    items = result.get("data", result.get("items", result))
                    if not isinstance(items, list) or not items: continue
                    
                    for item in reversed(items[:5]): 
                        node = item.get("node", item) if isinstance(item, dict) else item
                        
                        post_id = node.get("id") or node.get("pk")
                        if not post_id: continue
                        
                        if await history_col.find_one({"ig_post_id": post_id}):
                            continue
                            
                        await history_col.insert_one({"ig_post_id": post_id})
                        
                        media_type = node.get("media_type")
                        is_video = node.get("is_video", False)
                        if isinstance(media_type, int):
                            current_type = "video" if media_type == 2 else "photo"
                        elif isinstance(media_type, str):
                            current_type = "video" if "video" in media_type.lower() else "photo"
                        else:
                            current_type = "video" if is_video else "photo"
                        
                        code = node.get("code") or node.get("shortcode")
                        post_url = f"https://www.instagram.com/p/{code}/" if code else f"https://www.instagram.com/{ig_username}/"
                        author_name = ig_username
                        
                        for dc_id, config in dc_channels.items():
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
                
            await asyncio.sleep(3)

# ==========================================
# 4. X (Twitter) 監控輪詢
# ==========================================
@tasks.loop(minutes=15)
async def check_x_updates():
    if not X_API_KEY or subscriptions_col is None: return
    try:
        cursor = subscriptions_col.find({"x_id": {"$exists": True}})
        all_x_subs = await cursor.to_list(length=None)
    except Exception: return 
    if not all_x_subs: return

    async with aiohttp.ClientSession() as session:
        for sub_doc in all_x_subs:
            x_username = sub_doc["x_id"]
            dc_channels = sub_doc.get("channels", {})
            
            api_url = f"https://twitter-api45.p.rapidapi.com/timeline.php?screenname={x_username}"
            headers = {
                "X-RapidAPI-Key": X_API_KEY,
                "X-RapidAPI-Host": "twitter-api45.p.rapidapi.com",
                "Content-Type": "application/json"
            }
            
            try:
                async with session.get(api_url, headers=headers) as response:
                    if response.status != 200: 
                        await asyncio.sleep(3) 
                        continue
                    result = await response.json()
                    
                    items = result.get("timeline", result) if isinstance(result, dict) else result
                    if not isinstance(items, list) or not items: continue
                    
                    for item in reversed(items[:5]): 
                        tweet_id = item.get("tweet_id")
                        if not tweet_id: continue
                        
                        if await history_col.find_one({"x_tweet_id": tweet_id}):
                            continue
                            
                        await history_col.insert_one({"x_tweet_id": tweet_id})
                        
                        media = item.get("media", {})
                        current_type = "video" if "video" in media else "post"
                        
                        post_url = f"https://x.com/{x_username}/status/{tweet_id}"
                        author_name = item.get("author", {}).get("name", x_username)
                        
                        for dc_id, config in dc_channels.items():
                            if current_type not in config.get("types", ["post", "video"]):
                                continue 
                                
                            dc_channel = bot.get_channel(int(dc_id))
                            if not dc_channel: continue
                            
                            custom_msg = config.get(current_type)
                            if not custom_msg:
                                if current_type == "video":
                                    template = "🎬 **{author}** 在 X 發布了新影片！\n{link}"
                                else:
                                    template = "🐦 **{author}** 發布了新推文！\n{link}"
                            else:
                                template = custom_msg
                                
                            final_msg = template.replace("{author}", author_name).replace("{link}", post_url)
                            await dc_channel.send(final_msg)
            except Exception as e:
                print(f"檢查 X 帳號 {x_username} 失敗: {e}")
                
            await asyncio.sleep(3)

@check_youtube_updates.before_loop
async def before_check():
    await bot.wait_until_ready()

@check_ig_updates.before_loop
async def before_ig_check():
    await bot.wait_until_ready()

@check_x_updates.before_loop
async def before_x_check():
    await bot.wait_until_ready()

# ==========================================
# 5. 假 Web 伺服器
# ==========================================
async def handle(request):
    return web.Response(text="Discord Bot is alive, using YT, IG & X API with MongoDB!")

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