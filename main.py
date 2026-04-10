"""
洛克王国查蛋器 - RocoEgg 插件 v2.1
基于 https://github.com/mfskys/rocomegg 数据源

功能：
1. 根据尺寸和重量查询蛋对应的精灵
2. 一键从 GitHub 同步最新数据

数据收集已移交给上游处理：https://f.wps.cn/ksform/w/write/YUmapbHA/

作者: AI Developer
版本: 2.1
"""

import json
import math
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import aiohttp

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.api import AstrBotConfig


class EggData:
    """蛋数据结构"""

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
        return f"EggData({self.pet}: size[{self.size_min}-{self.size_max}], weight[{self.weight_min}-{self.weight_max}])"


class DataSyncManager:
    """数据同步管理器"""

    GITHUB_RAW_URL = "https://raw.githubusercontent.com/mfskys/rocomegg/main/public/data/egg-measurements-final.json"
    GITHUB_API_URL = "https://api.github.com/repos/mfskys/rocomegg/commits?path=public/data/egg-measurements-final.json&per_page=1"

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.egg_data_path = data_dir / "egg-measurements-final.json"
        self.sync_info_path = data_dir / "sync_info.json"
        self._ensure_data_dir()

    def _ensure_data_dir(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def load_local_data(self) -> Tuple[List[EggData], int]:
        """
        加载本地数据
        支持新旧两种格式：
        - 新格式 (groups): {"total": 840, "groups": [{"petId", "pet", "rangeItems", "exactItems"}]}
        - 旧格式 (items): {"total": 371, "items": [{"id", "pet", "eggDiameter", "eggWeight"}]}
        """
        egg_list = []

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

            elif items:
                total = data.get("total", len(items))
                for item in items:
                    egg = self._parse_egg_item(item)
                    if egg:
                        egg_list.append(egg)
                logger.info(f"成功加载 {len(egg_list)} 条本地蛋数据 (items 格式)")
                return egg_list, total

            else:
                logger.warning(f"数据文件格式未知")
                return egg_list, 0

        except Exception as e:
            logger.error(f"加载本地数据失败: {e}")
            return egg_list, 0

    def _parse_group_item(self, item: Dict, pet: str, pet_id: str) -> Optional[EggData]:
        """解析 groups 格式中的单个蛋数据项"""
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
        """解析旧格式 items 中的单个蛋数据项"""
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
        """解析范围字符串，支持格式: "0.25", "0.25-0.32", "0.25~0.32" """
        range_str = str(range_str).strip().replace("~", "-")

        if "-" in range_str:
            parts = range_str.split("-")
            if len(parts) == 2:
                try:
                    return float(parts[0].strip()), float(parts[1].strip())
                except ValueError:
                    pass
        else:
            try:
                val = float(range_str)
                return val, val
            except ValueError:
                pass
        return None, None

    async def check_update(self) -> Tuple[bool, str, str]:
        """检查是否有更新"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.GITHUB_API_URL, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        return False, "", f"GitHub API 请求失败: {response.status}"

                    commits = await response.json()
                    if not commits:
                        return False, "", "未获取到 commit 信息"

                    latest_commit = commits[0]["sha"]
                    commit_date = commits[0]["commit"]["committer"]["date"]

                    local_commit = ""
                    if self.sync_info_path.exists():
                        with open(self.sync_info_path, "r", encoding="utf-8") as f:
                            sync_info = json.load(f)
                            local_commit = sync_info.get("last_commit", "")

                    return latest_commit != local_commit, latest_commit, commit_date

        except Exception as e:
            logger.error(f"检查更新失败: {e}")
            return False, "", f"检查更新失败: {e}"

    async def sync_from_github(self) -> Tuple[bool, str, int]:
        """从 GitHub 同步数据"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.GITHUB_RAW_URL, timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        return False, f"GitHub 数据下载失败: {response.status}", 0

                    text = await response.text()
                    data = json.loads(text)

                    with open(self.egg_data_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)

                    has_update, latest_commit, _ = await self.check_update()

                    sync_info = {
                        "last_sync": datetime.now().isoformat(),
                        "last_commit": latest_commit if latest_commit else "unknown",
                        "total_items": data.get("total", 0),
                    }

                    with open(self.sync_info_path, "w", encoding="utf-8") as f:
                        json.dump(sync_info, f, ensure_ascii=False, indent=2)

                    item_count = data.get("total", 0)
                    return True, "数据同步成功", item_count

        except Exception as e:
            logger.error(f"数据同步失败: {e}")
            return False, f"同步失败: {e}", 0

    def get_sync_status(self) -> Dict:
        """获取同步状态信息"""
        status = {
            "has_local_data": self.egg_data_path.exists(),
            "last_sync": None,
            "last_commit": None,
            "total_items": 0,
        }

        if self.sync_info_path.exists():
            try:
                with open(self.sync_info_path, "r", encoding="utf-8") as f:
                    sync_info = json.load(f)
                    status["last_sync"] = sync_info.get("last_sync")
                    status["last_commit"] = sync_info.get("last_commit")
                    status["total_items"] = sync_info.get("total_items", 0)
            except:
                pass

        return status


@register("rocoegg", "AI Developer", "洛克王国查蛋器 - RocoEgg", "2.1")
class RocoEggPlugin(Star):
    """洛克王国查蛋器插件主类"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.egg_data: List[EggData] = []
        self.data_manager: Optional[DataSyncManager] = None
        self._init_data_dir()

    def _init_data_dir(self):
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "rocoegg"
        self.data_manager = DataSyncManager(self.data_dir)

    async def initialize(self):
        """插件初始化"""
        logger.info("洛克王国查蛋器插件正在初始化...")
        self.egg_data, _ = self.data_manager.load_local_data()

        if not self.egg_data:
            logger.warning("本地无数据，将在首次使用时提示同步")
        else:
            logger.info(f"查蛋器插件初始化完成，已加载 {len(self.egg_data)} 条蛋数据")

    # ============ 命令处理 ============

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
                "• /同步蛋数据 - 从 GitHub 同步最新数据\n"
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

            result = f"🥚 查询结果\n━━━━━━━━━━━━━━━\n📏 尺寸：{size:.3f}\n⚖️ 重量：{weight:.3f}\n━━━━━━━━━━━━━━━\n"

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
                f"🥚 查询结果\n━━━━━━━━━━━━━━━\n"
                f"📏 尺寸：{size:.3f}\n⚖️ 重量：{weight:.3f}\n━━━━━━━━━━━━━━━\n"
                f"❌ 未找到匹配的精灵\n\n"
                f"📤 数据收集请提交至：\n"
                f"https://f.wps.cn/ksform/w/write/YUmapbHA/"
            )

        yield event.plain_result(result)

    @filter.command("同步蛋数据")
    async def sync_data(self, event: AstrMessageEvent):
        """同步数据指令：/同步蛋数据"""
        yield event.plain_result("🔄 正在从 GitHub 同步数据...")

        success, message, item_count = await self.data_manager.sync_from_github()

        if success:
            self.egg_data, _ = self.data_manager.load_local_data()
            yield event.plain_result(
                f"✅ 数据同步成功\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📊 数据条数：{item_count}\n"
                f"💾 保存路径：{self.data_manager.egg_data_path}\n"
                f"⏰ 同步时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        else:
            yield event.plain_result(
                f"❌ 数据同步失败\n"
                f"━━━━━━━━━━━━━━━\n"
                f"错误信息：{message}\n\n"
                f"💡 提示：\n"
                f"• 请检查网络连接\n"
                f"• GitHub 可能需要代理访问"
            )

    @filter.command("蛋数据状态")
    async def data_status(self, event: AstrMessageEvent):
        """数据状态指令：/蛋数据状态"""
        status = self.data_manager.get_sync_status()

        if status["has_local_data"]:
            last_sync = status.get("last_sync", "未知")
            if last_sync and last_sync != "未知":
                try:
                    sync_time = datetime.fromisoformat(last_sync)
                    last_sync = sync_time.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    pass

            try:
                has_update, _, _ = await self.data_manager.check_update()
                update_status = "🟢 有更新可用" if has_update else "🟢 已是最新"
            except:
                update_status = "⚪ 无法检查"

            result = (
                f"📊 数据状态\n━━━━━━━━━━━━━━━\n"
                f"✅ 数据状态：已加载\n"
                f"📦 数据条数：{status['total_items']}\n"
                f"⏰ 最后同步：{last_sync}\n"
                f"🔖 Commit：{status['last_commit'][:8] if status['last_commit'] else 'N/A'}...\n"
                f"{update_status}\n\n"
                f"💡 使用 /同步蛋数据 更新数据"
            )
        else:
            result = (
                f"📊 数据状态\n━━━━━━━━━━━━━━━\n"
                f"⚠️ 数据状态：未加载\n"
                f"📦 数据条数：0\n\n"
                f"💡 首次使用请先执行：/同步蛋数据"
            )

        yield event.plain_result(result)

    @filter.command("rocoegg帮助")
    async def show_help(self, event: AstrMessageEvent):
        """帮助指令：/rocoegg帮助"""
        yield event.plain_result(
            f"🥚 洛克王国查蛋器 - RocoEgg v2.1\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔍 /查蛋 <尺寸> <重量>\n"
            f" 根据尺寸和重量查询蛋对应的精灵\n"
            f" 示例：/查蛋 0.25 14.5\n\n"
            f"🔄 /同步蛋数据\n"
            f" 从 GitHub 同步最新的蛋数据\n\n"
            f"📊 /蛋数据状态\n"
            f" 查看当前数据同步状态\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📚 数据来源\n"
            f"https://github.com/mfskys/rocomegg\n\n"
            f"📤 数据收集（提交新蛋数据）\n"
            f"https://f.wps.cn/ksform/w/write/YUmapbHA/"
        )

    # ============ 查询算法 ============

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
        """评估单个蛋数据的匹配度"""
        d_in = self._in_range(diameter, egg.size_min, egg.size_max)
        w_in = self._in_range(weight, egg.weight_min, egg.weight_max)
        d_point = self._is_point_range(egg.size_min, egg.size_max)
        w_point = self._is_point_range(egg.weight_min, egg.weight_max)

        exact = (
            d_point and w_point
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
        """按精灵聚合并计算综合得分"""
        groups = {}
        for item in evaluated:
            pet = item["egg"].pet
            if pet not in groups:
                groups[pet] = []
            groups[pet].append(item)

        merged = []
        for pet, items in groups.items():
            sorted_items = sorted(items, key=lambda x: x["score"], reverse=True)
            pet_score = sum(
                item["score"] * math.pow(0.58, i) for i, item in enumerate(sorted_items)
            )
            best = sorted_items[0]
            egg = best["egg"]

            merged.append({
                "pet": pet,
                "pet_id": egg.id,
                "match_count": len(sorted_items),
                "_score": pet_score,
                "egg": egg,
                "match_type": best["match_type"],
            })

        return sorted(merged, key=lambda x: x["_score"], reverse=True)

    def _normalize_probabilities(self, items: List[Dict]) -> List[Dict]:
        """归一化概率"""
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
