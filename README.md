# 洛克王国查蛋器 - RocoEgg v2.0

基于 [RocoEgg](https://github.com/mfskys/rocomegg) 数据源的 AstraBot 查蛋插件。

## v2.0 新特性

- ⚙️ **WebUI 可视化配置**：支持在管理面板直接配置
- ⏰ **定时自动同步**：支持 cron 表达式自动更新
- 📤 **数据收集**：用户可以提交新发现的蛋数据
- ✅ **审核系统**：管理员可审核用户提交的数据
- 📦 **数据导出**：导出兼容上游格式的数据

## 功能特性

- 🔍 **智能查蛋**：根据尺寸和重量快速查询蛋对应的精灵
- 🔄 **一键同步**：支持从 GitHub 一键同步最新数据
- 📊 **数据管理**：数据版本追踪，支持更新检测
- 🎯 **模糊匹配**：查询不到时自动推荐最接近的精灵
- ⚙️ **WebUI 配置**：可视化配置菜单，无需修改代码
- ⏰ **定时同步**：支持 cron 表达式自动同步
- 📤 **数据收集**：用户可提交新数据，共建数据库
- ✅ **审核系统**：管理员审核机制，保证数据质量
- 📦 **数据导出**：导出与上游兼容的数据格式

## 安装

### 前置依赖

安装 croniter（用于定时任务）：
```bash
pip install croniter>=1.3.0
```

或在插件目录下运行：
```bash
pip install -r requirements.txt
```

### 安装插件

1. 将插件文件夹放入 `AstrBot/data/plugins/` 目录
2. 重启 AstrBot 或在 WebUI 中重载插件
3. 在 WebUI → 插件配置 中设置相关选项
4. 首次使用执行 `/同步蛋数据` 获取数据

## WebUI 配置

在 AstrBot WebUI → 插件 → 配置 中可以设置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| auto_sync_enabled | 启用每日自动同步 | false |
| auto_sync_cron | 自动同步 cron 表达式 | 0 3 * * * (每天凌晨3点) |
| data_collection_enabled | 启用数据收集功能 | true |
| data_collection_review_mode | 数据收集审核模式 (strict/auto/disabled) | strict |
| feedback_enabled | 允许用户反馈数据 | true |
| notification_on_sync | 自动同步完成后通知 | true |
| notification_group_id | 通知群组 ID | "" |
| max_user_submissions_per_day | 每日用户提交上限 | 10 |

### Cron 表达式示例

- `0 3 * * *` - 每天凌晨 3 点
- `0 */6 * * *` - 每 6 小时
- `0 0,12 * * *` - 每天 0 点和 12 点
- `*/30 * * * *` - 每 30 分钟

## 使用说明

### 基础指令

```
/查蛋 <尺寸> <重量>
```

**示例：**
```
/查蛋 0.25 14.5
```

### 数据管理

```
/同步蛋数据           # 从 GitHub 同步最新数据
/蛋数据状态            # 查看数据状态和同步信息
/rocoegg帮助         # 显示帮助信息
```

### 数据收集（用户）

```
/提交蛋数据 <精灵名> <尺寸最小> <尺寸最大> <重量最小> <重量最大>
```

**示例：**
```
/提交蛋数据 阿米亚特 0.25 0.32 14.417 18.659
```

**提示：**
- 如果只知道单一值，最小和最大值填相同的数字
- 提交后需等待管理员审核
- 每个用户每日有提交上限

### 数据管理（管理员）

```
/审核蛋数据 list                    # 查看待审核列表
/审核蛋数据 approve <序号>          # 通过指定数据
/审核蛋数据 reject <序号>            # 拒绝指定数据
/导出蛋数据                         # 导出已审核的数据
```

## 数据格式

### 上游数据格式

插件使用与上游完全兼容的数据格式：

```json
{
  "total": 371,
  "items": [
    {
      "id": 1,
      "eggDiameter": "0.25-0.32",
      "eggWeight": "14.417-18.659",
      "pet": "阿米亚特"
    }
  ]
}
```

### 导出的数据格式

导出的数据与上游格式完全一致，可直接提交 PR：

```json
{
  "total": 5,
  "items": [
    {
      "id": 10001,
      "eggDiameter": "0.30-0.35",
      "eggWeight": "15.0-20.0",
      "pet": "新精灵"
    }
  ]
}
```

## 数据来源

- **主数据源**: [mfskys/rocomegg](https://github.com/mfskys/rocomegg)
- **数据格式**: JSON
- **更新频率**: 跟随上游仓库自动更新

## 如何贡献数据

### 方式一：通过插件提交

1. 发现新数据后使用 `/提交蛋数据` 提交
2. 等待管理员审核
3. 审核通过后数据会被导出

### 方式二：直接提交到上游

1. 使用 `/导出蛋数据` 导出 JSON
2. 访问 https://github.com/mfskys/rocomegg
3. Fork 仓库并修改 `public/data/egg-measurements-final.json`
4. 提交 Pull Request

## 数据结构

插件使用以下数据字段：

| 字段 | 说明 | 示例 |
|------|------|------|
| id | 数据ID | 1 |
| pet | 精灵名称 | 阿米亚特 |
| eggDiameter | 蛋尺寸范围 | 0.25-0.32 |
| eggWeight | 蛋重量范围 | 14.417-18.659 |

## 注意事项

1. **首次使用**：必须先执行 `/同步蛋数据` 下载数据
2. **网络要求**：同步数据需要访问 GitHub，国内用户可能需要配置代理
3. **定时同步**：需要安装 `croniter` 依赖
4. **管理员审核**：建议配置 `data_collection_review_mode` 为 `strict`
5. **数据备份**：用户提交的数据保存在 `data/plugin_data/rocoegg/user_submissions.json`

## 技术架构

### 数据流

```
上游仓库 (GitHub)
    ↓ 同步
本地数据 (JSON)
    ↓ 查询
用户查询结果
    ↓ 反馈
用户提交数据
    ↓ 审核
管理员审核
    ↓ 导出
导出文件 (兼容上游格式)
```

### 存储位置

- **主数据**: `data/plugin_data/rocoegg/egg-measurements-final.json`
- **同步信息**: `data/plugin_data/rocoegg/sync_info.json`
- **用户提交**: `data/plugin_data/rocoegg/user_submissions.json`
- **导出数据**: `data/plugin_data/rocoegg/user_export.json`

## 致谢

- 数据源: [RocoEgg](https://github.com/mfskys/rocomegg) by mfskys
- 框架: [AstraBot](https://github.com/AstrBotDevs/AstrBot)

## License

MIT License
