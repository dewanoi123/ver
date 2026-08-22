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

DEFAULT_SETTINGS = {
    "roblox_group_id": 33852603,
    "roblox_group_url": "https://www.roblox.com/groups/33852603",
    "roblox_map_url": "https://www.roblox.com/th/games/109069394163925/ver",
    "verified_role_id": None,
    "developer_role_id": None,
    "verified_emoji": "✅",
    "role_ids": {
        "or": None,
        "of_low": None,
        "of_high": None,
        "guest": None,
    },
    "rank_prefixes": {
        "or-1": "OR-1, PC",
        "or-2": "OR-2, PEC",
        "or-3": "OR-3, CPL",
        "or-4": "OR-4, SGT",
        "or-5": "OR-5, SSG",
        "or-6": "OR-6/OR-7, SFC",
        "or-7": "OR-6/OR-7, SFC",
        "or-8": "OR-8/OR-9, MSG",
        "or-9": "OR-8/OR-9, MSG",
        "of-1a": "OF-1A, LTP",
        "of-1b": "OF-1B, 1LT",
        "of-2": "OF-2, CPT",
        "of-3": "OF-3, MAJ",
        "of-4": "OF-4, LTC",
        "of-5": "OF-5, COL",
        "of-6": "OF-6, SRCOL",
        "of-7": "OF-7, PMG",
        "of-8": "OF-8, MG",
        "of-9": "OF-9, GEN",
    },
}

DEVELOPER_IDS = [2769442731, 11388802001, 909811599]

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
        print(f"Multi-Guild Auto-Role Sync System Ready as {self.user}")

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
        1: "OR-1, PC", 2: "OR-2, PEC", 3: "OR-3, CPL", 4: "OR-4, SGT",
        5: "OR-5, SSG", 6: "OR-6/OR-7, SFC", 7: "OR-6/OR-7, SFC",
        8: "OF-1A, LTP", 9: "OF-1B, 1LT", 10: "OF-2, CPT", 11: "OF-2, CPT",
        12: "OF-3, MAJ", 13: "OF-4, LTC", 14: "OF-5, COL", 15: "OF-6, SRCOL",
        16: "OF-7, PMG", 17: "OF-8, MG", 18: "OF-9, GEN",
    }
    return fallback.get(numeric_rank, "")

async def get_or_create_role(guild: discord.Guild, role_key: str, role_name: str, color: discord.Color, settings: dict):
    """ฟังก์ชันค้นหา หรือสร้าง Role ในเซิร์ฟเวอร์ให้อัตโนมัติหากยังไม่มี และบันทึก ID"""
    role_id = parse_id(settings.get("role_ids", {}).get(role_key)) if role_key in ["or", "of_low", "of_high", "guest"] else parse_id(settings.get(f"{role_key}_role_id"))
    
    role = guild.get_role(role_id) if role_id else None
    
    if not role:
        # ลองค้นหาตามชื่อก่อนเพื่อป้องกันการสร้างซ้ำ
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            try:
                role = await guild.create_role(name=role_name, color=color, reason="สร้าง Role อัตโนมัติสำหรับระบบ Verify Multi-Server")
            except discord.HTTPException as e:
                print(f"Cannot create role {role_name} in {guild.name}: {e}")
                return None
        
        # บันทึก Role ID ใหม่เข้า DB ของเซิร์ฟเวอร์นี้
        if role_key in ["or", "of_low", "of_high", "guest"]:
            settings["role_ids"][role_key] = role.id
        else:
            settings[f"{role_key}_role_id"] = role.id
            
        save_guild_settings(guild.id, settings)
        
    return role

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

        # เช็ค/สร้าง Verified Role
        verified_role = await get_or_create_role(guild, "verified", "Verified", discord.Color.from_rgb(87, 242, 135), settings)

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
        if verified_role:
            roles_to_add.append(verified_role)

        if is_dev:
            dev_role = await get_or_create_role(guild, "developer", "Developer", discord.Color.purple(), settings)
            if dev_role: roles_to_add.append(dev_role)
            nickname = f"Dev | {roblox_username}"
            display_rank_name = "Developer"
        elif is_in_group:
            rank_key = None
            role_title = rank_name or "Military Rank"
            
            if 1 <= rank_val <= 7:
                rank_key, role_title = "or", "OR | ชั้นประทวน"
            elif 8 <= rank_val <= 11:
                rank_key, role_title = "of_low", "OF Low | สัญญาบัตรต้น"
            elif 12 <= rank_val <= 18:
                rank_key, role_title = "of_high", "OF High | สัญญาบัตรสูง"

            if rank_key:
                rank_role = await get_or_create_role(guild, rank_key, role_title, discord.Color.blue(), settings)
                if rank_role: roles_to_add.append(rank_role)

            prefix = get_prefix_for_rank(rank_val, rank_name, settings)
            nickname = f"{prefix} | {roblox_username}" if prefix else roblox_username
            display_rank_name = rank_name or "ไม่ทราบชื่อยศ"
        else:
            guest_role = await get_or_create_role(guild, "guest", "Guest", discord.Color.light_grey(), settings)
            if guest_role: roles_to_add.append(guest_role)
            nickname = f"Guest | {roblox_username}"
            display_rank_name = "Guest"

        unique_roles = list({role.id: role for role in roles_to_add if role}.values())
        await member.edit(roles=unique_roles, nick=nickname[:32])
        return rank_val if not is_dev else 999, member.display_name, display_rank_name, None
    except discord.HTTPException as error:
        if error.code == 50013:
            msg = "บอทไม่มีสิทธิ์จัดการโรล/ชื่อ (Missing Permissions)"
        elif error.code == 10007:
            msg = "ไม่พบผู้ใช้ในเซิร์ฟเวอร์ Discord นี้"
        else:
            msg = f"Discord Error {error.code}"
        return None, None, None, msg
    except Exception as error:
        return None, None, None, f"Error: {str(error)}"

# =========================
# UI COMPONENTS
# =========================
class VerifyModal(discord.ui.Modal, title="ศูนย์ยืนยันตัวตน Roblox"):
    username = discord.ui.TextInput(
        label="ชื่อผู้ใช้ Roblox (Username)",
        placeholder="ตัวอย่าง: Player123",
        min_length=3,
        max_length=20,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        input_name = self.username.value.strip()
        roblox_id, correct_name = get_roblox_info_by_name(input_name)
        
        if not roblox_id:
            embed = discord.Embed(
                title="❌ ไม่พบชื่อผู้ใช้ Roblox",
                description=f"ไม่พบชื่อบัญชี **{input_name}** ในระบบ Roblox กรุณาตรวจสอบการสะกดอีกครั้ง",
                color=0xED4245
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        settings = get_guild_settings(interaction.guild_id)
        is_dev = int(roblox_id) in DEVELOPER_IDS
        is_in_group, _, _ = check_group_membership(roblox_id, settings["roblox_group_id"])
        
        if not is_in_group and not is_dev:
            embed = discord.Embed(
                title="⚠️ จำเป็นต้องเข้าร่วมกลุ่ม Roblox",
                description="คุณยังไม่ได้เป็นสมาชิกกลุ่ม Roblox ของเรา\nกรุณากดลิงก์ด้านล่างเพื่อเข้ากลุ่มก่อนทำรายการยืนยันตัวตนค่ะ",
                color=0xFEE75C
            )
            embed.add_field(name="🔗 ลิงก์กลุ่ม Roblox", value=f"[คลิกที่นี่เพื่อเข้ากลุ่ม]({settings['roblox_group_url']})")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        update_pending(interaction.user.id, roblox_id, correct_name)
        
        embed = discord.Embed(
            title="🎮 ยืนยันขั้นตอนสุดท้ายในเกม",
            description="บันทึกข้อมูลเรียบร้อยแล้ว! กรุณาเข้าแมพเพื่อยืนยันตัวตนใน Roblox",
            color=0x57F287
        )
        embed.add_field(name="👤 ชื่อบัญชี", value=f"```\n{correct_name}\n```", inline=True)
        embed.add_field(name="🆔 Roblox ID", value=f"```\n{roblox_id}\n```", inline=True)
        embed.add_field(name="🚀 ลิงก์แมพยืนยัน", value=f"[เข้าสู่แมพยืนยันตัวตน]({settings['roblox_map_url']})", inline=False)
        embed.set_footer(text="เมื่อเข้าแมพแล้ว ระบบจะอัพเดทยศและเปลี่ยนชื่อให้อัตโนมัติ")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ReVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="อัพเดทยศ", style=discord.ButtonStyle.success, emoji="🔄", custom_id="update_rank")
    async def update_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        user = get_user(interaction.user.id)
        if not user or not user["roblox_id"]:
            embed = discord.Embed(title="❌ ไม่พบข้อมูลการยืนยันตัวตน", color=0xED4245)
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
            embed = discord.Embed(title="❌ เกิดข้อผิดพลาดในการอัพเดท", description=err_msg, color=0xED4245)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        settings = get_guild_settings(guild_id)
        v_emoji = settings.get("verified_emoji", "✅")
        safe_v_emoji = get_safe_emoji(v_emoji)
        
        embed = discord.Embed(
            title=f"{safe_v_emoji} อัพเดทยศในเซิร์ฟเวอร์นี้สำเร็จ!",
            color=0x57F287
        )
        embed.add_field(name="👤 Roblox Username", value=f"`{user['roblox_username']}`", inline=True)
        embed.add_field(name="🎖️ ยศที่ได้รับ", value=f"`{rank_name}`", inline=True)
        embed.set_footer(text=f"เซิร์ฟเวอร์: {interaction.guild.name if interaction.guild else 'Discord'}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="เปลี่ยน บัญชี Roblox", style=discord.ButtonStyle.secondary, emoji="👤", custom_id="change_acc")
    async def change_acc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal())

class VerifyView(discord.ui.View):
    def __init__(self, emoji_str="✅"):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="ยืนยันตัวตน",
        style=discord.ButtonStyle.success,
        emoji="🗸",
        custom_id="persistent_verify",
    )
    async def start_v_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = get_user(interaction.user.id)
        
        if user and user["verified"]:
            await interaction.response.defer(ephemeral=True)
            guild_id = interaction.guild.id if interaction.guild else None
            
            # ทำการดึงยศ สร้างยศให้อัตโนมัติ (หากยังไม่มี) และเปลี่ยนชื่อ/แจกยศให้ทันที!
            result = await update_member_status(
                interaction.user.id,
                user["roblox_id"],
                user["roblox_username"],
                guild_id,
            )
            rank_val, display_name, rank_name, err_msg = result
            
            settings = get_guild_settings(guild_id)
            v_emoji = settings.get("verified_emoji", "✅")
            safe_v_emoji = get_safe_emoji(v_emoji)
            
            embed = discord.Embed(
                title=f"{safe_v_emoji} ยืนยันตัวตนสำเร็จ!",
                description=(
                    f"ซิงค์ข้อมูลบัญชีเรียบร้อยแล้วค่ะ!\n\n"
                    f"👤 **Roblox:** `{user['roblox_username']}`\n"
                    f"🎖️ **ยศเซิร์ฟเวอร์นี้:** `{rank_name or 'สำเร็จ'}`"
                ),
                color=0x57F287
            )
            await interaction.followup.send(embed=embed, view=ReVerifyView(), ephemeral=True)
        else:
            await interaction.response.send_modal(VerifyModal())

    @discord.ui.button(
        label="ทำไมต้องยืนยันตัวตน?",
        style=discord.ButtonStyle.secondary,
        emoji="📌",
        custom_id="why_verify_info",
    )
    async def why_verify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📌 ทำไมต้องยืนยันตัวตน?",
            description=(
                "• **เพื่อความปลอดภัย:** ป้องกันบัญชีปลอมและบอทก่อกวนภายในเซิร์ฟเวอร์\n"
                "• **รับยศและชื่อในเกมอัตโนมัติ:** ระบบจะดึงยศจากกลุ่ม Roblox มาตั้งคำนำหน้าและชื่อให้อัตโนมัติ\n"
                "• **ยืนยันครั้งเดียวใช้ได้ทุกเซิร์ฟ:** หากยืนยันในเซิร์ฟหลักแล้ว เมื่อไปเซิร์ฟรองเพียงกดปุ่มยืนยัน ยศและชื่อจะเปลี่ยนทันที!"
            ),
            color=0x2B2D31
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class CustomizeAllModal(discord.ui.Modal, title="ปรับแต่งระบบเซิร์ฟเวอร์"):
    group_id = discord.ui.TextInput(label="Roblox Group ID", required=False, placeholder="เช่น 33852603")
    group_url = discord.ui.TextInput(label="ลิงก์กลุ่ม Roblox", required=False, placeholder="https://www.roblox.com/groups/...")
    map_url = discord.ui.TextInput(label="ลิงก์แมพ Roblox", required=False, placeholder="https://www.roblox.com/games/...")
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
        placeholder="verified=ID; or=ID; of_low=ID; of_high=ID; guest=ID",
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.response.send_message("❌ คำสั่งนี้ใช้เฉพาะภายในเซิร์ฟเวอร์เท่านั้น", ephemeral=True)
            return

        settings = get_guild_settings(guild_id)
        if self.group_id.value.strip():
            gid = parse_id(self.group_id.value.strip())
            if gid: settings["roblox_group_id"] = gid
            
        if self.group_url.value.strip(): settings["roblox_group_url"] = self.group_url.value.strip()
        if self.map_url.value.strip(): settings["roblox_map_url"] = self.map_url.value.strip()

        if self.prefixes.value.strip():
            for item in self.prefixes.value.split(";"):
                if "=" not in item: continue
                k, v = item.split("=", 1)
                k, v = k.strip().lower(), v.strip()
                if k and v:
                    if "," not in v and "-" in k: settings["rank_prefixes"][k] = f"{k.upper()}, {v}"
                    else: settings["rank_prefixes"][k] = v

        if self.role_ids.value.strip():
            for item in self.role_ids.value.split(";"):
                if "=" not in item: continue
                rtype, rid_raw = item.split("=", 1)
                rtype = rtype.strip().lower()
                rid = parse_id(rid_raw)
                if not rid: continue
                if rtype in {"verified", "developer"}: settings[f"{rtype}_role_id"] = rid
                elif rtype in {"or", "of_low", "of_high", "guest"}: settings["role_ids"][rtype] = rid

        save_guild_settings(guild_id, settings)
        
        embed = discord.Embed(
            title="✅ บันทึกการตั้งค่าเรียบร้อยแล้ว",
            description=f"อัพเดทการตั้งค่าระบบสำหรับเซิร์ฟเวอร์ **{interaction.guild.name}** เรียบร้อยค่ะ",
            color=0x57F287
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

# =========================
# SLASH COMMANDS
# =========================
@bot.tree.command(name="ยืนยันตัวตน", description="ติดตั้ง Embed ยืนยันตัวตน (Administrator Only)")
@app_commands.default_permissions(administrator=True)
async def setup_verify(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("❌ คำสั่งนี้ต้องใช้ภายใน Server เท่านั้น", ephemeral=True)
        return

    settings = get_guild_settings(interaction.guild_id)
    v_emoji = settings.get("verified_emoji", "✅")
    
    embed = discord.Embed(
        title="✨ ยืนยันตัวตนด้วยปุ่ม ❗",
        description="➡️ **คลิกปุ่มด้านล่างเพื่อเริ่มการยืนยันตัวตนและเข้าสู่เซิร์ฟเวอร์ค่ะ!**",
        color=0x2B2D31
    )
    embed.set_author(name="👾 Powered by Verification System")
    embed.set_image(url="https://i.imgur.com/8N4X9pX.png")
    
    await interaction.channel.send(embed=embed, view=VerifyView(v_emoji))
    await interaction.response.send_message("✅ ติดตั้งหน้าต่างยืนยันตัวตนเรียบร้อยแล้วค่ะ", ephemeral=True)

@bot.tree.command(name="ตั้งค่าอีโมจิ", description="เปลี่ยนอีโมจิกดยืนยันตัวตน")
@app_commands.default_permissions(administrator=True)
async def set_emoji(interaction: discord.Interaction, อีโมจิ: str):
    if not interaction.guild_id: return
    settings = get_guild_settings(interaction.guild_id)
    settings["verified_emoji"] = อีโมจิ.strip()
    save_guild_settings(interaction.guild_id, settings)

    safe_e = get_safe_emoji(settings["verified_emoji"])
    await interaction.response.send_message(f"✅ อัพเดทอีโมจิยืนยันตัวตนเป็น {safe_e} เรียบร้อยแล้ว", ephemeral=True)

@bot.tree.command(name="ใส่โรล", description="ตั้งค่า Role ประจำเซิร์ฟเวอร์")
@app_commands.default_permissions(administrator=True)
@app_commands.choices(
    ประเภท=[
        app_commands.Choice(name="ยืนยันตัวตน (Verified)", value="verified"),
        app_commands.Choice(name="Developer", value="developer"),
        app_commands.Choice(name="OR (ชั้นประทวน)", value="or"),
        app_commands.Choice(name="OF Low (สัญญาบัตรระดับต้น)", value="of_low"),
        app_commands.Choice(name="OF High (สัญญาบัตรระดับสูง)", value="of_high"),
        app_commands.Choice(name="Guest (บุคคลภายนอก)", value="guest"),
    ]
)
async def set_role(interaction: discord.Interaction, ประเภท: app_commands.Choice[str], โรล: discord.Role):
    if not interaction.guild_id: return
    settings = get_guild_settings(interaction.guild_id)
    role_type = ประเภท.value
    if role_type in {"verified", "developer"}:
        settings[f"{role_type}_role_id"] = โรล.id
    else:
        settings["role_ids"][role_type] = โรล.id
    save_guild_settings(interaction.guild_id, settings)
    
    await interaction.response.send_message(f"✅ ตั้งค่า Role **{โรล.name}** ให้กับประเภท **{ประเภท.name}** สำเร็จ", ephemeral=True)

@bot.tree.command(name="ใส่คำนำหน้า", description="เพิ่มหรือแก้คำนำหน้าตามชื่อยศ Roblox")
@app_commands.default_permissions(administrator=True)
async def set_prefix(interaction: discord.Interaction, ยศ: str, คำนำหน้า: str):
    if not interaction.guild_id: return
    settings = get_guild_settings(interaction.guild_id)
    settings["rank_prefixes"][ยศ.strip().lower()] = f"{ยศ.strip()}, {คำนำหน้า.strip()}"
    save_guild_settings(interaction.guild_id, settings)
    await interaction.response.send_message(f"✅ ตั้งค่าคำนำหน้า `{ยศ.upper()}` -> `{คำนำหน้า}` สำเร็จ", ephemeral=True)

@bot.tree.command(name="ปรับแต่งทั้งหมด", description="เปิดหน้าต่างตั้งค่าระบบแบบรวม")
@app_commands.default_permissions(administrator=True)
async def customize_all(interaction: discord.Interaction):
    await interaction.response.send_modal(CustomizeAllModal())

@bot.tree.command(name="ดูการตั้งค่า", description="ตรวจสอบการตั้งค่าของเซิร์ฟเวอร์นี้")
@app_commands.default_permissions(administrator=True)
async def show_settings(interaction: discord.Interaction):
    if not interaction.guild_id: return
    settings = get_guild_settings(interaction.guild_id)
    role_ids = settings.get("role_ids", {})
    
    embed = discord.Embed(title=f"⚙️ การตั้งค่าระบบ | {interaction.guild.name}", color=0x5865F2)
    embed.add_field(name="Group ID", value=f"`{settings.get('roblox_group_id')}`", inline=True)
    embed.add_field(name="Verified Role ID", value=f"`{settings.get('verified_role_id')}`", inline=True)
    embed.add_field(
        name="Role IDs",
        value=f"• OR: `{role_ids.get('or')}`\n• OF Low: `{role_ids.get('of_low')}`\n• OF High: `{role_ids.get('of_high')}`\n• Guest: `{role_ids.get('guest')}`",
        inline=False
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# =========================
# WEBHOOK API
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
            "SELECT discord_id FROM users WHERE roblox_id = ? AND verified = 0 ORDER BY rowid DESC LIMIT 1",
            (str(roblox_id),),
        ).fetchone()
        
        if not row:
            row = conn.execute(
                "SELECT discord_id FROM users WHERE LOWER(pending_roblox_username) = ? AND verified = 0 ORDER BY rowid DESC LIMIT 1",
                (search_name,),
            ).fetchone()

    if not row:
        return {"ok": False, "message": f"ไม่พบชื่อ '{roblox_username}' ในรายการรอ กรุณากดปุ่มยืนยันใน Discord ก่อน"}

    rank, display_name, rank_name, err_msg = await update_member_status(
        row["discord_id"], roblox_id, roblox_username, guild_id
    )
    if rank is not None:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE users SET roblox_id = ?, roblox_username = ?, verified = 1, pending_roblox_username = NULL WHERE discord_id = ?",
                (str(roblox_id), roblox_username, row["discord_id"]),
            )
        return {"ok": True, "discord_username": display_name, "current_rank": rank_name}

    return {"ok": False, "message": err_msg or "บอทไม่มีสิทธิ์เปลี่ยนยศหรือเกิดข้อผิดพลาดในการเชื่อมต่อ"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
