# เครดิต
# By.ivzex | By.patxez | DEV.manpop79 | DEV.Fugus1234
# Upgraded: Integrated with Google Sheets Data Center

import os
import asyncio
import json
import re
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
SETTINGS_PATH = os.getenv("SETTINGS_PATH", "settings.json")

# 🔗 วางลิงก์ Google Apps Script URL ที่ได้จากขั้นตอนด้านบนตรงนี้ครับ
GOOGLE_SHEET_WEBAPP_URL = "https://script.google.com/macros/s/AKfycbz2DqSvKj0hScEqeF3vetbS9kZgqdXN99AHhOT8jtr_MCF9lFf1zkp5Gi78sG_6yLz9yA/exec"

DEFAULT_SETTINGS = {
    "roblox_group_id": 226834839,
    "roblox_group_url": "https://www.roblox.com/groups/226834839",
    "roblox_map_url": "https://www.roblox.com/th/games/78189317414125/By",
    "verified_role_id": 1479443343367995579,
    "developer_role_id": 1479469155399766129,
    "role_ids": {
        "or": 1479699133001629797,
        "of_low": 1479699314078122094,
        "of_high": 1479699471603470432,
        "guest": None,
    },
    "rank_prefixes": {
        "or-1": "OR-1, PC", "or-2": "OR-2, PEC", "or-3": "OR-3, CPL",
        "or-4": "OR-4, SGT", "or-5": "OR-5, SSG", "or-6": "OR-6/OR-7, SFC",
        "or-7": "OR-6/OR-7, SFC", "or-8": "OR-8/OR-9, MSG", "or-9": "OR-8/OR-9, MSG",
        "of-1a": "OF-1A, LTP", "of-1b": "OF-1B, 1LT", "of-2": "OF-2, CPT",
        "of-3": "OF-3, MAJ", "of-4": "OF-4, LTC", "of-5": "OF-5, COL",
        "of-6": "OF-6, SRCOL", "of-7": "OF-7, PMG", "of-8": "OF-8, MG", "of-9": "OF-9, GEN",
    },
}

DEVELOPER_IDS = [5711452462]
VERIFIED_EMOJI = "✅"


def load_settings():
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as file:
            saved = json.load(file)
        if isinstance(saved, dict):
            for key, value in saved.items():
                if key == "role_ids" and isinstance(value, dict):
                    settings["role_ids"].update(value)
                elif key == "rank_prefixes" and isinstance(value, dict):
                    settings["rank_prefixes"].update(value)
                else:
                    settings[key] = value
    except FileNotFoundError:
        save_settings(settings)
    except (json.JSONDecodeError, OSError) as error:
        print(f"[Settings] Error loading: {error}")
    return settings


def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as file:
            json.dump(settings, file, ensure_ascii=False, indent=2)
    except OSError as error:
        print(f"[Settings] Error saving: {error}")


def parse_id(value):
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None


# =========================
# GOOGLE SHEETS API HELPER
# =========================
def gs_update_pending(discord_id, username):
    try:
        requests.post(
            GOOGLE_SHEET_WEBAPP_URL,
            json={
                "action": "updatePending",
                "discord_id": str(discord_id),
                "pending_username": str(username).strip().lower(),
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[Google Sheet] Update Pending Error: {e}")


def gs_find_pending(username):
    try:
        res = requests.post(
            GOOGLE_SHEET_WEBAPP_URL,
            json={
                "action": "findPending",
                "search_name": str(username).strip().lower(),
            },
            timeout=10,
        )
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"[Google Sheet] Find Pending Error: {e}")
    return {"found": False}


def gs_complete_verify(row, roblox_id, roblox_username):
    try:
        requests.post(
            GOOGLE_SHEET_WEBAPP_URL,
            json={
                "action": "completeVerify",
                "row": row,
                "roblox_id": str(roblox_id),
                "roblox_username": str(roblox_username),
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[Google Sheet] Complete Verify Error: {e}")


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
        print(f"🚀 Google Sheets Verified Bot Ready: {self.user}")


bot = MyBot()


def get_roblox_id_by_name(username: str):
    try:
        res = requests.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": True},
            timeout=10,
        )
        if res.status_code == 200:
            data = res.json()
            if data.get("data"):
                return data["data"][0]["id"]
    except Exception as error:
        print(f"[Roblox API] Error fetching Roblox ID: {error}")
    return None


def check_group_membership(roblox_id: int):
    settings = load_settings()
    group_target_id = int(settings["roblox_group_id"])
    try:
        res = requests.get(
            f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles",
            timeout=10,
        )
        if res.status_code == 200:
            data = res.json()
            for group in data.get("data", []):
                if group["group"]["id"] == group_target_id:
                    return True, group["role"]["rank"], group["role"]["name"]
    except Exception as error:
        print(f"[Roblox API] Error checking group: {error}")
    return False, 0, None


def get_prefix_for_rank(rank_val, rank_name, settings):
    prefixes = settings.get("rank_prefixes", {})
    normalized_name = str(rank_name or "").strip().lower()

    for rank_key, prefix in prefixes.items():
        if str(rank_key).strip().lower() in normalized_name:
            return str(prefix).strip()

    numeric_fallback = {
        1: "OR-1, PC", 2: "OR-2, PEC", 3: "OR-3, CPL", 4: "OR-4, SGT",
        5: "OR-5, SSG", 6: "OR-6/OR-7, SFC", 7: "OR-6/OR-7, SFC",
        8: "OF-1A, LTP", 9: "OF-1B, 1LT", 10: "OF-2, CPT", 11: "OF-2, CPT",
        12: "OF-3, MAJ", 13: "OF-4, LTC", 14: "OF-5, COL", 15: "OF-6, SRCOL",
        16: "OF-7, PMG", 17: "OF-8, MG", 18: "OF-9, GEN",
    }
    return numeric_fallback.get(int(rank_val or 0), "")


async def update_member_status(discord_id, roblox_id, roblox_username, guild_id=None):
    settings = load_settings()
    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if guild is None and bot.guilds:
        guild = bot.guilds[0]
    if guild is None:
        return None, None, None

    try:
        member = await guild.fetch_member(int(discord_id))
        is_in_group, rank_val, rank_name = check_group_membership(roblox_id)
        is_dev = int(roblox_id) in DEVELOPER_IDS

        role_ids_to_manage = {
            parse_id(settings.get("verified_role_id")),
            parse_id(settings.get("developer_role_id")),
            *{parse_id(role_id) for role_id in settings.get("role_ids", {}).values()},
        }
        role_ids_to_manage.discard(None)

        roles_to_add = [
            role for role in member.roles
            if role != guild.default_role and role.id not in role_ids_to_manage
        ]
        
        verified_role = guild.get_role(parse_id(settings.get("verified_role_id")))
        if verified_role:
            roles_to_add.append(verified_role)

        if is_dev:
            developer_role = guild.get_role(parse_id(settings.get("developer_role_id")))
            if developer_role:
                roles_to_add.append(developer_role)
            nickname = f"Dev | {roblox_username}"
            display_rank_name = "Developer"
        elif is_in_group:
            if 1 <= rank_val <= 7:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("or")))
            elif 8 <= rank_val <= 11:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("of_low")))
            elif 12 <= rank_val <= 18:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("of_high")))
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

        # ถ้าเป็น Server Owner ให้ข้ามการแก้ยศเพื่อไม่ให้เกิด Permission Error
        if guild.owner_id == member.id:
            print(f"[Notice] {member.display_name} เป็น Owner ระบบอนุมัติสำเร็จโดยข้ามการปรับยศ")
            return rank_val if not is_dev else 999, member.display_name, display_rank_name

        await member.edit(roles=unique_roles, nick=nickname[:32])
        return rank_val if not is_dev else 999, member.display_name, display_rank_name

    except Exception as error:
        print(f"[Update Error] {error}")
        return 0, str(discord_id), "Verified"


# =========================
# UI COMPONENTS
# =========================
class VerifyModal(discord.ui.Modal, title="ยืนยันตัวตน Roblox"):
    username = discord.ui.TextInput(
        label="ใส่ชื่อใน Roblox (Username)",
        placeholder="พิมพ์ชื่อไอดี Roblox ของคุณที่นี่...",
        min_length=3,
        max_length=20,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        input_name = self.username.value.strip()
        await interaction.response.defer(ephemeral=True)

        roblox_id = get_roblox_id_by_name(input_name)
        if not roblox_id:
            await interaction.followup.send(
                f"❌ ไม่พบชื่อ Roblox: **{input_name}** กรุณาตรวจสอบการสะกดชื่ออีกครั้ง",
                ephemeral=True,
            )
            return

        is_dev = int(roblox_id) in DEVELOPER_IDS
        is_in_group, _, _ = check_group_membership(roblox_id)
        settings = load_settings()
        
        if not is_in_group and not is_dev:
            embed_error = discord.Embed(
                title="❌ กรุณาเข้ากลุ่ม Roblox",
                description=(
                    "คุณยังไม่ได้เข้ากลุ่มของเรา! กรุณาเข้ากลุ่มก่อนทำรายการต่อ\n\n"
                    f"🔗 **ลิงก์กลุ่ม:** [คลิกที่นี่เพื่อเข้ากลุ่ม]({settings['roblox_group_url']})"
                ),
                color=0xED4245,
            )
            await interaction.followup.send(embed=embed_error, ephemeral=True)
            return

        # บันทึกลง Google Sheet
        gs_update_pending(interaction.user.id, input_name)

        embed_success = discord.Embed(title="🎮 กรุณาเข้าเกมเพื่อยืนยันตัวตน", color=0x57F287)
        embed_success.add_field(name="Username", value=f"**{input_name}**", inline=False)
        embed_success.add_field(
            name="เกม Roblox", value=f"[คลิกเพื่อเข้าเกม]({settings['roblox_map_url']})", inline=False
        )
        embed_success.set_footer(text="เมื่อเข้าแมพแล้ว กดปุ่มยืนยันตัวตนในเกมได้ทันที")
        await interaction.followup.send(embed=embed_success, ephemeral=True)


class ReVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="เปลี่ยน Account", style=discord.ButtonStyle.primary, custom_id="change_acc")
    async def change_acc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal())


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="ยืนยันตัวตน", style=discord.ButtonStyle.success, emoji="✅", custom_id="persistent_verify"
    )
    async def start_v(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal())


# =========================
# FASTAPI WEBHOOK
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    load_settings()
    asyncio.create_task(bot.start(DISCORD_TOKEN))
    yield
    await bot.close()


app = FastAPI(lifespan=lifespan)


@app.post("/verify")
async def verify_endpoint(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "message": "Invalid JSON Payload"}

    roblox_id = data.get("robloxId")
    roblox_username = str(data.get("robloxUsername", "")).strip()
    guild_id = data.get("guildId")
    selected_division = data.get("selected_division", "army")

    # ค้นหาข้อมูลจาก Google Sheet
    gs_result = gs_find_pending(roblox_username)

    if not gs_result.get("found"):
        return {
            "ok": False,
            "message": f"ไม่พบชื่อ '{roblox_username}' ในรายการรอ กรุณากดปุ่มยืนยันใน Discord ก่อน!",
        }

    discord_id = gs_result.get("discord_id")
    row_num = gs_result.get("row")

    rank, display_name, rank_name = await update_member_status(
        discord_id, roblox_id, roblox_username, guild_id
    )

    if rank is not None:
        # อัปเดตสถานะใน Google Sheet ว่ายืนยันสำเร็จแล้ว
        gs_complete_verify(row_num, roblox_id, roblox_username)

        return {
            "ok": True,
            "discord_username": display_name,
            "current_rank": rank_name,
            "division": selected_division,
        }

    return {"ok": False, "message": "บอทไม่มีสิทธิ์เปลี่ยนยศ หรือไม่พบ Discord Server"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
