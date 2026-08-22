import os
import asyncio
import json
import re
import sqlite3
import requests
import discord
import uvicorn
from discord.ext import commands
from discord import app_commands
from fastapi import FastAPI, Request
from contextlib import asynccontextmanager

# =========================
# CONFIGURATION
# =========================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
PORT = int(os.getenv("PORT", 8888))
DB_PATH = os.getenv("DB_PATH", "database.db")

GIF_URL = "https://cdn.discordapp.com/attachments/1420812683124670596/1540669145161662584/original_ddaceecdd62614ddf9a488b75ef88075.gif?ex=6a8acb74&is=6a8979f4&hm=7dfcdf79662c2c31c862537e84fa6d7c0768406c383c75ab75d3cb7389be5025&"

COLOR_PRIMARY = 0x4dff00      # Dark Theme Elegance
COLOR_SUCCESS = 0x2ECC71      # Vivid Emerald
COLOR_ERROR = 0xE74C3C        # Crimson Red
COLOR_INFO = 0x3498DB         # Deep Cyber Blue
COLOR_ACCENT = 0xF1C40F       # Gold Accent

DEFAULT_SETTINGS = {
    "roblox_group_id": 33852603,
    "roblox_group_url": "https://www.roblox.com/groups/33852603",
    "roblox_map_url": "https://www.roblox.com/th/games/109069394163925/ver",
    "verified_role_id": 1537125389930074152,
    "developer_role_id": 1537109884263211018,
    "verified_emoji": "✅",
    "role_ids": {
        "or": 1537098607319195698,
        "cd": 1420812474227101707,
        "of_low": 1420812472616751278,
        "of_high": 1420812470905213019,
        "hq": 1420812465775710354,     # กองบัญชาการ
        "guest": None,
    },
    "rank_prefixes": {
        # --- ชั้นประทวน (OR-1 ถึง OR-9) ---
        "or-1": "OR-1, PVT",
        "or-2": "OR-2, PFC",
        "or-3": "OR-3, LCPL",
        "or-4": "OR-4, CPL",
        "or-5": "OR-5, SGT",
        "or-6": "OR-6, SM3",
        "or-7": "OR-7, SM2",
        "or-8": "OR-8, SM1",
        "or-9": "OR-9, SMS",

        # --- นายทหารสัญญาบัตร (OF-D ถึง OF-9) ---
        "of-d": "OF-D, ACO",
        "of-1a": "OF-1A, 2LT",
        "of-1b": "OF-1B, 1LT",
        "of-2": "OF-2, CPT",
        "of-3": "OF-3, MAJ",
        "of-4": "OF-4, LTC",
        "of-5": "OF-5, COL",
        "of-6": "OF-6, SRCOL",
        "of-7": "OF-7, MG",
        "of-8": "OF-8, LTG",
        "of-9": "OF-9, GEN",

        # --- การเมือง / จอมพล / ราชวงศ์ ---
        "deputy prime minister": "DPM",
        "prime minister": "PM",
        "mom rajawongse": "M.R.",
        "his serene highness": "H.S.H. Prince",
        "her highness": "H.H. Princess",
        "his royal highness": "H.R.H. Prince",
        "field marshal": "OF-10, FMS",
        "royal protectorate": "Protectorate",
        "crown prince": "Crown Prince",
        "her majesty": "H.M. Queen",
        "his majesty": "H.M. King"
    },
}

DEVELOPER_IDS = [2769442731]

def get_safe_emoji(emoji_str):
    if not emoji_str:
        return "✅"
    if isinstance(emoji_str, str) and emoji_str.startswith("<") and emoji_str.endswith(">"):
        try:
            return discord.PartialEmoji.from_str(emoji_str)
        except Exception:
            return "✅"
    return emoji_str

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                discord_id TEXT PRIMARY KEY,
                roblox_id TEXT,
                roblox_username TEXT,
                verified INTEGER DEFAULT 0,
                pending_roblox_username TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT PRIMARY KEY,
                settings_json TEXT
            )
            """
        )

def get_guild_settings(guild_id):
    if not guild_id:
        return json.loads(json.dumps(DEFAULT_SETTINGS))
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("SELECT settings_json FROM guild_settings WHERE guild_id = ?", (str(guild_id),))
        row = cursor.fetchone()
        
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if row:
        try:
            saved = json.loads(row[0])
            if isinstance(saved, dict):
                settings.update({k: v for k, v in saved.items() if k not in {"role_ids", "rank_prefixes"}})
                if isinstance(saved.get("role_ids"), dict):
                    settings["role_ids"].update(saved["role_ids"])
                if isinstance(saved.get("rank_prefixes"), dict):
                    settings["rank_prefixes"].update(saved["rank_prefixes"])
        except Exception as e:
            print(f"Error parsing settings for guild {guild_id}: {e}")
    return settings

def save_guild_settings(guild_id, settings):
    if not guild_id:
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO guild_settings (guild_id, settings_json)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET settings_json = excluded.settings_json
            """,
            (str(guild_id), json.dumps(settings, ensure_ascii=False))
        )

def parse_id(value):
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None

def get_user(discord_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM users WHERE discord_id = ?", (str(discord_id),)).fetchone()

def update_pending(discord_id, roblox_id, username):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO users (discord_id, roblox_id, roblox_username, pending_roblox_username, verified)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(discord_id) DO UPDATE SET
                roblox_id = excluded.roblox_id,
                roblox_username = excluded.roblox_username,
                pending_roblox_username = excluded.pending_roblox_username,
                verified = 0
            """,
            (str(discord_id), str(roblox_id), str(username), str(username).strip().lower()),
        )

# =========================
# BOT SETUP
# =========================
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(VerifyView())
        self.add_view(ReVerifyView())
        await self.tree.sync()
        print(f"Dev System Multi-Guild v7 slash commands synced for {self.user}")

bot = MyBot()

def get_roblox_info_by_name(username):
    try:
        response = requests.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": True},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("data"):
            return str(data["data"][0]["id"]), data["data"][0]["name"]
    except (requests.RequestException, ValueError) as error:
        print(f"Error fetching Roblox ID: {error}")
    return None, None

def check_group_membership(roblox_id, group_id):
    try:
        response = requests.get(
            f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles",
            timeout=15,
        )
        response.raise_for_status()
        for group in response.json().get("data", []):
            if group["group"]["id"] == int(group_id):
                return True, group["role"]["rank"], group["role"]["name"]
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        print(f"Error checking group membership: {error}")
    return False, 0, None

def get_prefix_for_rank(rank_val, rank_name, settings):
    prefixes = settings.get("rank_prefixes", {})
    normalized_name = str(rank_name or "").strip().lower()
    numeric_rank = int(rank_val or 0)

    rank_aliases = {
        1: {"or-1"}, 2: {"or-2"}, 3: {"or-3"}, 4: {"or-4"}, 5: {"or-5"},
        6: {"or-6", "or-7"}, 7: {"or-6", "or-7"},
        8: {"of-1a"}, 9: {"of-1b"}, 10: {"of-2"}, 11: {"of-2"},
        12: {"of-3"}, 13: {"of-4"}, 14: {"of-5"}, 15: {"of-6"},
        16: {"of-7"}, 17: {"of-8"}, 18: {"of-9"},
    }

    for rank_key, prefix in prefixes.items():
        key = str(rank_key).strip().lower()
        if key in normalized_name or key in rank_aliases.get(numeric_rank, set()):
            return str(prefix).strip()
        
    fallback = {
        1: "OR-1, PVT",
        2: "OR-2, PFC",
        3: "OR-3, LCPL",
        4: "OR-4, CPL",
        5: "OR-5, SGT",
        6: "OR-6, SM3",
        7: "OR-7, SM2",
        8: "OR-8, SM1",
        9: "OR-9, SMS",
        10: "OF-D, ACO",
        11: "OF-1A, 2LT",
        12: "OF-1B, 1LT",
        13: "OF-2, CPT",
        14: "OF-3, MAJ",
        15: "OF-4, LTC",
        16: "OF-5, COL",
        17: "OF-6, SRCOL",
        18: "OF-7, MG",
        19: "OF-8, LTG",
        20: "OF-9, GEN",
        22: "DPM",
        23: "PM",
        24: "M.R.",
        25: "H.S.H. Prince",
        26: "H.H. Princess",
        27: "H.R.H. Prince",
        28: "OF-10, FMS",
        29: "Protectorate",
        30: "Crown Prince",
        31: "H.M. Queen",
        32: "H.M. King"
    }
    return fallback.get(numeric_rank, "")

async def update_member_status(discord_id, roblox_id, roblox_username, guild_id=None):
    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if guild is None and bot.guilds:
        guild = bot.guilds[0]
    if guild is None:
        return None, None, None, "ไม่พบเซิร์ฟเวอร์ Discord ของบอท"

    settings = get_guild_settings(guild.id)

    try:
        member = await guild.fetch_member(int(discord_id))
        is_in_group, rank_val, rank_name = check_group_membership(roblox_id, settings["roblox_group_id"])
        is_dev = int(roblox_id) in DEVELOPER_IDS

        managed_role_ids = {
            parse_id(settings.get("verified_role_id")),
            parse_id(settings.get("developer_role_id")),
            *{parse_id(value) for value in settings.get("role_ids", {}).values()},
        }
        managed_role_ids.discard(None)

        roles_to_add = [
            role for role in member.roles
            if role != guild.default_role and role.id not in managed_role_ids
        ]
        verified_role = guild.get_role(parse_id(settings.get("verified_role_id")))
        if verified_role:
            roles_to_add.append(verified_role)

        if is_dev:
            developer_role = guild.get_role(parse_id(settings.get("developer_role_id")))
            if developer_role:
                roles_to_add.append(developer_role)
            nickname = f"ผู้ดูแลระบบ | {roblox_username}"
            display_rank_name = "Developer"
        elif is_in_group:
            if 1 <= rank_val <= 10:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("or")))
            elif 11 <= rank_val <= 13:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("cd")))
            elif 14 <= rank_val <= 17:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("of_low")))
            elif 18 <= rank_val <= 20:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("of_high")))
            elif (22 <= rank_val <= 23) or rank_val == 32:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("hq")))
            else:
                rank_role = None

            if rank_role:
                roles_to_add.append(rank_role)

            prefix = get_prefix_for_rank(rank_val, rank_name, settings)
            nickname = f"{prefix} | {roblox_username}" if prefix else roblox_username
            display_rank_name = rank_name or "ไม่ทราบชื่อยศ"
        else:
            guest_role = guild.get_role(parse_id(settings["role_ids"].get("guest")))
            if guest_role:
                roles_to_add.append(guest_role)
            nickname = f"Guest | {roblox_username}"
            display_rank_name = "Guest"

        unique_roles = list({role.id: role for role in roles_to_add}.values())
        await member.edit(roles=unique_roles, nick=nickname[:32])
        return rank_val if not is_dev else 999, member.display_name, display_rank_name, None
    except discord.HTTPException as error:
        if error.code == 50013:
            msg = "บอทไม่มีสิทธิ์จัดการโรล/ชื่อ (Missing Permissions) หรือ Role บอทอยู่ต่ำกว่า Role ที่ใส่"
        elif error.code == 10007:
            msg = "ไม่พบคุณใน Discord Server นี้ (อาจยังไม่ได้เข้าเซิร์ฟเวอร์)"
        else:
            msg = f"Discord Error {error.code}"
        print(f"Update Error [{error.code}]: {error}")
        return None, None, None, msg
    except Exception as error:
        msg = f"Error: {str(error)}"
        print(f"Update Error: {error}")
        return None, None, None, msg

# =========================
# ULTRA SSSSSS UI COMPONENTS
# =========================
class VerifyModal(discord.ui.Modal, title="⚡ ยืนยันตัวตน ROBLOX"):
    username = discord.ui.TextInput(
        label="ROBLOX USERNAME",
        placeholder="กรอกชื่อผู้ใช้ของคุณ (เช่น RobloxUser123)",
        min_length=3,
        max_length=20,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        input_name = self.username.value.strip()
        roblox_id, correct_name = get_roblox_info_by_name(input_name)
        
        if not roblox_id:
            embed = discord.Embed(
                title="❌ ไม่พบข้อมูลบัญชี",
                description=f"ไม่พบบัญชี Roblox ชื่อ **`{input_name}`** โปรดตรวจสอบตัวอักษรและลองใหม่อีกครั้ง",
                color=COLOR_ERROR
            )
            embed.set_footer(text="Verification System • Dev by : dewanoi123")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        settings = get_guild_settings(interaction.guild_id)
        is_dev = int(roblox_id) in DEVELOPER_IDS
        is_in_group, _, _ = check_group_membership(roblox_id, settings["roblox_group_id"])
        
        if not is_in_group and not is_dev:
            embed = discord.Embed(
                title="🚫 จำเป็นต้องเข้าร่วมกลุ่ม",
                description="คุณจำเป็นต้องเป็นสมาชิกของกลุ่ม Roblox ก่อนจึงจะทำการยืนยันตัวตนได้",
                color=COLOR_ERROR
            )
            embed.add_field(name="🔗 ลิงก์กลุ่ม", value=f"[คลิกที่นี่เพื่อเข้ากลุ่ม Roblox]({settings['roblox_group_url']})", inline=False)
            embed.set_footer(text="Verification System • Dev by : dewanoi123")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            try:
                await interaction.user.send(
                    f"⚠️ **แจ้งเตือน:** กรุณาเข้ากลุ่ม Roblox ก่อนทำการยืนยันตัวตน: {settings['roblox_group_url']}"
                )
            except discord.HTTPException:
                pass
            return

        update_pending(interaction.user.id, roblox_id, correct_name)
        
        embed = discord.Embed(
            title="📥 ขั้นตอนถัดไป: ยืนยันตัวตนในเกม",
            description="ระบบได้รับข้อมูลของคุณเรียบร้อยแล้ว โปรดเข้าเกมเพื่อทำรายการยืนยันให้เสร็จสมบูรณ์",
            color=COLOR_INFO
        )
        embed.add_field(name="👤 Roblox Username", value=f"```\n{correct_name}\n```", inline=True)
        embed.add_field(name="🆔 Roblox ID", value=f"```\n{roblox_id}\n```", inline=True)
        embed.add_field(
            name="🎮 เข้ายืนยันตัวตน",
            value=f"➡️ **[คลิกที่นี่เพื่อเข้าสู่แมพยืนยันตัวตน]({settings['roblox_map_url']})**",
            inline=False
        )
        embed.set_footer(text="ระบบกำลังรอการยืนยันจากในเกม...", icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ReVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="อัพเดทยศ", style=discord.ButtonStyle.success, emoji="🔄", custom_id="update_rank")
    async def update_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        user = get_user(interaction.user.id)
        if not user or not user["roblox_id"]:
            embed = discord.Embed(
                title="❌ ไม่พบข้อมูล",
                description="ไม่พบข้อมูลการบันทึกยืนยันตัวตนของคุณในระบบ",
                color=COLOR_ERROR
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        guild_id = interaction.guild.id if interaction.guild else None
        result = await update_member_status(
            interaction.user.id,
            user["roblox_id"],
            user["roblox_username"],
            guild_id,
        )
        rank_val, display_name, rank_name, err_msg = result
        if rank_val is None:
            embed = discord.Embed(
                title="❌ เกิดข้อผิดพลาด",
                description=f"```{err_msg}```",
                color=COLOR_ERROR
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        settings = get_guild_settings(guild_id)
        v_emoji = settings.get("verified_emoji", "✅")
        safe_v_emoji = get_safe_emoji(v_emoji)
        
        embed = discord.Embed(
            title=f"{safe_v_emoji} อัพเดทยศสำเร็จ!",
            description="ปรับปรุงยศและข้อมูลบทบาทของคุณเรียบร้อยแล้ว",
            color=COLOR_SUCCESS
        )
        embed.add_field(name="👤 ผู้ใช้", value=f"```\n{user['roblox_username']}\n```", inline=True)
        embed.add_field(name="🎖️ ยศปัจจุบัน", value=f"```\n{rank_name}\n```", inline=True)
        embed.set_footer(text="System Sync Completed", icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="เปลี่ยน Account", style=discord.ButtonStyle.secondary, emoji="🔁", custom_id="change_acc")
    async def change_acc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal())

class VerifyView(discord.ui.View):
    def __init__(self, emoji_str="✅"):
        super().__init__(timeout=None)
        try:
            self.start_v_btn.emoji = get_safe_emoji(emoji_str)
        except Exception:
            pass

    @discord.ui.button(
        label="ยืนยันตัวตนที่นี่",
        style=discord.ButtonStyle.primary,
        custom_id="persistent_verify",
    )
    async def start_v_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = get_user(interaction.user.id)
        if user and user["verified"]:
            settings = get_guild_settings(interaction.guild_id)
            v_emoji = settings.get("verified_emoji", "✅")
            safe_v_emoji = get_safe_emoji(v_emoji)
            
            embed = discord.Embed(
                title="✅ บัญชีนี้ได้รับการยืนยันตัวตนแล้ว",
                description="คุณมีข้อมูลผูกไว้กับระบบเรียบร้อยแล้ว หากต้องการอัพเดทยศหรือเปลี่ยนบัญชี เลือกปุ่มด้านล่าง",
                color=COLOR_INFO
            )
            embed.add_field(name="👤 Roblox Username", value=f"```{user['roblox_username']}```", inline=True)
            embed.add_field(name="🆔 Roblox ID", value=f"```{user['roblox_id']}```", inline=True)
            embed.add_field(name="⚡ สถานะ", value=f"```{safe_v_emoji} Verified```", inline=False)
            embed.set_image(url=GIF_URL) 
            embed.set_footer(text="Verification Panel • Dev by : dewanoi123")
            await interaction.response.send_message(embed=embed, view=ReVerifyView(), ephemeral=True)
        else:
            await interaction.response.send_modal(VerifyModal())

class CustomizeAllModal(discord.ui.Modal, title="⚙️ ปรับแต่งระบบทั้งหมด"):
    group_id = discord.ui.TextInput(
        label="Roblox Group ID",
        required=False,
        placeholder="ตัวเลขเท่านั้น เช่น 33852603",
    )
    group_url = discord.ui.TextInput(
        label="ลิงก์กลุ่ม Roblox",
        required=False,
        placeholder="https://www.roblox.com/groups/...",
    )
    map_url = discord.ui.TextInput(
        label="ลิงก์แมพ Roblox",
        required=False,
        placeholder="https://www.roblox.com/games/...",
    )
    prefixes = discord.ui.TextInput(
        label="คำนำหน้ายศ (แยกด้วย ;)",
        required=False,
        style=discord.TextStyle.paragraph,
        placeholder="or-1=PC; of-3=MAJ",
    )
    role_ids = discord.ui.TextInput(
        label="Role IDs (แยกด้วย ;)",
        required=False,
        style=discord.TextStyle.paragraph,
        placeholder="verified=ID; or=ID; cd=ID; of_low=ID; of_high=ID; hq=ID; guest=ID",
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        if not guild_id:
            embed = discord.Embed(title="❌ เกิดข้อผิดพลาด", description="คำสั่งนี้ต้องใช้ภายใน Server เท่านั้น", color=COLOR_ERROR)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        settings = get_guild_settings(guild_id)
        
        if self.group_id.value.strip():
            gid = parse_id(self.group_id.value.strip())
            if gid: settings["roblox_group_id"] = gid
            
        if self.group_url.value.strip():
            settings["roblox_group_url"] = self.group_url.value.strip()
        if self.map_url.value.strip():
            settings["roblox_map_url"] = self.map_url.value.strip()

        if self.prefixes.value.strip():
            for item in self.prefixes.value.split(";"):
                if "=" not in item: continue
                k, v = item.split("=", 1)
                k, v = k.strip().lower(), v.strip()
                if k and v:
                    if "," not in v and "-" in k:
                        settings["rank_prefixes"][k] = f"{k.upper()}, {v}"
                    else:
                        settings["rank_prefixes"][k] = v

        if self.role_ids.value.strip():
            for item in self.role_ids.value.split(";"):
                if "=" not in item: continue
                rtype, rid_raw = item.split("=", 1)
                rtype = rtype.strip().lower()
                rid = parse_id(rid_raw)
                if not rid: continue
                
                if rtype in {"verified", "developer"}:
                    settings[f"{rtype}_role_id"] = rid
                elif rtype in {"or", "cd", "of_low", "of_high", "hq", "guest"}:
                    settings["role_ids"][rtype] = rid

        save_guild_settings(guild_id, settings)
        embed = discord.Embed(
            title="✅ บันทึกการตั้งค่าเรียบร้อย",
            description="การตั้งค่าระบบถูกอัปเดตสำหรับเซิร์ฟเวอร์นี้แล้ว",
            color=COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

# =========================
# SLASH COMMANDS
# =========================
@bot.tree.command(name="ยืนยันตัวตน", description="ตั้งค่าระบบยืนยันตัวตน (Administrator Only)")
@app_commands.default_permissions(administrator=True)
async def setup_verify(interaction: discord.Interaction):
    settings = get_guild_settings(interaction.guild_id)
    v_emoji = settings.get("verified_emoji", "✅")
    
    embed = discord.Embed(
        title="✔️ ระบบยืนยันตัวตน | Roblox Verification",
        description=(
            "ยินดีต้อนรับสู่ระบบยืนยันตัวตน กรุณากดปุ่ม **`ยืนยันตัวตนที่นี่`** ด้านล่างเพื่อเริ่มต้นขั้นตอนผูกบัญชี Discord เข้ากับ Roblox\n\n"
            "**📌 สิ่งที่คุณต้องเตรียม:**\n"
            "• ชื่อผู้ใช้ Roblox (Username)\n"
            "• เข้าร่วมกลุ่ม Roblox ที่กำหนดให้เรียบร้อย"
        ),
        color=COLOR_PRIMARY,
    )
    if interaction.guild and interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)
    
    embed.set_image(url=GIF_URL)
    embed.set_footer(text="Roblox Verification Management System • Dev by : dewanoi123")
    
    await interaction.channel.send(embed=embed, view=VerifyView(v_emoji))
    
    confirm_embed = discord.Embed(
        title="✅ ดำเนินการสำเร็จ",
        description="ติดตั้งข้อความระบบยืนยันตัวตนลงในช่องนี้เรียบร้อยแล้ว",
        color=COLOR_SUCCESS
    )
    await interaction.response.send_message(embed=confirm_embed, ephemeral=True)

@bot.tree.command(name="ตั้งค่าอีโมจิ", description="เปลี่ยนอีโมจิกดยืนยันตัวตนของเซิร์ฟเวอร์นี้ (Administrator Only)")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(อีโมจิ="ใส่อีโมจิธรรมดา หรือ Custom Emoji เช่น <:name:ID>")
async def set_emoji(interaction: discord.Interaction, อีโมจิ: str):
    if not interaction.guild_id:
        embed = discord.Embed(title="❌ เกิดข้อผิดพลาด", description="คำสั่งนี้ต้องใช้ภายใน Server เท่านั้น", color=COLOR_ERROR)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    settings = get_guild_settings(interaction.guild_id)
    settings["verified_emoji"] = อีโมจิ.strip()
    save_guild_settings(interaction.guild_id, settings)

    safe_e = get_safe_emoji(settings["verified_emoji"])
    embed = discord.Embed(
        title="🎨 อัพเดทอีโมจิสำเร็จ",
        description=f"เปลี่ยนอีโมจิยืนยันตัวตนเป็น {safe_e} เรียบร้อยแล้ว\n\n*(พิมพ์ `/ยืนยันตัวตน` อีกครั้งเพื่อส่งปุ่มด้วยอีโมจิใหม่)*",
        color=COLOR_SUCCESS
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def clear_verification_data(interaction: discord.Interaction):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM users")
    embed = discord.Embed(
        title="⚠️ ล้างข้อมูลสำเร็จ",
        description="ลบข้อมูลการยืนยันตัวตนทั้งหมดในระบบแล้ว ผู้ใช้ทุกคนจะต้องทำการยืนยันตัวตนใหม่",
        color=COLOR_ACCENT
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="ล้างข้อมูล", description="ลบข้อมูลการยืนยันตัวตนทุกคน")
@app_commands.default_permissions(administrator=True)
async def reset_db_short(interaction: discord.Interaction):
    await clear_verification_data(interaction)

@bot.tree.command(name="ล้างข้อมูลทั้งหมด", description="ลบข้อมูลการยืนยันตัวตนทุกคน (คำสั่งเดิม)")
@app_commands.default_permissions(administrator=True)
async def reset_db_legacy(interaction: discord.Interaction):
    await clear_verification_data(interaction)

@bot.tree.command(name="ใส่โรล", description="ตั้งค่า Role ให้กับประเภทที่เลือกของเซิร์ฟเวอร์นี้")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    ประเภท="เลือกประเภทบทบาทที่ต้องการผูก Role",
    โรล="เลือก Role ที่ต้องการให้ระบบใช้",
)
@app_commands.choices(
    ประเภท=[
        app_commands.Choice(name="ยืนยันตัวตน", value="verified"),
        app_commands.Choice(name="Developer", value="developer"),
        app_commands.Choice(name="OR", value="or"),
        app_commands.Choice(name="CD(นายร้อย)", value="cd"),
        app_commands.Choice(name="OF Low", value="of_low"),
        app_commands.Choice(name="OF High", value="of_high"),
        app_commands.Choice(name="กองบัญชาการ (HQ)", value="hq"),
        app_commands.Choice(name="Guest", value="guest"),
    ]
)
async def set_role(interaction: discord.Interaction, ประเภท: app_commands.Choice[str], โรล: discord.Role):
    if not interaction.guild_id:
        embed = discord.Embed(title="❌ เกิดข้อผิดพลาด", description="คำสั่งนี้ต้องใช้ภายใน Server เท่านั้น", color=COLOR_ERROR)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    settings = get_guild_settings(interaction.guild_id)
    role_type = ประเภท.value
    if role_type in {"verified", "developer"}:
        settings[f"{role_type}_role_id"] = โรล.id
    else:
        settings["role_ids"][role_type] = โรล.id
    save_guild_settings(interaction.guild_id, settings)
    
    embed = discord.Embed(
        title="🏷️ ตั้งค่าบทบาทสำเร็จ",
        description=f"เชื่อมโยงบทบาท {โรล.mention} ให้กับประเภท **{ประเภท.name}** สำเร็จ",
        color=COLOR_SUCCESS
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="ใส่คำนำหน้า", description="เพิ่มหรือแก้คำนำหน้าตามชื่อยศ Roblox ของเซิร์ฟเวอร์นี้")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    ยศ="รหัสยศ เช่น OF-3 หรือ OR-1 ต้องตรงหรือเป็นส่วนหนึ่งของชื่อยศ Roblox",
    คำนำหน้า="ชื่อคำนำหน้า เช่น MAJ หรือ PC",
)
async def set_prefix(interaction: discord.Interaction, ยศ: str, คำนำหน้า: str):
    if not interaction.guild_id:
        embed = discord.Embed(title="❌ เกิดข้อผิดพลาด", description="คำสั่งนี้ต้องใช้ภายใน Server เท่านั้น", color=COLOR_ERROR)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    rank_code = ยศ.strip()
    title = คำนำหน้า.strip()
    if not rank_code or not title:
        embed = discord.Embed(title="❌ ข้อมูลไม่ครบถ้วน", description="กรุณาระบุยศและคำนำหน้าให้ครบถ้วน", color=COLOR_ERROR)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    settings = get_guild_settings(interaction.guild_id)
    settings["rank_prefixes"][rank_code.lower()] = f"{rank_code}, {title}"
    save_guild_settings(interaction.guild_id, settings)
    
    embed = discord.Embed(
        title="🏷️ เพิ่มคำนำหน้าสำเร็จ",
        description=f"บันทึกรูปแบบ: **`{rank_code}, {title}`** เรียบร้อยแล้ว",
        color=COLOR_SUCCESS
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="ปรับแต่งทั้งหมด", description="เปิดหน้าต่างปรับแต่งระบบกลุ่ม โรล และคำนำหน้าของเซิร์ฟเวอร์นี้")
@app_commands.default_permissions(administrator=True)
async def customize_all(interaction: discord.Interaction):
    await interaction.response.send_modal(CustomizeAllModal())

@bot.tree.command(name="ดูการตั้งค่า", description="ดูการตั้งค่าระบบปัจจุบันของเซิร์ฟเวอร์นี้")
@app_commands.default_permissions(administrator=True)
async def show_settings(interaction: discord.Interaction):
    if not interaction.guild_id:
        embed = discord.Embed(title="❌ เกิดข้อผิดพลาด", description="คำสั่งนี้ต้องใช้ภายใน Server เท่านั้น", color=COLOR_ERROR)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    settings = get_guild_settings(interaction.guild_id)
    role_ids = settings.get("role_ids", {})
    v_emoji = settings.get("verified_emoji", "✅")
    
    embed = discord.Embed(
        title="⚙️ การตั้งค่าระบบปัจจุบัน (Server Settings)",
        color=COLOR_INFO
    )
    embed.add_field(name="📌 Group ID", value=f"```{settings.get('roblox_group_id')}```", inline=True)
    embed.add_field(name="✅ Verified Role ID", value=f"```{settings.get('verified_role_id')}```", inline=True)
    embed.add_field(name="🎨 Verification Emoji", value=f"{v_emoji}", inline=True)
    
    roles_fmt = (
        f"• **OR:** `{role_ids.get('or')}`\n"
        f"• **CD (นายร้อย):** `{role_ids.get('cd')}`\n"
        f"• **OF Low:** `{role_ids.get('of_low')}`\n"
        f"• **OF High:** `{role_ids.get('of_high')}`\n"
        f"• **HQ (กองบัญชาการ):** `{role_ids.get('hq')}`\n"
        f"• **Guest:** `{role_ids.get('guest')}`"
    )
    embed.add_field(name="🎭 Role Configs", value=roles_fmt, inline=False)
    
    prefixes_str = "\n".join(f"`{key}` ➔ {value}" for key, value in settings.get("rank_prefixes", {}).items())
    embed.add_field(
        name="🏷️ Rank Prefixes",
        value=prefixes_str[:1024] if prefixes_str else "*ไม่มีข้อมูล*",
        inline=False
    )
    embed.set_footer(text="Configuration Panel • Dev by : dewanoi123")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# =========================
# FASTAPI WEBHOOK
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    asyncio.create_task(bot.start(DISCORD_TOKEN))
    yield
    await bot.close()

app = FastAPI(lifespan=lifespan)

@app.post("/verify")
async def verify_endpoint(request: Request):
    data = await request.json()
    roblox_id = data.get("robloxId")
    roblox_username = str(data.get("robloxUsername", "")).strip()
    guild_id = data.get("guildId")
    
    search_name = roblox_username.lower()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        
        row = conn.execute(
            """
            SELECT discord_id FROM users
            WHERE roblox_id = ? AND verified = 0
            ORDER BY rowid DESC LIMIT 1
            """,
            (str(roblox_id),),
        ).fetchone()
        
        if not row:
            row = conn.execute(
                """
                SELECT discord_id FROM users
                WHERE LOWER(pending_roblox_username) = ? AND verified = 0
                ORDER BY rowid DESC LIMIT 1
                """,
                (search_name,),
            ).fetchone()

    if not row:
        return {
            "ok": False,
            "message": f"ไม่พบชื่อ '{roblox_username}' ในรายการรอ (กรุณากดปุ่มยืนยันใน Discord ก่อน)",
        }

    rank, display_name, rank_name, err_msg = await update_member_status(
        row["discord_id"], roblox_id, roblox_username, guild_id
    )
    if rank is not None:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                UPDATE users
                SET roblox_id = ?, roblox_username = ?, verified = 1,
                    pending_roblox_username = NULL
                WHERE discord_id = ?
                """,
                (str(roblox_id), roblox_username, row["discord_id"]),
            )
        return {
            "ok": True,
            "discord_username": display_name,
            "current_rank": rank_name,
        }

    return {"ok": False, "message": err_msg or "บอทไม่มีสิทธิ์เปลี่ยนยศหรือไม่พบเซิร์ฟเวอร์ Discord"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
