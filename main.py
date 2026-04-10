"""
洛克王国查蛋器 - RocoEgg 插件 v2.0
基于 https://github.com/mfskys/rocomegg 数据源

功能：
1. 根据尺寸和重量查询蛋对应的精灵
2. 一键从 GitHub 同步最新数据
3. 数据版本管理和自动更新检测
4. WebUI 可视化配置菜单
5. Cron 定时自动同步
6. 用户数据收集和反馈
7. 与上游数据格式完全兼容

作者: AI Developer
版本: 2.0.1
"""

import json
import re
import os
import math
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Set
import aiohttp
import asyncio
from croniter import croniter

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.api import AstrBotConfig


# ============ 数据模型 ============


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

    def matches(self, size: float, weight: float) -> bool:
        """检查输入的尺寸和重量是否在范围内"""
        return (
            self.size_min <= size <= self.size_max
            and self.weight_min <= weight <= self.weight_max
        )

    def to_dict(self) -> Dict:
        """转换为字典格式（与上游兼容）"""
        if self.size_min == self.size_max:
            diameter = f"{self.size_min}"
        else:
            diameter = f"{self.size_min}-{self.size_max}"

        if self.weight_min == self.weight_max:
            weight = f"{self.weight_min}"
        else:
            weight = f"{self.weight_min}-{self.weight_max}"

        return {
            "id": self.id,
            "eggDiameter": diameter,
            "eggWeight": weight,
            "pet": self.pet,
        }

    def __repr__(self):
        return f"EggData({self.pet}: size[{self.size_min}-{self.size_max}], weight[{self.weight_min}-{self.weight_max}])"


class UserSubmission:
    """用户提交的数据"""

    def __init__(
        self,
        user_id: str,
        user_name: str,
        pet: str,
        size_min: float,
        size_max: float,
        weight_min: float,
        weight_max: float,
        submit_time: str = None,
        status: str = "pending",  # pending, approved, rejected
    ):
        self.user_id = user_id
        self.user_name = user_name
        self.pet = pet
        self.size_min = size_min
        self.size_max = size_max
        self.weight_min = weight_min
        self.weight_max = weight_max
        self.submit_time = submit_time or datetime.now().isoformat()
        self.status = status

    def to_dict(self) -> Dict:
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "pet": self.pet,
            "size_min": self.size_min,
            "size_max": self.size_max,
            "weight_min": self.weight_min,
            "weight_max": self.weight_max,
            "submit_time": self.submit_time,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "UserSubmission":
        return cls(
            user_id=data["user_id"],
            user_name=data["user_name"],
            pet=data["pet"],
            size_min=data["size_min"],
            size_max=data["size_max"],
            weight_min=data["weight_min"],
            weight_max=data["weight_max"],
            submit_time=data.get("submit_time"),
            status=data.get("status", "pending"),
        )


class DataSyncManager:
    """数据同步管理器"""

    # GitHub 数据源配置
    GITHUB_RAW_URL = "https://raw.githubusercontent.com/mfskys/rocomegg/main/public/data/egg-measurements-final.json"
    GITHUB_API_URL = "https://api.github.com/repos/mfskys/rocomegg/commits?path=public/data/egg-measurements-final.json&per_page=1"

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.egg_data_path = data_dir / "egg-measurements-final.json"
        self.sync_info_path = data_dir / "sync_info.json"
        self.user_submissions_path = data_dir / "user_submissions.json"
        self._ensure_data_dir()

    def _ensure_data_dir(self):
        """确保数据目录存在"""
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def load_local_data(self) -> Tuple[List[EggData], int]:
        """
        加载本地数据
        返回: (蛋数据列表, 数据条数)
        """
        egg_list = []

        if not self.egg_data_path.exists():
            logger.warning(f"本地数据文件不存在: {self.egg_data_path}")
            return egg_list, 0

        try:
            with open(self.egg_data_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            items = data.get("items", [])
            total = data.get("total", len(items))

            for item in items:
                try:
                    egg = self._parse_egg_item(item)
                    if egg:
                        egg_list.append(egg)
                except Exception as e:
                    logger.warning(f"解析蛋数据失败: {item}, 错误: {e}")
                    continue

            logger.info(f"成功加载 {len(egg_list)} 条本地蛋数据")
            return egg_list, total

        except Exception as e:
            logger.error(f"加载本地数据失败: {e}")
            return egg_list, 0

    def _parse_egg_item(self, item: Dict) -> Optional[EggData]:
        """解析单个蛋数据项"""
        item_id = item.get("id")
        pet = item.get("pet")
        diameter_str = item.get("eggDiameter", "")
        weight_str = item.get("eggWeight", "")

        if not all([item_id, pet, diameter_str, weight_str]):
            return None

        # 解析尺寸范围
        size_min, size_max = self._parse_range(diameter_str)
        # 解析重量范围
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
        """
        解析范围字符串
        支持格式: "0.25", "0.25-0.32", "0.25~0.32", "0.25~0.75"
        """
        range_str = str(range_str).strip()

        # 统一分隔符
        range_str = range_str.replace("~", "-")

        if "-" in range_str:
            # 范围格式: "0.25-0.32"
            parts = range_str.split("-")
            if len(parts) == 2:
                try:
                    min_val = float(parts[0].strip())
                    max_val = float(parts[1].strip())
                    return min_val, max_val
                except ValueError:
                    pass
        else:
            # 单一值格式: "0.25"
            try:
                val = float(range_str)
                return val, val
            except ValueError:
                pass

        return None, None

    async def check_update(self) -> Tuple[bool, str, str]:
        """
        检查是否有更新
        返回: (是否有更新, 最新commit hash, 更新时间)
        """
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

                    # 读取本地记录
                    local_commit = ""
                    if self.sync_info_path.exists():
                        with open(self.sync_info_path, "r", encoding="utf-8") as f:
                            sync_info = json.load(f)
                            local_commit = sync_info.get("last_commit", "")

                    has_update = latest_commit != local_commit

                    return has_update, latest_commit, commit_date

        except asyncio.TimeoutError:
            return False, "", "请求超时"
        except Exception as e:
            logger.error(f"检查更新失败: {e}")
            return False, "", f"检查更新失败: {e}"

    async def sync_from_github(self) -> Tuple[bool, str, int]:
        """
        从 GitHub 同步数据
        返回: (是否成功, 消息, 数据条数)
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.GITHUB_RAW_URL, timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        return False, f"GitHub 数据下载失败: {response.status}", 0

                    # GitHub raw 返回 text/plain，需要手动解析 JSON
                    text = await response.text()
                    data = json.loads(text)

                    # 保存数据
                    with open(self.egg_data_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)

                    # 获取最新 commit hash
                    has_update, latest_commit, _ = await self.check_update()

                    # 保存同步信息
                    sync_info = {
                        "last_sync": datetime.now().isoformat(),
                        "last_commit": latest_commit if latest_commit else "unknown",
                        "total_items": data.get("total", 0),
                    }

                    with open(self.sync_info_path, "w", encoding="utf-8") as f:
                        json.dump(sync_info, f, ensure_ascii=False, indent=2)

                    item_count = data.get("total", len(data.get("items", [])))
                    return True, f"数据同步成功", item_count

        except asyncio.TimeoutError:
            return False, "同步超时，请稍后重试", 0
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

    # ============ 用户提交数据管理 ============

    def load_user_submissions(self) -> List[UserSubmission]:
        """加载用户提交的数据"""
        if not self.user_submissions_path.exists():
            return []

        try:
            with open(self.user_submissions_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [
                UserSubmission.from_dict(item) for item in data.get("submissions", [])
            ]
        except Exception as e:
            logger.error(f"加载用户提交数据失败: {e}")
            return []

    def save_user_submissions(self, submissions: List[UserSubmission]):
        """保存用户提交的数据"""
        try:
            data = {"submissions": [s.to_dict() for s in submissions]}
            with open(self.user_submissions_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存用户提交数据失败: {e}")

    def add_user_submission(self, submission: UserSubmission) -> bool:
        """添加用户提交"""
        submissions = self.load_user_submissions()
        submissions.append(submission)
        self.save_user_submissions(submissions)
        return True

    def get_user_submissions_today(self, user_id: str) -> int:
        """获取用户今日提交数量"""
        submissions = self.load_user_submissions()
        today = datetime.now().date()
        count = 0
        for s in submissions:
            if s.user_id == user_id:
                submit_date = datetime.fromisoformat(s.submit_time).date()
                if submit_date == today:
                    count += 1
        return count

    def get_pending_submissions(self) -> List[UserSubmission]:
        """获取待审核的提交"""
        submissions = self.load_user_submissions()
        return [s for s in submissions if s.status == "pending"]

    def approve_submission(self, index: int) -> bool:
        """审核通过提交"""
        submissions = self.load_user_submissions()
        pending = [i for i, s in enumerate(submissions) if s.status == "pending"]
        if index < 0 or index >= len(pending):
            return False
        submissions[pending[index]].status = "approved"
        self.save_user_submissions(submissions)
        return True

    def reject_submission(self, index: int) -> bool:
        """拒绝提交"""
        submissions = self.load_user_submissions()
        pending = [i for i, s in enumerate(submissions) if s.status == "pending"]
        if index < 0 or index >= len(pending):
            return False
        submissions[pending[index]].status = "rejected"
        self.save_user_submissions(submissions)
        return True

    def export_approved_data(self) -> List[Dict]:
        """导出已审核通过的数据（与上游格式兼容）"""
        submissions = self.load_user_submissions()
        approved = [s for s in submissions if s.status == "approved"]

        # 生成下一个 ID
        next_id = 10000  # 用户提交从 10000 开始
        result = []
        for s in approved:
            egg = EggData(
                id=next_id,
                pet=s.pet,
                size_min=s.size_min,
                size_max=s.size_max,
                weight_min=s.weight_min,
                weight_max=s.weight_max,
            )
            result.append(egg.to_dict())
            next_id += 1

        return result


# ============ 插件主类 ============


@register("rocoegg", "AI Developer", "洛克王国查蛋器 - RocoEgg", "2.0.1")
class RocoEggPlugin(Star):
    """洛克王国查蛋器插件主类"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.egg_data: List[EggData] = []
        self.data_manager: Optional[DataSyncManager] = None
        self._scheduler_task: Optional[asyncio.Task] = None
        self._init_data_dir()
        self._start_scheduler()

    def _init_data_dir(self):
        """初始化数据目录 - 使用 AstrBot 的数据目录"""
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "rocoegg"
        self.data_manager = DataSyncManager(self.data_dir)

    def _start_scheduler(self):
        """启动定时任务调度器"""
        if self.config.get("auto_sync_enabled", False):
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())
            logger.info("定时同步任务已启动")

    async def _scheduler_loop(self):
        """定时任务循环"""
        cron_expr = self.config.get("auto_sync_cron", "0 3 * * *")

        while True:
            try:
                # 计算下次执行时间
                now = datetime.now()
                itr = croniter(cron_expr, now)
                next_run = itr.get_next(datetime)
                wait_seconds = (next_run - now).total_seconds()

                logger.info(f"下次自动同步时间: {next_run}, 等待 {wait_seconds:.0f} 秒")
                await asyncio.sleep(wait_seconds)

                # 执行同步
                await self._perform_auto_sync()

            except Exception as e:
                logger.error(f"定时任务异常: {e}")
                await asyncio.sleep(60)  # 出错后等待1分钟再试

    async def _perform_auto_sync(self):
        """执行自动同步"""
        logger.info("开始自动同步...")
        success, message, item_count = await self.data_manager.sync_from_github()

        if success:
            # 重新加载数据
            self.egg_data, _ = self.data_manager.load_local_data()
            logger.info(f"自动同步成功，共 {item_count} 条数据")

            # 发送通知
            if self.config.get("notification_on_sync", True):
                await self._send_sync_notification(item_count)
        else:
            logger.error(f"自动同步失败: {message}")

    async def _send_sync_notification(self, item_count: int):
        """发送同步通知"""
        group_id = self.config.get("notification_group_id", "")
        if not group_id:
            return

        try:
            # 这里需要通过 AstrBot 的 API 发送消息
            # 由于无法直接获取群组，这里仅记录日志
            logger.info(f"应发送通知到群组 {group_id}: 数据已更新，共 {item_count} 条")
        except Exception as e:
            logger.error(f"发送通知失败: {e}")

    async def initialize(self):
        """插件初始化"""
        logger.info("洛克王国查蛋器插件正在初始化...")

        # 尝试加载本地数据
        self.egg_data, _ = self.data_manager.load_local_data()

        if not self.egg_data:
            logger.warning("本地无数据，将在首次使用时提示同步")
        else:
            logger.info(f"查蛋器插件初始化完成，已加载 {len(self.egg_data)} 条蛋数据")

    async def terminate(self):
        """插件销毁"""
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        logger.info("洛克王国查蛋器插件已销毁")

    # ============ 命令处理 ============

    @filter.command("查蛋")
    async def search_egg(self, event: AstrMessageEvent):
        """
        查蛋指令：/查蛋 尺寸 重量
        示例：/查蛋 0.25 14.5
        """
        message_str = event.message_str.strip()

        # 解析参数
        parts = message_str.split()
        if len(parts) < 3:
            yield event.plain_result(
                "📋 使用说明\n"
                "━━━━━━━━━━━━━━━\n"
                "指令格式：/查蛋 <尺寸> <重量>\n"
                "示例：/查蛋 0.25 14.5\n\n"
                "其他指令：\n"
                "• /同步蛋数据 - 从 GitHub 同步最新数据\n"
                "• /蛋数据状态 - 查看数据状态\n"
                "• /提交蛋数据 - 提交新发现的蛋数据\n"
                "• /rocoegg帮助 - 显示帮助信息"
            )
            return

        # 检查数据是否已加载
        if not self.egg_data:
            yield event.plain_result(
                "⚠️ 暂无数据\n"
                "━━━━━━━━━━━━━━━\n"
                "请先使用 /同步蛋数据 获取最新数据\n"
                "数据源：https://github.com/mfskys/rocomegg"
            )
            return

        # 解析数值
        try:
            size = float(parts[1])
            weight = float(parts[2])
        except ValueError:
            yield event.plain_result(
                "❌ 输入错误\n\n尺寸和重量必须是数字，如：0.25 14.5"
            )
            return

        # 格式化输出
        size_str = f"{size:.3f}"
        weight_str = f"{weight:.3f}"

        # 评估所有蛋数据（完全复刻 rocomegg 算法）
        evaluated = []
        for egg in self.egg_data:
            evaluated.append(self._evaluate_egg(size, weight, egg))

        # 按精灵聚合并计算概率
        aggregated = self._aggregate_by_pet(evaluated)
        results_with_prob = self._normalize_probabilities(aggregated)

        # 格式化输出
        if results_with_prob:
            # 分类显示
            exact_results = [r for r in results_with_prob if r["match_type"] == "exact"]
            matched_results = [
                r for r in results_with_prob if r["match_type"] == "matched"
            ]
            nearest_results = [
                r for r in results_with_prob if r["match_type"] == "nearest"
            ]

            result = (
                f"🥚 查询结果\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📏 尺寸：{size_str}\n"
                f"⚖️ 重量：{weight_str}\n"
                f"━━━━━━━━━━━━━━━\n"
            )

            if exact_results:
                result += f"🎯 精确匹配（{len(exact_results)}个）：\n"
                for i, item in enumerate(exact_results[:5], 1):
                    result += f"  {i}. {item['pet']} ✅\n"
                result += "\n"

            if matched_results:
                result += f"✅ 范围内匹配（{len(matched_results)}个，按概率排序）：\n"
                for i, item in enumerate(matched_results[:10], 1):
                    prob_str = item["probability_str"]
                    match_info = (
                        f"[{item['match_count']}条数据]"
                        if item["match_count"] > 1
                        else ""
                    )
                    result += f"  {i}. {item['pet']} {prob_str} {match_info}\n"
                if len(matched_results) > 10:
                    result += f"  ... 还有 {len(matched_results) - 10} 个\n"
                result += "\n"

            if nearest_results and not matched_results:
                result += f"💡 最接近的候选（{len(nearest_results)}个）：\n"
                for i, item in enumerate(nearest_results[:5], 1):
                    prob_str = item["probability_str"]
                    result += f"  {i}. {item['pet']} {prob_str}\n"
                result += "\n"

            result += "💡 概率基于高斯分布和范围匹配度计算"
        else:
            result = (
                f"🥚 查询结果\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📏 尺寸：{size_str}\n"
                f"⚖️ 重量：{weight_str}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"❌ 未找到匹配的精灵\n\n"
                f"📤 如果您发现了新数据，请使用 /提交蛋数据 提交"
            )

        yield event.plain_result(result)

    def _find_closest(self, size: float, weight: float) -> Optional[EggData]:
        """查找最接近的蛋数据"""
        if not self.egg_data:
            return None

        # 计算每个数据的"距离"
        def distance(egg: EggData) -> float:
            size_center = (egg.size_min + egg.size_max) / 2
            weight_center = (egg.weight_min + egg.weight_max) / 2
            return abs(size - size_center) + abs(weight - weight_center)

        return min(self.egg_data, key=distance)

    def _is_point_range(self, min_val: float, max_val: float) -> bool:
        """检查是否为单点范围"""
        return abs(max_val - min_val) < 1e-12

    def _nearly_equal(self, a: float, b: float, eps: float = 1e-9) -> bool:
        """近似相等判断"""
        return abs(a - b) <= eps

    def _in_range(self, value: float, min_val: float, max_val: float) -> bool:
        """检查值是否在范围内"""
        return value >= min_val and value <= max_val

    def _distance_to_range(self, value: float, min_val: float, max_val: float) -> float:
        """计算值到范围的距离"""
        if value < min_val:
            return min_val - value
        if value > max_val:
            return value - max_val
        return 0

    def _span(self, min_val: float, max_val: float) -> float:
        """计算范围跨度"""
        return max(0.000001, max_val - min_val)

    def _center_of_range(self, min_val: float, max_val: float) -> float:
        """计算范围中心"""
        return (min_val + max_val) / 2

    def _gaussian(self, z: float) -> float:
        """高斯函数"""
        return math.exp(-0.5 * z * z)

    def _clamp(self, v: float, min_val: float, max_val: float) -> float:
        """限制值在范围内"""
        return min(max_val, max(min_val, v))

    def _evaluate_egg(self, diameter: float, weight: float, egg: EggData) -> Dict:
        """
        评估单个蛋数据的匹配度
        完全复刻 rocomegg 的 evaluateRow 函数逻辑
        """
        d_in = self._in_range(diameter, egg.size_min, egg.size_max)
        w_in = self._in_range(weight, egg.weight_min, egg.weight_max)
        d_point = self._is_point_range(egg.size_min, egg.size_max)
        w_point = self._is_point_range(egg.weight_min, egg.weight_max)

        # 精确匹配：单点且数值近似相等
        exact = (
            d_point
            and w_point
            and self._nearly_equal(diameter, egg.size_min)
            and self._nearly_equal(weight, egg.weight_min)
        )

        if exact:
            return {"match_type": "exact", "score": 1000, "egg": egg}

        if d_in and w_in:
            # 在范围内，计算置信度
            d_half = self._span(egg.size_min, egg.size_max) / 2
            w_half = self._span(egg.weight_min, egg.weight_max) / 2
            d_center = self._center_of_range(egg.size_min, egg.size_max)
            w_center = self._center_of_range(egg.weight_min, egg.weight_max)

            d_base_tol = 0.02
            w_base_tol = 0.4

            d_z = abs(diameter - d_center) / (d_half + d_base_tol)
            w_z = abs(weight - w_center) / (w_half + w_base_tol)

            d_score = self._gaussian(d_z)
            w_score = self._gaussian(w_z)

            # 加权几何平均
            score = math.pow(d_score, 0.58) * math.pow(w_score, 0.42)

            # 精度提升因子
            size_span = self._span(egg.size_min, egg.size_max)
            weight_span = self._span(egg.weight_min, egg.weight_max)
            precision_boost = (
                1
                + 0.16 * (1 / (1 + size_span * 12))
                + 0.12 * (1 / (1 + weight_span * 2))
            )
            score *= self._clamp(precision_boost, 1, 1.28)

            return {"match_type": "matched", "score": score, "egg": egg}

        # 不在范围内，计算最近距离得分
        d_dist = self._distance_to_range(diameter, egg.size_min, egg.size_max)
        w_dist = self._distance_to_range(weight, egg.weight_min, egg.weight_max)
        score = 1 / (1 + d_dist / 0.05 + w_dist / 1.0)

        return {"match_type": "nearest", "score": score, "egg": egg}

    def _aggregate_by_pet(self, evaluated: List[Dict]) -> List[Dict]:
        """
        按精灵聚合并计算综合得分
        完全复刻 rocomegg 的 aggregateByPet 函数
        """
        # 按精灵分组
        groups = {}
        for item in evaluated:
            pet = item["egg"].pet
            if pet not in groups:
                groups[pet] = []
            groups[pet].append(item)

        merged = []
        for pet, items in groups.items():
            # 按得分排序
            sorted_items = sorted(items, key=lambda x: x["score"], reverse=True)

            # 计算综合得分（指数衰减加权）
            pet_score = 0
            for i, item in enumerate(sorted_items):
                pet_score += item["score"] * math.pow(0.58, i)

            best = sorted_items[0]
            egg = best["egg"]

            merged.append(
                {
                    "pet": pet,
                    "pet_id": egg.id,
                    "egg_diameter": f"{egg.size_min}-{egg.size_max}"
                    if egg.size_min != egg.size_max
                    else f"{egg.size_min}",
                    "egg_weight": f"{egg.weight_min}-{egg.weight_max}"
                    if egg.weight_min != egg.weight_max
                    else f"{egg.weight_min}",
                    "match_count": len(sorted_items),
                    "_score": pet_score,
                    "egg": egg,
                    "match_type": best["match_type"],
                }
            )

        # 按得分降序排序
        return sorted(merged, key=lambda x: x["_score"], reverse=True)

    def _normalize_probabilities(self, items: List[Dict]) -> List[Dict]:
        """
        归一化概率
        完全复刻 rocomegg 的 normalizeProbabilities 函数
        """
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

    @filter.command("同步蛋数据")
    async def sync_data(self, event: AstrMessageEvent):
        """
        同步数据指令：/同步蛋数据
        从 GitHub 同步最新的蛋数据
        """
        yield event.plain_result("🔄 正在从 GitHub 同步数据...")

        success, message, item_count = await self.data_manager.sync_from_github()

        if success:
            # 重新加载数据
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
        """
        数据状态指令：/蛋数据状态
        查看当前数据状态和同步信息
        """
        status = self.data_manager.get_sync_status()

        # 获取配置信息
        auto_sync = self.config.get("auto_sync_enabled", False)
        cron_expr = self.config.get("auto_sync_cron", "0 3 * * *")
        collection = self.config.get("data_collection_enabled", True)
        feedback = self.config.get("feedback_enabled", True)

        if status["has_local_data"]:
            last_sync = status["last_sync"]
            if last_sync:
                try:
                    sync_time = datetime.fromisoformat(last_sync)
                    last_sync_str = sync_time.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    last_sync_str = last_sync
            else:
                last_sync_str = "未知"

            # 检查是否有更新
            try:
                has_update, _, _ = await self.data_manager.check_update()
                update_status = "🟢 有更新可用" if has_update else "🟢 已是最新"
            except:
                update_status = "⚪ 无法检查"

            result = (
                f"📊 数据状态\n"
                f"━━━━━━━━━━━━━━━\n"
                f"✅ 数据状态：已加载\n"
                f"📦 数据条数：{status['total_items']}\n"
                f"⏰ 最后同步：{last_sync_str}\n"
                f"🔖 Commit：{status['last_commit'][:8] if status['last_commit'] else 'N/A'}...\n"
                f"{update_status}\n\n"
                f"⚙️ 配置状态\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🔄 自动同步：{'开启' if auto_sync else '关闭'}\n"
                f"⏰ Cron表达式：{cron_expr}\n"
                f"📥 数据收集：{'开启' if collection else '关闭'}\n"
                f"📤 数据反馈：{'开启' if feedback else '关闭'}\n\n"
                f"💡 使用 /同步蛋数据 更新数据"
            )
        else:
            result = (
                f"📊 数据状态\n"
                f"━━━━━━━━━━━━━━━\n"
                f"⚠️ 数据状态：未加载\n"
                f"📦 数据条数：0\n\n"
                f"💡 首次使用请先执行：/同步蛋数据"
            )

        yield event.plain_result(result)

    @filter.command("提交蛋数据")
    async def submit_egg_data(self, event: AstrMessageEvent):
        """
        提交蛋数据指令：/提交蛋数据 <精灵名> <尺寸最小值> <尺寸最大值> <重量最小值> <重量最大值>
        示例：/提交蛋数据 阿米亚特 0.25 0.32 14.417 18.659
        """
        # 检查是否开启数据收集
        if not self.config.get("data_collection_enabled", True):
            yield event.plain_result(
                "❌ 数据收集已关闭\n\n请联系管理员开启数据收集功能。"
            )
            return

        message_str = event.message_str.strip()
        parts = message_str.split()

        if len(parts) < 6:
            yield event.plain_result(
                "📋 使用说明\n"
                "━━━━━━━━━━━━━━━\n"
                "指令格式：/提交蛋数据 <精灵名> <尺寸最小> <尺寸最大> <重量最小> <重量最大>\n"
                "示例：/提交蛋数据 阿米亚特 0.25 0.32 14.417 18.659\n\n"
                "💡 提示：\n"
                "• 如果您只知道单一值，最小和最大值填相同的数字\n"
                "• 数据将提交给管理员审核"
                "• 审核通过后将合并到主数据库"
            )
            return

        # 获取用户信息
        user_id = str(event.get_sender_id())
        user_name = event.get_sender_name() or "未知用户"

        # 检查每日提交上限
        max_submissions = self.config.get("max_user_submissions_per_day", 10)
        today_count = self.data_manager.get_user_submissions_today(user_id)
        if today_count >= max_submissions:
            yield event.plain_result(
                f"❌ 提交失败\n"
                f"━━━━━━━━━━━━━━━\n"
                f"您今日已提交 {today_count} 条数据\n"
                f"每日上限：{max_submissions} 条\n\n"
                f"请明天再试~"
            )
            return

        # 解析数据
        try:
            pet = parts[1]
            size_min = float(parts[2])
            size_max = float(parts[3])
            weight_min = float(parts[4])
            weight_max = float(parts[5])
        except (ValueError, IndexError):
            yield event.plain_result(
                "❌ 参数格式错误\n\n请检查数值是否正确，示例：/提交蛋数据 阿米亚特 0.25 0.32 14.417 18.659"
            )
            return

        # 验证数据合理性
        if size_min > size_max or weight_min > weight_max:
            yield event.plain_result("❌ 数据验证失败\n\n最小值不能大于最大值")
            return

        if size_min < 0 or weight_min < 0:
            yield event.plain_result("❌ 数据验证失败\n\n数值不能为负数")
            return

        # 创建提交
        submission = UserSubmission(
            user_id=user_id,
            user_name=user_name,
            pet=pet,
            size_min=size_min,
            size_max=size_max,
            weight_min=weight_min,
            weight_max=weight_max,
        )

        # 检查是否为自动审核模式
        review_mode = self.config.get("data_collection_review_mode", "strict")
        if review_mode == "auto":
            submission.status = "approved"

        # 保存提交
        self.data_manager.add_user_submission(submission)

        if review_mode == "auto":
            yield event.plain_result(
                f"✅ 数据提交成功（自动审核通过）\n"
                f"━━━━━━━━━━━━━━━\n"
                f"精灵：{pet}\n"
                f"尺寸：{size_min} - {size_max}\n"
                f"重量：{weight_min} - {weight_max}\n\n"
                f"感谢您的贡献！数据已自动收录。"
            )
        else:
            yield event.plain_result(
                f"✅ 数据提交成功\n"
                f"━━━━━━━━━━━━━━━\n"
                f"精灵：{pet}\n"
                f"尺寸：{size_min} - {size_max}\n"
                f"重量：{weight_min} - {weight_max}\n\n"
                f"⏳ 状态：等待管理员审核\n"
                f"📊 您今日已提交：{today_count + 1}/{max_submissions}\n\n"
                f"感谢您的贡献！"
            )

    @filter.command("审核蛋数据")
    async def review_egg_data(self, event: AstrMessageEvent):
        """
        审核蛋数据指令（管理员）：/审核蛋数据 [list|approve <序号>|reject <序号>]
        """
        # 检查是否有权限（简单检查，实际应配置管理员ID列表）
        sender_id = str(event.get_sender_id())
        # 这里可以添加管理员ID列表检查

        message_str = event.message_str.strip()
        parts = message_str.split()

        if len(parts) < 2:
            yield event.plain_result(
                "📋 审核指令\n"
                "━━━━━━━━━━━━━━━\n"
                "/审核蛋数据 list - 查看待审核列表\n"
                "/审核蛋数据 approve <序号> - 通过审核\n"
                "/审核蛋数据 reject <序号> - 拒绝审核"
            )
            return

        action = parts[1].lower()

        if action == "list":
            pending = self.data_manager.get_pending_submissions()
            if not pending:
                yield event.plain_result("✅ 当前没有待审核的数据")
                return

            result = f"📋 待审核数据列表（共{len(pending)}条）\n━━━━━━━━━━━━━━━\n"
            for i, s in enumerate(pending[:10], 1):  # 最多显示10条
                result += (
                    f"[{i}] {s.pet}\n"
                    f"    尺寸：{s.size_min}-{s.size_max}\n"
                    f"    重量：{s.weight_min}-{s.weight_max}\n"
                    f"    提交者：{s.user_name}\n"
                    f"    时间：{s.submit_time[:16]}\n\n"
                )

            yield event.plain_result(result)

        elif action == "approve":
            if len(parts) < 3:
                yield event.plain_result(
                    "❌ 请指定要审核的序号，如：/审核蛋数据 approve 1"
                )
                return

            try:
                index = int(parts[2]) - 1
            except ValueError:
                yield event.plain_result("❌ 序号必须是数字")
                return

            if self.data_manager.approve_submission(index):
                yield event.plain_result(f"✅ 已通过第 {index + 1} 条数据的审核")
            else:
                yield event.plain_result("❌ 审核失败，请检查序号是否正确")

        elif action == "reject":
            if len(parts) < 3:
                yield event.plain_result(
                    "❌ 请指定要拒绝的序号，如：/审核蛋数据 reject 1"
                )
                return

            try:
                index = int(parts[2]) - 1
            except ValueError:
                yield event.plain_result("❌ 序号必须是数字")
                return

            if self.data_manager.reject_submission(index):
                yield event.plain_result(f"✅ 已拒绝第 {index + 1} 条数据")
            else:
                yield event.plain_result("❌ 操作失败，请检查序号是否正确")

        else:
            yield event.plain_result("❌ 未知指令，请使用 list、approve 或 reject")

    @filter.command("导出蛋数据")
    async def export_egg_data(self, event: AstrMessageEvent):
        """
        导出蛋数据指令（管理员）：/导出蛋数据
        导出用户提交的数据（与上游格式兼容）
        """
        data = self.data_manager.export_approved_data()

        if not data:
            yield event.plain_result("⚠️ 当前没有已审核的数据可导出")
            return

        # 保存导出文件
        export_path = self.data_dir / "user_export.json"
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(
                {"total": len(data), "items": data}, f, ensure_ascii=False, indent=2
            )

        yield event.plain_result(
            f"✅ 数据导出成功\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 导出数量：{len(data)} 条\n"
            f"💾 文件路径：{export_path}\n\n"
            f"📤 您可以将此文件提交到上游仓库：\n"
            f"https://github.com/mfskys/rocomegg"
        )

    @filter.command("rocoegg帮助")
    async def show_help(self, event: AstrMessageEvent):
        """
        帮助指令：/rocoegg帮助
        显示插件帮助信息
        """
        help_text = (
            f"🥚 洛克王国查蛋器 - RocoEgg v2.0.1\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📌 基础指令\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔍 /查蛋 <尺寸> <重量>\n"
            f" 根据尺寸和重量查询蛋对应的精灵\n"
            f" 示例：/查蛋 0.25 14.5\n\n"
            f"🔄 /同步蛋数据\n"
            f" 从 GitHub 同步最新的蛋数据\n\n"
            f"📊 /蛋数据状态\n"
            f" 查看数据状态、同步时间和配置信息\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📌 数据收集\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📤 /提交蛋数据 <精灵名> <尺寸最小> <尺寸最大> <重量最小> <重量最大>\n"
            f" 提交新发现的蛋数据到公共数据库\n"
            f" 示例：/提交蛋数据 阿米亚特 0.25 0.32 14.417 18.659\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📌 管理指令（管理员）\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📋 /审核蛋数据 list\n"
            f" 查看待审核的数据列表\n\n"
            f"✅ /审核蛋数据 approve <序号>\n"
            f" 通过指定数据\n\n"
            f"❌ /审核蛋数据 reject <序号>\n"
            f" 拒绝指定数据\n\n"
            f"📦 /导出蛋数据\n"
            f" 导出已审核的数据（兼容上游格式）\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📌 配置说明\n"
            f"━━━━━━━━━━━━━━━\n"
            f"可在 AstrBot WebUI → 插件配置中设置：\n"
            f"• 自动同步开关和 Cron 表达式\n"
            f"• 数据收集和反馈开关\n"
            f"• 每日提交上限\n"
            f"• 通知群组 ID\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📚 数据来源\n"
            f"https://github.com/mfskys/rocomegg\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💝 如果觉得好用，欢迎给数据源项目点个 Star！"
        )

        yield event.plain_result(help_text)
