"""
洛克王国查蛋器 - RocoEgg 插件 v2.3.1
基于 https://github.com/mfskys/rocomegg 数据源

功能：
1. 根据尺寸和重量查询蛋对应的精灵
2. 手动或定时强制拉取最新数据

数据收集已移交给上游处理：https://f.wps.cn/ksform/w/write/YUmapbHA/

作者: AI Developer
版本: 2.3.1
"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


PLUGIN_NAME = "rocoegg"
PLUGIN_VERSION = "2.3.1"


class EggData:
    """单条蛋数据。"""

    def __init__(
        self,
        id: int,
        pet: str,
        size_min: float,
        size_max: float,
        weight_min: float,
        weight_max: float,
    ):
        self.id = id
        self.pet = pet
        self.size_min = size_min
        self.size_max = size_max
        self.weight_min = weight_min
        self.weight_max = weight_max

    def __repr__(self):
        return (
            f"EggData({self.pet}: "
            f"size[{self.size_min}-{self.size_max}], "
            f"weight[{self.weight_min}-{self.weight_max}])"
        )


class DataSyncManager:
    """数据同步管理器。"""

    DEFAULT_DATA_SOURCE_URL = (
        "https://raw.githubusercontent.com/"
        "mfskys/rocomegg/main/public/data/egg-measurements-final.json"
    )

    def __init__(
        self,
        data_dir: Path,
        github_proxy_url: str = "",
        data_source_url: str = "",
    ):
        self.data_dir = data_dir
        self.egg_data_path = data_dir / "egg-measurements-final.json"
        self.sync_info_path = data_dir / "sync_info.json"
        self.github_proxy_url = (github_proxy_url or "").strip()
        self.data_source_url = (data_source_url or self.DEFAULT_DATA_SOURCE_URL).strip()
        self._ensure_data_dir()

    def _ensure_data_dir(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def update_proxy_url(self, github_proxy_url: str = ""):
        self.github_proxy_url = (github_proxy_url or "").strip()

    def update_data_source_url(self, data_source_url: str = ""):
        self.data_source_url = (data_source_url or self.DEFAULT_DATA_SOURCE_URL).strip()

    def _build_request_url(self, url: str) -> str:
        proxy_url = self.github_proxy_url.rstrip("/")
        if not proxy_url:
            return url
        if "{url}" in proxy_url:
            return proxy_url.replace("{url}", url)
        return f"{proxy_url}/{url}"

    def load_local_data(self) -> Tuple[List[EggData], int]:
        """
        加载本地数据。
        支持两种格式：
        - groups: {"total": 840, "groups": [...]}
        - items: {"total": 371, "items": [...]}
        """
        egg_list: List[EggData] = []

        if not self.egg_data_path.exists():
            logger.warning(f"本地数据文件不存在: {self.egg_data_path}")
            return egg_list, 0

        try:
            with open(self.egg_data_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            groups = data.get("groups", [])
            items = data.get("items", [])

            if groups:
                total = data.get("total", 0)
                for group in groups:
                    pet = group.get("pet", "")
                    pet_id = group.get("petId", "")
                    for range_item in group.get("rangeItems", []):
                        egg = self._parse_group_item(range_item, pet, pet_id)
                        if egg:
                            egg_list.append(egg)
                    for exact_item in group.get("exactItems", []):
                        egg = self._parse_group_item(exact_item, pet, pet_id)
                        if egg:
                            egg_list.append(egg)
                logger.info(f"成功加载 {len(egg_list)} 条本地蛋数据 (groups 格式)")
                return egg_list, total

            if items:
                total = data.get("total", len(items))
                for item in items:
                    egg = self._parse_egg_item(item)
                    if egg:
                        egg_list.append(egg)
                logger.info(f"成功加载 {len(egg_list)} 条本地蛋数据 (items 格式)")
                return egg_list, total

            logger.warning("数据文件格式未知")
            return egg_list, 0

        except Exception as exc:
            logger.error(f"加载本地数据失败: {exc}")
            return egg_list, 0

    def _parse_group_item(self, item: Dict, pet: str, pet_id: str) -> Optional[EggData]:
        item_id = item.get("id")
        diameter_str = item.get("eggDiameter", "")
        weight_str = item.get("eggWeight", "")

        if not all([item_id, pet, diameter_str, weight_str]):
            return None

        size_min, size_max = self._parse_range(diameter_str)
        weight_min, weight_max = self._parse_range(weight_str)

        if size_min is None or weight_min is None:
            return None

        return EggData(
            id=item_id,
            pet=pet,
            size_min=size_min,
            size_max=size_max,
            weight_min=weight_min,
            weight_max=weight_max,
        )

    def _parse_egg_item(self, item: Dict) -> Optional[EggData]:
        item_id = item.get("id")
        pet = item.get("pet")
        diameter_str = item.get("eggDiameter", "")
        weight_str = item.get("eggWeight", "")

        if not all([item_id, pet, diameter_str, weight_str]):
            return None

        size_min, size_max = self._parse_range(diameter_str)
        weight_min, weight_max = self._parse_range(weight_str)

        if size_min is None or weight_min is None:
            return None

        return EggData(
            id=item_id,
            pet=pet,
            size_min=size_min,
            size_max=size_max,
            weight_min=weight_min,
            weight_max=weight_max,
        )

    def _parse_range(self, range_str: str) -> Tuple[Optional[float], Optional[float]]:
        range_str = str(range_str).strip().replace("~", "-")

        if "-" in range_str:
            parts = range_str.split("-")
            if len(parts) == 2:
                try:
                    return float(parts[0].strip()), float(parts[1].strip())
                except ValueError:
                    return None, None

        try:
            val = float(range_str)
            return val, val
        except ValueError:
            return None, None

    async def sync_from_github(self) -> Tuple[bool, str, int]:
        """从 GitHub 或代理强制拉取最新数据。"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self._build_request_url(self.data_source_url),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status != 200:
                        return False, f"数据下载失败: {response.status}", 0

                    text = await response.text()
                    data = json.loads(text)

                    with open(self.egg_data_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)

                    sync_info = {
                        "last_sync": datetime.now().isoformat(),
                        "total_items": data.get("total", 0),
                    }

                    with open(self.sync_info_path, "w", encoding="utf-8") as f:
                        json.dump(sync_info, f, ensure_ascii=False, indent=2)

                    item_count = data.get("total", 0)
                    return True, "数据同步成功", item_count

        except Exception as exc:
            logger.error(f"数据同步失败: {exc}")
            return False, f"同步失败: {exc}", 0

    def get_sync_status(self) -> Dict:
        """获取同步状态。"""
        status = {
            "has_local_data": self.egg_data_path.exists(),
            "last_sync": None,
            "total_items": 0,
        }

        if self.sync_info_path.exists():
            try:
                with open(self.sync_info_path, "r", encoding="utf-8") as f:
                    sync_info = json.load(f)
                    status["last_sync"] = sync_info.get("last_sync")
                    status["total_items"] = sync_info.get("total_items", 0)
            except Exception:
                pass

        return status


@register(PLUGIN_NAME, "AI Developer", "洛克王国查蛋器 - RocoEgg", PLUGIN_VERSION)
class RocoEggPlugin(Star):
    """洛克王国查蛋器插件主类。"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.egg_data: List[EggData] = []
        self.data_manager: Optional[DataSyncManager] = None
        self.scheduler = AsyncIOScheduler()
        self.sync_lock = asyncio.Lock()
        self._init_data_dir()

    def _init_data_dir(self):
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "rocoegg"
        self.data_manager = DataSyncManager(
            self.data_dir,
            self.config.get("github_proxy_url", ""),
            self.config.get("data_source_url", ""),
        )

    def _get_config(self) -> Dict:
        return {
            "github_proxy_url": self.config.get("github_proxy_url", ""),
            "data_source_url": self.config.get(
                "data_source_url",
                DataSyncManager.DEFAULT_DATA_SOURCE_URL,
            ),
            "auto_sync_enabled": self.config.get("auto_sync_enabled", True),
            "auto_sync_cron": self.config.get("auto_sync_cron", "0 1 * * *"),
            "auto_sync_notify_target": self.config.get("auto_sync_notify_target", "").strip(),
        }

    def _get_auto_sync_job(self):
        try:
            return self.scheduler.get_job("rocoegg_auto_sync")
        except Exception:
            return None

    def _format_proxy_status(self) -> str:
        return "已启用" if self.config.get("github_proxy_url", "").strip() else "未启用"

    async def _notify_target(self, message: str):
        notify_target = self._get_config().get("auto_sync_notify_target", "")
        if not notify_target:
            return
        try:
            await self.context.send_message(notify_target, MessageChain().message(message))
        except Exception as exc:
            logger.error(f"发送定时同步通知失败: {exc}")

    async def _execute_sync(self, source: str = "manual") -> Tuple[bool, str, int]:
        async with self.sync_lock:
            config = self._get_config()
            self.data_manager.update_proxy_url(config.get("github_proxy_url", ""))
            self.data_manager.update_data_source_url(config.get("data_source_url", ""))
            success, message, item_count = await self.data_manager.sync_from_github()
            if success:
                self.egg_data, _ = self.data_manager.load_local_data()
                logger.info(f"RocoEgg {source} 数据同步成功，共 {item_count} 条")
            else:
                logger.error(f"RocoEgg {source} 数据同步失败: {message}")
            return success, message, item_count

    async def _auto_sync_job(self):
        success, message, item_count = await self._execute_sync(source="auto")
        if success:
            logger.info(f"RocoEgg 定时同步完成，共 {item_count} 条数据")
            return

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        notify_message = (
            "RocoEgg 定时同步失败\n"
            f"时间: {now_str}\n"
            f"原因: {message}\n"
            f"代理: {self._format_proxy_status()}"
        )
        await self._notify_target(notify_message)

    async def _configure_auto_sync(self):
        config = self._get_config()

        try:
            self.scheduler.remove_job("rocoegg_auto_sync")
        except Exception:
            pass

        if not config.get("auto_sync_enabled", True):
            logger.info("RocoEgg 定时同步已关闭")
            return

        cron_expr = config.get("auto_sync_cron", "0 1 * * *").strip()
        try:
            trigger = CronTrigger.from_crontab(cron_expr)
        except Exception as exc:
            logger.error(f"RocoEgg 定时同步 cron 配置无效: {cron_expr}, error: {exc}")
            await self._notify_target(
                "RocoEgg 定时同步未启动\n"
                f"原因: cron 配置无效 ({cron_expr})\n"
                f"错误: {exc}"
            )
            return

        self.scheduler.add_job(
            self._auto_sync_job,
            trigger=trigger,
            id="rocoegg_auto_sync",
            misfire_grace_time=3600,
        )
        logger.info(f"RocoEgg 定时同步已启动，cron: {cron_expr}")

    async def initialize(self):
        logger.info("洛克王国查蛋器插件正在初始化...")
        self.data_manager.update_proxy_url(self.config.get("github_proxy_url", ""))
        self.data_manager.update_data_source_url(self.config.get("data_source_url", ""))
        self.egg_data, _ = self.data_manager.load_local_data()

        if not self.egg_data:
            logger.warning("本地暂无蛋数据，首次使用前请先执行 /同步蛋数据")
        else:
            logger.info(f"查蛋器插件初始化完成，已加载 {len(self.egg_data)} 条蛋数据")

        if not self.scheduler.running:
            self.scheduler.start()
        await self._configure_auto_sync()

    async def terminate(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
        logger.info("洛克王国查蛋器插件已卸载")

    @filter.command("查蛋")
    async def search_egg(self, event: AstrMessageEvent):
        """查蛋指令：/查蛋 尺寸 重量"""
        message_str = event.message_str.strip()
        parts = message_str.split()

        if len(parts) < 3:
            yield event.plain_result(
                "📋 使用说明\n"
                "━━━━━━━━━━━━━━━\n"
                "指令格式：/查蛋 <尺寸> <重量>\n"
                "示例：/查蛋 0.25 14.5\n\n"
                "其他指令：\n"
                "• /同步蛋数据 - 手动强制同步最新数据\n"
                "• /蛋数据状态 - 查看数据状态"
            )
            return

        if not self.egg_data:
            yield event.plain_result(
                "⚠️ 暂无数据\n"
                "━━━━━━━━━━━━━━━\n"
                "请先使用 /同步蛋数据 获取最新数据\n"
                "数据源：https://github.com/mfskys/rocomegg"
            )
            return

        try:
            size = float(parts[1])
            weight = float(parts[2])
        except ValueError:
            yield event.plain_result("❌ 输入错误\n\n尺寸和重量必须是数字，如：0.25 14.5")
            return

        evaluated = [self._evaluate_egg(size, weight, egg) for egg in self.egg_data]
        aggregated = self._aggregate_by_pet(evaluated)
        results_with_prob = self._normalize_probabilities(aggregated)

        if results_with_prob:
            exact_results = [r for r in results_with_prob if r["match_type"] == "exact"]
            matched_results = [r for r in results_with_prob if r["match_type"] == "matched"]
            nearest_results = [r for r in results_with_prob if r["match_type"] == "nearest"]

            result = (
                "🥚 查询结果\n"
                "━━━━━━━━━━━━━━━\n"
                f"📏 尺寸：{size:.3f}\n"
                f"⚖️ 重量：{weight:.3f}\n"
                "━━━━━━━━━━━━━━━\n"
            )

            if exact_results:
                result += f"🎯 精确匹配（{len(exact_results)}个）：\n"
                for i, item in enumerate(exact_results[:5], 1):
                    result += f"  {i}. {item['pet']} ✅\n"
                result += "\n"

            if matched_results:
                result += f"✅ 范围内匹配（{len(matched_results)}个，按概率排序）：\n"
                for i, item in enumerate(matched_results[:10], 1):
                    match_info = f"[{item['match_count']}条数据]" if item["match_count"] > 1 else ""
                    result += f"  {i}. {item['pet']} {item['probability_str']} {match_info}\n"
                if len(matched_results) > 10:
                    result += f"  ... 还有 {len(matched_results) - 10} 个\n"
                result += "\n"

            if nearest_results and not matched_results:
                result += f"💡 最接近的候选（{len(nearest_results)}个）：\n"
                for i, item in enumerate(nearest_results[:5], 1):
                    result += f"  {i}. {item['pet']} {item['probability_str']}\n"
                result += "\n"

            result += "💡 概率基于高斯分布和范围匹配度计算"
        else:
            result = (
                "🥚 查询结果\n"
                "━━━━━━━━━━━━━━━\n"
                f"📏 尺寸：{size:.3f}\n"
                f"⚖️ 重量：{weight:.3f}\n"
                "━━━━━━━━━━━━━━━\n"
                "❌ 未找到匹配的精灵\n\n"
                "📤 数据收集请提交至：\n"
                "https://f.wps.cn/ksform/w/write/YUmapbHA/"
            )

        yield event.plain_result(result)

    @filter.command("同步蛋数据")
    async def sync_data(self, event: AstrMessageEvent):
        """同步数据指令：/同步蛋数据"""
        yield event.plain_result("🔄 正在强制同步最新蛋数据，请稍候...")

        success, message, item_count = await self._execute_sync(source="manual")

        if success:
            yield event.plain_result(
                "✅ 数据同步成功\n"
                "━━━━━━━━━━━━━━━\n"
                f"📊 数据条数：{item_count}\n"
                f"💾 保存路径：{self.data_manager.egg_data_path}\n"
                f"⏰ 同步时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        else:
            yield event.plain_result(
                "❌ 数据同步失败\n"
                "━━━━━━━━━━━━━━━\n"
                f"错误信息：{message}\n\n"
                "💡 提示：\n"
                "• 请检查网络连接\n"
                "• 如访问 GitHub 较慢，可在 WebUI 配置 github_proxy_url"
            )

    @filter.command("蛋数据状态")
    async def data_status(self, event: AstrMessageEvent):
        """数据状态指令：/蛋数据状态"""
        config = self._get_config()
        self.data_manager.update_proxy_url(config.get("github_proxy_url", ""))
        status = self.data_manager.get_sync_status()

        last_sync = status.get("last_sync", "未知")
        if last_sync and last_sync != "未知":
            try:
                last_sync = datetime.fromisoformat(last_sync).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        auto_sync_enabled = "开启" if config.get("auto_sync_enabled", True) else "关闭"
        auto_sync_cron = config.get("auto_sync_cron", "0 1 * * *")
        job_registered = "已注册" if self._get_auto_sync_job() else "未注册"
        notify_target = config.get("auto_sync_notify_target", "")
        notify_status = notify_target if notify_target else "未配置"
        data_source_url = config.get("data_source_url", DataSyncManager.DEFAULT_DATA_SOURCE_URL)

        if status["has_local_data"]:
            result = (
                "📊 数据状态\n"
                "━━━━━━━━━━━━━━━\n"
                "✅ 数据状态：已加载\n"
                f"📦 数据条数：{status['total_items']}\n"
                f"⏰ 最后同步：{last_sync}\n\n"
                "⏰ 定时同步\n"
                f"• 开关：{auto_sync_enabled}\n"
                f"• Cron：{auto_sync_cron}\n"
                f"• 任务：{job_registered}\n"
                f"• 通知 UMO：{notify_status}\n"
                f"• 代理：{self._format_proxy_status()}\n"
                f"• 数据源：{data_source_url}"
            )
        else:
            result = (
                "📊 数据状态\n"
                "━━━━━━━━━━━━━━━\n"
                "⚠️ 数据状态：未加载\n"
                "📦 数据条数：0\n\n"
                "⏰ 定时同步\n"
                f"• 开关：{auto_sync_enabled}\n"
                f"• Cron：{auto_sync_cron}\n"
                f"• 任务：{job_registered}\n"
                f"• 通知 UMO：{notify_status}\n"
                f"• 代理：{self._format_proxy_status()}\n"
                f"• 数据源：{data_source_url}\n\n"
                "💡 首次使用请先执行：/同步蛋数据"
            )

        yield event.plain_result(result)

    @filter.command("rocoegg帮助")
    async def show_help(self, event: AstrMessageEvent):
        """帮助指令：/rocoegg帮助"""
        config = self._get_config()
        auto_sync_enabled = "开启" if config.get("auto_sync_enabled", True) else "关闭"
        auto_sync_cron = config.get("auto_sync_cron", "0 1 * * *")

        yield event.plain_result(
            f"🥚 洛克王国查蛋器 - RocoEgg v{PLUGIN_VERSION}\n"
            "━━━━━━━━━━━━━━━\n"
            "🔍 /查蛋 <尺寸> <重量>\n"
            " 根据尺寸和重量查询蛋对应的精灵\n"
            " 示例：/查蛋 0.25 14.5\n\n"
            "🔄 /同步蛋数据\n"
            " 手动强制同步最新蛋数据\n\n"
            "📊 /蛋数据状态\n"
            " 查看数据状态和定时同步状态\n\n"
            "⏰ 当前定时同步配置\n"
            f" 开关：{auto_sync_enabled}\n"
            f" Cron：{auto_sync_cron}\n\n"
            "🌐 WebUI 配置项\n"
            " github_proxy_url：GitHub 代理加速地址\n"
            " data_source_url：蛋数据拉取地址，默认当前 raw 链接\n"
            " auto_sync_enabled：是否启用定时同步\n"
            " auto_sync_cron：cron 表达式\n"
            " auto_sync_notify_target：失败通知 UMO，例如 獭獭:FriendMessage:942648152\n\n"
            "📎 数据来源\n"
            "https://github.com/mfskys/rocomegg\n\n"
            "📤 数据收集\n"
            "https://f.wps.cn/ksform/w/write/YUmapbHA/"
        )

    def _is_point_range(self, min_val: float, max_val: float) -> bool:
        return abs(max_val - min_val) < 1e-12

    def _nearly_equal(self, a: float, b: float, eps: float = 1e-9) -> bool:
        return abs(a - b) <= eps

    def _in_range(self, value: float, min_val: float, max_val: float) -> bool:
        return min_val <= value <= max_val

    def _distance_to_range(self, value: float, min_val: float, max_val: float) -> float:
        if value < min_val:
            return min_val - value
        if value > max_val:
            return value - max_val
        return 0

    def _span(self, min_val: float, max_val: float) -> float:
        return max(0.000001, max_val - min_val)

    def _center_of_range(self, min_val: float, max_val: float) -> float:
        return (min_val + max_val) / 2

    def _gaussian(self, z: float) -> float:
        return math.exp(-0.5 * z * z)

    def _clamp(self, v: float, min_val: float, max_val: float) -> float:
        return min(max_val, max(min_val, v))

    def _evaluate_egg(self, diameter: float, weight: float, egg: EggData) -> Dict:
        """评估单个蛋数据的匹配度。"""
        d_in = self._in_range(diameter, egg.size_min, egg.size_max)
        w_in = self._in_range(weight, egg.weight_min, egg.weight_max)
        d_point = self._is_point_range(egg.size_min, egg.size_max)
        w_point = self._is_point_range(egg.weight_min, egg.weight_max)

        exact = (
            d_point
            and w_point
            and self._nearly_equal(diameter, egg.size_min)
            and self._nearly_equal(weight, egg.weight_min)
        )

        if exact:
            return {"match_type": "exact", "score": 1000, "egg": egg}

        if d_in and w_in:
            d_half = self._span(egg.size_min, egg.size_max) / 2
            w_half = self._span(egg.weight_min, egg.weight_max) / 2
            d_center = self._center_of_range(egg.size_min, egg.size_max)
            w_center = self._center_of_range(egg.weight_min, egg.weight_max)

            d_z = abs(diameter - d_center) / (d_half + 0.02)
            w_z = abs(weight - w_center) / (w_half + 0.4)

            score = math.pow(self._gaussian(d_z), 0.58) * math.pow(self._gaussian(w_z), 0.42)

            size_span = self._span(egg.size_min, egg.size_max)
            weight_span = self._span(egg.weight_min, egg.weight_max)
            precision_boost = (
                1 + 0.16 * (1 / (1 + size_span * 12)) + 0.12 * (1 / (1 + weight_span * 2))
            )
            score *= self._clamp(precision_boost, 1, 1.28)

            return {"match_type": "matched", "score": score, "egg": egg}

        d_dist = self._distance_to_range(diameter, egg.size_min, egg.size_max)
        w_dist = self._distance_to_range(weight, egg.weight_min, egg.weight_max)
        score = 1 / (1 + d_dist / 0.05 + w_dist / 1.0)

        return {"match_type": "nearest", "score": score, "egg": egg}

    def _aggregate_by_pet(self, evaluated: List[Dict]) -> List[Dict]:
        """按精灵聚合并计算综合得分。"""
        groups: Dict[str, List[Dict]] = {}
        for item in evaluated:
            pet = item["egg"].pet
            groups.setdefault(pet, []).append(item)

        merged = []
        for pet, items in groups.items():
            sorted_items = sorted(items, key=lambda x: x["score"], reverse=True)
            pet_score = sum(item["score"] * math.pow(0.58, i) for i, item in enumerate(sorted_items))
            best = sorted_items[0]
            egg = best["egg"]

            merged.append(
                {
                    "pet": pet,
                    "pet_id": egg.id,
                    "match_count": len(sorted_items),
                    "_score": pet_score,
                    "egg": egg,
                    "match_type": best["match_type"],
                }
            )

        return sorted(merged, key=lambda x: x["_score"], reverse=True)

    def _normalize_probabilities(self, items: List[Dict]) -> List[Dict]:
        """归一化概率。"""
        total_score = sum(item["_score"] for item in items)

        if total_score <= 0:
            for item in items:
                item["probability"] = 0.0
                item["probability_str"] = "0%"
            return items

        for item in items:
            prob = (item["_score"] / total_score) * 100
            item["probability"] = round(prob, 2)
            item["probability_str"] = f"{item['probability']}%"

        return items
